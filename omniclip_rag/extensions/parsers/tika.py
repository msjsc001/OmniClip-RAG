from __future__ import annotations

import hashlib
from pathlib import Path

from ...models import ChunkRecord, ParsedFile
from ..normalizers.tika_output import normalize_tika_xhtml

TIKA_FORMAT_SUFFIXES: dict[str, tuple[str, ...]] = {
    'docx': ('.docx',),
    'doc': ('.doc',),
    'pptx': ('.pptx',),
    'ppt': ('.ppt',),
    'html': ('.html', '.htm'),
    'xml': ('.xml',),
    'txt': ('.txt',),
    'rtf': ('.rtf',),
    'epub': ('.epub',),
    'odt': ('.odt',),
    'mhtml': ('.mhtml', '.mht'),
    'eml': ('.eml',),
    'msg': ('.msg',),
    'xlsx': ('.xlsx',),
    'xls': ('.xls',),
}


def detect_tika_format(path: Path) -> str:
    """Map a file suffix onto the enabled Tika catalog key."""

    suffix = path.suffix.lower()
    for format_id, suffixes in TIKA_FORMAT_SUFFIXES.items():
        if suffix in suffixes:
            return format_id
    return ''


def enabled_tika_suffixes(format_ids: list[str]) -> tuple[str, ...]:
    """Return the flattened suffix whitelist for enabled Tika formats."""

    values: list[str] = []
    for format_id in format_ids:
        values.extend(TIKA_FORMAT_SUFFIXES.get(format_id, ()))
    return tuple(dict.fromkeys(values))


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
