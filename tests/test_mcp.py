import asyncio
import importlib
import json
import shutil
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from omniclip_rag.config import AppConfig, ensure_data_paths
from omniclip_rag.app_entry.mcp import run_mcp_selfcheck
from omniclip_rag.mcp.core import MCP_SEARCH_TOOL, MCP_STATUS_TOOL, OmniClipMcpApplication
from omniclip_rag.models import QueryInsights, QueryResult, SearchHit


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / '.tmp' / 'test_mcp'


class _FakeService:
    def __init__(self, *, config: AppConfig, paths, status_payload: dict[str, object], query_result: QueryResult | None = None) -> None:
        self.config = config
        self.paths = paths
        self._status_payload = dict(status_payload)
        self._query_result = query_result
        self.query_calls: list[dict[str, object]] = []

    def status_snapshot(self) -> dict[str, object]:
        return dict(self._status_payload)

    def query(self, query_text: str, **kwargs) -> QueryResult:
        self.query_calls.append({'query_text': query_text, **kwargs})
        if self._query_result is None:
            raise RuntimeError('query should not have been called')
        return self._query_result

    def _index_trace_metadata(self) -> dict[str, object]:
        return {'index_generation_id': 'index-generation:test123'}

    @staticmethod
    def _normalize_query_families(allowed_families) -> set[str]:
        supported = {'markdown', 'pdf', 'tika'}
        if allowed_families is None:
            return set(supported)
        normalized = {str(item).strip().lower() for item in allowed_families if str(item).strip()}
        return {item for item in normalized if item in supported}

    def close(self) -> None:
        return None


def _make_context(*, query_allowed: bool = True, query_result: QueryResult | None = None):
    data_paths = ensure_data_paths(str(TEST_ROOT / 'data'))
    config = AppConfig(vault_path='', data_root=str(data_paths.global_root), vector_backend='lancedb', vector_device='cpu')
    status_payload = {
        'query_allowed': query_allowed,
        'query_available_families': ['markdown', 'pdf', 'tika'] if query_allowed else [],
        'pending_rebuild': None,
        'index_state': 'ready' if query_allowed else 'missing',
        'index_ready': query_allowed,
        'snapshot_id': 'index-generation:test123',
    }
    service = _FakeService(config=config, paths=data_paths, status_payload=status_payload, query_result=query_result)
    bundle = SimpleNamespace(config=config, paths=data_paths, language_code='zh-CN', theme_code='system', scale_percent=100)
    return SimpleNamespace(bundle=bundle, service=service, applied_components=(), close=service.close)


