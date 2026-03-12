from __future__ import annotations

import locale
import re
from collections.abc import Callable

_CONTEXT_PAGE_RE = re.compile(r'^# 笔记名：(.*)$')
_CONTEXT_FRAGMENT_RE = re.compile(r'^笔记片段\d+：$')


def sort_text_value(value: str) -> str:
    raw = str(value or '').strip().casefold()
    try:
        return locale.strxfrm(raw)
    except Exception:
        return raw


def _page_group_key(hit) -> tuple[str, str]:
    return (str(getattr(hit, 'title', '') or ''), str(getattr(hit, 'source_path', '') or ''))


def sort_hits_by_page_average(hits: list[object]) -> list[object]:
    page_stats: dict[tuple[str, str], tuple[float, int, int]] = {}
    original_order = {getattr(hit, 'chunk_id', f'row-{index}'): index for index, hit in enumerate(hits)}
    for index, hit in enumerate(hits):
        key = _page_group_key(hit)
        total, count, first_index = page_stats.get(key, (0.0, 0, index))
        score = float(getattr(hit, 'score', 0.0) or 0.0)
        page_stats[key] = (total + score, count + 1, min(first_index, index))
    ordered_pages = sorted(
        page_stats.items(),
        key=lambda item: (
            -(item[1][0] / max(item[1][1], 1)),
            item[1][2],
            sort_text_value(item[0][0]),
        ),
    )
    page_order = {page_key: index for index, (page_key, _stats) in enumerate(ordered_pages)}
    return sorted(
        list(hits),
        key=lambda hit: (
            page_order.get(_page_group_key(hit), len(page_order)),
            original_order.get(getattr(hit, 'chunk_id', ''), 0),
        ),
    )


def collect_context_sections(
    context_text: str,
    *,
    translate: Callable[..., str],
) -> list[dict[str, object]]:
    sections: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for line_no, line in enumerate((context_text or '').splitlines(), start=1):
        page_match = _CONTEXT_PAGE_RE.match(line.strip())
        if page_match:
            if current is not None:
                sections.append(current)
            current = {'title': page_match.group(1).strip(), 'line': line_no, 'fragments': 0}
            continue
        if current is not None and _CONTEXT_FRAGMENT_RE.match(line.strip()):
            current['fragments'] = int(current['fragments']) + 1
    if current is not None:
        sections.append(current)

    seen_counts: dict[str, int] = {}
    for section in sections:
        title = str(section.get('title') or translate('none_value'))
        fragments = int(section.get('fragments') or 0)
        base = translate('context_jump_item', title=title, count=fragments)
        seen_counts[base] = seen_counts.get(base, 0) + 1
        display = base if seen_counts[base] == 1 else f"{base} [{seen_counts[base]}]"
        section['display'] = display
    return sections


def format_elapsed_ms(elapsed_ms: int, *, translate: Callable[..., str]) -> str:
    value = max(int(elapsed_ms or 0), 0)
    if value <= 0:
        return translate('query_limit_elapsed_unknown')
    if value < 1000:
        return translate('query_limit_elapsed_ms', value=value)
    return translate('query_limit_elapsed_s', value=f'{value / 1000:.1f}')


def _query_limit_device_label(device: str, *, translate: Callable[..., str]) -> str:
    return translate('query_limit_device_cuda') if str(device or '').strip().lower() == 'cuda' else translate('query_limit_device_cpu')


def _query_limit_reason_label(reason_code: str, *, translate: Callable[..., str]) -> str:
    normalized = str(reason_code or 'baseline').strip().lower() or 'baseline'
    key = f'query_limit_reason_{normalized}'
    try:
        return translate(key)
    except KeyError:
        return normalized


def render_query_limit_hint(recommendation: dict[str, object] | None, *, current_limit: str, translate: Callable[..., str]) -> str:
    if not recommendation:
        return translate('query_limit_hint_idle')
    minimum = int(recommendation.get('minimum', 0) or 0)
    maximum = int(recommendation.get('maximum', 0) or 0)
    preferred = int(recommendation.get('preferred', 0) or 0)
    if minimum <= 0 or maximum <= 0 or preferred <= 0:
        return translate('query_limit_hint_idle')
    return translate(
        'query_limit_hint_ready',
        current=current_limit or '0',
        minimum=minimum,
        maximum=maximum,
        preferred=preferred,
        device=_query_limit_device_label(str(recommendation.get('device', 'cpu')), translate=translate),
        elapsed=format_elapsed_ms(int(recommendation.get('elapsed_ms', 0) or 0), translate=translate),
        samples=int(recommendation.get('samples', 0) or 0),
        reason=_query_limit_reason_label(str(recommendation.get('reason_code', 'baseline')), translate=translate),
    )


def _query_stage_label(payload: dict[str, object] | None, *, translate: Callable[..., str]) -> str:
    stage_code = str((payload or {}).get('stage_status') or 'prepare').strip().lower() or 'prepare'
    key = f'query_stage_{stage_code}'
    try:
        return translate(key)
    except KeyError:
        return stage_code


def query_progress_detail(payload: dict[str, object] | None, *, translate: Callable[..., str]) -> str:
    stage = _query_stage_label(payload, translate=translate)
    data = payload or {}
    current = max(0, int(data.get('candidates') or data.get('hits') or 0))
    total = max(0, int(data.get('limit') or 0))
    if current > 0 and total > 0:
        return translate('query_status_running_detail_counts', stage=stage, current=current, total=total)
    return translate('query_status_running_detail', stage=stage)
