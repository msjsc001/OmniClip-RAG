from __future__ import annotations

import logging
import sys
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

from ..config import DataPaths
from .models import TikaFormatSelection, TikaFormatSupportTier, default_tika_format_selections
from .runtimes.tika_runtime import runtime_layout


LOGGER = logging.getLogger(__name__)


_CATALOG_CACHE: dict[tuple[str, int], list[TikaFormatSelection]] = {}

_PACKAGED_SUFFIX_LIST_RESOURCE = 'tika_suffixes_3.2.3.txt'


_CURATED_SUFFIX_ALIASES: dict[str, tuple[str, ...]] = {
    'html': ('.html', '.htm'),
    'mhtml': ('.mhtml', '.mht'),
}


def _resource_path(name: str) -> Path:
    if getattr(sys, 'frozen', False):
        base = Path(getattr(sys, '_MEIPASS', Path.cwd()))
    else:
        base = Path(__file__).resolve().parents[2]
    return base / 'resources' / name


def _read_packaged_suffix_ids(path: Path) -> set[str]:
    try:
        content = path.read_text(encoding='utf-8')
    except OSError:
        return set()
    suffix_ids: set[str] = set()
    for raw_line in content.splitlines():
        line = str(raw_line or '').strip()
        if not line or line.startswith('#'):
            continue
        suffix_ids.add(line.lower())
    return suffix_ids


def build_tika_format_catalog(paths: DataPaths | None = None, *, jar_path: Path | None = None) -> list[TikaFormatSelection]:
    """Return the full (curated + untested) Tika format catalog.

    The catalog prefers reading Tika's own `tika-mimetypes.xml` from the
    installed `tika-server-standard-*.jar`. When the jar is missing, we fall
    back to a bundled suffix list extracted from the matching Tika release so
    users can still pick formats before installing the runtime.
    """

    resolved_jar = jar_path
    if resolved_jar is None and paths is not None:
        resolved_jar = runtime_layout(paths).jar_path

    source_kind = ''
    source_path: Path | None = None
    if resolved_jar is not None and resolved_jar.exists():
        source_kind = 'jar'
        source_path = resolved_jar
    else:
        packaged = _resource_path(_PACKAGED_SUFFIX_LIST_RESOURCE)
        if packaged.exists():
            source_kind = 'resource'
            source_path = packaged

    if source_path is None:
        return default_tika_format_selections()

    try:
        stat = source_path.stat()
    except OSError:
        return default_tika_format_selections()

    cache_key = (
        f'{source_kind}:{str(source_path.resolve())}',
        int(getattr(stat, 'st_mtime_ns', int(stat.st_mtime * 1_000_000_000))),
    )
    cached = _CATALOG_CACHE.get(cache_key)
    if cached is not None:
        return [replace(item) for item in cached]

    curated = default_tika_format_selections()
    curated_by_id = {item.format_id: item for item in curated if item.format_id.lower() != 'pdf'}

    reserved_ids = set(curated_by_id.keys())
    for suffixes in _CURATED_SUFFIX_ALIASES.values():
        for suffix in suffixes:
            if suffix.startswith('.'):
                reserved_ids.add(suffix[1:].lower())

    skipped = 0
    if source_kind == 'jar':
        extracted_ids, skipped = _extract_suffix_ids_from_tika_jar(source_path)
        if skipped:
            LOGGER.info('Tika mimetype catalog skipped %s complex glob patterns while building format list.', skipped)
    else:
        extracted_ids = _read_packaged_suffix_ids(source_path)

    extracted_ids.discard('pdf')
    extracted_ids.difference_update(reserved_ids)

    extras: list[TikaFormatSelection] = []
    for format_id in sorted(extracted_ids):
        extras.append(
            TikaFormatSelection(
                format_id=format_id,
                display_name=_format_display_name(format_id),
                tier=TikaFormatSupportTier.UNTESTED,
                enabled=False,
                visible=True,
            )
        )

    catalog = list(curated) + extras
    _CATALOG_CACHE.clear()
    _CATALOG_CACHE[cache_key] = [replace(item) for item in catalog]
    return [replace(item) for item in catalog]


