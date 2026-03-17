from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from ...models import ChunkRecord, ParsedFile
from ..normalizers.tika_output import normalize_tika_xhtml

_FORMAT_SUFFIX_ALIASES: dict[str, tuple[str, ...]] = {
    # Curated aliases that collapse multiple common suffixes into one UI choice.
    'html': ('.html', '.htm'),
    'mhtml': ('.mhtml', '.mht'),
}


def detect_tika_format(path: Path) -> str:
    """Best-effort suffix mapping for display labeling.

    For multi-part suffixes (for example .tar.gz) this function may return the
    last segment only. The build/query pipeline should prefer the enabled-format
    matcher when an exact format id is required.
    """

    name_lower = path.name.lower()
    for format_id, suffixes in _FORMAT_SUFFIX_ALIASES.items():
        if any(name_lower.endswith(suffix) for suffix in suffixes):
            return format_id
    return path.suffix.lstrip('.').lower()


def suffix_patterns_for_format(format_id: str) -> tuple[str, ...]:
    """Return the suffix patterns (lowercase, with leading dot) for one format id."""

    normalized = str(format_id or '').strip().lower().lstrip('.')
    if not normalized:
        return ()
    aliases = _FORMAT_SUFFIX_ALIASES.get(normalized)
    if aliases:
        return tuple(dict.fromkeys([item.lower() for item in aliases if str(item).startswith('.')]))
    return (f'.{normalized}',)


def enabled_tika_suffixes(format_ids: Iterable[str]) -> tuple[str, ...]:
    """Return the flattened suffix whitelist for enabled Tika formats."""

    values: list[str] = []
    for format_id in format_ids:
        values.extend(suffix_patterns_for_format(str(format_id)))
    return tuple(dict.fromkeys(values))


def build_tika_suffix_matcher(enabled_formats: Iterable[str]) -> dict[str, list[tuple[str, str]]]:
    """Build a fast suffix matcher for enabled formats.

    Returns a dict keyed by the last suffix segment (e.g. ".gz") that maps to a
    list of (format_id, suffix_pattern) pairs. Each bucket is sorted by suffix
    length descending so multi-part suffixes win (e.g. ".tar.gz" before ".gz").
    """

    buckets: dict[str, list[tuple[str, str]]] = {}
    for format_id_raw in enabled_formats:
        format_id = str(format_id_raw or '').strip().lower()
        if not format_id:
            continue
        for suffix in suffix_patterns_for_format(format_id):
            suffix_lower = str(suffix).lower()
            if not suffix_lower.startswith('.'):
                continue
            last_segment = f".{suffix_lower.rsplit('.', 1)[-1]}" if '.' in suffix_lower[1:] else suffix_lower
            buckets.setdefault(last_segment, []).append((format_id, suffix_lower))
    for items in buckets.values():
        items.sort(key=lambda pair: len(pair[1]), reverse=True)
    return buckets


def parse_tika_file(source_root: Path, absolute_path: Path, xhtml: str, *, format_id: str) -> ParsedFile:
    """Convert normalized Tika XHTML into ParsedFile + ChunkRecord rows."""

    resolved_path = absolute_path.resolve()
    stat = resolved_path.stat()
    relative_path = str(resolved_path)
    title = resolved_path.name
    normalized = normalize_tika_xhtml(xhtml)
    content_hash = hashlib.sha1(xhtml.encode('utf-8', errors='ignore')).hexdigest()
    parsed = ParsedFile(
        vault_root=source_root,
        absolute_path=resolved_path,
        relative_path=relative_path,
        title=title,
        kind='tika',
        page_properties={'format_id': format_id},
        content_hash=content_hash,
        mtime=float(stat.st_mtime),
        size=int(stat.st_size),
    )
    if not normalized:
        normalized = [{'text': title, 'anchor': '', 'tag': 'stub'}]
    for position, row in enumerate(normalized, start=1):
        text = str(row.get('text') or '').strip()
        if not text:
            continue
        anchor = str(row.get('anchor') or '').strip() or f'Chunk {position}'
        chunk_id = f'{relative_path}::tika::{position}'
        parsed.chunks.append(
            ChunkRecord(
                chunk_id=chunk_id,
                source_path=relative_path,
                kind=f'tika_{format_id}',
                block_id=None,
                parent_chunk_id=None,
                title=title,
                anchor=anchor,
                raw_text=text,
                properties={
                    'format_id': format_id,
                    'source_root': str(source_root),
                    'tag': str(row.get('tag') or ''),
                },
                position=position,
                depth=0,
                line_start=position,
                line_end=position,
            )
        )
    return parsed
