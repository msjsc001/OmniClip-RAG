from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any

_BLOCK_TAGS = {'div', 'p', 'li', 'pre', 'blockquote', 'section', 'article'}
_HEADING_TAGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
_SKIP_TAGS = {'script', 'style'}
_WHITESPACE_RE = re.compile(r'\s+')
_PARAGRAPH_SPLIT_RE = re.compile(r'(?:\r?\n\s*){2,}')


def normalize_tika_content(
    content: str,
    *,
    content_type: str = '',
    metadata: object | None = None,
) -> list[dict[str, Any]]:
    """Normalize compatibility-first Tika output into chunk-ready rows.

    Why: Tika 3.x may expose the same document as plain text, JSON metadata, or
    XHTML depending on parser support. The extension pipeline should accept any
    surface that still yields stable正文段落，而不是把成功条件绑死在单一
    返回格式上。
    """

    normalized_type = str(content_type or '').strip().lower()
    if 'json' in normalized_type:
        return _normalize_tika_rmeta(content, metadata=metadata)
    if 'xhtml' in normalized_type or normalized_type.endswith('/xml') or normalized_type.endswith('+xml'):
        return normalize_tika_xhtml(content)
    return _normalize_tika_plain_text(content)


def normalize_tika_xhtml(xml_content: str) -> list[dict[str, Any]]:
    """Normalize Tika XHTML into chunk-ready paragraph rows.

    Why: Tika gives us one standardized XHTML surface across many formats, but
    the extension subsystem still needs a stable intermediate structure before
    chunking. This normalizer keeps only semantic block text, collapses noisy
    whitespace, and carries forward the latest heading so later stages can show
    explainable anchors without format-specific parsing code.
    """

    raw = str(xml_content or '').strip()
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        cleaned = _normalize_text(raw)
        return [{'text': cleaned, 'anchor': '', 'tag': 'text'}] if cleaned else []

    body = _find_body(root)
    if body is None:
        body = root
    results: list[dict[str, Any]] = []
    heading_stack: list[str] = []
    for element in body.iter():
        tag = _local_name(element.tag)
        if tag in _SKIP_TAGS:
            continue
        if tag in _HEADING_TAGS:
            heading_text = _normalize_text(' '.join(element.itertext()))
            if heading_text:
                heading_stack = [heading_text]
            continue
        if tag not in _BLOCK_TAGS:
            continue
        if _has_direct_block_children(element):
            continue
        text = _normalize_text(' '.join(element.itertext()))
        if not text:
            continue
        results.append(
            {
                'text': text,
                'anchor': heading_stack[-1] if heading_stack else '',
                'tag': tag,
            }
        )
    return results


def _normalize_tika_rmeta(content: str, *, metadata: object | None = None) -> list[dict[str, Any]]:
    records = metadata
    if records is None:
        try:
            records = json.loads(str(content or ''))
        except json.JSONDecodeError:
            records = None
    text_blocks = _extract_rmeta_blocks(records)
    if not text_blocks:
        return []
    return _normalize_tika_plain_text('\n\n'.join(text_blocks))


def _extract_rmeta_blocks(records: object) -> list[str]:
    items: list[str] = []
    iterable: list[object]
    if isinstance(records, dict):
        iterable = [records]
    elif isinstance(records, list):
        iterable = list(records)
    else:
        return items
    for record in iterable:
        if not isinstance(record, dict):
            continue
        for key in ('X-TIKA:content', 'content'):
            value = record.get(key)
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    items.append(cleaned)
                    break
    return items


def _normalize_tika_plain_text(text: str) -> list[dict[str, Any]]:
    raw = str(text or '').strip()
    if not raw:
        return []
    paragraphs = _PARAGRAPH_SPLIT_RE.split(raw)
    results: list[dict[str, Any]] = []
    for paragraph in paragraphs:
        cleaned = _normalize_text(paragraph.replace('\r', '\n'))
        if not cleaned:
            continue
        results.append({'text': cleaned, 'anchor': '', 'tag': 'text'})
    return results


def _find_body(root: ET.Element) -> ET.Element | None:
    for element in root.iter():
        if _local_name(element.tag) == 'body':
            return element
    return None


def _local_name(tag: object) -> str:
    text = str(tag or '')
    if '}' in text:
        return text.rsplit('}', 1)[-1].lower()
    return text.lower()


def _has_direct_block_children(element: ET.Element) -> bool:
    for child in list(element):
        child_tag = _local_name(child.tag)
        if child_tag in _BLOCK_TAGS or child_tag in _HEADING_TAGS:
            return True
    return False


def _normalize_text(text: str) -> str:
    clean = _WHITESPACE_RE.sub(' ', str(text or '')).strip()
    return clean
