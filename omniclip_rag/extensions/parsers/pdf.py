from __future__ import annotations

import hashlib
from pathlib import Path

from pypdf import PdfReader

from ...models import ChunkRecord, ParsedFile
from ..normalizers.pdf import normalize_pdf_pages


def inspect_pdf_file(pdf_path: Path) -> dict[str, int]:
    """Read lightweight PDF metadata for preflight without extracting page text."""

    absolute_path = pdf_path.resolve()
    stat = absolute_path.stat()
    reader = PdfReader(str(absolute_path))
    return {
        'size': int(stat.st_size),
        'page_count': int(len(reader.pages)),
    }


def extract_pdf_pages(pdf_path: Path) -> list[dict[str, object]]:
    """Extract raw text per page from a PDF using pypdf."""

    reader = PdfReader(str(pdf_path))
    pages: list[dict[str, object]] = []
    for index, page in enumerate(reader.pages, start=1):
        pages.append({'page_no': index, 'text': page.extract_text() or ''})
    return pages


def parse_pdf_file(source_root: Path, absolute_path: Path) -> ParsedFile:
    """Parse one PDF into isolated paragraph-like chunks."""

    absolute_path = absolute_path.resolve()
    stat = absolute_path.stat()
    raw_bytes = absolute_path.read_bytes()
    normalized_pages = normalize_pdf_pages(extract_pdf_pages(absolute_path))
    title = absolute_path.name
    source_path = str(absolute_path)
    chunks: list[ChunkRecord] = []
    for position, item in enumerate(normalized_pages):
        page_no = int(item.get('page_no', 0) or 0)
        text = str(item.get('text') or '').strip()
        if not text:
            continue
        chunk_id = _pdf_chunk_id(source_path, page_no, position, text)
        chunks.append(
            ChunkRecord(
                chunk_id=chunk_id,
                source_path=source_path,
                kind='pdf_paragraph',
                block_id=None,
                parent_chunk_id=None,
                title=title,
                anchor=f'第 {page_no} 页',
                raw_text=text,
                properties={'page_no': str(page_no), 'source_root': str(source_root)},
                refs=[],
                position=position,
                depth=0,
                line_start=page_no,
                line_end=page_no,
            )
        )
    if not chunks:
        chunks.append(
            ChunkRecord(
                chunk_id=_pdf_chunk_id(source_path, 1, 0, title),
                source_path=source_path,
                kind='pdf_stub',
                block_id=None,
                parent_chunk_id=None,
                title=title,
                anchor='第 1 页',
                raw_text='',
                position=0,
                depth=0,
                line_start=1,
                line_end=1,
            )
        )
    return ParsedFile(
        vault_root=source_root,
        absolute_path=absolute_path,
        relative_path=source_path,
        title=title,
        kind='pdf',
        page_properties={'source_root': str(source_root), 'page_count': str(len({int(item.get('page_no', 0) or 0) for item in normalized_pages}))},
        chunks=chunks,
        content_hash=hashlib.sha1(raw_bytes).hexdigest(),
        mtime=stat.st_mtime,
        size=stat.st_size,
    )


def _pdf_chunk_id(source_path: str, page_no: int, position: int, text: str) -> str:
    digest = hashlib.sha1(f'{source_path}|{page_no}|{position}|{text}'.encode('utf-8', errors='ignore')).hexdigest()[:20]
    return f'pdf:{page_no}:{position}:{digest}'