class McpTests(unittest.TestCase):
    def tearDown(self) -> None:
        if TEST_ROOT.exists():
            shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def test_mcp_entry_import_does_not_import_qt(self) -> None:
        removed: dict[str, object] = {}
        target_prefixes = ('PySide6', 'omniclip_rag.ui_next_qt')
        target_names = {'omniclip_rag.app_entry.mcp'}
        for name in list(sys.modules):
            if name in target_names or any(name.startswith(prefix) for prefix in target_prefixes):
                module = sys.modules.pop(name, None)
                if module is not None:
                    removed[name] = module
        try:
            importlib.import_module('omniclip_rag.app_entry.mcp')
            self.assertFalse(any(name.startswith('PySide6') for name in sys.modules))
            self.assertFalse(any(name.startswith('omniclip_rag.ui_next_qt') for name in sys.modules))
        finally:
            for name in list(sys.modules):
                if name in removed:
                    sys.modules.pop(name, None)
            sys.modules.update(removed)

    def test_status_tool_schema_is_read_only(self) -> None:
        context = _make_context()
        app = OmniClipMcpApplication(context)
        with patch('omniclip_rag.mcp.core.inspect_runtime_environment', return_value={'runtime_complete': True, 'active_runtime_dir': Path('D:/runtime'), 'preferred_runtime_dir': Path('D:/runtime')}), \
             patch('omniclip_rag.mcp.core.detect_acceleration', return_value={'torch_available': True, 'sentence_transformers_available': True, 'cuda_available': False}), \
             patch('omniclip_rag.mcp.core.is_local_model_ready', return_value=True), \
             patch('omniclip_rag.mcp.core.runtime_dependency_issue', return_value=''):
            tools = asyncio.run(app.server.list_tools())
        names = {tool.name: tool for tool in tools}
        self.assertIn(MCP_STATUS_TOOL, names)
        self.assertIn(MCP_SEARCH_TOOL, names)
        self.assertTrue(names[MCP_STATUS_TOOL].annotations.readOnlyHint)
        self.assertFalse(names[MCP_STATUS_TOOL].annotations.destructiveHint)
        self.assertTrue(names[MCP_STATUS_TOOL].annotations.idempotentHint)
        self.assertFalse(names[MCP_STATUS_TOOL].annotations.openWorldHint)

    def test_search_tool_returns_structured_content_and_text(self) -> None:
        hit = SearchHit(
            score=0.82,
            title='笔记样本',
            anchor='思维方式',
            source_path='pages/笔记样本.md',
            rendered_text='我的思维方式是先分析再行动。',
            chunk_id='chunk-1',
            display_text='我的思维方式是先分析再行动。',
            preview_text='我的思维方式是先分析再行动。',
            source_family='markdown',
            source_kind='markdown',
            source_label='Markdown · 笔记样本.md',
        )
        result = QueryResult(
            hits=[hit],
            context_text='context',
            insights=QueryInsights(runtime_warnings=('markdown_vector_cpu_ready',), query_stage={}),
        )
        context = _make_context(query_result=result)
        app = OmniClipMcpApplication(context)
        with patch('omniclip_rag.mcp.core.inspect_runtime_environment', return_value={'runtime_complete': True, 'active_runtime_dir': Path('D:/runtime'), 'preferred_runtime_dir': Path('D:/runtime')}), \
             patch('omniclip_rag.mcp.core.detect_acceleration', return_value={'torch_available': True, 'sentence_transformers_available': True, 'cuda_available': False}), \
             patch('omniclip_rag.mcp.core.is_local_model_ready', return_value=True), \
             patch('omniclip_rag.mcp.core.runtime_dependency_issue', return_value=''):
            payload = asyncio.run(app.server.call_tool(MCP_SEARCH_TOOL, {'query': '我的思维'}))
        self.assertFalse(payload.isError)
        self.assertEqual(payload.structuredContent['query'], '我的思维')
        self.assertEqual(payload.structuredContent['returned'], 1)
        self.assertEqual(payload.structuredContent['results'][0]['source_label'], 'Markdown · 笔记样本.md')
        self.assertIn('Markdown · 笔记样本.md', payload.content[0].text)
        self.assertFalse(context.service.query_calls[0]['export_result'])

    def test_search_tool_backfills_missing_source_label(self) -> None:
        hit = SearchHit(
            score=0.7,
            title='资本家',
            anchor='我的想法',
            source_path='pages/资本家.md',
            rendered_text='我的想法',
            chunk_id='chunk-source-label',
            display_text='我的想法',
            preview_text='我的想法',
            source_family='markdown',
            source_kind='markdown',
            source_label='',
        )
        result = QueryResult(hits=[hit], context_text='context', insights=QueryInsights())
        context = _make_context(query_result=result)
        app = OmniClipMcpApplication(context)
        with patch('omniclip_rag.mcp.core.inspect_runtime_environment', return_value={'runtime_complete': True, 'active_runtime_dir': Path('D:/runtime'), 'preferred_runtime_dir': Path('D:/runtime')}), \
             patch('omniclip_rag.mcp.core.detect_acceleration', return_value={'torch_available': True, 'sentence_transformers_available': True, 'cuda_available': False}), \
             patch('omniclip_rag.mcp.core.is_local_model_ready', return_value=True), \
             patch('omniclip_rag.mcp.core.runtime_dependency_issue', return_value=''):
            payload = asyncio.run(app.server.call_tool(MCP_SEARCH_TOOL, {'query': '我的想法'}))
        self.assertFalse(payload.isError)
        self.assertEqual(payload.structuredContent['results'][0]['source_label'], 'Markdown · 资本家')

    def test_search_tool_marks_lexical_degradation(self) -> None:
        hit = SearchHit(
            score=0.55,
            title='降级笔记',
            anchor='段落',
            source_path='pages/降级笔记.md',
            rendered_text='降级后仍然返回字面结果。',
            chunk_id='chunk-2',
            display_text='降级后仍然返回字面结果。',
            preview_text='降级后仍然返回字面结果。',
            source_family='markdown',
            source_kind='markdown',
            source_label='Markdown · 降级笔记.md',
        )
        result = QueryResult(
            hits=[hit],
            context_text='context',
            insights=QueryInsights(
                runtime_warnings=('markdown_vector_runtime_unavailable',),
                query_stage={'fallback_reason': 'vector_runtime_unavailable'},
            ),
        )
        context = _make_context(query_result=result)
        app = OmniClipMcpApplication(context)
        with patch('omniclip_rag.mcp.core.inspect_runtime_environment', return_value={'runtime_complete': False, 'runtime_missing_items': ['sentence-transformers'], 'active_runtime_dir': Path('D:/runtime'), 'preferred_runtime_dir': Path('D:/runtime')}), \
             patch('omniclip_rag.mcp.core.detect_acceleration', return_value={'torch_available': False, 'sentence_transformers_available': False, 'cuda_available': False}), \
             patch('omniclip_rag.mcp.core.is_local_model_ready', return_value=False), \
             patch('omniclip_rag.mcp.core.runtime_dependency_issue', return_value='runtime missing'):
            payload = asyncio.run(app.server.call_tool(MCP_SEARCH_TOOL, {'query': '我的思维'}))
        self.assertFalse(payload.isError)
        self.assertTrue(payload.structuredContent['degraded'])
        self.assertEqual(payload.structuredContent['effective_mode'], 'lexical_only')

    def test_search_tool_marks_backend_disabled_as_degraded(self) -> None:
        hit = SearchHit(
            score=0.6,
            title='后端关闭',
            anchor='段落',
            source_path='pages/backend-disabled.md',
            rendered_text='当前只走字面检索。',
            chunk_id='chunk-backend-disabled',
            display_text='当前只走字面检索。',
            preview_text='当前只走字面检索。',
            source_family='markdown',
            source_kind='markdown',
            source_label='Markdown · backend-disabled.md',
        )
        result = QueryResult(
            hits=[hit],
            context_text='context',
            insights=QueryInsights(
                runtime_warnings=('markdown_vector_backend_disabled',),
                query_stage={'fallback_reason': 'vector_backend_disabled'},
            ),
        )
        context = _make_context(query_result=result)
        context.bundle.config = replace(context.bundle.config, vector_backend='disabled')
        context.service.config = context.bundle.config
        app = OmniClipMcpApplication(context)
        with patch('omniclip_rag.mcp.core.inspect_runtime_environment', return_value={'runtime_complete': True, 'runtime_missing_items': [], 'active_runtime_dir': Path('D:/runtime'), 'preferred_runtime_dir': Path('D:/runtime')}), \
             patch('omniclip_rag.mcp.core.detect_acceleration', return_value={'torch_available': True, 'sentence_transformers_available': True, 'cuda_available': False}), \
             patch('omniclip_rag.mcp.core.is_local_model_ready', return_value=True), \
             patch('omniclip_rag.mcp.core.runtime_dependency_issue', return_value=''):
            payload = asyncio.run(app.server.call_tool(MCP_SEARCH_TOOL, {'query': '我的思维'}))
        self.assertFalse(payload.isError)
        self.assertTrue(payload.structuredContent['degraded'])
        self.assertEqual(payload.structuredContent['effective_mode'], 'lexical_only')
        self.assertIn('markdown_vector_backend_disabled', payload.structuredContent['warnings'])

    def test_search_tool_respects_top_k_and_snippet_limit(self) -> None:
        hits = [
            SearchHit(
                score=0.9 - index * 0.1,
                title=f'Note {index}',
                anchor='Anchor',
                source_path=f'pages/note-{index}.md',
                rendered_text='这是一个很长的片段内容 ' * 20,
                chunk_id=f'chunk-{index}',
                display_text='这是一个很长的片段内容 ' * 20,
                preview_text='这是一个很长的片段内容 ' * 20,
                source_family='markdown',
                source_kind='markdown',
                source_label=f'Markdown · note-{index}.md',
            )
            for index in range(3)
        ]
        result = QueryResult(hits=hits, context_text='context', insights=QueryInsights())
        context = _make_context(query_result=result)
        app = OmniClipMcpApplication(context)
        with patch('omniclip_rag.mcp.core.inspect_runtime_environment', return_value={'runtime_complete': True, 'active_runtime_dir': Path('D:/runtime'), 'preferred_runtime_dir': Path('D:/runtime')}), \
             patch('omniclip_rag.mcp.core.detect_acceleration', return_value={'torch_available': True, 'sentence_transformers_available': True, 'cuda_available': False}), \
             patch('omniclip_rag.mcp.core.is_local_model_ready', return_value=True), \
             patch('omniclip_rag.mcp.core.runtime_dependency_issue', return_value=''):
            payload = asyncio.run(app.server.call_tool(MCP_SEARCH_TOOL, {'query': '长片段', 'top_k': 2, 'max_snippet_chars': 90}))
        self.assertFalse(payload.isError)
        self.assertEqual(payload.structuredContent['returned'], 2)
        self.assertLessEqual(len(payload.structuredContent['results'][0]['snippet']), 90)

    def test_search_tool_returns_index_not_ready_error(self) -> None:
        context = _make_context(query_allowed=False, query_result=None)
        app = OmniClipMcpApplication(context)
        with patch('omniclip_rag.mcp.core.inspect_runtime_environment', return_value={'runtime_complete': False, 'runtime_missing_items': [], 'active_runtime_dir': Path('D:/runtime'), 'preferred_runtime_dir': Path('D:/runtime')}), \
             patch('omniclip_rag.mcp.core.detect_acceleration', return_value={'torch_available': False, 'sentence_transformers_available': False, 'cuda_available': False}), \
             patch('omniclip_rag.mcp.core.is_local_model_ready', return_value=False), \
             patch('omniclip_rag.mcp.core.runtime_dependency_issue', return_value='runtime missing'):
            payload = asyncio.run(app.server.call_tool(MCP_SEARCH_TOOL, {'query': '我的思维'}))
        self.assertTrue(payload.isError)
        self.assertEqual(payload.structuredContent['error_code'], 'index_not_ready')

    def test_selfcheck_persists_shared_payload(self) -> None:
        hit = SearchHit(
            score=0.88,
            title='Canary',
            anchor='Section',
            source_path='pages/canary.md',
            rendered_text='canary body',
            chunk_id='chunk-3',
            display_text='canary body',
            preview_text='canary body',
            source_family='markdown',
            source_kind='markdown',
            source_label='Markdown · canary.md',
        )
        result = QueryResult(hits=[hit], context_text='ctx', insights=QueryInsights())
        context = _make_context(query_result=result)
        app = OmniClipMcpApplication(context)
        with patch('omniclip_rag.mcp.core.inspect_runtime_environment', return_value={'runtime_complete': True, 'runtime_missing_items': [], 'active_runtime_dir': Path('D:/runtime'), 'preferred_runtime_dir': Path('D:/runtime')}), \
             patch('omniclip_rag.mcp.core.detect_acceleration', return_value={'torch_available': True, 'sentence_transformers_available': True, 'cuda_available': False}), \
             patch('omniclip_rag.mcp.core.is_local_model_ready', return_value=True), \
             patch('omniclip_rag.mcp.core.runtime_dependency_issue', return_value=''):
            payload = app.selfcheck_payload(query='我的思维')
        self.assertTrue(payload['ok'])
        self.assertTrue((context.bundle.paths.shared_root / 'mcp_selfcheck.json').exists())

    def test_run_mcp_selfcheck_writes_output_file(self) -> None:
        hit = SearchHit(
            score=0.88,
            title='Canary',
            anchor='Section',
            source_path='pages/canary.md',
            rendered_text='canary body',
            chunk_id='chunk-4',
            display_text='canary body',
            preview_text='canary body',
            source_family='markdown',
            source_kind='markdown',
            source_label='Markdown · canary.md',
        )
        result = QueryResult(hits=[hit], context_text='ctx', insights=QueryInsights())
        context = _make_context(query_result=result)
        output_path = TEST_ROOT / 'selfcheck.json'
        with patch('omniclip_rag.app_entry.mcp.create_headless_context', return_value=context), \
             patch('omniclip_rag.mcp.core.inspect_runtime_environment', return_value={'runtime_complete': True, 'runtime_missing_items': [], 'active_runtime_dir': Path('D:/runtime'), 'preferred_runtime_dir': Path('D:/runtime')}), \
             patch('omniclip_rag.mcp.core.detect_acceleration', return_value={'torch_available': True, 'sentence_transformers_available': True, 'cuda_available': False}), \
             patch('omniclip_rag.mcp.core.is_local_model_ready', return_value=True), \
             patch('omniclip_rag.mcp.core.runtime_dependency_issue', return_value=''):
            exit_code = run_mcp_selfcheck(data_root='', vault_path='', query='我的思维', output_path=str(output_path))
        self.assertEqual(exit_code, 0)
        payload = json.loads(output_path.read_text(encoding='utf-8'))
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['query'], '我的思维')


if __name__ == '__main__':
    unittest.main()
