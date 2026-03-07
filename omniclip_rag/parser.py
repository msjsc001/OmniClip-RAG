from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from .models import ChunkRecord, ParsedFile


BULLET_RE = re.compile(r"^(?P<indent>[ \t]*)-\s+(?P<value>.*)$")
PROPERTY_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<key>[^:\n]+?)::\s*(?P<value>.*)$")
HEADING_RE = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$")
BLOCK_REF_RE = re.compile(r"\(\(([0-9a-fA-F-]{36})\)\)")
EMBED_RE = re.compile(r"\{\{embed\s+\(\(([0-9a-fA-F-]{36})\)\)\}\}")
PAGE_REF_RE = re.compile(r"\[\[([^\]]+)\]\]")


@dataclass(slots=True)
class _LogseqNode:
    indent: int
    line_start: int
    raw_title: str
    position: int
    parent: "_LogseqNode | None" = None
    block_id: str | None = None
    properties: dict[str, str] = field(default_factory=dict)
    extra_lines: list[str] = field(default_factory=list)
    line_end: int = 1


def parse_markdown_file(vault_root: Path, absolute_path: Path) -> ParsedFile:
    text = absolute_path.read_text(encoding="utf-8", errors="ignore")
    stat = absolute_path.stat()
    relative_path = absolute_path.resolve().relative_to(vault_root.resolve()).as_posix()
    file_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()
    kind = _detect_kind(absolute_path, text)
    if kind == "logseq":
        parsed = _parse_logseq(vault_root, absolute_path, relative_path, text)
    else:
        parsed = _parse_standard_markdown(vault_root, absolute_path, relative_path, text)
    parsed.content_hash = file_hash
    parsed.mtime = stat.st_mtime
    parsed.size = stat.st_size
    return parsed


def _detect_kind(path: Path, text: str) -> str:
    lowered = path.as_posix().lower()
    if "/pages/" in lowered or "/journals/" in lowered:
        if any(marker in text for marker in ("id::", "{{embed", "((", "alias::", "file::")):
            return "logseq"
    has_bullets = any(BULLET_RE.match(line.expandtabs(4)) for line in text.splitlines())
    has_logseq_markers = any(marker in text for marker in ("id::", "{{embed", "((", "alias::", "file::"))
    return "logseq" if has_bullets and has_logseq_markers else "markdown"


def _parse_logseq(vault_root: Path, absolute_path: Path, relative_path: str, text: str) -> ParsedFile:
    title = absolute_path.stem
    page_properties: dict[str, str] = {}
    nodes: list[_LogseqNode] = []
    stack: list[_LogseqNode] = []
    saw_first_block = False

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        expanded = raw_line.expandtabs(4)
        stripped = expanded.strip()
        if not stripped:
            continue

        bullet_match = BULLET_RE.match(expanded)
        property_match = PROPERTY_RE.match(expanded)

        if not saw_first_block and property_match and len(property_match.group("indent")) == 0:
            key = property_match.group("key").strip()
            value = property_match.group("value").strip()
            page_properties[key] = value
            continue

        if bullet_match:
            saw_first_block = True
            indent = len(bullet_match.group("indent"))
            while stack and stack[-1].indent >= indent:
                stack.pop()
            parent = stack[-1] if stack else None
            node = _LogseqNode(
                indent=indent,
                line_start=line_number,
                line_end=line_number,
                raw_title=bullet_match.group("value").strip(),
                position=len(nodes),
                parent=parent,
            )
            nodes.append(node)
            stack.append(node)
            continue

        if stack and property_match:
            indent = len(property_match.group("indent"))
            current = stack[-1]
            if indent > current.indent:
                key = property_match.group("key").strip()
                value = property_match.group("value").strip()
                if key.lower() == "id":
                    current.block_id = value
                else:
                    current.properties[key] = value
                current.line_end = line_number
                continue

        if stack:
            indent = len(expanded) - len(expanded.lstrip(" "))
            current = stack[-1]
            if indent > current.indent:
                current.extra_lines.append(stripped)
                current.line_end = line_number
                continue

    chunks = [_logseq_node_to_chunk(relative_path, title, node) for node in nodes]
    if not chunks:
        chunks.append(
            ChunkRecord(
                chunk_id=_chunk_id(relative_path, "page_stub", 0, title, None),
                source_path=relative_path,
                kind="page_stub",
                block_id=None,
                title=title,
                anchor=title,
                raw_text="",
                position=0,
            )
        )
    return ParsedFile(
        vault_root=vault_root,
        absolute_path=absolute_path,
        relative_path=relative_path,
        title=title,
        kind="logseq",
        page_properties=page_properties,
        chunks=chunks,
    )


