import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.models import SearchHit
from omniclip_rag.reranker import CrossEncoderReranker, create_reranker


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


if __name__ == '__main__':
    unittest.main()
