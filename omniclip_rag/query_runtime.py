from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import QueryInsights, QueryLimitRecommendation, SearchHit

_QUERY_RUNTIME_VERSION = 1
_QUERY_RUNTIME_MAX_SAMPLES = 40
_FRAGMENT_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff-]+", re.UNICODE)


def select_query_hits(hits: list[SearchHit], limit: int) -> tuple[list[SearchHit], QueryInsights]:
    if limit <= 0 or not hits:
        return [], QueryInsights(hydrated_candidates=len(hits))
    deduped_hits, duplicate_count = _dedupe_hits(hits)
    selected_hits, suppressed_same_page = _select_novel_hits(deduped_hits, limit)
    insights = QueryInsights(
        hydrated_candidates=len(hits),
        selected_hits=len(selected_hits),
        suppressed_duplicates=duplicate_count,
        suppressed_same_page=suppressed_same_page,
        page_diversity=len({hit.source_path for hit in selected_hits}),
    )
    return selected_hits, insights


class QueryRuntimeAdvisor:
    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file

    def record_and_recommend(
        self,
        *,
        resolved_device: str,
        query_limit: int,
        elapsed_ms: int,
        selected_hits: int,
        hydrated_candidates: int,
        reranker_enabled: bool = False,
        reranker_degraded: bool = False,
        reranker_oom: bool = False,
    ) -> QueryLimitRecommendation:
        state = self._read_state()
        samples = list(state.get('samples', []))
        normalized_device = self._normalize_device(resolved_device)
        samples.append(
            {
                'device': normalized_device,
                'limit': max(int(query_limit or 0), 1),
                'elapsed_ms': max(int(elapsed_ms or 0), 0),
                'selected_hits': max(int(selected_hits or 0), 0),
                'hydrated_candidates': max(int(hydrated_candidates or 0), 0),
                'reranker_enabled': bool(reranker_enabled),
                'reranker_degraded': bool(reranker_degraded),
                'reranker_oom': bool(reranker_oom),
                'recorded_at': _utc_now(),
            }
        )
        samples = samples[-_QUERY_RUNTIME_MAX_SAMPLES:]
        state['version'] = _QUERY_RUNTIME_VERSION
        state['samples'] = samples
        self._write_state(state)
        return self._build_recommendation(samples, normalized_device, bool(reranker_enabled))

    def current_recommendation(self, resolved_device: str, reranker_enabled: bool = False) -> QueryLimitRecommendation:
        state = self._read_state()
        samples = list(state.get('samples', []))
        return self._build_recommendation(samples, self._normalize_device(resolved_device), bool(reranker_enabled))

    def _build_recommendation(self, samples: list[dict[str, object]], device: str, reranker_enabled: bool) -> QueryLimitRecommendation:
        device_samples = [sample for sample in samples if self._normalize_device(sample.get('device')) == device and bool(sample.get('reranker_enabled')) == bool(reranker_enabled)]
        preferred, minimum, maximum = _device_base_range(device, reranker_enabled)
        reason_code = 'baseline'
        elapsed_ms = 0
        if device_samples:
            elapsed_ms = int(sum(int(sample.get('elapsed_ms', 0) or 0) for sample in device_samples) / len(device_samples))
            avg_limit = sum(int(sample.get('limit', preferred) or preferred) for sample in device_samples) / len(device_samples)
            avg_selected = sum(int(sample.get('selected_hits', 0) or 0) for sample in device_samples) / len(device_samples)
            selected_ratio = avg_selected / max(avg_limit, 1)
            degradation_count = sum(1 for sample in device_samples if sample.get('reranker_degraded') or sample.get('reranker_oom'))
            if degradation_count:
                preferred = max(minimum, int(round(avg_limit * 0.7)))
                maximum = max(preferred + 4, int(round(avg_limit * 0.82)))
                reason_code = 'slow'
            elif elapsed_ms >= 2200:
                preferred = max(minimum, int(round(avg_limit * 0.75)))
                maximum = max(preferred + 4, int(round(avg_limit * 0.9)))
                reason_code = 'slow'
            elif elapsed_ms >= 1200:
                preferred = max(minimum, int(round(avg_limit * 0.9)))
                maximum = max(preferred + 6, maximum - 4)
                reason_code = 'steady'
            elif elapsed_ms <= 450 and selected_ratio >= 0.8:
                preferred = min(maximum, int(round(avg_limit * 1.2)))
                maximum = max(maximum, preferred + (12 if device == 'cuda' else 6))
                reason_code = 'expand'
            elif elapsed_ms <= 650 and selected_ratio <= 0.45:
                preferred = max(minimum, int(round(avg_limit * 0.9)))
                reason_code = 'trim'
            else:
                preferred = int(round(avg_limit))
                reason_code = 'steady'
        minimum = max(6, min(minimum, preferred))
        maximum = max(preferred, maximum)
        return QueryLimitRecommendation(
            device=device,
            preferred=int(preferred),
            minimum=int(minimum),
            maximum=int(maximum),
            reason_code=reason_code,
            samples=len(device_samples),
            elapsed_ms=elapsed_ms,
        )

    def _normalize_device(self, value: object) -> str:
        normalized = str(value or 'cpu').strip().lower()
        if normalized == 'auto':
            return 'cpu'
        if normalized in {'cuda', 'gpu'}:
            return 'cuda'
        return 'cpu'

    def _read_state(self) -> dict[str, object]:
        if not self.state_file.exists():
            return {'version': _QUERY_RUNTIME_VERSION, 'samples': []}
        try:
            payload = json.loads(self.state_file.read_text(encoding='utf-8'))
        except Exception:
            return {'version': _QUERY_RUNTIME_VERSION, 'samples': []}
        if not isinstance(payload, dict):
            return {'version': _QUERY_RUNTIME_VERSION, 'samples': []}
        payload.setdefault('samples', [])
        return payload

    def _write_state(self, payload: dict[str, object]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.state_file.with_suffix(self.state_file.suffix + '.tmp')
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        temp_path.replace(self.state_file)


def _device_base_range(device: str, reranker_enabled: bool) -> tuple[int, int, int]:
    if reranker_enabled:
        if device == 'cuda':
            return 50, 30, 70
        return 15, 10, 24
    if device == 'cuda':
        return 36, 20, 60
    return 15, 8, 24


def _dedupe_hits(hits: list[SearchHit]) -> tuple[list[SearchHit], int]:
    deduped: list[SearchHit] = []
    fragment_keys: set[str] = set()
    duplicate_count = 0
    for hit in hits:
        fragment = _fragment_text(hit)
        if not fragment or fragment in {'-', '- '}:
            duplicate_count += 1
            continue
        fragment_key = _normalize_fragment_key(fragment)
        if fragment_key and fragment_key in fragment_keys:
            duplicate_count += 1
            continue
        duplicate = False
        for existing in deduped:
            existing_fragment = _fragment_text(existing)
            if existing.source_path != hit.source_path:
                continue
            if hit.anchor == existing.anchor:
                duplicate = True
                break
            if _fragment_is_covered(fragment, existing_fragment):
                duplicate = True
                break
            if _hit_is_descendant_of(hit, existing) and _fragment_similarity(fragment, existing_fragment) >= 0.58:
                duplicate = True
                break
        if duplicate:
            duplicate_count += 1
            continue
        deduped.append(hit)
        if fragment_key:
            fragment_keys.add(fragment_key)
    return deduped, duplicate_count


def _select_novel_hits(hits: list[SearchHit], limit: int) -> tuple[list[SearchHit], int]:
    remaining = list(hits)
    selected: list[SearchHit] = []
    suppressed_same_page = 0
    while remaining and len(selected) < limit:
        best_index = -1
        best_score = float('-inf')
        best_skip = False
        for index, hit in enumerate(remaining):
            effective_score, should_skip = _effective_hit_score(hit, selected)
            if should_skip and effective_score > best_score:
                best_index = index
                best_score = effective_score
                best_skip = True
                continue
            if not should_skip and effective_score > best_score:
                best_index = index
                best_score = effective_score
                best_skip = False
        if best_index < 0:
            break
        candidate = remaining.pop(best_index)
        if best_skip:
            suppressed_same_page += 1
            continue
        selected.append(candidate)
    suppressed_same_page += len(remaining)
    return selected, suppressed_same_page


def _effective_hit_score(hit: SearchHit, selected: list[SearchHit]) -> tuple[float, bool]:
    same_page = [item for item in selected if item.source_path == hit.source_path]
    if not same_page:
        return hit.score, False
    fragment = _fragment_text(hit)
    novelty = 1.0
    same_branch = False
    for existing in same_page:
        existing_fragment = _fragment_text(existing)
        if _fragment_is_covered(fragment, existing_fragment):
            return float('-inf'), True
        novelty = min(novelty, 1.0 - _fragment_similarity(fragment, existing_fragment))
        if _anchor_branch(hit.anchor) == _anchor_branch(existing.anchor):
            same_branch = True
    if novelty < 0.14:
        return float('-inf'), True
    effective = hit.score
    effective -= min(len(same_page) * 2.2, 5.0)
    effective += novelty * 4.0
    if not same_branch:
        effective += 1.2
    return effective, False


def _fragment_text(hit: SearchHit) -> str:
    return (hit.display_text or hit.rendered_text or '').strip()


def _normalize_fragment_key(fragment: str) -> str:
    return '\n'.join(line.rstrip() for line in fragment.splitlines() if line.strip())


def _anchor_parts(anchor: str) -> list[str]:
    return [part.strip() for part in str(anchor or '').split(' > ') if part.strip()]


def _anchor_branch(anchor: str) -> tuple[str, ...]:
    parts = _anchor_parts(anchor)
    return tuple(parts[:2] if len(parts) >= 2 else parts)


def _hit_is_descendant_of(hit: SearchHit, other: SearchHit) -> bool:
    if hit.source_path != other.source_path:
        return False
    hit_parts = _anchor_parts(hit.anchor)
    other_parts = _anchor_parts(other.anchor)
    return len(hit_parts) > len(other_parts) and hit_parts[: len(other_parts)] == other_parts


def _fragment_similarity(left: str, right: str) -> float:
    left_lines = {line.strip() for line in left.splitlines() if line.strip()}
    right_lines = {line.strip() for line in right.splitlines() if line.strip()}
    if not left_lines or not right_lines:
        return 0.0
    line_overlap = len(left_lines & right_lines) / max(min(len(left_lines), len(right_lines)), 1)
    left_tokens = set(_FRAGMENT_TOKEN_RE.findall(left.lower()))
    right_tokens = set(_FRAGMENT_TOKEN_RE.findall(right.lower()))
    token_overlap = 0.0
    if left_tokens and right_tokens:
        token_overlap = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    return max(line_overlap, token_overlap)


def _fragment_is_covered(fragment: str, other: str) -> bool:
    fragment_lines = [line.rstrip() for line in fragment.splitlines() if line.strip()]
    other_lines = [line.rstrip() for line in other.splitlines() if line.strip()]
    if not fragment_lines or len(fragment_lines) > len(other_lines):
        return False
    start = 0
    for line in fragment_lines:
        matched = False
        for index in range(start, len(other_lines)):
            if other_lines[index] == line:
                start = index + 1
                matched = True
                break
        if not matched:
            return False
    return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')
