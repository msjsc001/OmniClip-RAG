from __future__ import annotations

DEFAULT_PAGE_FILTER_RULES: tuple[tuple[bool, str], ...] = (
    (True, r"^2026-.*\.android$"),
    (True, r"^.*\.sync-conflict-\d{8}-\d{6}-[A-Z0-9]+$"),
    (True, r"^\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2}\.\d{3}Z\.(?:Desktop|android)$"),
    (True, r"^hls__.*?_\d+_\d+_\d+_\d+$"),
)


def serialize_page_filter_rules(rules: list[tuple[bool, str]] | tuple[tuple[bool, str], ...]) -> str:
    lines: list[str] = []
    for enabled, pattern in rules:
        rule = str(pattern or '').strip()
        if not rule:
            continue
        lines.append(f"{'1' if enabled else '0'}\t{rule}")
    return '\n'.join(lines)


def deserialize_page_filter_rules(raw_rules: str) -> list[tuple[bool, str]]:
    parsed: list[tuple[bool, str]] = []
    for raw_line in str(raw_rules or '').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        enabled = True
        pattern = line
        if '\t' in line:
            flag, rest = line.split('\t', 1)
            if flag in {'0', '1'}:
                enabled = flag == '1'
                pattern = rest.strip()
        if pattern:
            parsed.append((enabled, pattern))
    return parsed


def merge_page_filter_defaults(raw_rules: str) -> str:
    parsed = deserialize_page_filter_rules(raw_rules)
    existing = {pattern for _enabled, pattern in parsed}
    merged = list(parsed)
    for enabled, pattern in DEFAULT_PAGE_FILTER_RULES:
        if pattern not in existing:
            merged.append((enabled, pattern))
    return serialize_page_filter_rules(merged)


def count_enabled_page_filter_rules(raw_rules: str) -> tuple[int, int]:
    rules = deserialize_page_filter_rules(raw_rules)
    enabled = sum(1 for is_enabled, _pattern in rules if is_enabled)
    return enabled, len(rules)
