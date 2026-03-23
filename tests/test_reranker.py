import shutil
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from omniclip_rag.canary_backend import CANARY_RERANKER_MODEL_ID
from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.models import SearchHit
from omniclip_rag.reranker import (
    CanaryTorchReranker,
    CrossEncoderReranker,
    create_reranker,
    get_local_reranker_dir,
    is_local_reranker_ready,
    release_process_reranker_resources,
    reranker_download_guidance_context,
)
from omniclip_rag.vector_index import _MODEL_DOWNLOAD_IGNORE_PATTERNS


ROOT = Path(__file__).resolve().parents[1]
TEST_DATA_ROOT = ROOT / ".tmp" / "test_reranker_data"


class _FakeModel:
    def __init__(self, device: str) -> None:
        self.device = device

    def predict(self, pairs, batch_size=4, show_progress_bar=False):
        if self.device == 'cuda':
            raise RuntimeError('CUDA out of memory')
        return [0.9 - index * 0.1 for index, _pair in enumerate(pairs)]


class RerankerTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_DATA_ROOT.exists():
            shutil.rmtree(TEST_DATA_ROOT)

    def test_create_reranker_returns_null_when_disabled(self) -> None:
        paths = ensure_data_paths(str(TEST_DATA_ROOT))
        config = AppConfig(vault_path='.', data_root=str(paths.global_root), reranker_enabled=False)
        reranker = create_reranker(config, paths)
        hits, outcome = reranker.rerank('test', [], 10)
        self.assertEqual(hits, [])
        self.assertFalse(outcome.enabled)

    def test_create_reranker_returns_builtin_canary_backend(self) -> None:
        paths = ensure_data_paths(str(TEST_DATA_ROOT / 'canary'))
        config = AppConfig(
            vault_path='.',
            data_root=str(paths.global_root),
            reranker_enabled=True,
            reranker_model=CANARY_RERANKER_MODEL_ID,
        )
        reranker = create_reranker(config, paths)
        self.assertIsInstance(reranker, CanaryTorchReranker)
        self.assertTrue(is_local_reranker_ready(config, paths))

    def test_cross_encoder_reranker_falls_back_to_cpu_after_cuda_oom(self) -> None:
        paths = ensure_data_paths(str(TEST_DATA_ROOT))
        model_dir = paths.cache_dir / 'models' / 'BAAI__bge-reranker-v2-m3'
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / 'config.json').write_text('{}', encoding='utf-8')
        (model_dir / 'pytorch_model.bin').write_text('x', encoding='utf-8')
        config = AppConfig(
            vault_path='.',
            data_root=str(paths.global_root),
            reranker_enabled=True,
            reranker_batch_size_cpu=2,
            reranker_batch_size_cuda=4,
            vector_device='cuda',
        )
        reranker = CrossEncoderReranker(config, paths, loader=lambda local_dir, device: _FakeModel(device))
        hits = [
            SearchHit(score=40.0, title='A', anchor='A', source_path='a.md', rendered_text='alpha', chunk_id='a'),
            SearchHit(score=30.0, title='B', anchor='B', source_path='b.md', rendered_text='beta', chunk_id='b'),
        ]
        with patch('omniclip_rag.reranker.resolve_vector_device', return_value='cuda'):
            reranked, outcome = reranker.rerank('test', hits, 2)
        self.assertTrue(outcome.enabled)
        self.assertTrue(outcome.applied)
        self.assertTrue(outcome.oom_recovered)
        self.assertTrue(outcome.degraded_to_cpu)
        self.assertEqual(outcome.resolved_device, 'cpu')
        self.assertEqual(len(reranked), 2)

    def test_reranker_download_guidance_context_builds_commands_and_dirs(self) -> None:
        paths = ensure_data_paths(str(TEST_DATA_ROOT / 'manual_reranker_context'))
        config = AppConfig(vault_path='.', data_root=str(paths.global_root), reranker_enabled=True)
        result = reranker_download_guidance_context(config, paths)
        self.assertEqual(result['model'], config.reranker_model)
        self.assertTrue(Path(result['model_dir']).exists())
        self.assertIn('https://huggingface.co/', result['official_url'])
        self.assertIn('https://hf-mirror.com/', result['mirror_url'])
        self.assertIn('hf download', result['official_download_command'])
        self.assertIn('HF_ENDPOINT', result['mirror_download_command'])
        for pattern in _MODEL_DOWNLOAD_IGNORE_PATTERNS:
            self.assertIn(pattern, result['official_download_command'])
            self.assertIn(pattern, result['mirror_download_command'])

    def test_cross_encoder_reranker_warmup_forwards_download_source_and_log(self) -> None:
        paths = ensure_data_paths(str(TEST_DATA_ROOT / 'warmup_download_source'))
        config = AppConfig(vault_path='.', data_root=str(paths.global_root), reranker_enabled=True)
        reranker = CrossEncoderReranker(config, paths)
        callback = lambda _message: None
        with patch('omniclip_rag.reranker.is_local_reranker_ready', side_effect=[False, True]), \
             patch('omniclip_rag.reranker.download_hf_repo_snapshot') as download_mock, \
             patch.object(reranker, '_load_model', return_value=object()):
            result = reranker.warmup(allow_download=True, download_source='mirror', download_log=callback)
        download_mock.assert_called_once_with(
            repo_id=config.reranker_model,
            local_dir=get_local_reranker_dir(config, paths),
            hf_home_dir=paths.cache_dir / 'models' / '_hf_home',
            local_files_only=False,
            download_source='mirror',
            download_log=callback,
            missing_dependency_message='当前还缺少 huggingface-hub 运行时，暂时不能下载重排模型缓存。',
        )
        self.assertTrue(result['model_ready'])

    def test_cross_encoder_reranker_warmup_can_skip_prewarm_after_download(self) -> None:
        paths = ensure_data_paths(str(TEST_DATA_ROOT / 'warmup_download_only'))
        config = AppConfig(vault_path='.', data_root=str(paths.global_root), reranker_enabled=True)
        reranker = CrossEncoderReranker(config, paths)
        with patch('omniclip_rag.reranker.is_local_reranker_ready', side_effect=[False, True]), \
             patch('omniclip_rag.reranker.download_hf_repo_snapshot'), \
             patch.object(reranker, '_load_model') as load_mock:
            result = reranker.warmup(allow_download=True, warmup_after_download=False)
        load_mock.assert_not_called()
        self.assertTrue(result['model_ready'])

    def test_release_process_reranker_resources_clears_loaded_models(self) -> None:
        paths = ensure_data_paths(str(TEST_DATA_ROOT / 'release_process_reranker'))
        config = AppConfig(vault_path='.', data_root=str(paths.global_root), reranker_enabled=True)
        reranker = CrossEncoderReranker(config, paths, loader=lambda _local_dir, _device: object())
        reranker._models['cpu'] = object()
        release_process_reranker_resources(clear_cuda=False)
        self.assertEqual(reranker._models, {})

    def test_default_loader_uses_direct_local_files_only_flag(self) -> None:
        paths = ensure_data_paths(str(TEST_DATA_ROOT))
        config = AppConfig(vault_path='.', data_root=str(paths.global_root), reranker_enabled=True)
        reranker = CrossEncoderReranker(config, paths)
        cross_encoder_ctor = Mock(return_value=object())
        fake_module = types.SimpleNamespace(CrossEncoder=cross_encoder_ctor)
        with patch.dict(sys.modules, {'sentence_transformers': fake_module}):
            reranker._default_loader(paths.cache_dir / 'models' / 'BAAI__bge-reranker-v2-m3', 'cpu')
        cross_encoder_ctor.assert_called_once()
        self.assertTrue(cross_encoder_ctor.call_args.kwargs.get('local_files_only'))
        self.assertNotIn('automodel_args', cross_encoder_ctor.call_args.kwargs)

if __name__ == '__main__':
    unittest.main()
