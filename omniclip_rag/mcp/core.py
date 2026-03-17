from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp import types
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .. import __version__
from ..headless.bootstrap import HeadlessContext
from ..models import QueryResult, SearchHit
from ..service import OmniClipService
from ..vector_index import detect_acceleration, inspect_runtime_environment, is_local_model_ready, resolve_vector_device, runtime_dependency_issue


LOGGER = logging.getLogger(__name__)

MCP_SEARCH_TOOL = 'omniclip.search'
MCP_STATUS_TOOL = 'omniclip.status'
MCP_DEFAULT_TOP_K = 5
MCP_MAX_TOP_K = 8
MCP_DEFAULT_SNIPPET_CHARS = 800
MCP_MAX_SNIPPET_CHARS = 1200
MCP_SELFTEST_QUERY = '我的思维'
_DEGRADED_WARNING_CODES = {
    'markdown_vector_runtime_unavailable',
    'markdown_vector_index_missing',
    'markdown_vector_query_failed',
}
_READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


class OmniClipMcpApplication:
    def __init__(self, context: HeadlessContext) -> None:
        self.context = context
        self.bundle = context.bundle
        self.service = context.service
        self.server = FastMCP(
            'OmniClip RAG MCP Server',
            instructions=(
                'Use omniclip.status to inspect readiness and degradation before deep search. '
                'Use omniclip.search for read-only local knowledge retrieval.'
            ),
            log_level='WARNING',
        )
        self._register_tools()

    def _register_tools(self) -> None:
        @self.server.tool(
            name=MCP_STATUS_TOOL,
            description='Return OmniClip query readiness, runtime status, and current live snapshot.',
            annotations=_READ_ONLY_ANNOTATIONS,
        )
        def status() -> types.CallToolResult:
            return self.status_result()

        @self.server.tool(
            name=MCP_SEARCH_TOOL,
            description='Search OmniClip local knowledge across markdown, PDF, and Tika-backed sources.',
            annotations=_READ_ONLY_ANNOTATIONS,
        )
        def search(
            query: str,
            allowed_families: list[str] | None = None,
            top_k: int | None = None,
            max_snippet_chars: int | None = None,
        ) -> types.CallToolResult:
            return self.search_result(
                query=query,
                allowed_families=allowed_families,
                top_k=top_k,
                max_snippet_chars=max_snippet_chars,
            )

    def run_stdio(self) -> None:
        self.server.run(transport='stdio')

    def status_payload(self) -> dict[str, Any]:
        service_status = self.service.status_snapshot()
        runtime_state = inspect_runtime_environment()
        acceleration = detect_acceleration(force_refresh=False)
        vector_enabled = str(self.bundle.config.vector_backend or 'disabled').strip().lower() not in {'', 'disabled', 'none', 'off'}
        model_ready = bool(is_local_model_ready(self.bundle.config, self.bundle.paths)) if vector_enabled else False
        semantic_runtime_ready = bool(
            vector_enabled
            and runtime_state.get('runtime_complete')
            and acceleration.get('torch_available')
            and acceleration.get('sentence_transformers_available')
            and model_ready
            and not runtime_dependency_issue(self.bundle.config)
        )
        query_ready = bool(service_status.get('query_allowed'))
        default_mode = 'hybrid' if query_ready and semantic_runtime_ready else 'lexical_only'
        degraded_default = default_mode != 'hybrid'
        requested_device = str(self.bundle.config.vector_device or 'auto').strip().lower() or 'auto'
        resolved_device = resolve_vector_device(requested_device)
        if not query_ready:
            device = 'unavailable'
        elif semantic_runtime_ready and acceleration.get('cuda_available') and resolved_device == 'cuda':
            device = 'cuda'
        else:
            device = 'cpu'
        warnings = self._status_warnings(
            service_status=service_status,
            runtime_state=runtime_state,
            semantic_runtime_ready=semantic_runtime_ready,
            degraded_default=degraded_default,
        )
        index_meta = self.service._index_trace_metadata()
        return {
            'version': __version__,
            'query_ready': query_ready,
            'default_mode': default_mode,
            'semantic_runtime_ready': semantic_runtime_ready,
            'degraded_default': degraded_default,
            'device': device,
            'available_families': list(service_status.get('query_available_families') or []),
            'data_root': str(self.bundle.paths.global_root),
            'runtime_root': str(runtime_state.get('active_runtime_dir') or ''),
            'runtime_preferred_root': str(runtime_state.get('preferred_runtime_dir') or ''),
            'snapshot_id': str(index_meta.get('index_generation_id') or ''),
            'warnings': warnings,
            'last_selfcheck_ok': bool(self._last_selfcheck_payload().get('ok')),
        }

    def status_result(self) -> types.CallToolResult:
        payload = self.status_payload()
        return self._result(
            payload=payload,
            text=self._format_status_text(payload),
        )

    def search_payload(
        self,
        *,
        query: str,
        allowed_families: list[str] | None = None,
        top_k: int | None = None,
        max_snippet_chars: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        normalized_query = str(query or '').strip()
        if not normalized_query:
            return (
                {
                    'error_code': 'query_empty',
                    'message': 'Query text is required.',
                },
                True,
            )
        normalized_families = self.service._normalize_query_families(allowed_families)
        if not normalized_families:
            return (
                {
                    'error_code': 'query_family_none_selected',
                    'message': 'No supported query families were selected.',
                    'supported_families': ['markdown', 'pdf', 'tika'],
                },
                True,
            )
        status_payload = self.status_payload()
        if not status_payload.get('query_ready'):
            return (
                {
                    'error_code': 'index_not_ready',
                    'message': 'OmniClip indexes are not ready yet.',
                    'status': status_payload,
                },
                True,
            )

        limit = min(max(int(top_k or MCP_DEFAULT_TOP_K), 1), MCP_MAX_TOP_K)
        snippet_limit = min(max(int(max_snippet_chars or MCP_DEFAULT_SNIPPET_CHARS), 80), MCP_MAX_SNIPPET_CHARS)
        try:
            result = self.service.query(
                normalized_query,
                limit=limit,
                allowed_families=sorted(normalized_families),
                query_mode='hybrid',
                export_result=False,
            )
        except RuntimeError as exc:
            error_message = str(exc).strip() or exc.__class__.__name__
            error_code = 'index_not_ready' if ('索引还没建立' in error_message or '当前索引未完成' in error_message) else 'query_failed'
            return (
                {
                    'error_code': error_code,
                    'message': error_message,
                    'status': status_payload,
                },
                True,
            )

        payload = self._build_search_payload(
            query=normalized_query,
            requested_families=sorted(normalized_families),
            limit=limit,
            snippet_limit=snippet_limit,
            result=result,
        )
        return payload, False

    def search_result(
        self,
        *,
        query: str,
        allowed_families: list[str] | None = None,
        top_k: int | None = None,
        max_snippet_chars: int | None = None,
    ) -> types.CallToolResult:
        payload, is_error = self.search_payload(
            query=query,
            allowed_families=allowed_families,
            top_k=top_k,
            max_snippet_chars=max_snippet_chars,
        )
        formatter = self._format_search_error_text if is_error else self._format_search_text
        return self._result(payload=payload, text=formatter(payload), is_error=is_error)

    def selfcheck_payload(self, *, query: str = MCP_SELFTEST_QUERY) -> dict[str, Any]:
        status_payload = self.status_payload()
        search_payload, search_error = self.search_payload(
            query=query,
            allowed_families=['markdown', 'pdf', 'tika'],
            top_k=3,
            max_snippet_chars=240,
        )
        ok = bool(status_payload.get('query_ready')) and not search_error
        payload = {
            'ok': ok,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'status': status_payload,
            'search': search_payload,
            'query': query,
        }
        self._write_last_selfcheck(payload)
        return payload

    def _build_search_payload(
        self,
        *,
        query: str,
        requested_families: list[str],
        limit: int,
        snippet_limit: int,
        result: QueryResult,
    ) -> dict[str, Any]:
        warnings = list(dict.fromkeys(str(item) for item in (result.insights.runtime_warnings or ()) if str(item)))
        degraded = any(item in _DEGRADED_WARNING_CODES for item in warnings)
        query_stage = dict(result.insights.query_stage or {})
        if str(query_stage.get('fallback_reason') or '').strip() == 'markdown_index_not_ready':
            warnings.append('markdown_index_not_ready')
            degraded = True
        effective_mode = 'lexical_only' if degraded else 'hybrid'
        index_meta = self.service._index_trace_metadata()
        status_payload = self.status_payload()
        hits_payload = [
            self._format_hit_payload(rank=index + 1, hit=hit, snippet_limit=snippet_limit)
            for index, hit in enumerate(result.hits[:limit])
        ]
        return {
            'query': query,
            'effective_mode': effective_mode,
            'degraded': degraded,
            'warnings': warnings,
            'available_families': status_payload['available_families'],
            'requested_families': requested_families,
            'returned': len(hits_payload),
            'snapshot_id': str(index_meta.get('index_generation_id') or ''),
            'results': hits_payload,
        }

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        normalized = ' '.join(str(text or '').split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(limit - 1, 1)].rstrip() + '…'

    def _format_hit_payload(self, *, rank: int, hit: SearchHit, snippet_limit: int) -> dict[str, Any]:
        snippet_source = hit.preview_text or hit.display_text or hit.rendered_text
        source_label = self._resolve_source_label(hit)
        payload: dict[str, Any] = {
            'rank': rank,
            'source_family': str(hit.source_family or 'markdown'),
            'source_kind': str(hit.source_kind or hit.source_family or 'markdown'),
            'source_label': source_label,
            'score': float(hit.score or 0.0),
            'snippet': self._truncate_text(snippet_source, snippet_limit),
            'source_name': Path(hit.source_path).name if str(hit.source_path or '').strip() else '',
        }
        if int(hit.page_no or 0) > 0:
            payload['page_no'] = int(hit.page_no)
        section_heading = str(hit.anchor or '').strip()
        if section_heading:
            payload['section_heading'] = section_heading
        return payload

    def _resolve_source_label(self, hit: SearchHit) -> str:
        explicit = str(getattr(hit, 'source_label', '') or '').strip()
        if explicit:
            return explicit
        family = str(getattr(hit, 'source_family', '') or 'markdown').strip().lower() or 'markdown'
        source_name = Path(str(getattr(hit, 'source_path', '') or '')).name or str(getattr(hit, 'title', '') or '').strip() or family.upper()
        if family == 'pdf':
            page_no = int(getattr(hit, 'page_no', 0) or 0)
            page_label = f'第 {page_no} 页' if page_no > 0 else (str(getattr(hit, 'anchor', '') or '').strip() or 'PDF')
            return f'PDF · {source_name} · {page_label}'
        if family == 'tika':
            source_kind = str(getattr(hit, 'source_kind', '') or 'tika').strip().lower() or 'tika'
            format_label = source_kind.upper() if source_kind else 'Tika'
            return f'{format_label}(Tika) · {source_name}'
        title = str(getattr(hit, 'title', '') or '').strip()
        label_name = title or source_name or 'Markdown'
        return f'Markdown · {label_name}'

    def _format_status_text(self, payload: dict[str, Any]) -> str:
        warnings = payload.get('warnings') or []
        warning_line = f"Warnings: {', '.join(warnings)}" if warnings else 'Warnings: none'
        return '\n'.join(
            (
                'OmniClip MCP status',
                f"Version: {payload.get('version')}",
                f"Query ready: {payload.get('query_ready')}",
                f"Default mode: {payload.get('default_mode')}",
                f"Semantic runtime ready: {payload.get('semantic_runtime_ready')}",
                f"Device: {payload.get('device')}",
                f"Available families: {', '.join(payload.get('available_families') or []) or 'none'}",
                f"Snapshot: {payload.get('snapshot_id') or 'none'}",
                warning_line,
            )
        )

    def _format_search_text(self, payload: dict[str, Any]) -> str:
        lines = [
            f"OmniClip search returned {payload.get('returned', 0)} result(s).",
            f"Effective mode: {payload.get('effective_mode')}.",
        ]
        warnings = payload.get('warnings') or []
        if warnings:
            lines.append(f"Warnings: {', '.join(warnings)}")
        for item in payload.get('results', []):
            lines.append(f"{item.get('rank')}. [{item.get('source_label')}] score {float(item.get('score') or 0.0):.3f}")
            lines.append(f"   Snippet: {item.get('snippet')}")
        return '\n'.join(lines)

    def _format_search_error_text(self, payload: dict[str, Any]) -> str:
        lines = [
            'OmniClip search failed.',
            f"Error: {payload.get('error_code')}",
            f"Message: {payload.get('message')}",
        ]
        status = payload.get('status')
        if isinstance(status, dict):
            families = ', '.join(status.get('available_families') or []) or 'none'
            lines.append(f"Available families: {families}")
            lines.append(f"Default mode: {status.get('default_mode')}")
        return '\n'.join(lines)

    def _status_warnings(
        self,
        *,
        service_status: dict[str, Any],
        runtime_state: dict[str, Any],
        semantic_runtime_ready: bool,
        degraded_default: bool,
    ) -> list[str]:
        warnings: list[str] = []
        if service_status.get('pending_rebuild'):
            warnings.append('index_pending_rebuild')
        if not service_status.get('query_allowed'):
            warnings.append('index_not_ready')
        if degraded_default:
            warnings.append('semantic_runtime_unavailable')
        missing_items = [str(item) for item in (runtime_state.get('runtime_missing_items') or []) if str(item)]
        if missing_items:
            warnings.append('runtime_missing:' + ','.join(missing_items[:6]))
        return warnings

    def _last_selfcheck_path(self) -> Path:
        return self.bundle.paths.shared_root / 'mcp_selfcheck.json'

    def _last_selfcheck_payload(self) -> dict[str, Any]:
        path = self._last_selfcheck_path()
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_last_selfcheck(self, payload: dict[str, Any]) -> None:
        path = self._last_selfcheck_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    @staticmethod
    def _result(*, payload: dict[str, Any], text: str, is_error: bool = False) -> types.CallToolResult:
        return types.CallToolResult(
            content=[types.TextContent(type='text', text=text)],
            structuredContent=payload,
            isError=is_error,
        )
