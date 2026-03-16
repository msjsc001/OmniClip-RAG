from pathlib import Path
import json
import shutil
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from omniclip_rag import parser as parser_module
from omniclip_rag.app_logging import shutdown_logging
from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.errors import BuildCancelledError, RuntimeDependencyError
from omniclip_rag.models import RerankOutcome, SearchHit
from omniclip_rag.retrieval_policy import build_query_profile
from omniclip_rag.service import OmniClipService
from omniclip_rag.timing import load_build_history
ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = ROOT / "logseq笔记样本"
TEST_DATA_ROOT = ROOT / ".tmp" / "test_service_data"
class _StubVectorIndex:
    def __init__(self) -> None:
        self.reset_called = False
    def rebuild(self, documents, *, total=None, on_progress=None, pause_event=None, cancel_event=None, progress_offset=0, reset_index=True):
        return None
    def upsert(self, documents):
        return None
    def delete(self, chunk_ids):
        return None
    def search(self, query_text, limit):
        return []
    def warmup(self):
        return {"backend": "stub", "model": None, "dimension": 0}
    def reset(self):
        self.reset_called = True
class _InjectVectorIndex(_StubVectorIndex):
    def __init__(self) -> None:
        super().__init__()
        self.injected: list[SimpleNamespace] = []
    def search(self, query_text, limit):
        return list(self.injected[:limit])
class _GpuVectorIndex(_InjectVectorIndex):
    def last_execution_report(self):
        return {
            'requested_device': 'cuda',
            'resolved_device': 'cuda',
            'model_device': 'cuda:0',
            'actual_device': 'cuda:0',
            'cuda_peak_mem_before': 1024,
            'cuda_peak_mem_after': 4096,
            'cuda_peak_mem_delta': 3072,
            'execution_error_class': '',
            'execution_error_message': '',
        }
class _FailingVectorIndex(_StubVectorIndex):
    def __init__(self) -> None:
        super().__init__()
        self.fail_upsert_once = True
        self.deleted_batches: list[list[str]] = []
        self.upsert_batches: list[list[str]] = []
    def upsert(self, documents):
        self.upsert_batches.append([item.get('chunk_id', '') for item in documents])
        if self.fail_upsert_once:
            self.fail_upsert_once = False
            raise RuntimeError('vector busy')
        return None
    def delete(self, chunk_ids):
        self.deleted_batches.append(list(chunk_ids))
        return None
class _ProfilingVectorIndex(_StubVectorIndex):
    def rebuild(self, documents, *, total=None, on_progress=None, pause_event=None, cancel_event=None, progress_offset=0, reset_index=True):
        count = int(total or 0)
        if on_progress is not None:
            on_progress(
                {
                    'stage': 'vectorizing',
                    'current': count,
                    'total': count,
                    'encoded_count': count,
                    'written_count': count,
                    'write_queue_depth': 0,
                    'write_queue_capacity': 4,
                    'staged_write_rows': 0,
                    'encode_elapsed_total_ms': 120.0,
                    'prepare_elapsed_total_ms': 80.0,
                    'write_elapsed_total_ms': 40.0,
                    'write_flush_count': 3,
                }
            )
        return None
class _ResumeAwareVectorIndex(_StubVectorIndex):
    def __init__(self, *, fail_once: bool = False) -> None:
        super().__init__()
        self.fail_once = fail_once
        self.rebuild_calls: list[dict[str, object]] = []
        self.deleted_batches: list[list[str]] = []

    def rebuild(self, documents, *, total=None, on_progress=None, pause_event=None, cancel_event=None, progress_offset=0, reset_index=True):
        docs = list(documents)
        self.rebuild_calls.append({'progress_offset': progress_offset, 'reset_index': reset_index, 'docs': [item.get('chunk_id', '') for item in docs]})
        if self.fail_once:
            self.fail_once = False
            if on_progress is not None:
                on_progress({
                    'stage': 'vectorizing',
                    'current': min(progress_offset + 2, int(total or (progress_offset + len(docs)))),
                    'total': int(total or (progress_offset + len(docs))),
                    'encoded_count': progress_offset + 2,
                    'written_count': progress_offset + 2,
                    'write_queue_depth': 0,
                    'write_queue_capacity': 4,
                    'staged_write_rows': 0,
                })
            raise RuntimeError('cuda out of memory while encoding vectors')
        if on_progress is not None:
            final_total = int(total or (progress_offset + len(docs)))
            on_progress({
                'stage': 'vectorizing',
                'current': final_total,
                'total': final_total,
                'encoded_count': final_total,
                'written_count': final_total,
                'write_queue_depth': 0,
                'write_queue_capacity': 4,
                'staged_write_rows': 0,
            })
        return None

    def delete(self, chunk_ids):
        self.deleted_batches.append(list(chunk_ids))
        return None
class _SlowVectorIndex(_StubVectorIndex):
    def rebuild(self, documents, *, total=None, on_progress=None, pause_event=None, cancel_event=None, progress_offset=0, reset_index=True):
        time.sleep(0.25)
        docs = list(documents)
        if on_progress is not None:
            final_total = int(total or (progress_offset + len(docs)))
            on_progress({
                'stage': 'vectorizing',
                'current': final_total,
                'total': final_total,
                'encoded_count': final_total,
                'written_count': final_total,
                'write_queue_depth': 0,
                'write_queue_capacity': 4,
                'staged_write_rows': 0,
            })
        return None
