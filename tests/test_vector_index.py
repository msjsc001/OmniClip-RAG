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
from omniclip_rag.vector_index import LanceDbVectorIndex, create_vector_index, is_local_model_ready


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

    def add(self, rows):
        for row in rows:
            self.rows.append(dict(row))

    def delete(self, condition: str):
        if "chunk_id IN" not in condition:
            return
        inside = condition.split("(", 1)[1].rsplit(")", 1)[0]
        ids = {item.strip().strip("'") for item in inside.split(",") if item.strip()}
        self.rows = [row for row in self.rows if row["chunk_id"] not in ids]

    def search(self, vector):
        return _FakeSearchQuery(self.rows, vector)


class _FakeDb:
    def __init__(self):
        self.tables: dict[str, _FakeTable] = {}

    def create_table(self, name: str, schema=None, mode="overwrite"):
        if mode == "overwrite" or name not in self.tables:
            self.tables[name] = _FakeTable(self, name, schema)
        return self.tables[name]

    def drop_table(self, name: str):
        self.tables.pop(name, None)

    def open_table(self, name: str):
        return self.tables[name]

    def list_tables(self):
        return list(self.tables.keys())


def _fake_lancedb_modules() -> dict[str, object]:
    fake_db = _FakeDb()
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
