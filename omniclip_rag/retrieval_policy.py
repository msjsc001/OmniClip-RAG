from __future__ import annotations

import re
from dataclasses import dataclass

from .models import SearchHit

QUERY_TERM_RE = re.compile(r"[\w一-鿿-]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class QueryProfile:
    kind: str
    terms: tuple[str, ...]
    use_vector: bool
    candidate_limit: int
    hydration_pool_size: int
    lexical_weight: float
    fts_weight: float
    like_weight: float
    vector_weight: float
    rrf_weight: float
    low_coverage_penalty: float


def build_query_profile(query_text: str, limit: int) -> QueryProfile:
    base = max(int(limit or 0), 1)
    stripped = query_text.strip()
    terms = tuple(_tokenize_query(query_text))
    if not stripped:
        return QueryProfile(
            kind='blank',
            terms=terms,
            use_vector=False,
            candidate_limit=max(base * 8, 64),
            hydration_pool_size=max(base * 4, 24),
            lexical_weight=1.0,
            fts_weight=1.0,
            like_weight=1.0,
            vector_weight=0.0,
            rrf_weight=0.0,
            low_coverage_penalty=0.0,
        )
    if len(stripped) <= 1:
        return QueryProfile(
            kind='single_char',
            terms=terms,
            use_vector=False,
            candidate_limit=max(base * 24, 240),
            hydration_pool_size=max(base * 8, 48),
            lexical_weight=1.35,
            fts_weight=1.1,
            like_weight=1.0,
            vector_weight=0.0,
            rrf_weight=0.55,
            low_coverage_penalty=0.0,
        )
    if len(terms) <= 1 and len(stripped) <= 4 and not _contains_cjk(stripped):
        return QueryProfile(
            kind='short_keyword',
            terms=terms,
            use_vector=True,
            candidate_limit=max(base * 16, 160),
            hydration_pool_size=max(base * 6, 36),
            lexical_weight=1.18,
            fts_weight=1.05,
            like_weight=1.0,
            vector_weight=0.72,
            rrf_weight=0.68,
            low_coverage_penalty=4.0,
        )
    if _looks_like_natural_query(stripped, terms):
        return QueryProfile(
            kind='natural_query',
            terms=terms,
            use_vector=True,
            candidate_limit=max(base * 12, 120),
            hydration_pool_size=max(base * 5, 30),
            lexical_weight=1.0,
            fts_weight=1.0,
            like_weight=0.95,
            vector_weight=1.08,
            rrf_weight=0.82,
            low_coverage_penalty=12.0,
        )
    return QueryProfile(
        kind='concept',
        terms=terms,
        use_vector=True,
        candidate_limit=max(base * 10, 96),
        hydration_pool_size=max(base * 4, 24),
        lexical_weight=1.04,
        fts_weight=1.0,
        like_weight=0.95,
        vector_weight=1.15,
        rrf_weight=0.9,
        low_coverage_penalty=12.0,
    )


def rank_candidates(query_text: str, rows, vector_candidates: dict[str, float], profile: QueryProfile) -> list[SearchHit]:
    normalized_query = query_text.strip().lower()
    signal_rows: list[dict[str, object]] = []
    for row in rows:
        vector_similarity = float(vector_candidates.get(row['chunk_id'], 0.0) or 0.0)
        lexical = _score_query(query_text, row['title'], row['anchor'], row['rendered_text'])
        fts_rank = row['fts_rank'] if 'fts_rank' in row.keys() else None
        like_hits = row['like_hits'] if 'like_hits' in row.keys() else 0
        fts_score = _score_fts_rank(fts_rank)
        like_score = float(like_hits or 0) * 8.0
        coverage = _query_coverage(query_text, row['title'], row['anchor'], row['rendered_text'])
        signal_rows.append(
            {
                'row': row,
                'lexical': lexical,
                'fts_rank': fts_rank,
                'fts_score': fts_score,
                'like_hits': like_hits,
                'like_score': like_score,
                'vector_similarity': vector_similarity,
                'coverage': coverage,
                'title_direct': bool(normalized_query and normalized_query in str(row['title']).lower()),
                'anchor_direct': bool(normalized_query and normalized_query in str(row['anchor']).lower()),
            }
        )

    rank_maps = _build_rank_maps(signal_rows)
    hits: list[SearchHit] = []
    for item in signal_rows:
        row = item['row']
        lexical = float(item['lexical']) * profile.lexical_weight
        fts_score = float(item['fts_score']) * profile.fts_weight
        like_score = float(item['like_score']) * profile.like_weight
        vector_score = float(item['vector_similarity']) * 20.0 * profile.vector_weight
        coverage = float(item['coverage'])
        raw_score = lexical + fts_score + like_score + vector_score
        raw_score += _rrf_bonus(str(row['chunk_id']), rank_maps, profile.rrf_weight)
        raw_score -= _length_penalty(str(row['rendered_text']), coverage)
        if len(profile.terms) > 1 and coverage < 0.45:
            raw_score -= profile.low_coverage_penalty
        score = _normalize_score(raw_score)
        if lexical <= 0 and fts_score <= 0 and like_score <= 0 and float(item['vector_similarity']) > 0:
            score = max(score, _semantic_only_score(float(item['vector_similarity'])))
        if score <= 0:
            continue
        hits.append(
            SearchHit(
                score=score,
                title=row['title'],
                anchor=row['anchor'],
                source_path=row['source_path'],
                rendered_text=row['rendered_text'],
                chunk_id=row['chunk_id'],
                display_text='',
                preview_text=_build_preview_text(query_text, row['rendered_text']),
                reason=_build_hit_reason(
                    query_text,
                    row['title'],
                    row['anchor'],
                    row['rendered_text'],
                    item['fts_rank'],
                    item['like_hits'],
                    float(item['vector_similarity']),
                ),
            )
        )
    hits.sort(key=lambda item: item.score, reverse=True)
    return hits


def _build_rank_maps(signal_rows: list[dict[str, object]]) -> dict[str, dict[str, int]]:
    def _rank_by(key: str, *, reverse: bool, predicate) -> dict[str, int]:
        ranked = [item for item in signal_rows if predicate(item)]
        ranked.sort(key=lambda item: float(item[key]), reverse=reverse)
        return {str(item['row']['chunk_id']): index for index, item in enumerate(ranked)}

    lexical_ranks = _rank_by('lexical', reverse=True, predicate=lambda item: float(item['lexical']) > 0)
    like_ranks = _rank_by('like_score', reverse=True, predicate=lambda item: float(item['like_score']) > 0)
    vector_ranks = _rank_by('vector_similarity', reverse=True, predicate=lambda item: float(item['vector_similarity']) > 0)

    fts_candidates = [item for item in signal_rows if item['fts_rank'] is not None]
    fts_candidates.sort(key=lambda item: float(item['fts_rank']))
    fts_ranks = {str(item['row']['chunk_id']): index for index, item in enumerate(fts_candidates)}

    return {
        'lexical': lexical_ranks,
        'like': like_ranks,
        'vector': vector_ranks,
        'fts': fts_ranks,
    }


def _rrf_bonus(chunk_id: str, rank_maps: dict[str, dict[str, int]], weight: float) -> float:
    if weight <= 0:
        return 0.0
    score = 0.0
    for name, ranks in rank_maps.items():
        rank = ranks.get(chunk_id)
        if rank is None:
            continue
        local_weight = 1.0
        if name == 'lexical':
            local_weight = 1.15
        elif name == 'vector':
            local_weight = 1.05
        score += local_weight / (60.0 + rank + 1.0)
    return score * 120.0 * weight


def _looks_like_natural_query(stripped: str, terms: tuple[str, ...]) -> bool:
    if len(terms) >= 3:
        return True
    return any(token in stripped for token in ('?', '？', '，', ',', '。', ';', '；', ':', '：', '怎么', '为什么', '如何'))


def _contains_cjk(text: str) -> bool:
    return any('一' <= char <= '鿿' for char in text)


def _tokenize_query(query_text: str) -> list[str]:
    terms = [term.lower() for term in QUERY_TERM_RE.findall(query_text.strip()) if term.strip()]
    return terms or ([query_text.strip().lower()] if query_text.strip() else [])


def _score_query(query_text: str, title: str, anchor: str, rendered_text: str) -> float:
    normalized_query = query_text.strip().lower()
    if not normalized_query:
        return 0.0

    score = 0.0
    title_lower = title.lower()
    anchor_lower = anchor.lower()
    text_lower = rendered_text.lower()
    terms = _tokenize_query(query_text)

    if normalized_query in title_lower:
        score += 64.0
    if normalized_query in anchor_lower:
        score += 52.0
    if normalized_query in text_lower:
        score += 28.0

    body_term_hits = 0
    covered_terms = 0
    for term in terms:
        term_hit = False
        if term in title_lower:
            score += 16.0
            term_hit = True
        if term in anchor_lower:
            score += 12.0
            term_hit = True
        occurrences = text_lower.count(term)
        if occurrences:
            body_term_hits += min(occurrences, 6)
            term_hit = True
        if term_hit:
            covered_terms += 1

    if body_term_hits:
        score += body_term_hits * 4.5

    coverage = covered_terms / max(len(terms), 1)
    if coverage >= 1.0:
        score += 10.0
    elif coverage >= 0.66:
        score += 4.0
    else:
        score -= 4.0

    if _contains_cjk(normalized_query) and normalized_query in text_lower:
        score += 10.0

    return score


def _score_fts_rank(fts_rank: object) -> float:
    if fts_rank is None:
        return 0.0
    try:
        rank = float(fts_rank)
    except (TypeError, ValueError):
        return 0.0
    if rank <= 0:
        return 24.0
    return 24.0 / (1.0 + rank)


def _query_coverage(query_text: str, title: str, anchor: str, rendered_text: str) -> float:
    terms = _tokenize_query(query_text)
    if not terms:
        return 0.0
    combined = f"{title}\n{anchor}\n{rendered_text}".lower()
    matched = sum(1 for term in terms if term in combined)
    return matched / len(terms)


def _length_penalty(rendered_text: str, coverage: float) -> float:
    normalized_length = len(re.sub(r'\s+', ' ', rendered_text).strip())
    overflow = max(normalized_length - 640, 0)
    if overflow <= 0:
        return 0.0
    base_penalty = min(overflow / 220.0, 12.0)
    if coverage >= 1.0:
        return base_penalty * 0.25
    if coverage >= 0.66:
        return base_penalty * 0.5
    return base_penalty


def _normalize_score(raw_score: float) -> float:
    return max(0.0, min(float(raw_score), 100.0))


def _semantic_only_score(vector_similarity: float) -> float:
    similarity = max(float(vector_similarity or 0.0), 0.0)
    if similarity <= 0.0:
        return 0.0
    if similarity <= 0.15:
        return min(10.0 + similarity * 12.0, 12.0)
    return min(12.0 + (similarity - 0.15) * 60.0, 40.0)


def _preview_source_text(rendered_text: str) -> str:
    return ' '.join(line.strip() for line in rendered_text.splitlines() if line.strip())


def _build_preview_text(query_text: str, rendered_text: str, limit: int = 220) -> str:
    source = _preview_source_text(rendered_text)
    if not source:
        return ''
    lowered = source.lower()
    positions = [lowered.find(term) for term in _tokenize_query(query_text) if term and lowered.find(term) >= 0]
    if not positions:
        return _truncate(source, limit=limit)
    start = max(min(positions) - 48, 0)
    end = min(start + limit, len(source))
    snippet = source[start:end].strip()
    if start > 0:
        snippet = '…' + snippet
    if end < len(source):
        snippet = snippet.rstrip() + '…'
    return snippet


def _truncate(text: str, limit: int = 220) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + '…'


def _build_hit_reason(
    query_text: str,
    title: str,
    anchor: str,
    rendered_text: str,
    fts_rank: object,
    like_hits: object,
    vector_score: float,
) -> str:
    normalized_query = query_text.strip().lower()
    title_lower = title.lower()
    anchor_lower = anchor.lower()
    text_lower = rendered_text.lower()
    reasons: list[str] = []
    if normalized_query and normalized_query in title_lower:
        reasons.append('标题直达')
    elif normalized_query and normalized_query in anchor_lower:
        reasons.append('语义路径直达')
    if normalized_query and normalized_query in text_lower:
        reasons.append('正文命中')
    if fts_rank is not None:
        reasons.append('全文检索')
    if float(like_hits or 0) > 0:
        reasons.append('关键词匹配')
    if vector_score > 0.15:
        reasons.append('语义相似')
    if not reasons:
        reasons.append('综合相关')
    return ' + '.join(dict.fromkeys(reasons))
