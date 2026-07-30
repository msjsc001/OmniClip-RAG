from __future__ import annotations

import json
import os
import sys
import traceback
from dataclasses import asdict, fields, replace
from pathlib import Path
from typing import Callable

from .config import AppConfig, ensure_data_paths, normalize_vault_path
from .models import (
    QueryInsights,
    QueryLimitRecommendation,
    QueryResult,
    RerankOutcome,
    SearchHit,
)
from .service import OmniClipService, release_process_query_resources
from .vector_index import semantic_query_session


def query_worker_command(*, request_path: Path, output_path: Path, progress_path: Path) -> list[str]:
    args = [
        '--query-worker',
        '--request',
        str(request_path),
        '--output',
        str(output_path),
        '--progress',
        str(progress_path),
    ]
    if getattr(sys, 'frozen', False):
        return [sys.executable, *args]
    launcher = Path(__file__).resolve().parents[1] / 'launcher.py'
    return [sys.executable, str(launcher), *args]


def query_worker_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment['OMNICLIP_INTERNAL_WORKER'] = '1'
    environment['PYTHONUNBUFFERED'] = '1'
    return environment


def query_worker_creationflags() -> int:
    if os.name != 'nt':
        return 0
    subprocess_module = __import__('subprocess')
    return (
        getattr(subprocess_module, 'CREATE_NO_WINDOW', 0x08000000)
        | getattr(subprocess_module, 'BELOW_NORMAL_PRIORITY_CLASS', 0x00004000)
    )


def _json_default(value: object):
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f'Object of type {value.__class__.__name__} is not JSON serializable')


def write_json_atomic(target: Path, payload: dict[str, object]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f'{target.name}.{os.getpid()}.tmp')
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, default=_json_default),
        encoding='utf-8',
    )
    os.replace(temporary, target)


def config_to_payload(config: AppConfig) -> dict[str, object]:
    return dict(asdict(config))


def config_from_payload(payload: dict[str, object]) -> AppConfig:
    allowed = {item.name for item in fields(AppConfig)}
    values = {key: value for key, value in dict(payload or {}).items() if key in allowed}
    return AppConfig(**values)


def query_result_to_payload(result: QueryResult) -> dict[str, object]:
    return asdict(result)


def query_result_from_payload(payload: dict[str, object]) -> QueryResult:
    insights_payload = dict(payload.get('insights') or {})
    recommendation_payload = insights_payload.get('recommendation')
    reranker_payload = insights_payload.get('reranker')
    insights_payload['runtime_warnings'] = tuple(insights_payload.get('runtime_warnings') or ())
    insights_payload['runtime_requirements'] = tuple(
        dict(item)
        for item in (insights_payload.get('runtime_requirements') or ())
        if isinstance(item, dict)
    )
    insights_payload['trace_lines'] = tuple(insights_payload.get('trace_lines') or ())
    insights_payload['recommendation'] = (
        QueryLimitRecommendation(**dict(recommendation_payload))
        if isinstance(recommendation_payload, dict)
        else None
    )
    insights_payload['reranker'] = (
        RerankOutcome(**dict(reranker_payload))
        if isinstance(reranker_payload, dict)
        else None
    )
    allowed_insight_fields = {item.name for item in fields(QueryInsights)}
    insights = QueryInsights(
        **{key: value for key, value in insights_payload.items() if key in allowed_insight_fields}
    )
    hits = [SearchHit(**dict(item)) for item in list(payload.get('hits') or ())]
    return QueryResult(
        hits=hits,
        context_text=str(payload.get('context_text') or ''),
        insights=insights,
    )


def _selected_vaults(config: AppConfig) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    raw_values = list(getattr(config, 'md_selected_vault_paths', ()) or ())
    if not raw_values:
        raw_values = [getattr(config, 'vault_path', '')]
    for raw_value in raw_values:
        normalized = normalize_vault_path(raw_value)
        lowered = normalized.lower()
        if not normalized or lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(normalized)
    return ordered


def _decorate_hits(hits: list[SearchHit], *, vault_path: str, multi_vault: bool) -> list[SearchHit]:
    if not multi_vault:
        return list(hits)
    vault_name = Path(vault_path).name or vault_path
    decorated: list[SearchHit] = []
    for hit in hits:
        source_label = str(getattr(hit, 'source_label', '') or '').strip()
        source_label = f'{vault_name} · {source_label}' if source_label else vault_name
        decorated.append(replace(hit, source_label=source_label))
    return decorated