class _CudaReranker:
    def rerank(self, query_text, hits, limit):
        limited = list(hits[:limit])
        return limited, RerankOutcome(
            enabled=True,
            applied=True,
            requested_device='cuda',
            resolved_device='cuda',
            model_device='cuda:0',
            actual_device='cuda:0',
            candidate_count=len(hits),
            reranked_count=len(limited),
            cuda_peak_mem_before=2048,
            cuda_peak_mem_after=8192,
            cuda_peak_mem_delta=6144,
        )
class _LoweringReranker:
    def __init__(self) -> None:
        self.last_candidate_limit: int | None = None
    def warmup(self, *, allow_download: bool = False):
        return {'backend': 'stub', 'model': 'stub', 'model_ready': True}
    def rerank(self, query_text: str, hits: list[SearchHit], candidate_limit: int):
        self.last_candidate_limit = candidate_limit
        reranked: list[SearchHit] = []
        for index, hit in enumerate(hits):
            score = hit.score
            if index == 0:
                score = 29.0
            reranked.append(
                SearchHit(
                    score=score,
                    title=hit.title,
                    anchor=hit.anchor,
                    source_path=hit.source_path,
                    rendered_text=hit.rendered_text,
                    chunk_id=hit.chunk_id,
                    display_text=hit.display_text,
                    preview_text=hit.preview_text,
                    reason=hit.reason,
                )
            )
        return reranked, SimpleNamespace(enabled=True, applied=True, resolved_device='cpu', reranked_count=min(candidate_limit, len(hits)), degraded_to_cpu=False, oom_recovered=False)
