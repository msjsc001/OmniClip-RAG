import math
import sys
import threading
import time
import types
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.errors import RuntimeDependencyError
from omniclip_rag.vector_index import LanceDbVectorIndex, create_vector_index, is_local_model_ready, model_download_guidance_context, runtime_dependency_issue, runtime_guidance_context


ROOT = Path(__file__).resolve().parents[1]
TEST_DATA_ROOT = ROOT / ".tmp" / "test_vector_data"


class FakeEmbedder:
    def encode(self, texts, *, batch_size=16, show_progress_bar=False, normalize_embeddings=True):
        vectors = []
        for text in texts:
            length = float(len(text))
            checksum = float(sum(ord(char) for char in text) % 997)
            vowels = float(sum(char in "aeiouAEIOU块嵌入热更" for char in text))
            vectors.append([length, checksum, vowels])
        return vectors


class _FakeFieldType:
    def __init__(self, list_size: int):
        self.list_size = list_size


class _FakeField:
    def __init__(self, list_size: int):
        self.type = _FakeFieldType(list_size)


class _FakeSchema:
    def __init__(self, list_size: int):
        self._field = _FakeField(list_size)

    def field(self, _name: str):
        return self._field


class _FakeSearchQuery:
    def __init__(self, rows, vector):
        ranked = []
        for row in rows:
            distance = math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(row["vector"], vector, strict=True)))
            ranked.append({**row, "_distance": distance})
        ranked.sort(key=lambda item: item["_distance"])
        self._rows = ranked
        self._limit = len(ranked)

    def limit(self, limit: int):
        self._limit = limit
        return self

    def to_list(self):
        return self._rows[: self._limit]


class _FakeTable:
    def __init__(self, db, name: str, schema):
        self._db = db
        self.name = name
        self.schema = schema
        self.rows: list[dict[str, object]] = []
        self.add_calls = 0
        self.add_batch_sizes: list[int] = []

    def add(self, rows):
        self.add_calls += 1
        batch_rows = [dict(row) for row in rows]
        self.add_batch_sizes.append(len(batch_rows))
        self.rows.extend(batch_rows)

    def delete(self, condition: str):
        if "chunk_id IN" not in condition:
            return
        inside = condition.split("(", 1)[1].rsplit(")", 1)[0]
        ids = {item.strip().strip("'") for item in inside.split(",") if item.strip()}
        self.rows = [row for row in self.rows if row["chunk_id"] not in ids]

    def search(self, vector):
        return _FakeSearchQuery(self.rows, vector)


class _FakeDb:
    def __init__(self, table_cls=_FakeTable):
        self.tables: dict[str, _FakeTable] = {}
        self._table_cls = table_cls

    def create_table(self, name: str, schema=None, mode="overwrite"):
        if mode == "overwrite" or name not in self.tables:
            self.tables[name] = self._table_cls(self, name, schema)
        return self.tables[name]

    def drop_table(self, name: str):
        self.tables.pop(name, None)

    def open_table(self, name: str):
        return self.tables[name]

    def list_tables(self):
        return list(self.tables.keys())


class _MemoryPressureTable(_FakeTable):
    def add(self, rows):
        self.add_calls += 1
        batch_rows = [dict(row) for row in rows]
        self.add_batch_sizes.append(len(batch_rows))
        if len(batch_rows) > 8:
            raise MemoryError('out of memory while adding rows')
        self.rows.extend(batch_rows)


def _fake_lancedb_modules(table_cls=_FakeTable) -> dict[str, object]:
    fake_db = _FakeDb(table_cls=table_cls)
    fake_lancedb = types.ModuleType("lancedb")
    fake_lancedb.connect = lambda _path: fake_db

    fake_pyarrow = types.ModuleType("pyarrow")
    fake_pyarrow.string = lambda: "string"
    fake_pyarrow.float32 = lambda: "float32"
    fake_pyarrow.list_ = lambda _inner, dimension: ("list", dimension)
    fake_pyarrow.field = lambda name, spec: (name, spec)
    fake_pyarrow.schema = lambda fields: _FakeSchema(fields[-1][1][1])
    return {"lancedb": fake_lancedb, "pyarrow": fake_pyarrow}


class VectorIndexTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_DATA_ROOT.exists():
            shutil.rmtree(TEST_DATA_ROOT)

    def test_factory_returns_null_when_disabled(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "factory"))
        config = AppConfig(vault_path=str(ROOT), data_root=str(data_paths.global_root))
        index = create_vector_index(config, data_paths)
        self.assertEqual(index.search("anything", 5), [])

    def test_factory_returns_runtime_placeholder_when_lancedb_runtime_is_missing(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "missing_runtime"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend="lancedb",
            vector_runtime="torch",
            vector_device="cuda",
        )
        with patch('omniclip_rag.vector_index.LanceDbVectorIndex', side_effect=ModuleNotFoundError('No module named lancedb')):
            index = create_vector_index(config, data_paths)
        self.assertEqual(index.search("anything", 5), [])
        with self.assertRaises(RuntimeDependencyError) as context:
            index.warmup()
        self.assertIn('install_runtime.ps1', str(context.exception).lower())

    def test_runtime_guidance_context_reports_runtime_folder_status(self) -> None:
        app_root = TEST_DATA_ROOT / 'app_root'
        runtime_dir = app_root / 'runtime'
        app_root.mkdir(parents=True, exist_ok=True)
        with patch('omniclip_rag.vector_index._application_root_dir', return_value=app_root), \
             patch('omniclip_rag.vector_index._ACCELERATION_CACHE', None):
            context = runtime_guidance_context('torch', 'cuda', force_refresh=True)
            self.assertFalse(context['runtime_exists'])
            self.assertFalse(context['runtime_complete'])
            self.assertIn('runtime 文件夹：未检测到', context['plain_text'])
            self.assertIn('Set-Location -LiteralPath', context['install_command'])

            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / '_runtime_bootstrap.json').write_text('{}', encoding='utf-8')
            (runtime_dir / 'torch').mkdir(exist_ok=True)
            incomplete = runtime_guidance_context('torch', 'cuda', force_refresh=True)
        self.assertTrue(incomplete['runtime_exists'])
        self.assertFalse(incomplete['runtime_complete'])
        self.assertTrue(incomplete['runtime_missing_items'])

    def test_runtime_dependency_issue_returns_guidance_when_imports_are_missing(self) -> None:
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(TEST_DATA_ROOT),
            vector_backend='lancedb',
            vector_runtime='torch',
            vector_device='auto',
        )

        def fake_import(name: str):
            if name == 'lancedb':
                raise ModuleNotFoundError('No module named lancedb')
            return object()

        with patch('omniclip_rag.vector_index.runtime_guidance_context', return_value={'plain_text': 'guidance'}) as guidance_mock,              patch('omniclip_rag.vector_index.importlib.import_module', side_effect=fake_import):
            issue = runtime_dependency_issue(config)

        self.assertEqual(issue, 'guidance')
        guidance_mock.assert_called_once()

    def test_lancedb_backend_rebuild_search_and_delete(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "main"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend="lancedb",
        )
        with patch.dict(sys.modules, _fake_lancedb_modules()):
            index = LanceDbVectorIndex(config, data_paths, embedder_factory=FakeEmbedder)
            index.rebuild(
                [
                    {
                        "chunk_id": "a",
                        "source_path": "pages/a.md",
                        "title": "A",
                        "anchor": "A",
                        "rendered_text": "块嵌入 示例",
                    },
                    {
                        "chunk_id": "b",
                        "source_path": "pages/b.md",
                        "title": "B",
                        "anchor": "B",
                        "rendered_text": "完全不同的内容",
                    },
                ]
            )
            hits = index.search("块嵌入", 2)
            self.assertTrue(hits)
            self.assertEqual(hits[0].chunk_id, "a")

            index.upsert(
                [
                    {
                        "chunk_id": "c",
                        "source_path": "pages/c.md",
                        "title": "C",
                        "anchor": "C",
                        "rendered_text": "热更新验证",
                    }
                ]
            )
            hits = index.search("热更新验证", 2)
            self.assertEqual(hits[0].chunk_id, "c")

            index.delete(["c"])
            hits = index.search("热更新验证", 3)
            self.assertTrue(all(hit.chunk_id != "c" for hit in hits))

            index.reset()
            self.assertEqual(index.search("块嵌入", 2), [])

    def test_lancedb_rebuild_accepts_iterable_stream(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "iterable_rebuild"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend="lancedb",
            vector_batch_size=2,
        )
        with patch.dict(sys.modules, _fake_lancedb_modules()):
            index = LanceDbVectorIndex(config, data_paths, embedder_factory=FakeEmbedder)
            documents = (
                {
                    "chunk_id": f"stream-{index_id}",
                    "source_path": f"pages/{index_id}.md",
                    "title": f"T{index_id}",
                    "anchor": f"A{index_id}",
                    "rendered_text": f"内容 {index_id}",
                }
                for index_id in range(5)
            )
            index.rebuild(documents, total=5)
            hits = index.search("内容 4", 2)
            self.assertTrue(hits)
            self.assertEqual(hits[0].chunk_id, "stream-4")


    def test_lancedb_rebuild_waits_while_paused_and_reports_progress(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "paused_rebuild"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend="lancedb",
            vector_batch_size=2,
        )
        documents = [
            {
                "chunk_id": f"c{index}",
                "source_path": f"pages/{index}.md",
                "title": f"T{index}",
                "anchor": f"A{index}",
                "rendered_text": f"chunk {index}",
            }
            for index in range(5)
        ]
        progress: list[dict[str, object]] = []
        pause_event = threading.Event()
        pause_event.set()
        outcome: dict[str, object] = {}

        def worker(index: LanceDbVectorIndex) -> None:
            try:
                index.rebuild(documents, on_progress=progress.append, pause_event=pause_event)
                outcome['done'] = True
            except Exception as exc:
                outcome['error'] = exc

        with patch.dict(sys.modules, _fake_lancedb_modules()):
            index = LanceDbVectorIndex(config, data_paths, embedder_factory=FakeEmbedder)
            thread = threading.Thread(target=worker, args=(index,), daemon=True)
            thread.start()
            time.sleep(0.2)
            self.assertTrue(thread.is_alive())
            self.assertEqual(progress, [])
            pause_event.clear()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertNotIn('error', outcome)
            self.assertTrue(progress)
            self.assertEqual(progress[-1]['current'], len(documents))
            self.assertEqual(progress[-1]['total'], len(documents))
            self.assertIn('build_profile', progress[-1])
            self.assertIn('encode_batch_size', progress[-1])
            self.assertIn('write_batch_size', progress[-1])
            self.assertIn('encoded_count', progress[-1])
            self.assertIn('written_count', progress[-1])
            self.assertIn('write_queue_depth', progress[-1])
            self.assertIn('write_queue_capacity', progress[-1])
            self.assertIn('write_flush_count', progress[-1])

    def test_lancedb_rebuild_reports_pipeline_metrics(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "pipeline_metrics"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend="lancedb",
            vector_batch_size=2,
        )
        documents = [
            {
                "chunk_id": f"m{index}",
                "source_path": f"pages/{index}.md",
                "title": f"T{index}",
                "anchor": f"A{index}",
                "rendered_text": f"metric chunk {index}",
            }
            for index in range(12)
        ]
        progress: list[dict[str, object]] = []
        with patch.dict(sys.modules, _fake_lancedb_modules()):
            index = LanceDbVectorIndex(config, data_paths, embedder_factory=FakeEmbedder)
            index.rebuild(documents, on_progress=progress.append)
        self.assertTrue(progress)
        final = progress[-1]
        self.assertEqual(final['current'], len(documents))
        self.assertEqual(final['written_count'], len(documents))
        self.assertEqual(final['encoded_count'], len(documents))
        self.assertGreaterEqual(int(final['write_queue_capacity']), 1)
        self.assertGreaterEqual(int(final['write_flush_count']), 1)
        self.assertGreaterEqual(float(final['encode_elapsed_total_ms']), 0.0)
        self.assertGreaterEqual(float(final['prepare_elapsed_total_ms']), 0.0)
        self.assertGreaterEqual(float(final['write_elapsed_total_ms']), 0.0)


    def test_lancedb_rebuild_caps_tail_write_batches(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "tail_write_cap"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend="lancedb",
            vector_batch_size=64,
            build_resource_profile='peak',
        )
        documents = [
            {
                "chunk_id": f"cap{index}",
                "source_path": f"pages/{index}.md",
                "title": f"T{index}",
                "anchor": f"A{index}",
                "rendered_text": f"tail cap chunk {index}",
            }
            for index in range(260)
        ]
        with patch.dict(sys.modules, _fake_lancedb_modules()):
            index = LanceDbVectorIndex(config, data_paths, embedder_factory=FakeEmbedder)
            index.rebuild(documents, on_progress=lambda _payload: None)
            table = index._table()
        self.assertGreater(table.add_calls, 1)
        self.assertLessEqual(max(table.add_batch_sizes), 384)

    def test_lancedb_rebuild_retries_smaller_write_batches_after_memory_pressure(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "memory_pressure_retry"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend="lancedb",
            vector_batch_size=64,
            build_resource_profile='peak',
        )
        documents = [
            {
                "chunk_id": f"retry{index}",
                "source_path": f"pages/{index}.md",
                "title": f"T{index}",
                "anchor": f"A{index}",
                "rendered_text": f"retry chunk {index}",
            }
            for index in range(40)
        ]
        progress: list[dict[str, object]] = []
        with patch.dict(sys.modules, _fake_lancedb_modules(_MemoryPressureTable)):
            index = LanceDbVectorIndex(config, data_paths, embedder_factory=FakeEmbedder)
            index.rebuild(documents, on_progress=progress.append)
            table = index._table()
        self.assertEqual(len(table.rows), len(documents))
        self.assertTrue(any(size <= 8 for size in table.add_batch_sizes))
        self.assertEqual(progress[-1]['written_count'], len(documents))

    def test_lancedb_rebuild_trims_oversized_texts_before_encoding(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "oversized_trim"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend="lancedb",
            vector_batch_size=3,
            vector_device='cpu',
        )
        documents = [
            {
                "chunk_id": f"long{index}",
                "source_path": f"pages/{index}.md",
                "title": f"T{index}",
                "anchor": f"A{index}",
                "rendered_text": "超长片段" * 6000,
            }
            for index in range(3)
        ]
        seen_batches: list[list[str]] = []

        class RecordingEmbedder:
            def encode(self, texts, *, batch_size=16, show_progress_bar=False, normalize_embeddings=True):
                seen_batches.append(list(texts))
                return [[float(len(text)), 1.0, 0.0] for text in texts]

        with patch.dict(sys.modules, _fake_lancedb_modules()):
            index = LanceDbVectorIndex(config, data_paths, embedder_factory=RecordingEmbedder)
            index.rebuild(documents)

        self.assertTrue(seen_batches)
        self.assertTrue(all(len(text) <= 8000 for batch in seen_batches for text in batch))
        self.assertEqual([len(batch) for batch in seen_batches], [2, 1])

    def test_lancedb_rebuild_emits_encoding_heartbeat_for_slow_batches(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "slow_encode_heartbeat"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend="lancedb",
            vector_batch_size=1,
            vector_device='cpu',
        )
        progress: list[dict[str, object]] = []

        class SlowEmbedder:
            def encode(self, texts, *, batch_size=16, show_progress_bar=False, normalize_embeddings=True):
                time.sleep(0.35)
                return [[float(len(text)), 1.0, 0.0] for text in texts]

        documents = [
            {
                "chunk_id": "slow",
                "source_path": "pages/slow.md",
                "title": "Slow",
                "anchor": "Slow",
                "rendered_text": "这个批次会被故意放慢",
            }
        ]

        with patch('omniclip_rag.vector_index._VECTOR_PROGRESS_HEARTBEAT_SECONDS', 0.05), \
             patch('omniclip_rag.vector_index._VECTOR_STALL_STACK_DUMP_SECONDS', 99.0), \
             patch.dict(sys.modules, _fake_lancedb_modules()):
            index = LanceDbVectorIndex(config, data_paths, embedder_factory=SlowEmbedder)
            index.rebuild(documents, on_progress=progress.append)

        self.assertTrue(any(item.get('stage_status') == 'encoding' and int(item.get('current', 0) or 0) == 0 for item in progress))
        self.assertEqual(progress[-1]['written_count'], 1)

    def test_lancedb_warmup_returns_dimension(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "warmup"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend="lancedb",
            vector_model="BAAI/bge-m3",
        )
        with patch.dict(sys.modules, _fake_lancedb_modules()):
            index = LanceDbVectorIndex(config, data_paths, embedder_factory=FakeEmbedder)
            result = index.warmup()
        self.assertEqual(result["backend"], "lancedb")
        self.assertEqual(result["model"], "BAAI/bge-m3")
        self.assertEqual(result["dimension"], 3)
        self.assertFalse(result["model_ready"])

    def test_model_ready_requires_weight_file(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "ready"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend="lancedb",
            vector_model="BAAI/bge-m3",
            vector_runtime="torch",
        )
        model_dir = data_paths.cache_dir / "models" / "BAAI__bge-m3"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "modules.json").write_text("{}", encoding="utf-8")
        (model_dir / "config.json").write_text("{}", encoding="utf-8")
        self.assertFalse(is_local_model_ready(config, data_paths))
        (model_dir / "pytorch_model.bin").write_bytes(b"ok")
        self.assertTrue(is_local_model_ready(config, data_paths))

    def test_model_download_guidance_context_builds_commands_and_dirs(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "manual_model_context"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_model="BAAI/bge-m3",
        )
        result = model_download_guidance_context(config, data_paths)
        self.assertEqual(result["model"], "BAAI/bge-m3")
        self.assertTrue(Path(result["model_dir"]).exists())
        self.assertTrue(Path(result["hf_home_dir"]).exists())
        self.assertIn('https://huggingface.co/BAAI/bge-m3', result["official_url"])
        self.assertIn('https://hf-mirror.com/BAAI/bge-m3', result["mirror_url"])
        self.assertIn('hf download', result["official_download_command"])
        self.assertIn(result["model_dir"], result["official_download_command"])
        self.assertIn('HF_ENDPOINT', result["mirror_download_command"])

    def test_default_embedder_downloads_model_into_local_cache_without_symlink(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "bootstrap"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend="lancedb",
            vector_model="BAAI/bge-m3",
            vector_runtime="torch",
        )
        calls: dict[str, object] = {}

        def fake_snapshot_download(**kwargs):
            calls["snapshot"] = kwargs
            local_dir = Path(kwargs["local_dir"])
            local_dir.mkdir(parents=True, exist_ok=True)
            (local_dir / "modules.json").write_text("{}", encoding="utf-8")
            (local_dir / "config.json").write_text("{}", encoding="utf-8")
            (local_dir / "pytorch_model.bin").write_bytes(b"ok")
            return str(local_dir)

        class FakeSentenceTransformer:
            def __init__(self, model_name_or_path, **kwargs):
                calls["sentence_transformer"] = {"model_name_or_path": model_name_or_path, **kwargs}

        fake_hub = types.ModuleType("huggingface_hub")
        fake_hub.snapshot_download = fake_snapshot_download
        fake_hub.constants = types.SimpleNamespace(
            HF_HOME="",
            hf_cache_home="",
            HF_HUB_CACHE="",
            HUGGINGFACE_HUB_CACHE="",
            HUGGINGFACE_ASSETS_CACHE="",
            HF_XET_CACHE="",
            HF_HUB_DISABLE_XET=False,
        )
        fake_sentence_transformers = types.ModuleType("sentence_transformers")
        fake_sentence_transformers.SentenceTransformer = FakeSentenceTransformer

        with patch.dict(
            sys.modules,
            {
                **_fake_lancedb_modules(),
                "huggingface_hub": fake_hub,
                "sentence_transformers": fake_sentence_transformers,
            },
        ):
            index = LanceDbVectorIndex(config, data_paths)
            embedder = index._default_embedder_factory()

        self.assertIsInstance(embedder, FakeSentenceTransformer)
        snapshot = calls["snapshot"]
        runtime = calls["sentence_transformer"]
        self.assertEqual(snapshot["repo_id"], "BAAI/bge-m3")
        self.assertFalse(snapshot["local_files_only"])
        self.assertEqual(runtime["model_name_or_path"], snapshot["local_dir"])
        self.assertTrue(runtime["local_files_only"])
        self.assertEqual(runtime["backend"], "torch")
        self.assertEqual(Path(fake_hub.constants.HF_HOME).parent.name, "models")
        self.assertTrue(fake_hub.constants.HF_HUB_DISABLE_XET)

    def test_default_embedder_reports_missing_runtime_cleanly(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "runtime_missing"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend="lancedb",
            vector_model="BAAI/bge-m3",
            vector_runtime="torch",
            vector_device="cuda",
        )
        model_dir = data_paths.cache_dir / "models" / "BAAI__bge-m3"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "modules.json").write_text("{}", encoding="utf-8")
        (model_dir / "config.json").write_text("{}", encoding="utf-8")
        (model_dir / "pytorch_model.bin").write_bytes(b"ok")

        fake_hub = types.ModuleType("huggingface_hub")
        fake_hub.snapshot_download = lambda **kwargs: None
        fake_hub.constants = types.SimpleNamespace(
            HF_HOME="",
            hf_cache_home="",
            HF_HUB_CACHE="",
            HUGGINGFACE_HUB_CACHE="",
            HUGGINGFACE_ASSETS_CACHE="",
            HF_XET_CACHE="",
            HF_HUB_DISABLE_XET=False,
        )

        with patch.dict(
            sys.modules,
            {
                **_fake_lancedb_modules(),
                "huggingface_hub": fake_hub,
                "sentence_transformers": None,
            },
        ):
            index = LanceDbVectorIndex(config, data_paths)
            with self.assertRaises(RuntimeDependencyError) as ctx:
                index._default_embedder_factory()

        message = str(ctx.exception)
        self.assertIn('install_runtime.ps1', message.lower())
        self.assertIn('sentence-transformers', message)
        self.assertIn('Set-Location -LiteralPath', message)
        self.assertIn('第二步：在 Windows 终端里安装 runtime', message)
        self.assertIn('如果只使用CPU', message)
        self.assertNotIn('说明文档', message)

    def test_default_embedder_skips_network_when_local_model_is_ready(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "offline_ready"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend="lancedb",
            vector_model="BAAI/bge-m3",
            vector_runtime="torch",
        )
        model_dir = data_paths.cache_dir / "models" / "BAAI__bge-m3"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "modules.json").write_text("{}", encoding="utf-8")
        (model_dir / "config.json").write_text("{}", encoding="utf-8")
        (model_dir / "pytorch_model.bin").write_bytes(b"ok")
        calls: dict[str, int] = {"snapshot": 0, "sentence": 0}

        def fake_snapshot_download(**kwargs):
            calls["snapshot"] += 1
            raise AssertionError("snapshot_download should not be called when local model is ready")

        class FakeSentenceTransformer:
            def __init__(self, model_name_or_path, **kwargs):
                calls["sentence"] += 1
                self.model_name_or_path = model_name_or_path
                self.kwargs = kwargs

        fake_hub = types.ModuleType("huggingface_hub")
        fake_hub.snapshot_download = fake_snapshot_download
        fake_hub.constants = types.SimpleNamespace(
            HF_HOME="",
            hf_cache_home="",
            HF_HUB_CACHE="",
            HUGGINGFACE_HUB_CACHE="",
            HUGGINGFACE_ASSETS_CACHE="",
            HF_XET_CACHE="",
            HF_HUB_DISABLE_XET=False,
        )
        fake_sentence_transformers = types.ModuleType("sentence_transformers")
        fake_sentence_transformers.SentenceTransformer = FakeSentenceTransformer

        with patch.dict(
            sys.modules,
            {
                **_fake_lancedb_modules(),
                "huggingface_hub": fake_hub,
                "sentence_transformers": fake_sentence_transformers,
            },
        ):
            index = LanceDbVectorIndex(config, data_paths)
            embedder = index._default_embedder_factory()

        self.assertIsInstance(embedder, FakeSentenceTransformer)
        self.assertEqual(calls["snapshot"], 0)
        self.assertEqual(calls["sentence"], 1)


if __name__ == "__main__":
    unittest.main()
