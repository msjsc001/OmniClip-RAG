import builtins
import gc
import json
from contextlib import nullcontext
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
from omniclip_rag.canary_backend import CANARY_VECTOR_MODEL_ID
from omniclip_rag.vector_index import LanceDbVectorIndex, _MODEL_DOWNLOAD_IGNORE_PATTERNS, _discover_active_runtime_dir, _preferred_runtime_dir_path, _probe_runtime_semantic_core_inprocess, _runtime_component_dependency_ids, _runtime_import_environment, _runtime_search_roots, _sanitize_local_model_snapshot, build_runtime_install_command, create_vector_index, detect_acceleration, inspect_runtime_environment, is_local_model_ready, model_download_guidance_context, prepare_local_model_snapshot, probe_runtime_gpu_execution, refresh_runtime_capability_snapshot, runtime_dependency_issue, runtime_guidance_context, runtime_management_snapshot, probe_runtime_gpu_query_execution


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


class _StrictQueryTypeTable(_FakeTable):
    def search(self, vector):
        if not isinstance(vector, list):
            raise TypeError(f'Unsupported query type: {type(vector)}')
        return super().search(vector)


def _fake_lancedb_modules(table_cls=_FakeTable) -> dict[str, object]:
    fake_db = _FakeDb(table_cls=table_cls)
    fake_lancedb = types.ModuleType("lancedb")
    fake_lancedb.__file__ = str(ROOT / 'runtime' / 'lancedb' / '__init__.py')
    fake_lancedb.connect = lambda _path: fake_db

    fake_pyarrow = types.ModuleType("pyarrow")
    fake_pyarrow.__file__ = str(ROOT / 'runtime' / 'pyarrow' / '__init__.py')
    fake_pyarrow.string = lambda: "string"
    fake_pyarrow.float32 = lambda: "float32"
    fake_pyarrow.list_ = lambda _inner, dimension: ("list", dimension)
    fake_pyarrow.field = lambda name, spec: (name, spec)
    fake_pyarrow.schema = lambda fields: _FakeSchema(fields[-1][1][1])
    return {"lancedb": fake_lancedb, "pyarrow": fake_pyarrow}


def _write_minimal_vector_store_runtime(runtime_root: Path) -> None:
    runtime_root = Path(runtime_root)
    live_roots = [
        runtime_root,
        runtime_root / 'components' / 'vector-store',
    ]
    for live_root in live_roots:
        for package_name in ('pyarrow', 'pandas', 'lancedb', 'onnxruntime'):
            package_dir = live_root / package_name
            package_dir.mkdir(parents=True, exist_ok=True)
            (package_dir / '__init__.py').write_text('', encoding='utf-8')


def _runtime_root_patches(app_root: Path):
    runtime_dir = app_root / 'runtime'
    return patch.multiple(
        'omniclip_rag.vector_index',
        _application_root_dir=lambda: app_root,
        _preferred_runtime_dir_path=lambda: runtime_dir,
    )


def _reset_runtime_test_modules() -> None:
    for module_name in (
        'torch',
        'sentence_transformers',
        'transformers',
        'huggingface_hub',
        'safetensors',
        'numpy',
        'scipy',
        'pyarrow',
        'pandas',
        'lancedb',
        'onnxruntime',
    ):
        sys.modules.pop(module_name, None)


def _rmtree_retry(path: Path, *, attempts: int = 5) -> None:
    if not path.exists():
        return

    def _onerror(func, target, _exc_info):
        target_path = Path(target)
        try:
            target_path.chmod(0o700)
        except Exception:
            pass
        try:
            func(target)
        except Exception:
            return

    last_error: Exception | None = None
    for _ in range(max(int(attempts or 1), 1)):
        try:
            shutil.rmtree(path, onerror=_onerror)
            return
        except Exception as exc:
            last_error = exc
            _reset_runtime_test_modules()
            gc.collect()
            time.sleep(0.1)
    if path.exists() and last_error is not None:
        raise last_error


class VectorIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_dont_write_bytecode = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        _reset_runtime_test_modules()
        _rmtree_retry(TEST_DATA_ROOT)

    def tearDown(self) -> None:
        _reset_runtime_test_modules()
        gc.collect()
        sys.dont_write_bytecode = self._previous_dont_write_bytecode
        _rmtree_retry(TEST_DATA_ROOT)

    def test_factory_returns_null_when_disabled(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "factory"))
        config = AppConfig(vault_path=str(ROOT), data_root=str(data_paths.global_root))
        index = create_vector_index(config, data_paths)
        self.assertEqual(index.search("anything", 5), [])

    def test_builtin_canary_model_is_ready_without_download(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "canary_model"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend='lancedb',
            vector_model=CANARY_VECTOR_MODEL_ID,
            vector_device='cpu',
        )
        self.assertTrue(is_local_model_ready(config, data_paths))
        index = LanceDbVectorIndex(config, data_paths)
        embedder = index._default_embedder_factory()
        self.assertEqual(getattr(embedder, 'device', ''), 'cpu')

    def test_sanitize_local_model_snapshot_strips_nested_config_dicts(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "sanitize_model"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend='lancedb',
        )
        model_dir = Path(prepare_local_model_snapshot.__globals__['get_local_model_dir'](config, data_paths))
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / 'modules.json').write_text(
            json.dumps(
                [
                    {'idx': 0, 'name': 'transformer', 'path': '0_Transformer'},
                    {'idx': 1, 'name': 'pooling', 'path': '1_Pooling'},
                ],
                ensure_ascii=False,
            ) + '\n',
            encoding='utf-8',
        )
        (model_dir / 'config.json').write_text(
            json.dumps({'model_type': 'xlm-roberta', 'transformers_version': '4.33.0'}) + '\n',
            encoding='utf-8',
        )
        (model_dir / 'pytorch_model.bin').write_bytes(b'weights')
        (model_dir / 'tokenizer_config.json').write_text(
            json.dumps({'tokenizer_class': 'XLMRobertaTokenizer', 'config': {'model_type': 'xlm-roberta'}}, ensure_ascii=False) + '\n',
            encoding='utf-8',
        )
        (model_dir / 'sentence_bert_config.json').write_text(
            json.dumps(
                {
                    'max_seq_length': 8192,
                    'tokenizer_args': {'config': {'model_type': 'xlm-roberta'}},
                    'config_args': {'trust_remote_code': False},
                },
                ensure_ascii=False,
            ) + '\n',
            encoding='utf-8',
        )
        transformer_dir = model_dir / '0_Transformer'
        transformer_dir.mkdir(parents=True, exist_ok=True)
        (transformer_dir / 'tokenizer_config.json').write_text(
            json.dumps(
                {
                    'tokenizer_class': 'XLMRobertaTokenizer',
                    'tokenizer_args': {'padding_side': 'right'},
                    'config': {'model_type': 'xlm-roberta'},
                },
                ensure_ascii=False,
            ) + '\n',
            encoding='utf-8',
        )
        pooling_dir = model_dir / '1_Pooling'
        pooling_dir.mkdir(parents=True, exist_ok=True)
        (pooling_dir / 'config.json').write_text(
            json.dumps(
                {
                    'pooling_mode_mean_tokens': True,
                    'config_args': {'config': {'model_type': 'xlm-roberta'}},
                },
                ensure_ascii=False,
            ) + '\n',
            encoding='utf-8',
        )

        repaired = _sanitize_local_model_snapshot(model_dir)

        self.assertEqual(
            set(repaired),
            {
                'config.json',
                'tokenizer_config.json',
                'sentence_bert_config.json',
                '0_Transformer/tokenizer_config.json',
                '1_Pooling/config.json',
            },
        )
        root_config_payload = json.loads((model_dir / 'config.json').read_text(encoding='utf-8'))
        tokenizer_payload = json.loads((model_dir / 'tokenizer_config.json').read_text(encoding='utf-8'))
        sentence_payload = json.loads((model_dir / 'sentence_bert_config.json').read_text(encoding='utf-8'))
        transformer_payload = json.loads((transformer_dir / 'tokenizer_config.json').read_text(encoding='utf-8'))
        pooling_payload = json.loads((pooling_dir / 'config.json').read_text(encoding='utf-8'))
        self.assertNotIn('transformers_version', root_config_payload)
        self.assertNotIn('config', tokenizer_payload)
        self.assertNotIn('config', sentence_payload['tokenizer_args'])
        self.assertNotIn('config', transformer_payload)
        self.assertNotIn('config', pooling_payload['config_args'])

    def test_sanitize_local_model_snapshot_keeps_clean_configs_unchanged(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "sanitize_model_clean"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend='lancedb',
        )
        model_dir = Path(prepare_local_model_snapshot.__globals__['get_local_model_dir'](config, data_paths))
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / 'modules.json').write_text(
            json.dumps([{'idx': 0, 'name': 'transformer', 'path': '0_Transformer'}], ensure_ascii=False) + '\n',
            encoding='utf-8',
        )
        (model_dir / 'config.json').write_text(json.dumps({'model_type': 'xlm-roberta'}) + '\n', encoding='utf-8')
        (model_dir / 'pytorch_model.bin').write_bytes(b'weights')
        root_tokenizer_path = model_dir / 'tokenizer_config.json'
        root_tokenizer_text = json.dumps({'tokenizer_class': 'XLMRobertaTokenizer'}, ensure_ascii=False) + '\n'
        root_tokenizer_path.write_text(root_tokenizer_text, encoding='utf-8')
        transformer_dir = model_dir / '0_Transformer'
        transformer_dir.mkdir(parents=True, exist_ok=True)
        nested_config_path = transformer_dir / 'config.json'
        nested_config_text = json.dumps({'hidden_size': 1024, 'model_type': 'xlm-roberta'}, ensure_ascii=False) + '\n'
        nested_config_path.write_text(nested_config_text, encoding='utf-8')

        repaired = _sanitize_local_model_snapshot(model_dir)

        self.assertEqual(repaired, [])
        self.assertEqual(root_tokenizer_path.read_text(encoding='utf-8'), root_tokenizer_text)
        self.assertEqual(nested_config_path.read_text(encoding='utf-8'), nested_config_text)

    def test_default_embedder_factory_retries_after_sanitizing_nested_model_config(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "sanitize_model_retry"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend='lancedb',
            vector_model='BAAI/bge-m3',
            vector_runtime='torch',
            vector_device='cpu',
        )
        model_dir = data_paths.cache_dir / 'models' / 'BAAI__bge-m3'
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / 'modules.json').write_text(
            json.dumps([{'idx': 0, 'name': 'transformer', 'path': '0_Transformer'}], ensure_ascii=False) + '\n',
            encoding='utf-8',
        )
        (model_dir / 'config.json').write_text(json.dumps({'model_type': 'xlm-roberta'}) + '\n', encoding='utf-8')
        (model_dir / 'pytorch_model.bin').write_bytes(b'weights')
        transformer_dir = model_dir / '0_Transformer'
        transformer_dir.mkdir(parents=True, exist_ok=True)
        nested_tokenizer_path = transformer_dir / 'tokenizer_config.json'
        nested_tokenizer_path.write_text(
            json.dumps(
                {
                    'tokenizer_class': 'XLMRobertaTokenizer',
                    'config': {'model_type': 'xlm-roberta'},
                },
                ensure_ascii=False,
            ) + '\n',
            encoding='utf-8',
        )
        calls = {'count': 0}

        class FakeSentenceTransformer:
            def __init__(self, model_name_or_path, **kwargs):
                calls['count'] += 1
                payload = json.loads((Path(model_name_or_path) / '0_Transformer' / 'tokenizer_config.json').read_text(encoding='utf-8'))
                if isinstance(payload.get('config'), dict):
                    raise AttributeError("'dict' object has no attribute 'model_type'")
                self.model_name_or_path = model_name_or_path
                self.kwargs = kwargs

        fake_sentence_transformers = types.ModuleType("sentence_transformers")
        fake_sentence_transformers.SentenceTransformer = FakeSentenceTransformer

        with patch.dict(sys.modules, {"sentence_transformers": fake_sentence_transformers}), \
             patch('omniclip_rag.vector_index.prepare_local_model_snapshot', return_value={'model_ready': True, 'local_model_dir': str(model_dir)}), \
             patch('omniclip_rag.vector_index._runtime_import_environment', side_effect=lambda **_: nullcontext()):
            index = LanceDbVectorIndex(config, data_paths)
            embedder = index._default_embedder_factory()

        self.assertIsInstance(embedder, FakeSentenceTransformer)
        self.assertEqual(calls['count'], 2)
        repaired_payload = json.loads(nested_tokenizer_path.read_text(encoding='utf-8'))
        self.assertNotIn('config', repaired_payload)

    def test_default_embedder_factory_retries_after_stripping_transformers_version_metadata(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "sanitize_model_transformers_version_retry"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend='lancedb',
            vector_model='BAAI/bge-m3',
            vector_runtime='torch',
            vector_device='cpu',
        )
        model_dir = data_paths.cache_dir / 'models' / 'BAAI__bge-m3'
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / 'modules.json').write_text(
            json.dumps([{'idx': 0, 'name': 'transformer', 'path': ''}], ensure_ascii=False) + '\n',
            encoding='utf-8',
        )
        root_config_path = model_dir / 'config.json'
        root_config_path.write_text(
            json.dumps({'model_type': 'xlm-roberta', 'transformers_version': '4.33.0'}) + '\n',
            encoding='utf-8',
        )
        (model_dir / 'sentence_bert_config.json').write_text(
            json.dumps({'max_seq_length': 8192, 'do_lower_case': False}, ensure_ascii=False) + '\n',
            encoding='utf-8',
        )
        (model_dir / 'tokenizer_config.json').write_text(
            json.dumps({'tokenizer_class': 'XLMRobertaTokenizer', 'model_max_length': 8192}, ensure_ascii=False) + '\n',
            encoding='utf-8',
        )
        (model_dir / 'pytorch_model.bin').write_bytes(b'weights')
        calls = {'count': 0}

        class FakeSentenceTransformer:
            def __init__(self, model_name_or_path, **kwargs):
                calls['count'] += 1
                payload = json.loads((Path(model_name_or_path) / 'config.json').read_text(encoding='utf-8'))
                if payload.get('transformers_version') == '4.33.0':
                    raise AttributeError("'dict' object has no attribute 'model_type'")
                self.model_name_or_path = model_name_or_path
                self.kwargs = kwargs

        fake_sentence_transformers = types.ModuleType("sentence_transformers")
        fake_sentence_transformers.SentenceTransformer = FakeSentenceTransformer

        with patch.dict(sys.modules, {"sentence_transformers": fake_sentence_transformers}), \
             patch('omniclip_rag.vector_index.prepare_local_model_snapshot', return_value={'model_ready': True, 'local_model_dir': str(model_dir)}), \
             patch('omniclip_rag.vector_index._runtime_import_environment', side_effect=lambda **_: nullcontext()):
            index = LanceDbVectorIndex(config, data_paths)
            embedder = index._default_embedder_factory()

        self.assertIsInstance(embedder, FakeSentenceTransformer)
        self.assertEqual(calls['count'], 2)
        repaired_payload = json.loads(root_config_path.read_text(encoding='utf-8'))
        self.assertNotIn('transformers_version', repaired_payload)

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
        with _runtime_root_patches(app_root), \
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


    def test_runtime_guidance_context_passes_through_gpu_probe_and_execution_fields(self) -> None:
        app_root = TEST_DATA_ROOT / 'gpu_context_passthrough'
        runtime_dir = app_root / 'runtime'
        app_root.mkdir(parents=True, exist_ok=True)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        acceleration_payload = {
            'gpu_present': True,
            'gpu_name': 'NVIDIA RTX',
            'cuda_available': True,
            'torch_available': True,
            'torch_version': '2.10.0+cu128',
            'torch_cuda_build': '12.8',
            'sentence_transformers_available': True,
            'gpu_probe_state': 'verified',
            'gpu_probe_verified': True,
            'gpu_probe_reason': '',
            'gpu_probe_error_class': '',
            'gpu_probe_error_message': '',
            'gpu_probe_actual_device': 'cuda:0',
            'gpu_probe_elapsed_ms': 42,
            'gpu_probe_verified_at': '2026-03-17T04:10:00Z',
            'gpu_probe_runtime_instance_id': 'runtime-instance:abc',
            'gpu_execution_state': 'verified',
            'gpu_execution_verified': True,
            'gpu_execution_reason': '',
            'gpu_execution_error_class': '',
            'gpu_execution_error_message': '',
            'gpu_execution_actual_device': 'cuda:0',
            'gpu_execution_reranker_actual_device': 'cuda:0',
            'gpu_execution_elapsed_ms': 314,
            'gpu_execution_verified_at': '2026-03-17T04:10:02Z',
            'gpu_execution_runtime_instance_id': 'runtime-instance:abc',
        }
        with _runtime_root_patches(app_root), patch('omniclip_rag.vector_index.resolve_vector_device', return_value='cuda'):
            context = runtime_guidance_context('torch', 'cuda', force_refresh=False, acceleration_payload=acceleration_payload)
        self.assertEqual(context.get('torch_cuda_build'), '12.8')
        self.assertEqual(context.get('gpu_probe_state'), 'verified')
        self.assertTrue(context.get('gpu_probe_verified'))
        self.assertEqual(context.get('gpu_execution_state'), 'verified')
        self.assertTrue(context.get('gpu_execution_verified'))
        self.assertEqual(context.get('gpu_execution_reranker_actual_device'), 'cuda:0')


    def test_build_runtime_install_command_uses_single_quoted_literals_once(self) -> None:
        expected_root = Path(r'D:/软件编写/OmniClip RAG/dist/OmniClipRAG-v0.2.4')
        with patch('omniclip_rag.vector_index._application_root_dir', return_value=expected_root), \
             patch('omniclip_rag.vector_index._install_runtime_script_relative', return_value=r'.\InstallRuntime.ps1'):
            command = build_runtime_install_command('cuda', source='mirror', component='semantic-core')
        self.assertIn(f"-LiteralPath '{expected_root}'", command)
        self.assertNotIn("-LiteralPath ''", command)
        self.assertIn(r"& '.\InstallRuntime.ps1'", command)
        self.assertIn('-WaitForProcessName OmniClipRAG', command)
        self.assertIn('-Component semantic-core', command)

    def test_build_runtime_install_command_injects_runtime_root_override_once(self) -> None:
        expected_root = Path(r'D:/软件编写/OmniClip RAG/dist/OmniClipRAG-v0.2.4')
        runtime_root = Path(r'D:/Users/test/AppData/Roaming/OmniClip RAG/shared/runtime')
        with patch('omniclip_rag.vector_index._application_root_dir', return_value=expected_root), \
             patch('omniclip_rag.vector_index._install_runtime_script_relative', return_value=r'.\InstallRuntime.ps1'):
            command = build_runtime_install_command('cpu', source='official', component='all', runtime_root=runtime_root)
        self.assertIn(f"$env:OMNICLIP_RUNTIME_ROOT = '{runtime_root}'", command)
        self.assertEqual(command.count('OMNICLIP_RUNTIME_ROOT'), 1)

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

    def test_detect_acceleration_bootstraps_runtime_import_paths_before_probing(self) -> None:
        app_root = TEST_DATA_ROOT / 'runtime_bootstrap_probe_app'
        runtime_dir = app_root / 'runtime'
        semantic_root = runtime_dir / 'components' / 'semantic-core'
        semantic_root.mkdir(parents=True, exist_ok=True)
        (semantic_root / 'torch').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'sentence_transformers').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'transformers' / 'utils').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'huggingface_hub').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'safetensors').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'numpy' / '_core').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'scipy' / 'linalg').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'torch' / '__init__.py').write_text(
            'class cuda:\n'
            '    @staticmethod\n'
            '    def is_available():\n'
            '        return False\n'
            '\n'
            '__version__ = "2.10.0"\n',
            encoding='utf-8',
        )
        (semantic_root / 'sentence_transformers' / '__init__.py').write_text(
            'from transformers.configuration_utils import PretrainedConfig\n',
            encoding='utf-8',
        )
        (semantic_root / 'transformers' / '__init__.py').write_text('', encoding='utf-8')
        (semantic_root / 'transformers' / 'configuration_utils.py').write_text(
            'class PretrainedConfig:\n'
            '    pass\n',
            encoding='utf-8',
        )
        (semantic_root / 'transformers' / 'utils' / '__init__.py').write_text('', encoding='utf-8')
        (semantic_root / 'huggingface_hub' / '__init__.py').write_text('', encoding='utf-8')
        (semantic_root / 'huggingface_hub' / 'hf_api.py').write_text('', encoding='utf-8')
        (semantic_root / 'safetensors' / '__init__.py').write_text('', encoding='utf-8')
        (semantic_root / 'numpy' / '__init__.py').write_text('__version__ = "2.0.0"\n', encoding='utf-8')
        (semantic_root / 'scipy' / '__init__.py').write_text('', encoding='utf-8')
        (semantic_root / '_runtime_bootstrap.json').write_text('{}', encoding='utf-8')
        _write_minimal_vector_store_runtime(runtime_dir)
        for module_name in ['torch', 'sentence_transformers', 'transformers', 'huggingface_hub', 'safetensors', 'numpy', 'scipy', 'pyarrow', 'pandas', 'lancedb', 'onnxruntime']:
            sys.modules.pop(module_name, None)
        with _runtime_root_patches(app_root), \
             patch('omniclip_rag.vector_index._ACCELERATION_CACHE', None), \
             patch('omniclip_rag.vector_index._detect_nvidia_gpus', return_value=[]), \
             patch('omniclip_rag.vector_index._detect_nvcc_version', return_value=''):
            payload = detect_acceleration(force_refresh=True)
        self.assertTrue(payload['torch_available'])
        self.assertTrue(payload['sentence_transformers_available'])
        self.assertEqual(payload['sentence_transformers_error'], '')


    def test_detect_acceleration_ignores_pending_runtime_payloads_until_restart(self) -> None:
        app_root = TEST_DATA_ROOT / 'runtime_pending_probe_app'
        runtime_dir = app_root / 'runtime'
        semantic_root = runtime_dir / 'components' / 'semantic-core'
        pending_dir = runtime_dir / '.pending' / 'semantic-core' / 'payload'
        semantic_root.mkdir(parents=True, exist_ok=True)
        (semantic_root / 'torch').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'sentence_transformers').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'transformers' / 'utils').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'huggingface_hub').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'safetensors').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'numpy' / '_core').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'scipy' / 'linalg').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'torch' / '__init__.py').write_text(
            'class cuda:\n'
            '    @staticmethod\n'
            '    def is_available():\n'
            '        return False\n'
            '\n'
            '__version__ = "2.10.0"\n',
            encoding='utf-8',
        )
        (semantic_root / 'sentence_transformers' / '__init__.py').write_text(
            'from transformers.configuration_utils import PretrainedConfig\n',
            encoding='utf-8',
        )
        (semantic_root / 'transformers' / '__init__.py').write_text('', encoding='utf-8')
        (semantic_root / 'transformers' / 'configuration_utils.py').write_text(
            'class PretrainedConfig:\n'
            '    pass\n',
            encoding='utf-8',
        )
        (semantic_root / 'transformers' / 'utils' / '__init__.py').write_text('', encoding='utf-8')
        (semantic_root / 'huggingface_hub' / '__init__.py').write_text('', encoding='utf-8')
        (semantic_root / 'huggingface_hub' / 'hf_api.py').write_text('', encoding='utf-8')
        (semantic_root / 'safetensors' / '__init__.py').write_text('', encoding='utf-8')
        (semantic_root / 'numpy' / '__init__.py').write_text('__version__ = "2.0.0"\n', encoding='utf-8')
        (semantic_root / 'scipy' / '__init__.py').write_text('', encoding='utf-8')
        (semantic_root / '_runtime_bootstrap.json').write_text('{}', encoding='utf-8')
        _write_minimal_vector_store_runtime(runtime_dir)
        (pending_dir / 'huggingface_hub').mkdir(parents=True, exist_ok=True)
        (pending_dir / 'huggingface_hub' / '__init__.py').write_text(
            'raise RuntimeError("pending should not be imported")\n',
            encoding='utf-8',
        )
        manifest_path = runtime_dir / '.pending' / 'semantic-core' / 'manifest.json'
        manifest_path.write_text(json.dumps({'component': 'semantic-core', 'payload_dir': str(pending_dir)}), encoding='utf-8')
        for module_name in ['torch', 'sentence_transformers', 'transformers', 'huggingface_hub', 'safetensors', 'numpy', 'scipy', 'pyarrow', 'pandas', 'lancedb', 'onnxruntime']:
            sys.modules.pop(module_name, None)
        with _runtime_root_patches(app_root), \
             patch('omniclip_rag.vector_index._ACCELERATION_CACHE', None), \
             patch('omniclip_rag.vector_index._detect_nvidia_gpus', return_value=[]), \
             patch('omniclip_rag.vector_index._detect_nvcc_version', return_value=''):
            payload = detect_acceleration(force_refresh=True)
            runtime_state = inspect_runtime_environment()
        self.assertTrue(payload['sentence_transformers_available'])
        self.assertEqual(payload['sentence_transformers_error'], '')
        self.assertTrue(runtime_state['runtime_pending'])
        self.assertIn('semantic-core', runtime_state['runtime_pending_components'])
        self.assertEqual(runtime_state['runtime_missing_items'], [])
        self.assertNotIn('huggingface-hub', payload['sentence_transformers_error'])

    def test_probe_runtime_gpu_execution_runs_zero_download_cuda_smoke(self) -> None:
        runtime_dir = TEST_DATA_ROOT / 'gpu_probe_runtime'
        runtime_dir.mkdir(parents=True, exist_ok=True)

        class _FakeTensor:
            def __init__(self, device: str):
                self.device = device

        class _FakeCuda:
            def __init__(self) -> None:
                self._peak = 0

            def is_available(self) -> bool:
                return True

            def device_count(self) -> int:
                return 1

            def get_device_name(self, _index: int) -> str:
                return 'Fake CUDA'

            def max_memory_allocated(self, _device: str) -> int:
                return self._peak

            def reset_peak_memory_stats(self, _device: str) -> None:
                self._peak = 0

            def synchronize(self, _device: str | None = None) -> None:
                return None

            def empty_cache(self) -> None:
                return None

        fake_torch = types.ModuleType('torch')
        fake_torch.__version__ = '2.10.0+cu128'
        fake_torch.version = types.SimpleNamespace(cuda='12.8')
        fake_torch.cuda = _FakeCuda()

        def _ones(_shape, *, device=None):
            return _FakeTensor(str(device or 'cpu'))

        def _matmul(left, _right):
            fake_torch.cuda._peak = 4096
            return _FakeTensor(str(getattr(left, 'device', 'cuda:0') or 'cuda:0'))

        fake_torch.ones = _ones
        fake_torch.matmul = _matmul

        with patch.dict(sys.modules, {'torch': fake_torch}, clear=False), \
             patch('omniclip_rag.vector_index._runtime_dir_path', return_value=runtime_dir), \
             patch('omniclip_rag.vector_index.runtime_trace_metadata', return_value={'runtime_instance_id': 'runtime-1', 'live_runtime_id': 'live-1'}), \
             patch('omniclip_rag.vector_index._detect_nvidia_gpus', return_value=['Fake CUDA']), \
             patch('omniclip_rag.vector_index._runtime_import_environment', side_effect=lambda **_: nullcontext()):
            payload = probe_runtime_gpu_execution(force_refresh=True)

        self.assertTrue(payload['success'])
        self.assertEqual(payload['state'], 'verified')
        self.assertEqual(payload['actual_device'], 'cuda:0')
        self.assertEqual(payload['runtime_instance_id'], 'runtime-1')
        self.assertGreater(int(payload['cuda_peak_mem_delta']), 0)

    def test_refresh_runtime_capability_snapshot_merges_gpu_probe_fields(self) -> None:
        probe_payload = {
            'success': True,
            'state': 'verified',
            'reason': '',
            'execution_error_class': '',
            'execution_error_message': '',
            'actual_device': 'cuda:0',
            'elapsed_ms': 42,
            'completed_at': '2026-03-16T12:00:00Z',
            'runtime_instance_id': 'runtime-2',
            'torch_cuda_build': '12.8',
        }
        query_payload = {
            'success': True,
            'state': 'verified',
            'reason': '',
            'execution_error_class': '',
            'execution_error_message': '',
            'actual_device': 'cuda:0',
            'reranker_actual_device': 'cuda:0',
            'elapsed_ms': 57,
            'completed_at': '2026-03-16T12:00:01Z',
            'runtime_instance_id': 'runtime-2',
        }
        with patch('omniclip_rag.vector_index.detect_acceleration', return_value={
            'gpu_present': True,
            'torch_available': True,
            'sentence_transformers_available': True,
            'cuda_available': True,
            'torch_cuda_build': '12.8',
        }), patch('omniclip_rag.vector_index.probe_runtime_gpu_execution', return_value=probe_payload) as probe_mock, patch(
            'omniclip_rag.vector_index.probe_runtime_gpu_query_execution', return_value=query_payload
        ) as query_mock:
            payload = refresh_runtime_capability_snapshot(force_refresh=True)

        probe_mock.assert_called_once_with(force_refresh=True)
        query_mock.assert_called_once_with(force_refresh=True)
        self.assertEqual(payload['gpu_probe_state'], 'verified')
        self.assertTrue(payload['gpu_probe_verified'])
        self.assertEqual(payload['gpu_execution_state'], 'verified')
        self.assertTrue(payload['gpu_execution_verified'])
        self.assertEqual(payload['gpu_execution_actual_device'], 'cuda:0')
        self.assertEqual(payload['gpu_execution_reranker_actual_device'], 'cuda:0')
        self.assertEqual(payload['gpu_execution_runtime_instance_id'], 'runtime-2')
        self.assertEqual(payload['torch_cuda_build'], '12.8')

    def test_runtime_management_snapshot_reuses_cached_gpu_probe_until_verification_is_requested(self) -> None:
        with patch('omniclip_rag.vector_index.detect_acceleration', return_value={
            'gpu_present': True,
            'torch_available': True,
            'sentence_transformers_available': True,
            'cuda_available': True,
            'torch_cuda_build': '12.8',
        }), patch('omniclip_rag.vector_index._runtime_dir_path', return_value=TEST_DATA_ROOT / 'runtime_mgmt_snapshot'), patch(
            'omniclip_rag.vector_index.runtime_trace_metadata',
            return_value={'runtime_instance_id': 'runtime-3', 'live_runtime_id': 'live-3'},
        ), patch(
            'omniclip_rag.vector_index._merge_cached_gpu_execution_state',
            side_effect=lambda payload, _runtime_dir, _meta: payload.update({
                'gpu_execution_state': 'verified',
                'gpu_execution_verified': True,
                'gpu_execution_reason': '',
                'gpu_execution_actual_device': 'cuda:0',
                'gpu_execution_runtime_instance_id': 'runtime-3',
            }),
        ), patch('omniclip_rag.vector_index.probe_runtime_gpu_execution') as probe_mock:
            payload = runtime_management_snapshot(force_refresh=True, verify_gpu=False)

        probe_mock.assert_not_called()
        self.assertEqual(payload['gpu_execution_state'], 'verified')
        self.assertTrue(payload['gpu_execution_verified'])

    def test_probe_runtime_gpu_query_execution_reuses_query_canary(self) -> None:
        runtime_dir = TEST_DATA_ROOT / 'gpu_query_probe_runtime'
        runtime_dir.mkdir(parents=True, exist_ok=True)
        with patch('omniclip_rag.vector_index._runtime_dir_path', return_value=runtime_dir), \
             patch('omniclip_rag.vector_index.runtime_trace_metadata', return_value={'runtime_instance_id': 'runtime-q1', 'live_runtime_id': 'live-q1'}), \
             patch('omniclip_rag.vector_index._detect_nvidia_gpus', return_value=['Fake CUDA']), \
             patch('omniclip_rag.runtime_canary.run_gpu_query_canary', return_value={
                 'success': True,
                 'state': 'verified',
                 'reason': '',
                 'actual_device': 'cuda:0',
                 'reranker_actual_device': 'cuda:0',
                 'elapsed_ms': 27,
             }):
            payload = probe_runtime_gpu_query_execution(force_refresh=True)
        self.assertTrue(payload['success'])
        self.assertEqual(payload['state'], 'verified')
        self.assertEqual(payload['actual_device'], 'cuda:0')
        self.assertEqual(payload['runtime_instance_id'], 'runtime-q1')

    def test_runtime_management_snapshot_verification_runs_smoke_then_query_probe(self) -> None:
        with patch('omniclip_rag.vector_index.detect_acceleration', return_value={
            'gpu_present': True,
            'torch_available': True,
            'sentence_transformers_available': True,
            'cuda_available': True,
            'torch_cuda_build': '12.8',
        }), patch('omniclip_rag.vector_index._runtime_dir_path', return_value=TEST_DATA_ROOT / 'runtime_mgmt_snapshot_verify'), patch(
            'omniclip_rag.vector_index.runtime_trace_metadata',
            return_value={'runtime_instance_id': 'runtime-verify', 'live_runtime_id': 'live-verify'},
        ), patch('omniclip_rag.vector_index._merge_cached_gpu_probe_state'), patch('omniclip_rag.vector_index._merge_cached_gpu_execution_state'), patch(
            'omniclip_rag.vector_index.probe_runtime_gpu_execution',
            return_value={'success': True, 'state': 'verified', 'reason': '', 'actual_device': 'cuda:0', 'elapsed_ms': 11, 'completed_at': '2026-03-16T00:00:00Z', 'runtime_instance_id': 'runtime-verify'},
        ) as smoke_mock, patch(
            'omniclip_rag.vector_index.probe_runtime_gpu_query_execution',
            return_value={'success': True, 'state': 'verified', 'reason': '', 'actual_device': 'cuda:0', 'reranker_actual_device': 'cuda:0', 'elapsed_ms': 25, 'completed_at': '2026-03-16T00:00:01Z', 'runtime_instance_id': 'runtime-verify'},
        ) as query_mock:
            payload = runtime_management_snapshot(force_refresh=True, verify_gpu=True)
        smoke_mock.assert_called_once_with(force_refresh=True)
        query_mock.assert_called_once_with(force_refresh=True)
        self.assertEqual(payload['gpu_probe_state'], 'verified')
        self.assertTrue(payload['gpu_probe_verified'])
        self.assertEqual(payload['gpu_execution_state'], 'verified')
        self.assertTrue(payload['gpu_execution_verified'])
        self.assertEqual(payload['gpu_execution_reranker_actual_device'], 'cuda:0')

    def test_runtime_capability_state_migrates_legacy_gpu_execution_probe_to_gpu_probe(self) -> None:
        runtime_dir = TEST_DATA_ROOT / 'runtime_capability_migrate'
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / '_runtime_capabilities.json').write_text(json.dumps({
            'version': 1,
            'gpu_execution_probe': {
                'success': True,
                'state': 'verified',
                'runtime_instance_id': 'runtime-legacy',
            },
        }), encoding='utf-8')
        with patch('omniclip_rag.vector_index._runtime_dir_path', return_value=runtime_dir), \
             patch('omniclip_rag.vector_index.runtime_trace_metadata', return_value={'runtime_instance_id': 'runtime-legacy', 'live_runtime_id': 'live-legacy'}), \
             patch('omniclip_rag.vector_index._detect_nvidia_gpus', return_value=['Fake CUDA']), \
             patch('omniclip_rag.vector_index._detect_nvcc_version', return_value=''), \
             patch('omniclip_rag.vector_index._probe_runtime_semantic_core_inprocess', return_value={'torch_available': False, 'sentence_transformers_available': False, 'torch_error': '', 'sentence_transformers_error': '', 'cuda_available': False, 'cuda_device_count': 0, 'cuda_name': ''}), \
             patch('omniclip_rag.vector_index._probe_runtime_semantic_core', return_value={'probe_error': 'skip'}):
            payload = detect_acceleration(force_refresh=True)
        self.assertEqual(payload['gpu_probe_state'], 'verified')
        self.assertTrue(payload['gpu_probe_verified'])
        self.assertEqual(payload['gpu_execution_state'], 'not-run')

    def test_inprocess_semantic_probe_does_not_poison_later_runtime_imports(self) -> None:
        app_root = TEST_DATA_ROOT / 'runtime_probe_no_poison'
        runtime_dir = app_root / 'runtime'
        semantic_root = runtime_dir / 'components' / 'semantic-core'
        semantic_root.mkdir(parents=True, exist_ok=True)
        (semantic_root / 'torch').mkdir(parents=True, exist_ok=True)
        (semantic_root / 'sentence_transformers').mkdir(parents=True, exist_ok=True)
        (semantic_root / '_runtime_bootstrap.json').write_text('{}', encoding='utf-8')
        (semantic_root / 'torch' / '__init__.py').write_text(
            'class cuda:\n'
            '    @staticmethod\n'
            '    def is_available():\n'
            '        return False\n'
            '\n'
            '__version__ = "2.10.0"\n',
            encoding='utf-8',
        )
        (semantic_root / 'sentence_transformers' / '__init__.py').write_text(
            'import builtins\n'
            'count = getattr(builtins, "_omniclip_sentence_transformers_import_count", 0) + 1\n'
            'builtins._omniclip_sentence_transformers_import_count = count\n'
            'if count > 1:\n'
            '    raise ImportError("cannot load module more than once per process")\n'
            '\n'
            'class SentenceTransformer:\n'
            '    def __init__(self, *args, **kwargs):\n'
            '        self.args = args\n'
            '        self.kwargs = kwargs\n',
            encoding='utf-8',
        )
        _write_minimal_vector_store_runtime(runtime_dir)

        module_names = ['torch', 'sentence_transformers']
        for module_name in module_names:
            sys.modules.pop(module_name, None)
        if hasattr(builtins, '_omniclip_sentence_transformers_import_count'):
            delattr(builtins, '_omniclip_sentence_transformers_import_count')
        try:
            with _runtime_root_patches(app_root):
                first = _probe_runtime_semantic_core_inprocess(runtime_dir)
                second = _probe_runtime_semantic_core_inprocess(runtime_dir)
                with _runtime_import_environment(component_id='semantic-core'):
                    from sentence_transformers import SentenceTransformer

                    embedder = SentenceTransformer('local-model')
            self.assertTrue(first['torch_available'])
            self.assertTrue(first['sentence_transformers_available'])
            self.assertTrue(second['torch_available'])
            self.assertTrue(second['sentence_transformers_available'])
            self.assertEqual(embedder.args, ('local-model',))
            self.assertEqual(getattr(builtins, '_omniclip_sentence_transformers_import_count', 0), 1)
        finally:
            for module_name in module_names:
                sys.modules.pop(module_name, None)
            if hasattr(builtins, '_omniclip_sentence_transformers_import_count'):
                delattr(builtins, '_omniclip_sentence_transformers_import_count')



    def test_runtime_search_roots_include_componentized_vector_store_for_semantic_core(self) -> None:
        from omniclip_rag.vector_index import _runtime_search_roots

        app_root = TEST_DATA_ROOT / 'runtime_search_roots_dependencies'
        runtime_dir = app_root / 'runtime'
        semantic_root = runtime_dir / 'components' / 'semantic-core'
        vector_store_root = runtime_dir / 'components' / 'vector-store'
        semantic_root.mkdir(parents=True, exist_ok=True)
        _write_minimal_vector_store_runtime(runtime_dir)

        roots = _runtime_search_roots(runtime_dir, include_pending=False, component_id='semantic-core')
        resolved = [Path(item).resolve() for item in roots]
        self.assertIn(semantic_root.resolve(), resolved)
        self.assertIn(vector_store_root.resolve(), resolved)

    def test_lancedb_backend_rebuild_search_and_delete(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "main"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend="lancedb",
            vector_device='cpu',
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


    def test_lancedb_index_keeps_vector_store_runtime_lazy_until_table_access(self) -> None:
        runtime_dir = TEST_DATA_ROOT / 'lazy_runtime_root' / 'runtime'
        semantic_root = runtime_dir / 'components' / 'semantic-core'
        semantic_root.mkdir(parents=True, exist_ok=True)
        _write_minimal_vector_store_runtime(runtime_dir)
        for package in ('torch', 'sentence_transformers', 'transformers', 'huggingface_hub', 'safetensors', 'numpy', 'scipy'):
            target = semantic_root / package
            target.mkdir(parents=True, exist_ok=True)
            (target / '__init__.py').write_text('', encoding='utf-8')

        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / 'lazy_runtime_paths'))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend='lancedb',
            vector_runtime='torch',
            vector_device='cpu',
        )

        with patch.dict(sys.modules, _fake_lancedb_modules(), clear=False),              patch('omniclip_rag.vector_index._application_root_dir', return_value=runtime_dir.parent):
            index = LanceDbVectorIndex(config, data_paths, embedder_factory=FakeEmbedder)
            self.assertIsNone(index._db)
            self.assertFalse(index._table_exists())
            self.assertIsNone(index._db)
            index.rebuild([
                {
                    'chunk_id': 'lazy-a',
                    'source_path': 'demo.md',
                    'title': 'Demo',
                    'anchor': 'A',
                    'rendered_text': '我的思维',
                }
            ])
            self.assertIsNotNone(index._db)
            hits = index.search('我的思维', 5)
        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk_id, 'lazy-a')

    def test_lancedb_search_coerces_numpy_like_query_vectors_before_querying(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / 'query_vector_coercion'))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend='lancedb',
            vector_device='cpu',
        )

        class NumpyLikeEmbedder:
            def encode(self, texts, *, batch_size=16, show_progress_bar=False, normalize_embeddings=True):
                vectors = []
                for text in texts:
                    values = [float(len(text)), 1.0, 0.0]
                    vectors.append(types.SimpleNamespace(tolist=lambda vals=values: list(vals)))
                return vectors

        with patch.dict(sys.modules, _fake_lancedb_modules(_StrictQueryTypeTable)):
            index = LanceDbVectorIndex(config, data_paths, embedder_factory=NumpyLikeEmbedder)
            index.rebuild([
                {
                    'chunk_id': 'a',
                    'source_path': 'pages/a.md',
                    'title': 'A',
                    'anchor': 'A',
                    'rendered_text': '我的思维框架',
                }
            ])
            hits = index.search('我的思维', 5)
        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk_id, 'a')

    def test_lancedb_rebuild_accepts_iterable_stream(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "iterable_rebuild"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_backend="lancedb",
            vector_batch_size=2,
            vector_device='cpu',
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
            vector_device='cpu',
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
            vector_device='cpu',
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
            vector_device='cpu',
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
            vector_device='cpu',
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
            vector_device='cpu',
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
        self.assertIn('--exclude', result["official_download_command"])
        self.assertIn('--exclude', result["mirror_download_command"])
        for pattern in _MODEL_DOWNLOAD_IGNORE_PATTERNS:
            self.assertIn(pattern, result["official_download_command"])
            self.assertIn(pattern, result["mirror_download_command"])

    def test_prepare_local_model_snapshot_uses_hf_mirror_only_for_unsupported_repo(self) -> None:
        mirror_paths = ensure_data_paths(str(TEST_DATA_ROOT / "mirror_bootstrap"))
        official_paths = ensure_data_paths(str(TEST_DATA_ROOT / "official_bootstrap"))
        mirror_config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(mirror_paths.global_root),
            vector_model="Example/custom-model",
        )
        official_config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(official_paths.global_root),
            vector_model="Example/custom-model",
        )
        calls: list[dict[str, object]] = []

        def fake_snapshot_download(**kwargs):
            calls.append(dict(kwargs))
            local_dir = Path(str(kwargs["local_dir"]))
            local_dir.mkdir(parents=True, exist_ok=True)
            (local_dir / "modules.json").write_text("{}", encoding="utf-8")
            (local_dir / "config.json").write_text("{}", encoding="utf-8")
            (local_dir / "pytorch_model.bin").write_bytes(b"ok")
            return str(local_dir)

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

        with patch.dict(sys.modules, {"huggingface_hub": fake_hub}):
            prepare_local_model_snapshot(mirror_config, mirror_paths, allow_download=True, download_source='mirror')
            prepare_local_model_snapshot(official_config, official_paths, allow_download=True, download_source='official')

        self.assertEqual(calls[0]["endpoint"], 'https://hf-mirror.com')
        self.assertIsNone(calls[1]["endpoint"])
        self.assertEqual(tuple(calls[0]["ignore_patterns"]), _MODEL_DOWNLOAD_IGNORE_PATTERNS)
        self.assertEqual(tuple(calls[1]["ignore_patterns"]), _MODEL_DOWNLOAD_IGNORE_PATTERNS)
        self.assertFalse(calls[0]["local_dir_use_symlinks"])
        self.assertFalse(calls[1]["local_dir_use_symlinks"])

    def test_prepare_local_model_snapshot_prefers_modelscope_for_supported_repo(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "modelscope_bootstrap"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_model="BAAI/bge-m3",
        )
        hf_calls: list[dict[str, object]] = []
        modelscope_calls: list[dict[str, object]] = []

        def fake_ms_snapshot_download(**kwargs):
            modelscope_calls.append(dict(kwargs))
            local_dir = Path(str(kwargs["local_dir"]))
            local_dir.mkdir(parents=True, exist_ok=True)
            (local_dir / "modules.json").write_text("{}", encoding="utf-8")
            (local_dir / "config.json").write_text("{}", encoding="utf-8")
            (local_dir / "pytorch_model.bin").write_bytes(b"ok")
            return str(local_dir)

        def fake_hf_snapshot_download(**kwargs):
            hf_calls.append(dict(kwargs))
            raise AssertionError('huggingface_hub should not be used when ModelScope succeeds first')

        fake_hub = types.ModuleType("huggingface_hub")
        fake_hub.snapshot_download = fake_hf_snapshot_download
        fake_hub.constants = types.SimpleNamespace(
            HF_HOME="",
            hf_cache_home="",
            HF_HUB_CACHE="",
            HUGGINGFACE_HUB_CACHE="",
            HUGGINGFACE_ASSETS_CACHE="",
            HF_XET_CACHE="",
            HF_HUB_DISABLE_XET=False,
        )
        fake_modelscope = types.ModuleType("modelscope")
        fake_modelscope_hub = types.ModuleType("modelscope.hub")
        fake_modelscope_snapshot = types.ModuleType("modelscope.hub.snapshot_download")
        fake_modelscope_snapshot.snapshot_download = fake_ms_snapshot_download

        with patch.dict(
            sys.modules,
            {
                "huggingface_hub": fake_hub,
                "modelscope": fake_modelscope,
                "modelscope.hub": fake_modelscope_hub,
                "modelscope.hub.snapshot_download": fake_modelscope_snapshot,
            },
        ):
            result = prepare_local_model_snapshot(config, data_paths, allow_download=True, download_source='mirror')

        self.assertTrue(result["model_ready"])
        self.assertEqual(len(modelscope_calls), 1)
        self.assertEqual(modelscope_calls[0]["model_id"], "BAAI/bge-m3")
        self.assertEqual(tuple(modelscope_calls[0]["ignore_patterns"]), _MODEL_DOWNLOAD_IGNORE_PATTERNS)
        self.assertEqual(hf_calls, [])

    def test_prepare_local_model_snapshot_emits_download_log_and_progress_lines(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "download_log_bootstrap"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_model="BAAI/bge-m3",
        )
        logs: list[str] = []

        def fake_snapshot_download(**kwargs):
            tqdm_class = kwargs.get("tqdm_class")
            if tqdm_class is not None:
                bar = tqdm_class(total=100, desc='weights.bin')
                bar.update(50)
                bar.update(50)
                bar.close()
            local_dir = Path(str(kwargs["local_dir"]))
            local_dir.mkdir(parents=True, exist_ok=True)
            (local_dir / "modules.json").write_text("{}", encoding="utf-8")
            (local_dir / "config.json").write_text("{}", encoding="utf-8")
            (local_dir / "pytorch_model.bin").write_bytes(b"ok")
            return str(local_dir)

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

        with patch.dict(sys.modules, {"huggingface_hub": fake_hub}):
            prepare_local_model_snapshot(
                config,
                data_paths,
                allow_download=True,
                download_source='mirror',
                download_log=logs.append,
            )

        self.assertTrue(any('准备下载 Hugging Face 仓库' in line for line in logs))
        self.assertTrue(any('目标目录：' in line for line in logs))
        self.assertTrue(any('镜像源（推荐）' in line for line in logs))
        self.assertTrue(any('weights.bin' in line for line in logs))
        self.assertTrue(any('模型目录校验通过' in line for line in logs))

    def test_prepare_local_model_snapshot_retries_official_when_hf_mirror_rejects_nonessential_file(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "mirror_retry_bootstrap"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_model="Example/custom-model",
        )
        calls: list[dict[str, object]] = []

        class FakeHfHubHTTPError(Exception):
            pass

        def fake_snapshot_download(**kwargs):
            calls.append(dict(kwargs))
            if len(calls) == 1:
                raise FakeHfHubHTTPError(
                    '403 Forbidden: Cannot access content at: '
                    'https://hf-mirror.com/api/resolve-cache/models/BAAI/bge-m3/imgs%2F.DS_Store'
                )
            local_dir = Path(str(kwargs["local_dir"]))
            local_dir.mkdir(parents=True, exist_ok=True)
            (local_dir / "modules.json").write_text("{}", encoding="utf-8")
            (local_dir / "config.json").write_text("{}", encoding="utf-8")
            (local_dir / "pytorch_model.bin").write_bytes(b"ok")
            return str(local_dir)

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
        fake_hub_errors = types.ModuleType("huggingface_hub.errors")
        fake_hub_errors.HfHubHTTPError = FakeHfHubHTTPError

        with patch.dict(sys.modules, {"huggingface_hub": fake_hub, "huggingface_hub.errors": fake_hub_errors}):
            result = prepare_local_model_snapshot(config, data_paths, allow_download=True, download_source='mirror')

        self.assertTrue(result["model_ready"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["endpoint"], 'https://hf-mirror.com')
        self.assertIsNone(calls[1]["endpoint"])
        self.assertEqual(tuple(calls[0]["ignore_patterns"]), _MODEL_DOWNLOAD_IGNORE_PATTERNS)
        self.assertEqual(tuple(calls[1]["ignore_patterns"]), _MODEL_DOWNLOAD_IGNORE_PATTERNS)
        self.assertFalse(calls[0]["local_dir_use_symlinks"])
        self.assertFalse(calls[1]["local_dir_use_symlinks"])

    def test_prepare_local_model_snapshot_falls_back_from_modelscope_to_hf_mirror_and_official(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / "modelscope_fallback_bootstrap"))
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(data_paths.global_root),
            vector_model="BAAI/bge-m3",
        )
        hf_calls: list[dict[str, object]] = []
        modelscope_calls: list[dict[str, object]] = []

        class FakeModelScopeError(Exception):
            pass

        class FakeHfHubHTTPError(Exception):
            pass

        def fake_ms_snapshot_download(**kwargs):
            modelscope_calls.append(dict(kwargs))
            raise FakeModelScopeError('modelscope ssl failed')

        def fake_hf_snapshot_download(**kwargs):
            hf_calls.append(dict(kwargs))
            if len(hf_calls) == 1:
                raise FakeHfHubHTTPError('403 Forbidden: Cannot access content at: https://hf-mirror.com/api/models/BAAI/bge-m3')
            local_dir = Path(str(kwargs["local_dir"]))
            local_dir.mkdir(parents=True, exist_ok=True)
            (local_dir / "modules.json").write_text("{}", encoding="utf-8")
            (local_dir / "config.json").write_text("{}", encoding="utf-8")
            (local_dir / "pytorch_model.bin").write_bytes(b"ok")
            return str(local_dir)

        fake_hub = types.ModuleType("huggingface_hub")
        fake_hub.snapshot_download = fake_hf_snapshot_download
        fake_hub.constants = types.SimpleNamespace(
            HF_HOME="",
            hf_cache_home="",
            HF_HUB_CACHE="",
            HUGGINGFACE_HUB_CACHE="",
            HUGGINGFACE_ASSETS_CACHE="",
            HF_XET_CACHE="",
            HF_HUB_DISABLE_XET=False,
        )
        fake_modelscope = types.ModuleType("modelscope")
        fake_modelscope_hub = types.ModuleType("modelscope.hub")
        fake_modelscope_snapshot = types.ModuleType("modelscope.hub.snapshot_download")
        fake_modelscope_snapshot.snapshot_download = fake_ms_snapshot_download

        with patch.dict(
            sys.modules,
            {
                "huggingface_hub": fake_hub,
                "modelscope": fake_modelscope,
                "modelscope.hub": fake_modelscope_hub,
                "modelscope.hub.snapshot_download": fake_modelscope_snapshot,
            },
        ):
            result = prepare_local_model_snapshot(config, data_paths, allow_download=True, download_source='mirror')

        self.assertTrue(result["model_ready"])
        self.assertEqual(len(modelscope_calls), 1)
        self.assertEqual(len(hf_calls), 2)
        self.assertEqual(hf_calls[0]["endpoint"], 'https://hf-mirror.com')
        self.assertIsNone(hf_calls[1]["endpoint"])

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
        ), patch('omniclip_rag.vector_index._runtime_import_environment', side_effect=lambda **_: nullcontext()):
            index = LanceDbVectorIndex(config, data_paths)
            embedder = index._default_embedder_factory()

        self.assertIsInstance(embedder, FakeSentenceTransformer)
        snapshot = calls["snapshot"]
        runtime = calls["sentence_transformer"]
        self.assertEqual(snapshot["repo_id"], "BAAI/bge-m3")
        self.assertFalse(snapshot["local_files_only"])
        self.assertIsNone(snapshot["endpoint"])
        self.assertEqual(tuple(snapshot["ignore_patterns"]), _MODEL_DOWNLOAD_IGNORE_PATTERNS)
        self.assertFalse(snapshot["local_dir_use_symlinks"])
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
        ), patch('omniclip_rag.vector_index._runtime_import_environment', side_effect=lambda **_: nullcontext()):
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
        ), patch('omniclip_rag.vector_index._runtime_import_environment', side_effect=lambda **_: nullcontext()):
            index = LanceDbVectorIndex(config, data_paths)
            embedder = index._default_embedder_factory()

        self.assertIsInstance(embedder, FakeSentenceTransformer)
        self.assertEqual(calls["snapshot"], 0)
        self.assertEqual(calls["sentence"], 1)


    def test_vector_store_runtime_dependency_order_keeps_semantic_core_first(self) -> None:
        self.assertEqual(_runtime_component_dependency_ids('vector-store'), ('semantic-core', 'vector-store'))

    def test_runtime_search_roots_for_vector_store_prefer_semantic_component_before_legacy_root(self) -> None:
        app_root = TEST_DATA_ROOT / 'runtime_search_roots' / 'app'
        runtime_dir = app_root / 'runtime'
        semantic_root = runtime_dir / 'components' / 'semantic-core'
        semantic_root.mkdir(parents=True, exist_ok=True)
        (semantic_root / 'torch').mkdir(parents=True, exist_ok=True)
        (runtime_dir / 'lancedb').mkdir(parents=True, exist_ok=True)

        with _runtime_root_patches(app_root):
            roots = _runtime_search_roots(runtime_dir, include_pending=False, component_id='vector-store')

        self.assertGreaterEqual(len(roots), 2)
        self.assertEqual(roots[0].resolve(), semantic_root.resolve())
        self.assertEqual(roots[1].resolve(), runtime_dir.resolve())

    def test_frozen_runtime_discovery_stays_on_preferred_runtime_root(self) -> None:
        dist_root = TEST_DATA_ROOT / 'frozen_runtime_drift' / 'dist'
        current_app = dist_root / 'OmniClipRAG-v0.3.0'
        sibling_app = dist_root / 'OmniClipRAG-v0.2.4'
        current_runtime = current_app / 'runtime'
        sibling_runtime = sibling_app / 'runtime'
        current_runtime.mkdir(parents=True, exist_ok=True)
        _write_minimal_vector_store_runtime(sibling_runtime)
        preferred_runtime = TEST_DATA_ROOT / 'frozen_runtime_drift' / 'shared' / 'runtime'

        with patch.multiple(
            'omniclip_rag.vector_index',
            _application_root_dir=lambda: current_app,
            _preferred_runtime_dir_path=lambda: preferred_runtime,
        ), patch.object(sys, 'frozen', True, create=True):
            active_runtime = _discover_active_runtime_dir()

        self.assertEqual(active_runtime.resolve(), preferred_runtime.resolve())

    def test_frozen_preferred_runtime_root_is_shared_appdata_runtime(self) -> None:
        app_root = TEST_DATA_ROOT / 'preferred_frozen_runtime' / 'OmniClipRAG-v0.3.0'
        data_root = TEST_DATA_ROOT / 'preferred_frozen_runtime' / 'OmniClip RAG'
        app_root.mkdir(parents=True, exist_ok=True)
        data_root.mkdir(parents=True, exist_ok=True)
        with patch('omniclip_rag.vector_index._application_root_dir', return_value=app_root), \
             patch('omniclip_rag.vector_index.resolve_active_data_root', return_value=types.SimpleNamespace(path=data_root.resolve(), source='bootstrap', bootstrap_file=data_root / 'bootstrap.json')), \
             patch.object(sys, 'frozen', True, create=True):
            runtime_root = _preferred_runtime_dir_path()
        self.assertEqual(runtime_root.resolve(), (data_root / 'shared' / 'runtime').resolve())


    def test_runtime_dependency_issue_uses_cached_acceleration_by_default(self) -> None:
        config = AppConfig(
            vault_path=str(ROOT),
            data_root=str(TEST_DATA_ROOT),
            vector_backend='lancedb',
            vector_runtime='torch',
            vector_device='auto',
        )
        with patch('omniclip_rag.vector_index.inspect_runtime_environment', return_value={
            'runtime_exists': True,
            'runtime_complete': True,
            'runtime_missing_items': [],
            'runtime_pending': False,
            'runtime_pending_components': [],
            'runtime_dir': TEST_DATA_ROOT / 'runtime',
            'runtime_has_content': True,
        }), \
             patch('omniclip_rag.vector_index.runtime_component_status', side_effect=[{'missing_items': [], 'ready': True}, {'missing_items': [], 'ready': True}]), \
             patch('omniclip_rag.vector_index.detect_acceleration', return_value={'torch_available': True, 'sentence_transformers_available': True}) as acceleration_mock:
            issue = runtime_dependency_issue(config)
        self.assertIsNone(issue)
        acceleration_mock.assert_called_once_with(force_refresh=False)


if __name__ == "__main__":
    unittest.main()