class ServiceTests(unittest.TestCase):
    def tearDown(self) -> None:
        for path in (
            TEST_DATA_ROOT,
            ROOT / ".tmp" / "watch_vault_test",
            ROOT / ".tmp" / "watch_data_test",
            ROOT / ".tmp" / "duplicate_vault_test",
            ROOT / ".tmp" / "duplicate_data_test",
            ROOT / ".tmp" / "resume_vault_test",
            ROOT / ".tmp" / "resume_data_test",
            ROOT / ".tmp" / "paused_vault_test",
            ROOT / ".tmp" / "paused_data_test",
            ROOT / ".tmp" / "vector_merge_vault_test",
            ROOT / ".tmp" / "vector_merge_data_test",
            ROOT / ".tmp" / "redact_vault_test",
            ROOT / ".tmp" / "redact_data_test",
            ROOT / ".tmp" / "ref_vault_test",
            ROOT / ".tmp" / "ref_data_test",
            ROOT / ".tmp" / "semantic_floor_vault_test",
            ROOT / ".tmp" / "semantic_floor_data_test",
            ROOT / ".tmp" / "dedupe_vault_test",
            ROOT / ".tmp" / "dedupe_data_test",
            ROOT / ".tmp" / "page_block_vault_test",
            ROOT / ".tmp" / "page_block_data_test",
            ROOT / ".tmp" / "reindex_locked_vault_test",
            ROOT / ".tmp" / "reindex_locked_data_test",
            ROOT / ".tmp" / "vector_dirty_vault_test",
            ROOT / ".tmp" / "vector_dirty_data_test",
            ROOT / ".tmp" / "snapshot_offline_vault_test",
            ROOT / ".tmp" / "snapshot_offline_data_test",
            ROOT / '.tmp' / 'cancelled_rebuild_vault_test',
            ROOT / '.tmp' / 'cancelled_rebuild_data_test',
            ROOT / '.tmp' / 'watch_guard_vault_test',
            ROOT / '.tmp' / 'watch_guard_data_test',
            ROOT / '.tmp' / 'vector_resume_vault_test',
            ROOT / '.tmp' / 'vector_resume_data_test',
            ROOT / '.tmp' / 'bootstrap_model_only',
            ROOT / '.tmp' / 'reranker_disabled_vault_test',
            ROOT / '.tmp' / 'reranker_disabled_data_test',
        ):
            shutdown_logging()
            if path.exists():
                shutil.rmtree(path)
    def test_rebuild_and_query(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        try:
            stats = service.rebuild_index()
            self.assertGreaterEqual(stats["files"], 3)
            hits, context_pack = service.query("块嵌入", limit=5)
            self.assertTrue(hits)
            self.assertIn("# RAG结果", context_pack)
            self.assertIn("# 笔记名：Logseq笔记样本", context_pack)
            self.assertIn("笔记片段1：", context_pack)
            self.assertNotIn("## Usage protocol", context_pack)
            self.assertIn("- 下边是块嵌入（块内嵌）", hits[0].display_text)
            self.assertIn("这是一个块的子内容（C）", hits[0].display_text)
        finally:
            service.close()
    def test_query_emits_progress_stages(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / 'query_progress'))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        try:
            service.rebuild_index()
            progress: list[dict[str, object]] = []
            result = service.query('块嵌入', limit=5, on_progress=progress.append)
            self.assertTrue(result.hits)
            self.assertGreaterEqual(len(progress), 4)
            self.assertEqual(progress[0].get('stage'), 'query')
            self.assertEqual(progress[0].get('stage_status'), 'prepare')
            stages = [str(item.get('stage_status')) for item in progress]
            self.assertIn('rank', stages)
            self.assertEqual(stages[-1], 'context')
            self.assertTrue(all(float(item.get('overall_percent', 0.0) or 0.0) >= 0.0 for item in progress))
        finally:
            service.close()

    def test_markdown_query_degrades_to_lexical_when_vector_runtime_is_not_ready(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / 'query_runtime_degraded'))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root), vector_backend='disabled', reranker_enabled=False)
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        try:
            service.rebuild_index()
            with patch('omniclip_rag.service.runtime_dependency_issue', return_value='当前还不能开始本地语义建库或向量查询。'):
                result = service.query('块嵌入', limit=5, score_threshold=0, allowed_families={'markdown'})
            self.assertTrue(result.hits)
            self.assertTrue(all(hit.source_family == 'markdown' for hit in result.hits))
            self.assertEqual(result.insights.runtime_warnings, ('markdown_vector_runtime_unavailable',))
        finally:
            service.close()

    def test_markdown_query_reports_cpu_ready_when_semantic_runtime_is_healthy_without_cuda(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / 'query_runtime_cpu_ready'))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root), vector_backend='lancedb', vector_device='auto', reranker_enabled=False)
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        try:
            with patch.object(service, '_ensure_vector_runtime_ready', return_value=None):
                service.rebuild_index()
            with patch('omniclip_rag.service.runtime_dependency_issue', return_value=None),                  patch('omniclip_rag.service.resolve_vector_device', return_value='cpu'):
                result = service.query('块嵌入', limit=5, score_threshold=0, allowed_families={'markdown'})
            self.assertTrue(result.hits)
            self.assertIn('markdown_vector_cpu_ready', result.insights.runtime_warnings)
            self.assertNotIn('markdown_vector_runtime_unavailable', result.insights.runtime_warnings)
        finally:
            service.close()

    def test_markdown_query_reports_cuda_execution_when_vector_and_reranker_run_on_gpu(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / 'query_runtime_cuda_ready'))
        config = AppConfig(
            vault_path=str(SAMPLE_ROOT),
            data_root=str(data_paths.global_root),
            vector_backend='lancedb',
            vector_device='cuda',
            reranker_enabled=True,
        )
        service = OmniClipService(config, data_paths)
        service.vector_index = _GpuVectorIndex()
        service.reranker = _CudaReranker()
        try:
            with patch.object(service, '_ensure_vector_runtime_ready', return_value=None), \
                 patch('omniclip_rag.service.resolve_vector_device', return_value='cuda'):
                service.rebuild_index()
                chunk_row = service.store.connection.execute(
                    "SELECT chunk_id FROM chunks ORDER BY chunk_id LIMIT 1"
                ).fetchone()
                self.assertIsNotNone(chunk_row)
                service.vector_index.injected = [SimpleNamespace(chunk_id=chunk_row['chunk_id'], score=0.95)]
                result = service.query('块嵌入', limit=5, score_threshold=0, allowed_families={'markdown'})
            self.assertTrue(result.hits)
            self.assertIn('markdown_vector_cuda_ready', result.insights.runtime_warnings)
            self.assertIn('markdown_reranker_cuda_ready', result.insights.runtime_warnings)
            self.assertEqual(result.insights.query_stage['vector_actual_device'], 'cuda:0')
            self.assertEqual(result.insights.query_stage['reranker_actual_device'], 'cuda:0')
        finally:
            service.close()

    def test_single_character_query_skips_vector_noise(self) -> None:
        vault_copy = ROOT / ".tmp" / "single_char_vault_test"
        data_root = ROOT / ".tmp" / "single_char_data_test"
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / "page_a.md").write_text("- 鞋子记录\n  id:: 11111111-1111-1111-1111-111111111111\n", encoding="utf-8")
        (vault_copy / "page_b.md").write_text("- 完全无关的日志\n  id:: 22222222-2222-2222-2222-222222222222\n", encoding="utf-8")
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        vector_stub = _InjectVectorIndex()
        service.vector_index = vector_stub
        try:
            service.rebuild_index()
            page_b_chunk = service.store.connection.execute(
                "SELECT chunk_id FROM chunks WHERE source_path = 'page_b.md' LIMIT 1"
            ).fetchone()["chunk_id"]
            vector_stub.injected = [SimpleNamespace(chunk_id=page_b_chunk, score=0.99)]
            hits, context = service.query("鞋", limit=5)
            self.assertTrue(hits)
            self.assertNotIn("page_b", {hit.title for hit in hits})
            self.assertNotIn("完全无关的日志", context)
        finally:
            service.close()
    def test_bootstrap_reranker_works_when_disabled(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / 'bootstrap_reranker_disabled'))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root), reranker_enabled=False)
        service = OmniClipService(config, data_paths)
        try:
            with patch('omniclip_rag.service.CrossEncoderReranker') as reranker_cls:
                reranker_cls.return_value.warmup.return_value = {'backend': 'cross-encoder', 'model': config.reranker_model, 'model_ready': True}
                result = service.bootstrap_reranker()
            reranker_cls.assert_called_once()
            reranker_cls.return_value.warmup.assert_called_once_with(allow_download=True)
            self.assertTrue(result['model_ready'])
        finally:
            service.close()

    def test_bootstrap_model_downloads_snapshot_without_vector_runtime(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / 'bootstrap_model_only'))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root), vector_backend='lancedb', vector_device='cuda')
        service = OmniClipService(config, data_paths)
        try:
            with patch('omniclip_rag.service.prepare_local_model_snapshot', return_value={'model_ready': True, 'model': config.vector_model}) as prepare_mock, \
                 patch('omniclip_rag.service.runtime_dependency_issue', return_value='runtime missing') as runtime_mock:
                result = service.bootstrap_model()
            prepare_mock.assert_called_once_with(config, data_paths, allow_download=True)
            runtime_mock.assert_not_called()
            self.assertTrue(result['model_ready'])
        finally:
            service.close()

    def test_query_skips_reranker_when_disabled_even_if_instance_exists(self) -> None:
        vault_copy = ROOT / '.tmp' / 'reranker_disabled_vault_test'
        data_root = ROOT / '.tmp' / 'reranker_disabled_data_test'
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / 'page_a.md').write_text('- 我的日程\n  id:: 11111111-1111-1111-1111-111111111111\n', encoding='utf-8')
        (vault_copy / 'page_b.md').write_text('- 我的待办\n  id:: 22222222-2222-2222-2222-222222222222\n', encoding='utf-8')
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root), vector_backend='disabled', reranker_enabled=False)
        service = OmniClipService(config, data_paths)
        reranker_called: list[bool] = []

        class _GuardReranker:
            def warmup(self, *, allow_download: bool = False):
                return {'backend': 'guard'}
            def rerank(self, query_text: str, hits: list[SearchHit], candidate_limit: int):
                reranker_called.append(True)
                raise AssertionError('reranker should stay disabled')

        service.reranker = _GuardReranker()
        service.vector_index = _StubVectorIndex()
        try:
            service.rebuild_index()
            result = service.query('我的', limit=10, score_threshold=0)
            self.assertTrue(result.hits)
            self.assertEqual(reranker_called, [])
            self.assertFalse(result.insights.reranker.applied)
            self.assertEqual(result.insights.reranker.skipped_reason, 'disabled')
        finally:
            service.close()

    def test_query_filters_by_final_reranked_score(self) -> None:
        vault_copy = ROOT / ".tmp" / "threshold_rerank_vault_test"
        data_root = ROOT / ".tmp" / "threshold_rerank_data_test"
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / "page_a.md").write_text("- 我的日程\n  id:: 11111111-1111-1111-1111-111111111111\n", encoding="utf-8")
        (vault_copy / "page_b.md").write_text("- 我的待办\n  id:: 22222222-2222-2222-2222-222222222222\n", encoding="utf-8")
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root), reranker_enabled=True)
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        service.reranker = _LoweringReranker()
        try:
            service.rebuild_index()
            result = service.query("我的", limit=39, score_threshold=35)
            self.assertTrue(result.hits)
            self.assertTrue(all(hit.score >= 35 for hit in result.hits))
            self.assertEqual(service.reranker.last_candidate_limit, min(2, max(39, build_query_profile("我的", 39).hydration_pool_size)))
        finally:
            service.close()
    def test_semantic_only_hits_keep_reasonable_score_floor(self) -> None:
        vault_copy = ROOT / ".tmp" / "semantic_floor_vault_test"
        data_root = ROOT / ".tmp" / "semantic_floor_data_test"
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / "page_a.md").write_text("- 鞋履搭配\n  id:: 77777777-7777-7777-7777-777777777777\n", encoding="utf-8")
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        vector_stub = _InjectVectorIndex()
        service.vector_index = vector_stub
        try:
            service.rebuild_index()
            page_chunk = service.store.connection.execute(
                "SELECT chunk_id FROM chunks WHERE source_path = 'page_a.md' LIMIT 1"
            ).fetchone()["chunk_id"]
            vector_stub.injected = [SimpleNamespace(chunk_id=page_chunk, score=0.32)]
            hits, _ = service.query("穿搭风格", limit=3, score_threshold=12)
            self.assertTrue(hits)
            self.assertGreaterEqual(hits[0].score, 12.0)
            self.assertIn("语义相似", hits[0].reason)
        finally:
            service.close()
    def test_query_merges_vector_only_candidates(self) -> None:
        vault_copy = ROOT / ".tmp" / "vector_merge_vault_test"
        data_root = ROOT / ".tmp" / "vector_merge_data_test"
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / "page_a.md").write_text("- 鞋子记录\n  id:: 11111111-1111-1111-1111-111111111111\n", encoding="utf-8")
        (vault_copy / "page_b.md").write_text("- 足部装备\n  id:: 22222222-2222-2222-2222-222222222222\n", encoding="utf-8")
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        vector_stub = _InjectVectorIndex()
        service.vector_index = vector_stub
        try:
            service.rebuild_index()
            page_b_chunk = service.store.connection.execute(
                "SELECT chunk_id FROM chunks WHERE source_path = 'page_b.md' LIMIT 1"
            ).fetchone()["chunk_id"]
            vector_stub.injected = [SimpleNamespace(chunk_id=page_b_chunk, score=0.97)]
            hits, _ = service.query("鞋子", limit=5)
            self.assertGreaterEqual(len(hits), 2)
            self.assertIn("page_a", {hit.title for hit in hits})
            self.assertIn("page_b", {hit.title for hit in hits})
        finally:
            service.close()
    def test_context_pack_merges_same_parent_siblings(self) -> None:
        hits = [
            SearchHit(
                score=96.0,
                title='如何极致的用好MyLifeOrganized',
                anchor='总结 > 第二部分 > 步骤三 > 区分“日程”与“待办” > 核心原则',
                source_path='pages/mlo.md',
                rendered_text='',
                chunk_id='a',
                display_text='- **总结**\n    - **第二部分**\n        - **步骤三**\n            - **区分“日程”与“待办”**\n                - **核心原则**：MLO 是待办，不是日程表。',
            ),
            SearchHit(
                score=95.0,
                title='如何极致的用好MyLifeOrganized',
                anchor='总结 > 第二部分 > 步骤三 > 区分“日程”与“待办” > 如何操作',
                source_path='pages/mlo.md',
                rendered_text='',
                chunk_id='b',
                display_text='- **总结**\n    - **第二部分**\n        - **步骤三**\n            - **区分“日程”与“待办”**\n                - **如何操作**：固定出席型事务放进日历，而不是待办。',
            ),
            SearchHit(
                score=94.0,
                title='如何极致的用好MyLifeOrganized',
                anchor='总结 > 第二部分 > 步骤三 > 区分“日程”与“待办” > 极致用法',
                source_path='pages/mlo.md',
                rendered_text='',
                chunk_id='c',
                display_text='- **总结**\n    - **第二部分**\n        - **步骤三**\n            - **区分“日程”与“待办”**\n                - **极致用法**：与日历双向同步，保持待办列表纯净。',
            ),
        ]
        context = OmniClipService.compose_context_pack_text('我的日程', hits)
        self.assertEqual(context.count('笔记片段1：'), 1)
        self.assertEqual(context.count('笔记片段2：'), 0)
        self.assertIn('**核心原则**', context)
        self.assertIn('**如何操作**', context)
        self.assertIn('**极致用法**', context)
    def test_context_pack_redacts_core_secrets(self) -> None:
        vault_copy = ROOT / ".tmp" / "redact_vault_test"
        data_root = ROOT / ".tmp" / "redact_data_test"
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / "付款记录.md").write_text(
            "- 付款记录\n  - 鞋子订单\n    - 密码: abc123456\n    - 备注: 已付款\n",
            encoding="utf-8",
        )
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        try:
            service.rebuild_index()
            hits, context = service.query("密码", limit=3)
            self.assertTrue(hits)
            self.assertIn("[被RAG过滤/Filtered by RAG]", context)
            self.assertNotIn("abc123456", context)
        finally:
            service.close()
    def test_context_pack_resolves_block_ref_without_uuid(self) -> None:
        vault_copy = ROOT / ".tmp" / "ref_vault_test"
        data_root = ROOT / ".tmp" / "ref_data_test"
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / "穿着.md").write_text(
            "- 黄帝客\n"
            "  id:: 33333333-3333-3333-3333-333333333333\n"
            "  - 1:1底 7000步\n"
            "- 我今天的穿着\n"
            "  - ((33333333-3333-3333-3333-333333333333))\n",
            encoding="utf-8",
        )
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        try:
            service.rebuild_index()
            hits, context = service.query("穿着", limit=3)
            self.assertTrue(hits)
            self.assertIn("黄帝客", context)
            self.assertNotIn("33333333-3333-3333-3333-333333333333", context)
        finally:
            service.close()
    def test_query_dedupes_overlapping_and_duplicate_fragments(self) -> None:
        vault_copy = ROOT / ".tmp" / "dedupe_vault_test"
        data_root = ROOT / ".tmp" / "dedupe_data_test"
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / "手机笔记.md").write_text(
            "- 鞋子记录\n"
            "  - 棕色鞋\n"
            "    - 20260219 9000步\n"
            "  - 白鞋\n"
            "    - 20250228 7000步最跟部已经磨开\n"
            "- 鞋底胶综合硬度不如木片，是否能垫小木片\n",
            encoding="utf-8",
        )
        (vault_copy / "2026-02-24T11_42_55.580Z.android.md").write_text(
            "- 鞋子记录\n"
            "  - 棕色鞋\n"
            "    - 20260219 9000步\n"
            "  - 白鞋\n"
            "    - 20250228 7000步最跟部已经磨开\n",
            encoding="utf-8",
        )
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        try:
            service.rebuild_index()
            hits, context = service.query("鞋", limit=5)
            self.assertGreaterEqual([hit.title for hit in hits].count("手机笔记"), 2)
            self.assertIn("手机笔记", {hit.title for hit in hits})
            self.assertIn("鞋底胶综合硬度不如木片", context)
            self.assertEqual(sum(1 for hit in hits if hit.title == "手机笔记" and hit.anchor.startswith("鞋子记录")), 2)
        finally:
            service.close()
    def test_ai_collaboration_export_mode_adds_guidance_without_changing_hits(self) -> None:
        vault_copy = ROOT / '.tmp' / 'ai_collab_vault_test'
        data_root = ROOT / '.tmp' / 'ai_collab_data_test'
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / '手机笔记.md').write_text('- 鞋子记录\n  - 棕色鞋\n    - 20260219 9000步\n', encoding='utf-8')
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root), context_export_mode='ai-collab')
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        try:
            service.rebuild_index()
            result = service.query('鞋', limit=3)
            self.assertTrue(result.hits)
            self.assertIn('AI协作模式', result.context_text)
            self.assertIn('检索关键词', result.context_text)
        finally:
            service.close()
    def test_query_supports_page_blocklist_rules(self) -> None:
        vault_copy = ROOT / ".tmp" / "page_block_vault_test"
        data_root = ROOT / ".tmp" / "page_block_data_test"
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / "2026-02-24T11_42_55.580Z.android.md").write_text("- 棕色鞋\n  - 20260219 9000步\n", encoding="utf-8")
        (vault_copy / "手机笔记.md").write_text("- 鞋子记录\n  - 棕色鞋\n    - 20260219 9000步\n", encoding="utf-8")
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(
            vault_path=str(vault_copy),
            data_root=str(data_paths.global_root),
            page_blocklist_rules="1\t^2026-.*\\.android$\n0\t^手机笔记$",
        )
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        try:
            service.rebuild_index()
            hits, context = service.query("鞋", limit=5)
            self.assertTrue(hits)
            self.assertEqual({hit.title for hit in hits}, {"手机笔记"})
            self.assertIn("手机笔记", context)
            self.assertNotIn("2026-02-24T11_42_55.580Z.android", context)
        finally:
            service.close()
    def test_reindex_updates_changed_file(self) -> None:
        vault_copy = ROOT / ".tmp" / "watch_vault_test"
        data_root = ROOT / ".tmp" / "watch_data_test"
        shutil.copytree(SAMPLE_ROOT, vault_copy)
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        try:
            service.rebuild_index()
            target = vault_copy / "pages" / "Logseq笔记样本.md"
            with target.open("a", encoding="utf-8") as handle:
                handle.write("\n- 热更新查询验证\n  id:: 22222222-2222-2222-2222-222222222222\n")
            service.reindex_paths(["pages/Logseq笔记样本.md"], [])
            hits, _ = service.query("热更新查询验证", limit=3)
            self.assertTrue(hits)
            self.assertEqual(hits[0].anchor, "热更新查询验证")
        finally:
            service.close()
    def test_reindex_keeps_previous_index_when_changed_file_is_temporarily_unreadable(self) -> None:
        vault_copy = ROOT / '.tmp' / 'reindex_locked_vault_test'
        data_root = ROOT / '.tmp' / 'reindex_locked_data_test'
        vault_copy.mkdir(parents=True, exist_ok=True)
        target = vault_copy / 'page_a.md'
        target.write_text('- 旧内容\n  id:: dddddddd-dddd-dddd-dddd-dddddddddddd\n', encoding='utf-8')
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        try:
            service.rebuild_index()
            target.write_text('- 新内容\n  id:: dddddddd-dddd-dddd-dddd-dddddddddddd\n', encoding='utf-8')
            with patch('omniclip_rag.parser.parse_markdown_file', side_effect=PermissionError('locked')):
                stats = service.reindex_paths(['page_a.md'], [])
            old_hits, _ = service.query('旧内容', limit=3)
            new_hits, _ = service.query('新内容', limit=3)
            self.assertTrue(old_hits)
            self.assertFalse(new_hits)
            self.assertIn('page_a.md', stats.get('skipped_changed_paths', []))
        finally:
            service.close()
    def test_reindex_marks_vector_state_dirty_then_repairs_it(self) -> None:
        vault_copy = ROOT / '.tmp' / 'vector_dirty_vault_test'
        data_root = ROOT / '.tmp' / 'vector_dirty_data_test'
        vault_copy.mkdir(parents=True, exist_ok=True)
        target = vault_copy / 'page_a.md'
        target.write_text('- 初始内容\n  id:: eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee\n', encoding='utf-8')
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        try:
            service.rebuild_index()
            failing_vector = _FailingVectorIndex()
            service.vector_index = failing_vector
            target.write_text('- 更新内容\n  id:: eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee\n', encoding='utf-8')
            stats = service.reindex_paths(['page_a.md'], [])
            self.assertEqual(stats.get('vector_dirty'), 1)
            self.assertIn('page_a.md', (service._read_watch_state() or {}).get('dirty_vector_paths', []))
            hits, _ = service.query('更新内容', limit=3)
            self.assertTrue(hits)
            snapshot, _ = service._snapshot_safe()
            repair_events = service._repair_watch_state(snapshot or {})
            self.assertTrue(repair_events)
            self.assertIsNone(service._read_watch_state())
            self.assertGreaterEqual(len(failing_vector.upsert_batches), 2)
        finally:
            service.close()
    def test_snapshot_safe_reports_vault_offline_instead_of_empty_snapshot(self) -> None:
        vault_copy = ROOT / '.tmp' / 'snapshot_offline_vault_test'
        data_root = ROOT / '.tmp' / 'snapshot_offline_data_test'
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / 'page_a.md').write_text('- 初始内容\n', encoding='utf-8')
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        try:
            service.rebuild_index()
            shutil.rmtree(vault_copy)
            snapshot, reason = service._snapshot_safe()
            self.assertIsNone(snapshot)
            self.assertIn('vault', (reason or '').lower())
        finally:
            service.close()
    def test_rebuild_skips_unreadable_markdown_files(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        original_parse = parser_module.parse_markdown_file
        def side_effect(vault_root, absolute_path):
            if absolute_path.name == "这是一个标题点击后可以进入另外一篇笔记.md":
                raise PermissionError("denied")
            return original_parse(vault_root, absolute_path)
        try:
            with patch("omniclip_rag.parser.parse_markdown_file", side_effect=side_effect):
                stats = service.rebuild_index()
            self.assertGreaterEqual(stats["files"], 1)
            self.assertGreaterEqual(stats["chunks"], 1)
        finally:
            service.close()
    def test_rebuild_demotes_duplicate_block_ids_instead_of_crashing(self) -> None:
        vault_copy = ROOT / '.tmp' / 'duplicate_vault_test'
        data_root = ROOT / '.tmp' / 'duplicate_data_test'
        if vault_copy.exists():
            shutil.rmtree(vault_copy)
        if data_root.exists():
            shutil.rmtree(data_root)
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / 'page_a.md').write_text(
            '- 第一块\n  id:: 11111111-1111-1111-1111-111111111111\n',
            encoding='utf-8',
        )
        (vault_copy / 'page_b.md').write_text(
            '- 第二块\n  id:: 11111111-1111-1111-1111-111111111111\n',
            encoding='utf-8',
        )
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        try:
            stats = service.rebuild_index()
            self.assertEqual(stats['files'], 2)
            self.assertEqual(stats['duplicate_block_ids'], 1)
            rows = service.store.connection.execute(
                'SELECT source_path, block_id, properties_json FROM chunks ORDER BY source_path'
            ).fetchall()
            self.assertEqual(rows[0]['block_id'], '11111111-1111-1111-1111-111111111111')
            self.assertIsNone(rows[1]['block_id'])
            self.assertIn('_duplicate_block_id', rows[1]['properties_json'])
        finally:
            service.close()
    def test_rebuild_waits_while_paused_then_continues(self) -> None:
        vault_copy = ROOT / '.tmp' / 'paused_vault_test'
        data_root = ROOT / '.tmp' / 'paused_data_test'
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / 'page_a.md').write_text('- 第一块\n  id:: cccccccc-cccc-cccc-cccc-cccccccccccc\n', encoding='utf-8')
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        original_parse = parser_module.parse_markdown_file
        parse_called = threading.Event()
        pause_event = threading.Event()
        pause_event.set()
        outcome: dict[str, object] = {}
        def wrapped_parse(vault_root, absolute_path):
            parse_called.set()
            return original_parse(vault_root, absolute_path)
        def worker():
            service = OmniClipService(config, data_paths)
            service.vector_index = _StubVectorIndex()
            try:
                outcome['stats'] = service.rebuild_index(pause_event=pause_event)
            except Exception as exc:
                outcome['error'] = exc
            finally:
                service.close()
        thread = threading.Thread(target=worker, daemon=True)
        with patch('omniclip_rag.parser.parse_markdown_file', side_effect=wrapped_parse):
            thread.start()
            time.sleep(0.25)
            self.assertTrue(thread.is_alive())
            self.assertFalse(parse_called.is_set())
            pause_event.clear()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertNotIn('error', outcome)
        self.assertEqual(outcome['stats']['files'], 1)
    def test_rebuild_can_resume_after_interruption(self) -> None:
        vault_copy = ROOT / '.tmp' / 'resume_vault_test'
        data_root = ROOT / '.tmp' / 'resume_data_test'
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / 'page_a.md').write_text('- 第一块\n  id:: aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa\n', encoding='utf-8')
        (vault_copy / 'page_b.md').write_text('- 第二块\n  id:: bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb\n', encoding='utf-8')
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        original_parse = parser_module.parse_markdown_file
        call_count = {'value': 0}
        def failing_parse(vault_root, absolute_path):
            call_count['value'] += 1
            if call_count['value'] == 2:
                raise RuntimeError('simulated crash')
            return original_parse(vault_root, absolute_path)
        try:
            with patch('omniclip_rag.parser.parse_markdown_file', side_effect=failing_parse):
                with self.assertRaises(RuntimeError):
                    service.rebuild_index()
            pending = service.pending_rebuild()
            self.assertIsNotNone(pending)
            self.assertEqual(pending['completed'], 1)
        finally:
            service.close()
        resumed = OmniClipService(config, data_paths)
        resumed.vector_index = _StubVectorIndex()
        try:
            stats = resumed.rebuild_index(resume=True)
            self.assertEqual(stats['files'], 2)
            self.assertIsNone(resumed.pending_rebuild())
        finally:
            resumed.close()
    def test_rebuild_resume_state_is_compact(self) -> None:
        vault_copy = ROOT / '.tmp' / 'resume_vault_test'
        data_root = ROOT / '.tmp' / 'resume_data_test'
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / 'page_a.md').write_text('- 第一块\n  id:: aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa\n', encoding='utf-8')
        (vault_copy / 'page_b.md').write_text('- 第二块\n  id:: bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb\n', encoding='utf-8')
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        original_parse = parser_module.parse_markdown_file
        call_count = {'value': 0}

        def failing_parse(vault_root, absolute_path):
            call_count['value'] += 1
            if call_count['value'] == 2:
                raise RuntimeError('simulated crash')
            return original_parse(vault_root, absolute_path)

        try:
            with patch('omniclip_rag.parser.parse_markdown_file', side_effect=failing_parse):
                with self.assertRaises(RuntimeError):
                    service.rebuild_index()
            payload = json.loads(service._rebuild_state_file.read_text(encoding='utf-8'))
            self.assertIn('manifest_signature', payload)
            self.assertNotIn('file_manifest', payload)
            self.assertNotIn('completed_paths', payload)
            self.assertEqual(int(payload.get('indexing_cursor', 0) or 0), 1)
        finally:
            service.close()

    def test_rebuild_can_resume_vector_stage_from_durable_cursor(self) -> None:
        vault_copy = ROOT / '.tmp' / 'vector_resume_vault_test'
        data_root = ROOT / '.tmp' / 'vector_resume_data_test'
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / 'page_a.md').write_text(
            '- 第一块\n  id:: 11111111-1111-1111-1111-111111111111\n- 第二块\n  id:: 22222222-2222-2222-2222-222222222222\n- 第三块\n  id:: 33333333-3333-3333-3333-333333333333\n',
            encoding='utf-8',
        )
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        service.vector_index = _ResumeAwareVectorIndex(fail_once=True)
        try:
            with patch('omniclip_rag.service.REBUILD_VECTOR_RESUME_REWIND', 1):
                with self.assertRaises(RuntimeError):
                    service.rebuild_index()
                pending = service.pending_rebuild()
                self.assertIsNotNone(pending)
                self.assertEqual(pending['phase'], 'vectorizing')
                self.assertEqual(int(pending['completed']), 2)
        finally:
            service.close()

        resumed = OmniClipService(config, data_paths)
        tracker = _ResumeAwareVectorIndex(fail_once=False)
        resumed.vector_index = tracker
        try:
            with patch('omniclip_rag.service.REBUILD_VECTOR_RESUME_REWIND', 1):
                stats = resumed.rebuild_index(resume=True)
            self.assertEqual(stats['files'], 1)
            self.assertTrue(tracker.deleted_batches)
            self.assertEqual(tracker.rebuild_calls[0]['progress_offset'], 1)
            self.assertFalse(bool(tracker.rebuild_calls[0]['reset_index']))
        finally:
            resumed.close()

    def test_rebuild_watchdog_writes_diagnostic_report_when_progress_stalls(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / 'watchdog_rebuild'))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        service.vector_index = _SlowVectorIndex()
        progress_events: list[dict[str, object]] = []
        try:
            with patch('omniclip_rag.service.REBUILD_WATCHDOG_INTERVAL_SECONDS', 0.02), \
                 patch('omniclip_rag.service.REBUILD_WATCHDOG_STALL_SECONDS', 0.05), \
                 patch('omniclip_rag.service.REBUILD_WATCHDOG_REPEAT_SECONDS', 0.05):
                service.rebuild_index(on_progress=lambda payload: progress_events.append(dict(payload)))
            self.assertTrue(any(bool(item.get('watchdog_stalled')) for item in progress_events))
            diagnostics = sorted((data_paths.logs_dir / 'diagnostics').glob('rebuild-watchdog-*.json'))
            self.assertTrue(diagnostics)
        finally:
            service.close()

    def test_rebuild_fails_fast_when_vector_runtime_is_not_ready(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT / 'runtime_precheck'))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root), vector_backend='lancedb')
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        try:
            with patch('omniclip_rag.service.runtime_dependency_issue', return_value='当前还不能开始本地语义建库或向量查询。'):
                with self.assertRaises(RuntimeDependencyError):
                    service.rebuild_index()
        finally:
            service.close()
    def test_cancelled_rebuild_keeps_index_pending(self) -> None:
        vault_copy = ROOT / '.tmp' / 'cancelled_rebuild_vault_test'
        data_root = ROOT / '.tmp' / 'cancelled_rebuild_data_test'
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / 'page_a.md').write_text('- 第一块\n  id:: aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa\n', encoding='utf-8')
        (vault_copy / 'page_b.md').write_text('- 第二块\n  id:: bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb\n', encoding='utf-8')
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        cancel_event = threading.Event()
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        try:
            def on_progress(payload: dict[str, object]) -> None:
                if str(payload.get('stage') or '') == 'indexing' and int(payload.get('current', 0) or 0) >= 1:
                    cancel_event.set()
            with self.assertRaises(BuildCancelledError):
                service.rebuild_index(on_progress=on_progress, cancel_event=cancel_event)
            snapshot = service.status_snapshot()
            self.assertEqual(snapshot['index_state'], 'pending')
            self.assertFalse(snapshot['index_ready'])
            self.assertFalse(snapshot['query_allowed'])
            self.assertIsNotNone(snapshot['pending_rebuild'])
            self.assertGreaterEqual(int((snapshot['stats'] or {}).get('chunks', 0) or 0), 1)
        finally:
            service.close()
    def test_watch_requires_ready_index_marker(self) -> None:
        vault_copy = ROOT / '.tmp' / 'watch_guard_vault_test'
        data_root = ROOT / '.tmp' / 'watch_guard_data_test'
        vault_copy.mkdir(parents=True, exist_ok=True)
        (vault_copy / 'page_a.md').write_text('- 第一块\n  id:: cccccccc-cccc-cccc-cccc-cccccccccccc\n', encoding='utf-8')
        data_paths = ensure_data_paths(str(data_root))
        config = AppConfig(vault_path=str(vault_copy), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        service.vector_index = _StubVectorIndex()
        try:
            with self.assertRaises(RuntimeError):
                service.watch_until_stopped(threading.Event(), interval=0.01, force_polling=True)
            snapshot = service.status_snapshot()
            self.assertEqual(snapshot['index_state'], 'missing')
            self.assertFalse(snapshot['watch_allowed'])
            self.assertEqual(snapshot['stats'], {'files': 0, 'chunks': 0, 'refs': 0})
        finally:
            service.close()
    def test_estimate_space_records_preflight(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root), vector_backend="lancedb")
        service = OmniClipService(config, data_paths)
        try:
            report = service.estimate_space()
            self.assertGreater(report.vault_file_count, 0)
            self.assertGreater(report.required_free_bytes, 0)
            latest = service.store.fetch_latest_preflight()
            self.assertIsNotNone(latest)
            self.assertEqual(latest["vault_file_count"], report.vault_file_count)
        finally:
            service.close()
    def test_rebuild_records_vector_pipeline_history(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        service.vector_index = _ProfilingVectorIndex()
        try:
            service.rebuild_index()
            history = load_build_history(data_paths.state_dir / 'build_history.json')
            self.assertTrue(history)
            latest = history[-1]
            self.assertAlmostEqual(float(latest['vector_prepare_seconds']), 0.08, places=3)
            self.assertAlmostEqual(float(latest['vector_write_seconds']), 0.04, places=3)
            self.assertEqual(int(latest['vector_write_flush_count']), 3)
        finally:
            service.close()
    def test_clear_exports_removes_export_files(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        try:
            export_path = data_paths.exports_dir / 'old_context.md'
            export_path.write_text('stale', encoding='utf-8')
            service.clear_data(clear_exports=True)
            self.assertFalse(export_path.exists())
        finally:
            service.close()
    def test_clear_index_also_resets_vector_storage(self) -> None:
        data_paths = ensure_data_paths(str(TEST_DATA_ROOT))
        config = AppConfig(vault_path=str(SAMPLE_ROOT), data_root=str(data_paths.global_root))
        service = OmniClipService(config, data_paths)
        stub = _StubVectorIndex()
        service.vector_index = stub
        try:
            service.rebuild_index()
            self.assertTrue(data_paths.sqlite_file.exists())
            service.clear_data(clear_index=True)
            self.assertTrue(stub.reset_called)
            self.assertEqual(service.store.stats(), {"files": 0, "chunks": 0, "refs": 0})
        finally:
            service.close()
if __name__ == "__main__":
    unittest.main()


