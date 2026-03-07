from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ChunkRecord:
    chunk_id: str
    source_path: str
    kind: str
    block_id: str | None
    title: str
    anchor: str
    raw_text: str
    properties: dict[str, str] = field(default_factory=dict)
    refs: list[tuple[str, str]] = field(default_factory=list)
    position: int = 0
    line_start: int = 1
    line_end: int = 1


@dataclass(slots=True)
class ParsedFile:
    vault_root: Path
    absolute_path: Path
    relative_path: str
    title: str
    kind: str
    page_properties: dict[str, str] = field(default_factory=dict)
    chunks: list[ChunkRecord] = field(default_factory=list)
    content_hash: str = ""
    mtime: float = 0.0
    size: int = 0


@dataclass(slots=True)
class SearchHit:
    score: float
    title: str
    anchor: str
    source_path: str
    rendered_text: str
    chunk_id: str


@dataclass(slots=True)
class SpaceEstimate:
    run_at: str
    vault_file_count: int
    vault_total_bytes: int
    parsed_chunk_count: int
    ref_count: int
    logseq_file_count: int
    markdown_file_count: int
    estimated_sqlite_bytes: int
    estimated_fts_bytes: int
    estimated_vector_bytes: int
    estimated_model_bytes: int
    estimated_peak_temp_bytes: int
    safety_margin_bytes: int
    current_state_bytes: int
    current_model_cache_bytes: int
    required_free_bytes: int
    available_free_bytes: int
    vector_backend: str
    vector_model: str
    can_proceed: bool
    risk_level: str
    notes: list[str] = field(default_factory=list)

    @property
    def estimated_index_bytes(self) -> int:
        return self.estimated_sqlite_bytes + self.estimated_fts_bytes + self.estimated_vector_bytes

    @property
    def headroom_bytes(self) -> int:
        return self.available_free_bytes - self.required_free_bytes