def merge_tika_format_selections(
    current: Iterable[TikaFormatSelection] | None,
    base_catalog: Iterable[TikaFormatSelection] | None,
) -> list[TikaFormatSelection]:
    """Merge persisted user selections onto the latest catalog.

    Rules:
    - Keep catalog order and catalog metadata (tier/display_name).
    - Preserve user `enabled` and `visible` flags by `format_id`.
    - Preserve unknown legacy selections (append as UNTESTED) so users do not
      lose previously enabled formats when the catalog changes.
    - Never allow PDF to enter the catalog.
    """

    base = [item for item in (base_catalog or []) if str(getattr(item, 'format_id', '')).strip().lower() != 'pdf']
    current_items = [item for item in (current or []) if str(getattr(item, 'format_id', '')).strip().lower() != 'pdf']

    base_by_id = {item.format_id: item for item in base}
    current_by_id = {item.format_id: item for item in current_items if item.format_id}

    merged: list[TikaFormatSelection] = []
    seen: set[str] = set()
    for item in base:
        current_item = current_by_id.get(item.format_id)
        if current_item is None:
            merged.append(replace(item))
        else:
            merged.append(replace(item, enabled=bool(current_item.enabled), visible=bool(current_item.visible)))
        seen.add(item.format_id)

    for format_id, current_item in current_by_id.items():
        if not format_id or format_id in seen:
            continue
        display_name = str(current_item.display_name or '').strip() or _format_display_name(format_id)
        merged.append(
            TikaFormatSelection(
                format_id=format_id,
                display_name=display_name,
                tier=TikaFormatSupportTier.UNTESTED,
                enabled=bool(current_item.enabled),
                visible=bool(current_item.visible),
            )
        )
    return merged


def _format_display_name(format_id: str) -> str:
    normalized = str(format_id or '').strip().lstrip('.').lower()
    label = normalized.upper() if normalized else 'TIKA'
    suffix = f'.{normalized}' if normalized else ''
    return f'{label} ({suffix})'.strip()


def _extract_suffix_ids_from_tika_jar(jar_path: Path) -> tuple[set[str], int]:
    """Return (suffix_ids, skipped_pattern_count) from Tika's bundled mimetypes xml."""

    xml_bytes = _read_tika_mimetypes_xml(jar_path)
    if not xml_bytes:
        return set(), 0

    try:
        root = ET.fromstring(xml_bytes)
    except Exception as exc:
        LOGGER.warning('Failed to parse tika-mimetypes.xml from %s: %s: %s', jar_path, type(exc).__name__, exc)
        return set(), 0

    suffix_ids: set[str] = set()
    skipped = 0
    for elem in root.iter():
        if _local_tag(elem.tag) != 'glob':
            continue
        raw_pattern = str(elem.attrib.get('pattern') or '').strip()
        suffix_id = _normalize_glob_suffix_id(raw_pattern)
        if not suffix_id:
            skipped += 1
            continue
        suffix_ids.add(suffix_id)
    return suffix_ids, skipped


def _read_tika_mimetypes_xml(jar_path: Path) -> bytes:
    try:
        with zipfile.ZipFile(jar_path, 'r') as archive:
            candidates = [name for name in archive.namelist() if name.lower().endswith('tika-mimetypes.xml')]
            if not candidates:
                return b''
            candidates.sort(key=len)
            with archive.open(candidates[0], 'r') as fp:
                return fp.read()
    except Exception as exc:
        LOGGER.warning('Failed to read tika-mimetypes.xml from %s: %s: %s', jar_path, type(exc).__name__, exc)
        return b''


def _local_tag(tag: str) -> str:
    if '}' in tag:
        return tag.rsplit('}', 1)[-1]
    return tag


def _normalize_glob_suffix_id(pattern: str) -> str:
    """Convert Tika glob patterns into a deterministic suffix id.

    Supported examples:
    - "*.docx"      -> "docx"
    - "*.tar.gz"    -> "tar.gz"
    - "*.[Rr][Tt][Ff]" -> "rtf"
    """

    raw = str(pattern or '').strip()
    if not raw.startswith('*.'):
        return ''
    if '/' in raw or '\\' in raw:
        return ''
    if '*' in raw[2:]:
        return ''
    if '?' in raw or '{' in raw or '}' in raw:
        return ''

    tail = raw[2:]
    normalized = _normalize_bracket_case_insensitive(tail)
    if not normalized:
        return ''
    if normalized.endswith('.') or normalized.startswith('.'):
        return ''
    if '..' in normalized:
        return ''

    for ch in normalized:
        if ch.isalnum():
            continue
        if ch in ('.', '_', '-', '+'):
            continue
        return ''
    return normalized.lower()


def _normalize_bracket_case_insensitive(value: str) -> str:
    result: list[str] = []
    text = str(value or '')
    index = 0
    while index < len(text):
        ch = text[index]
        if ch == '[':
            end = text.find(']', index + 1)
            if end < 0:
                return ''
            content = text[index + 1 : end]
            if len(content) != 2:
                return ''
            a, b = content[0], content[1]
            if not (a.isalpha() and b.isalpha()):
                return ''
            if a.lower() != b.lower():
                return ''
            result.append(a.lower())
            index = end + 1
            continue
        if ch.isalnum() or ch in ('.', '_', '-', '+'):
            result.append(ch.lower())
            index += 1
            continue
        return ''
    return ''.join(result)