@semantic_query_session()
def execute_query_request(
    request: dict[str, object],
    *,
    on_progress: Callable[[dict[str, object]], None] | None = None,
    release_process_resources: bool = True,
) -> tuple[QueryResult, dict[str, object]]:
    config = config_from_payload(dict(request.get('config') or {}))
    query_text = str(request.get('query_text') or '').strip()
    if not query_text:
        raise ValueError('Query text is empty.')
    score_threshold = float(request.get('score_threshold') or 0.0)
    allowed_families = tuple(
        str(item).strip().lower()
        for item in list(request.get('allowed_families') or ())
        if str(item).strip()
    )
    copy_result = bool(request.get('copy_result'))
    query_mode = str(request.get('query_mode') or 'hybrid').strip() or 'hybrid'
    selected_vaults = _selected_vaults(config)
    if not selected_vaults:
        raise RuntimeError('No markdown vault is enabled for querying.')

    total = len(selected_vaults)
    merged_hits: list[SearchHit] = []
    runtime_warnings: list[str] = []
    runtime_requirements: list[dict[str, object]] = []
    trace_lines: list[str] = []
    snapshots: dict[str, dict[str, object]] = {}
    shared_reranker = None
    single_result: QueryResult | None = None
    try:
        for index, vault_path in enumerate(selected_vaults, start=1):
            vault_config = replace(
                config,
                vault_path=vault_path,
                md_selected_vault_paths=list(selected_vaults),
            )
            paths = ensure_data_paths(getattr(vault_config, 'data_root', None), vault_path)
            service = OmniClipService(vault_config, paths)
            if shared_reranker is None:
                shared_reranker = service.reranker
            else:
                service.reranker = shared_reranker
            try:
                snapshot = service.status_snapshot()
                snapshots[vault_path] = dict(snapshot)
                available = {
                    str(item).strip().lower()
                    for item in snapshot.get('query_available_families', ())
                    if str(item).strip()
                }
                requested = {item for item in allowed_families if item}
                if not (available & requested):
                    if on_progress is not None:
                        on_progress({
                            'stage_status': 'skip_unavailable',
                            'overall_percent': index / max(total, 1) * 100.0,
                            'vault_path': vault_path,
                            'current_path': vault_path,
                        })
                    trace_lines.append(f'已跳过未就绪来源目录：{vault_path}')
                    continue

                def emit_inner(payload: dict[str, object]) -> None:
                    if on_progress is None:
                        return
                    progress = dict(payload or {})
                    inner_percent = float(progress.get('overall_percent', 0.0) or 0.0)
                    progress['overall_percent'] = (
                        (index - 1) + max(0.0, min(inner_percent, 100.0)) / 100.0
                    ) / max(total, 1) * 100.0
                    progress['vault_path'] = vault_path
                    progress.setdefault('current_path', vault_path)
                    progress.setdefault('stage_status', 'query_vault')
                    on_progress(progress)

                result = service.query(
                    query_text,
                    copy_result=copy_result,
                    score_threshold=score_threshold,
                    allowed_families=allowed_families,
                    on_progress=emit_inner,
                    query_mode=query_mode,
                )
                single_result = result
                merged_hits.extend(
                    _decorate_hits(
                        list(getattr(result, 'hits', ()) or ()),
                        vault_path=vault_path,
                        multi_vault=total > 1,
                    )
                )
                insights = getattr(result, 'insights', None)
                if insights is not None:
                    runtime_warnings.extend(tuple(getattr(insights, 'runtime_warnings', ()) or ()))
                    for requirement in tuple(getattr(insights, 'runtime_requirements', ()) or ()):
                        if not isinstance(requirement, dict):
                            continue
                        enriched = dict(requirement)
                        enriched.setdefault('vault_path', vault_path)
                        runtime_requirements.append(enriched)
                    trace_lines.extend(tuple(getattr(insights, 'trace_lines', ()) or ()))
            finally:
                service.close(release_process_resources=False)

        if total == 1 and single_result is not None:
            return single_result, dict(snapshots.get(selected_vaults[0]) or {})
        merged_hits.sort(key=lambda hit: float(getattr(hit, 'score', 0.0) or 0.0), reverse=True)
        final_limit = max(int(getattr(config, 'query_limit', 15) or 15), 1)
        final_hits = merged_hits[:final_limit]
        unique_requirements: list[dict[str, object]] = []
        seen_requirements: set[str] = set()
        for requirement in runtime_requirements:
            marker = json.dumps(requirement, ensure_ascii=False, sort_keys=True, default=_json_default)
            if marker in seen_requirements:
                continue
            seen_requirements.add(marker)
            unique_requirements.append(requirement)
        result = QueryResult(
            hits=final_hits,
            context_text='',
            insights=QueryInsights(
                selected_hits=len(final_hits),
                runtime_warnings=tuple(dict.fromkeys(runtime_warnings)),
                runtime_requirements=tuple(unique_requirements),
                trace_lines=tuple(dict.fromkeys(trace_lines)),
                query_plan={
                    'mode': 'multi_vault_fanout' if total > 1 else query_mode,
                    'vaults': list(selected_vaults),
                },
            ),
        )
        return result, {
            'mode': 'multi_vault_fanout' if total > 1 else query_mode,
            'vaults': list(selected_vaults),
            'vault_snapshots': snapshots,
        }
    finally:
        if release_process_resources:
            release_process_query_resources()


def run_query_worker(*, request_path: str, output_path: str, progress_path: str) -> int:
    request_target = Path(request_path)
    output_target = Path(output_path)
    progress_target = Path(progress_path)

    def emit_progress(payload: dict[str, object]) -> None:
        write_json_atomic(progress_target, dict(payload))

    try:
        request = json.loads(request_target.read_text(encoding='utf-8'))
        # This worker exits immediately after writing its response. Explicitly
        # moving or destroying large CUDA models before that write can crash
        # inside PyTorch native code and discard an otherwise completed query.
        # Let Windows reclaim the process resources after the response is safe.
        result, status_snapshot = execute_query_request(
            request,
            on_progress=emit_progress,
            release_process_resources=False,
        )
        payload = {
            'status': 'ok',
            'query_text': str(request.get('query_text') or ''),
            'copied': bool(request.get('copy_result')),
            'score_threshold': float(request.get('score_threshold') or 0.0),
            'allowed_families': list(request.get('allowed_families') or ()),
            'result': query_result_to_payload(result),
            'status_snapshot': status_snapshot,
        }
        write_json_atomic(output_target, payload)
        return 0
    except BaseException as exc:
        try:
            write_json_atomic(output_target, {
                'status': 'error',
                'error_class': exc.__class__.__name__,
                'error_message': str(exc).strip() or exc.__class__.__name__,
                'traceback': traceback.format_exc(),
            })
        except OSError:
            pass
        return 1