def _logseq_node_to_chunk(relative_path: str, page_title: str, node: _LogseqNode) -> ChunkRecord:
    breadcrumb_parts: list[str] = []
    cursor = node
    lineage: list[_LogseqNode] = []
    while cursor is not None:
        lineage.append(cursor)
        cursor = cursor.parent
    for item in reversed(lineage):
        cleaned = _clean_inline_markup(item.raw_title)
        if cleaned:
            breadcrumb_parts.append(cleaned)
    anchor = " > ".join(breadcrumb_parts) if breadcrumb_parts else page_title
    body_parts = [node.raw_title.strip(), *node.extra_lines]
    raw_text = "\n".join(part for part in body_parts if part).strip()
    refs: list[tuple[str, str]] = []
    for target in EMBED_RE.findall(raw_text):
        refs.append(("embed", target))
    for target in BLOCK_REF_RE.findall(raw_text):
        refs.append(("block_ref", target))
    return ChunkRecord(
        chunk_id=_chunk_id(relative_path, "logseq_block", node.position, node.raw_title, node.block_id),
        source_path=relative_path,
        kind="logseq_block",
        block_id=node.block_id,
        title=page_title,
        anchor=anchor,
        raw_text=raw_text,
        properties=node.properties,
        refs=refs,
        position=node.position,
        line_start=node.line_start,
        line_end=node.line_end,
    )


def _parse_standard_markdown(vault_root: Path, absolute_path: Path, relative_path: str, text: str) -> ParsedFile:
    title = absolute_path.stem
    if not text.strip():
        return ParsedFile(
            vault_root=vault_root,
            absolute_path=absolute_path,
            relative_path=relative_path,
            title=title,
            kind="markdown",
            chunks=[
                ChunkRecord(
                    chunk_id=_chunk_id(relative_path, "page_stub", 0, title, None),
                    source_path=relative_path,
                    kind="page_stub",
                    block_id=None,
                    title=title,
                    anchor=title,
                    raw_text="",
                    position=0,
                )
            ],
        )

    lines = text.splitlines()
    sections: list[ChunkRecord] = []
    heading_stack: list[tuple[int, str]] = []
    body_lines: list[str] = []
    section_line_start = 1
    section_heading = title

    def flush(line_end: int) -> None:
        nonlocal body_lines, section_line_start, section_heading
        body = "\n".join(body_lines).strip()
        anchor = " > ".join([item for _, item in heading_stack]) if heading_stack else title
        if not body and not heading_stack and not sections:
            anchor = title
        if body or anchor:
            position = len(sections)
            refs: list[tuple[str, str]] = []
            for target in EMBED_RE.findall(body):
                refs.append(("embed", target))
            for target in BLOCK_REF_RE.findall(body):
                refs.append(("block_ref", target))
            sections.append(
                ChunkRecord(
                    chunk_id=_chunk_id(relative_path, "md_section", position, section_heading, None),
                    source_path=relative_path,
                    kind="md_section",
                    block_id=None,
                    title=title,
                    anchor=anchor,
                    raw_text=body,
                    refs=refs,
                    position=position,
                    line_start=section_line_start,
                    line_end=max(section_line_start, line_end),
                )
            )
        body_lines = []

    for line_number, raw_line in enumerate(lines, start=1):
        heading_match = HEADING_RE.match(raw_line)
        if heading_match:
            flush(line_number - 1)
            level = len(heading_match.group("marks"))
            heading = heading_match.group("title").strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, heading))
            section_heading = heading
            section_line_start = line_number
            continue
        body_lines.append(raw_line.strip())

    flush(max(1, len(lines)))
    if not sections:
        sections.append(
            ChunkRecord(
                chunk_id=_chunk_id(relative_path, "page_stub", 0, title, None),
                source_path=relative_path,
                kind="page_stub",
                block_id=None,
                title=title,
                anchor=title,
                raw_text="",
                position=0,
            )
        )
    return ParsedFile(
        vault_root=vault_root,
        absolute_path=absolute_path,
        relative_path=relative_path,
        title=title,
        kind="markdown",
        chunks=sections,
    )


def _chunk_id(source_path: str, kind: str, position: int, seed: str, block_id: str | None) -> str:
    if block_id:
        return f"logseq:{block_id}"
    digest = hashlib.sha1(f"{source_path}|{kind}|{position}|{seed}".encode("utf-8")).hexdigest()[:16]
    return f"{kind}:{digest}"


def _clean_inline_markup(text: str) -> str:
    cleaned = PAGE_REF_RE.sub(r"\1", text)
    cleaned = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()

