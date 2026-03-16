from __future__ import annotations

import re
from typing import Any

_SENTENCE_END_RE = re.compile(r'[。！？!?;；:：.]$')
_BULLET_RE = re.compile(r'^(?:[-*•]|\d+[.)]|[A-Z][.)])\s+')
_GARBAGE_RE = re.compile(r'^[\s\W_]+$')
_PAGE_NUMBER_RE = re.compile(r'^(?:page\s+)?\d+$', re.IGNORECASE)


def normalize_pdf_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clean pypdf page text into paragraph-like records with page numbers.

    The goal is to remove obvious OCR/pagination noise, stitch soft line-breaks,
    and avoid producing half-sentence chunks. The return shape intentionally
    mirrors the future extension-build intermediate structure.
    """

    normalized: list[dict[str, Any]] = []
    for page in pages:
        page_no = int(page.get('page_no', 0) or 0)
        raw_text = str(page.get('text') or '')
        lines = _clean_lines(raw_text.splitlines())
        paragraphs = _stitch_lines(lines)
        if normalized and paragraphs and _should_merge_across_pages(str(normalized[-1]['text']), paragraphs[0]):
            normalized[-1]['text'] = _merge_lines(str(normalized[-1]['text']), paragraphs[0])
            paragraphs = paragraphs[1:]
        for paragraph in paragraphs:
            text = paragraph.strip()
            if text:
                normalized.append({'text': text, 'page_no': page_no})
    return normalized


def _clean_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    for raw_line in lines:
        line = re.sub(r'\s+', ' ', raw_line or '').strip()
        if not line:
            continue
        if _PAGE_NUMBER_RE.match(line):
            continue
        if len(line) <= 1 and _GARBAGE_RE.match(line):
            continue
        if len(re.sub(r'[A-Za-z0-9一-鿿]', '', line)) >= max(len(line) - 2, 3):
            continue
        cleaned.append(line)
    return cleaned


def _stitch_lines(lines: list[str]) -> list[str]:
    if not lines:
        return []
    paragraphs: list[str] = []
    current = lines[0]
    for line in lines[1:]:
        if _should_start_new_paragraph(current, line):
            paragraphs.append(current.strip())
            current = line
        else:
            current = _merge_lines(current, line)
    if current.strip():
        paragraphs.append(current.strip())
    return paragraphs


def _should_start_new_paragraph(current: str, nxt: str) -> bool:
    if not current.strip():
        return True
    if _SENTENCE_END_RE.search(current.strip()):
        return True
    if _BULLET_RE.match(nxt):
        return True
    if len(nxt) <= 72 and nxt.isupper():
        return True
    if nxt[:1].isupper() and len(current) >= 48:
        return True
    return False


def _should_merge_across_pages(current: str, nxt: str) -> bool:
    if not current.strip() or not nxt.strip():
        return False
    if _SENTENCE_END_RE.search(current.strip()):
        return False
    if _BULLET_RE.match(nxt):
        return False
    if nxt[:1].isupper() and len(current) >= 48:
        return False
    return True


def _merge_lines(current: str, nxt: str) -> str:
    left = current.rstrip()
    right = nxt.lstrip()
    if left.endswith('-') and right[:1].islower():
        return left[:-1] + right
    return f'{left} {right}'.strip()


__all__ = ['normalize_pdf_pages']
