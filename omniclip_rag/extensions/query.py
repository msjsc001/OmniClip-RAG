from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from ..models import SearchHit
from .models import ExtensionIndexState
from .parsers.tika import detect_tika_format
from .registry import ExtensionRegistry

LOGGER = logging.getLogger(__name__)
_FUSION_RRF_K = 40.0
_FAMILY_WEIGHTS = {
    'markdown': 1.0,
    'pdf': 0.99,
    'tika': 0.985,
}


class ExtensionQueryBroker:
    """Fuse isolated extension hits into the main query flow without contaminating Markdown storage.

    Why: Markdown, PDF, and Tika each live in physically isolated stores, so their
    raw vector distances cannot be compared directly. The broker therefore only
    trusts each family's internal ordering and then performs a second-stage rank
    fusion across families.
    """

    def __init__(self, *, config, paths) -> None:
        self.config = config
        self.paths = paths

    def close(self) -> None:
        """Phase 5 keeps the broker stateless; retained for future symmetry."""
        return None

    def collect_pdf_hits(self, query_text: str, *, limit: int, profile) -> list[SearchHit]:
        registry_state = ExtensionRegistry().load(self.paths)
        if not registry_state.pdf_config.enabled:
            return []
        if registry_state.snapshot.pdf.index_state != ExtensionIndexState.READY:
            return []

        from .service import PdfExtensionService

        service = PdfExtensionService(self.config, self.paths)
        try:
            return service.query_hits(query_text, limit=limit, profile=profile)
        except Exception as exc:
            LOGGER.warning('PDF extension query failed and was isolated from the Markdown mainline: %s: %s', type(exc).__name__, exc)
            return []
        finally:
            service.close()

    def collect_tika_hits(self, query_text: str, *, limit: int, profile) -> list[SearchHit]:
        registry_state = ExtensionRegistry().load(self.paths)
        if not registry_state.tika_config.enabled:
            return []
        if registry_state.snapshot.tika.index_state != ExtensionIndexState.READY:
            return []

        from .service import TikaExtensionService

        service = TikaExtensionService(self.config, self.paths)
        try:
            return service.query_hits(query_text, limit=limit, profile=profile)
        except Exception as exc:
            LOGGER.warning('Tika extension query failed and was isolated from the Markdown mainline: %s: %s', type(exc).__name__, exc)
            return []
        finally:
            service.close()

    def collect_extension_hits(self, query_text: str, *, limit: int, profile, allowed_families: set[str] | None = None) -> dict[str, list[SearchHit]]:
        enabled = {item.strip().lower() for item in (allowed_families or {'pdf', 'tika'}) if str(item).strip()}
        family_hits: dict[str, list[SearchHit]] = {}
        if 'pdf' in enabled:
            pdf_hits = self.collect_pdf_hits(query_text, limit=limit, profile=profile)
            if pdf_hits:
                family_hits['pdf'] = pdf_hits
        if 'tika' in enabled:
            tika_hits = self.collect_tika_hits(query_text, limit=limit, profile=profile)
            if tika_hits:
                family_hits['tika'] = tika_hits
        return family_hits

    def fuse_ranked_hits(
        self,
        markdown_hits: list[SearchHit],
        pdf_hits: list[SearchHit] | None = None,
        tika_hits: list[SearchHit] | None = None,
        *,
        limit: int,
    ) -> list[SearchHit]:
        family_hits: dict[str, list[SearchHit]] = {}
        if markdown_hits:
            family_hits['markdown'] = list(markdown_hits)
        if pdf_hits:
            family_hits['pdf'] = list(pdf_hits)
        if tika_hits:
            family_hits['tika'] = list(tika_hits)
        return self.fuse_family_hits(family_hits, limit=limit)

    def fuse_family_hits(self, family_hits: dict[str, list[SearchHit]], *, limit: int) -> list[SearchHit]:
        if not family_hits:
            return []
        if len(family_hits) == 1:
            return list(next(iter(family_hits.values())))

        fused_rows: list[tuple[float, float, int, SearchHit]] = []
        for family, hits in family_hits.items():
            family_weight = _FAMILY_WEIGHTS.get(family, 1.0)
            for rank, hit in enumerate(hits, start=1):
                lexical_anchor = min(max(float(getattr(hit, 'score', 0.0) or 0.0), 0.0), 100.0) / 100.0
                fusion_score = family_weight / (_FUSION_RRF_K + rank)
                fused_rows.append((fusion_score, lexical_anchor, rank, hit))

        fused_rows.sort(key=lambda item: (item[0], item[1], -item[2]), reverse=True)
        top_n = max(int(limit or 0), 1) * 4
        top_rows = fused_rows[:top_n]
        max_score = max((row[0] for row in top_rows), default=1.0) or 1.0
        min_score = min((row[0] for row in top_rows), default=0.0)
        spread = max(max_score - min_score, 1e-9)

        ranked_hits: list[SearchHit] = []
        for _fusion_score, _lexical_anchor, _rank, hit in top_rows:
            # Keep the original per-family relevance score for display.
            # Cross-family fusion only decides ordering; it must never overwrite
            # the source-local relevance score and make unrelated hits look like
            # they are all "100 分".
            ranked_hits.append(hit)
        return ranked_hits


def markdown_source_label(hit: SearchHit) -> str:
    title = str(getattr(hit, 'title', '') or '').strip()
    source_name = title or Path(str(getattr(hit, 'source_path', '') or '')).name or 'Markdown'
    return f'Markdown · {source_name}'


def normalize_markdown_hit(hit: SearchHit) -> SearchHit:
    source_label = markdown_source_label(hit)
    return replace(
        hit,
        source_family='markdown',
        source_kind='markdown',
        source_label=source_label,
    )


def infer_tika_kind(hit: SearchHit) -> str:
    source_path = str(getattr(hit, 'source_path', '') or '').strip()
    if not source_path:
        return 'tika'
    return detect_tika_format(Path(source_path)) or Path(source_path).suffix.lstrip('.').lower() or 'tika'


__all__ = ['ExtensionQueryBroker', 'normalize_markdown_hit', 'infer_tika_kind']
