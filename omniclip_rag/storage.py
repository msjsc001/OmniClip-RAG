from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path

from .models import ParsedFile, SpaceEstimate


QUERY_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff-]+", re.UNICODE)


class MetadataStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL;")
        self.connection.execute("PRAGMA foreign_keys=ON;")
        self._initialize_schema()

    def close(self) -> None:
        self.connection.close()

    def _initialize_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                source_path TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                page_properties_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                mtime REAL NOT NULL,
                size INTEGER NOT NULL,
                indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                kind TEXT NOT NULL,
                block_id TEXT,
                title TEXT NOT NULL,
                anchor TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                rendered_text TEXT NOT NULL DEFAULT '',
                properties_json TEXT NOT NULL,
                position INTEGER NOT NULL,
                line_start INTEGER NOT NULL,
                line_end INTEGER NOT NULL,
                FOREIGN KEY(source_path) REFERENCES files(source_path) ON DELETE CASCADE
            );

            CREATE UNIQUE INDEX IF NOT EXISTS chunks_block_id_idx
            ON chunks(block_id)
            WHERE block_id IS NOT NULL;

            CREATE INDEX IF NOT EXISTS chunks_source_path_idx
            ON chunks(source_path);

            CREATE TABLE IF NOT EXISTS refs (
                source_chunk_id TEXT NOT NULL,
                target_block_id TEXT NOT NULL,
                ref_type TEXT NOT NULL,
                PRIMARY KEY (source_chunk_id, target_block_id, ref_type),
                FOREIGN KEY(source_chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS refs_target_idx
            ON refs(target_block_id);

            CREATE TABLE IF NOT EXISTS preflight_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                vault_path TEXT NOT NULL,
                vector_backend TEXT NOT NULL,
                vector_model TEXT NOT NULL,
                vault_file_count INTEGER NOT NULL,
                vault_total_bytes INTEGER NOT NULL,
                parsed_chunk_count INTEGER NOT NULL,
                ref_count INTEGER NOT NULL,
                logseq_file_count INTEGER NOT NULL,
                markdown_file_count INTEGER NOT NULL,
                estimated_sqlite_bytes INTEGER NOT NULL,
                estimated_fts_bytes INTEGER NOT NULL,
                estimated_vector_bytes INTEGER NOT NULL,
                estimated_model_bytes INTEGER NOT NULL,
                estimated_peak_temp_bytes INTEGER NOT NULL,
                safety_margin_bytes INTEGER NOT NULL,
                current_state_bytes INTEGER NOT NULL,
                current_model_cache_bytes INTEGER NOT NULL,
                required_free_bytes INTEGER NOT NULL,
                available_free_bytes INTEGER NOT NULL,
                can_proceed INTEGER NOT NULL,
                risk_level TEXT NOT NULL,
                notes_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
            USING fts5(
                chunk_id UNINDEXED,
                title,
                anchor,
                rendered_text,
                tokenize = 'unicode61'
            );
            """
        )
        self.connection.commit()

    def reset_all(self) -> None:
        self.connection.executescript(
            """
            DELETE FROM refs;
            DELETE FROM chunks_fts;
            DELETE FROM chunks;
            DELETE FROM files;
            """
        )
        self.connection.commit()

    def record_preflight(self, report: SpaceEstimate, vault_path: str) -> None:
        payload = asdict(report)
        notes = payload.pop("notes")
        payload.pop("headroom_bytes", None)
        payload.pop("estimated_index_bytes", None)
        self.connection.execute(
            """
            INSERT INTO preflight_runs (
                run_at, vault_path, vector_backend, vector_model,
                vault_file_count, vault_total_bytes, parsed_chunk_count, ref_count,
                logseq_file_count, markdown_file_count,
                estimated_sqlite_bytes, estimated_fts_bytes, estimated_vector_bytes,
                estimated_model_bytes, estimated_peak_temp_bytes, safety_margin_bytes,
                current_state_bytes, current_model_cache_bytes,
                required_free_bytes, available_free_bytes,
                can_proceed, risk_level, notes_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["run_at"],
                vault_path,
                payload["vector_backend"],
                payload["vector_model"],
                payload["vault_file_count"],
                payload["vault_total_bytes"],
                payload["parsed_chunk_count"],
                payload["ref_count"],
                payload["logseq_file_count"],
                payload["markdown_file_count"],
                payload["estimated_sqlite_bytes"],
                payload["estimated_fts_bytes"],
                payload["estimated_vector_bytes"],
                payload["estimated_model_bytes"],
                payload["estimated_peak_temp_bytes"],
                payload["safety_margin_bytes"],
                payload["current_state_bytes"],
                payload["current_model_cache_bytes"],
                payload["required_free_bytes"],
                payload["available_free_bytes"],
                1 if payload["can_proceed"] else 0,
                payload["risk_level"],
                json.dumps(notes, ensure_ascii=False),
            ),
        )
        self.connection.commit()

    def fetch_latest_preflight(self) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT *
            FROM preflight_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    def replace_file(self, parsed_file: ParsedFile) -> list[str]:
        self.delete_files([parsed_file.relative_path])
        duplicate_block_ids = self._demote_duplicate_block_ids(parsed_file)
        self.connection.execute(
            """
            INSERT INTO files (
                source_path, kind, title, page_properties_json,
                content_hash, mtime, size
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                parsed_file.relative_path,
                parsed_file.kind,
                parsed_file.title,
                json.dumps(parsed_file.page_properties, ensure_ascii=False),
                parsed_file.content_hash,
                parsed_file.mtime,
                parsed_file.size,
            ),
        )
        for chunk in parsed_file.chunks:
            self.connection.execute(
                """
                INSERT INTO chunks (
                    chunk_id, source_path, kind, block_id, title, anchor,
                    raw_text, rendered_text, properties_json,
                    position, line_start, line_end
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?)
                """,
                (
                    chunk.chunk_id,
                    chunk.source_path,
                    chunk.kind,
                    chunk.block_id,
                    chunk.title,
                    chunk.anchor,
                    chunk.raw_text,
                    json.dumps(chunk.properties, ensure_ascii=False),
                    chunk.position,
                    chunk.line_start,
                    chunk.line_end,
                ),
            )
            for ref_type, target in chunk.refs:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO refs (source_chunk_id, target_block_id, ref_type)
                    VALUES (?, ?, ?)
                    """,
                    (chunk.chunk_id, target, ref_type),
                )
        self.connection.commit()
        return duplicate_block_ids

    def delete_files(self, relative_paths: Iterable[str]) -> None:
        paths = [item for item in relative_paths if item]
        if not paths:
            return
        placeholders = ",".join("?" for _ in paths)
        chunk_rows = self.connection.execute(
            f"SELECT chunk_id FROM chunks WHERE source_path IN ({placeholders})",
            paths,
        ).fetchall()
        chunk_ids = [row["chunk_id"] for row in chunk_rows]
        if chunk_ids:
            fts_placeholders = ",".join("?" for _ in chunk_ids)
            self.connection.execute(
                f"DELETE FROM chunks_fts WHERE chunk_id IN ({fts_placeholders})",
                chunk_ids,
            )
        self.connection.execute(
            f"DELETE FROM files WHERE source_path IN ({placeholders})",
            paths,
        )
        self.connection.commit()

    def get_block_ids_for_paths(self, relative_paths: Iterable[str]) -> set[str]:
        paths = [item for item in relative_paths if item]
        if not paths:
            return set()
        placeholders = ",".join("?" for _ in paths)
        rows = self.connection.execute(
            f"""
            SELECT block_id
            FROM chunks
            WHERE source_path IN ({placeholders}) AND block_id IS NOT NULL
            """,
            paths,
        ).fetchall()
        return {row["block_id"] for row in rows if row["block_id"]}

    def get_chunk_ids_for_paths(self, relative_paths: Iterable[str]) -> list[str]:
        paths = [item for item in relative_paths if item]
        if not paths:
            return []
        placeholders = ",".join("?" for _ in paths)
        rows = self.connection.execute(
            f"SELECT chunk_id FROM chunks WHERE source_path IN ({placeholders})",
            paths,
        ).fetchall()
        return [row["chunk_id"] for row in rows]

    def get_transitive_dependent_paths(self, block_ids: set[str]) -> set[str]:
        frontier = set(block_ids)
        seen_ids = set(block_ids)
        paths: set[str] = set()
        while frontier:
            placeholders = ",".join("?" for _ in frontier)
            rows = self.connection.execute(
                f"""
                SELECT DISTINCT chunks.block_id, chunks.source_path
                FROM refs
                JOIN chunks ON chunks.chunk_id = refs.source_chunk_id
                WHERE refs.target_block_id IN ({placeholders})
                """,
                list(frontier),
            ).fetchall()
            next_frontier: set[str] = set()
            for row in rows:
                paths.add(row["source_path"])
                block_id = row["block_id"]
                if block_id and block_id not in seen_ids:
                    seen_ids.add(block_id)
                    next_frontier.add(block_id)
            frontier = next_frontier
        return paths

    def fetch_render_rows(self, source_paths: Iterable[str] | None = None) -> list[sqlite3.Row]:
        if source_paths is None:
            query = """
            SELECT chunks.*, files.page_properties_json
            FROM chunks
            JOIN files ON files.source_path = chunks.source_path
            ORDER BY chunks.source_path, chunks.position
            """
            return self.connection.execute(query).fetchall()

        paths = [item for item in source_paths if item]
        if not paths:
            return []
        placeholders = ",".join("?" for _ in paths)
        query = f"""
        SELECT chunks.*, files.page_properties_json
        FROM chunks
        JOIN files ON files.source_path = chunks.source_path
        WHERE chunks.source_path IN ({placeholders})
        ORDER BY chunks.source_path, chunks.position
        """
        return self.connection.execute(query, paths).fetchall()

    def fetch_block_lookup(self) -> dict[str, sqlite3.Row]:
        rows = self.connection.execute(
            """
            SELECT chunks.*, files.page_properties_json
            FROM chunks
            JOIN files ON files.source_path = chunks.source_path
            WHERE chunks.block_id IS NOT NULL
            """
        ).fetchall()
        return {row["block_id"]: row for row in rows if row["block_id"]}

    def update_rendered_chunks(self, payloads: list[tuple[str, str]]) -> None:
        if not payloads:
            return
        chunk_ids = [chunk_id for chunk_id, _ in payloads]
        placeholders = ",".join("?" for _ in chunk_ids)
        self.connection.execute(
            f"DELETE FROM chunks_fts WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        )
        self.connection.executemany(
            "UPDATE chunks SET rendered_text = ? WHERE chunk_id = ?",
            [(rendered, chunk_id) for chunk_id, rendered in payloads],
        )
        rows = self.connection.execute(
            f"""
            SELECT chunk_id, title, anchor, rendered_text
            FROM chunks
            WHERE chunk_id IN ({placeholders})
            """,
            chunk_ids,
        ).fetchall()
        self.connection.executemany(
            """
            INSERT INTO chunks_fts (chunk_id, title, anchor, rendered_text)
            VALUES (?, ?, ?, ?)
            """,
            [(row["chunk_id"], row["title"], row["anchor"], row["rendered_text"]) for row in rows],
        )
        self.connection.commit()

    def fetch_all_rendered_chunks(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT chunk_id, title, anchor, source_path, rendered_text
            FROM chunks
            ORDER BY source_path, position
            """
        ).fetchall()

    def fetch_vector_documents(self, source_paths: Iterable[str] | None = None) -> list[dict[str, str]]:
        rows = self.fetch_render_rows(source_paths)
        return [
            {
                "chunk_id": row["chunk_id"],
                "source_path": row["source_path"],
                "title": row["title"],
                "anchor": row["anchor"],
                "rendered_text": row["rendered_text"],
            }
            for row in rows
            if row["rendered_text"]
        ]

    def search_candidates(self, query_text: str, limit: int) -> list[sqlite3.Row]:
        candidate_map: dict[str, dict[str, object]] = {}
        for row in self._search_fts(query_text, limit):
            candidate_map[row["chunk_id"]] = dict(row)
        for row in self._search_like(query_text, limit):
            payload = dict(row)
            existing = candidate_map.get(row["chunk_id"])
            if existing is None:
                candidate_map[row["chunk_id"]] = payload
                continue
            existing["like_hits"] = max(int(existing.get("like_hits") or 0), int(payload.get("like_hits") or 0))
            if existing.get("fts_rank") is None and payload.get("fts_rank") is not None:
                existing["fts_rank"] = payload["fts_rank"]
        return [self._row_from_dict(item) for item in candidate_map.values()]

    def stats(self) -> dict[str, int]:
        file_count = self.connection.execute("SELECT COUNT(*) AS count FROM files").fetchone()["count"]
        chunk_count = self.connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"]
        ref_count = self.connection.execute("SELECT COUNT(*) AS count FROM refs").fetchone()["count"]
        return {"files": file_count, "chunks": chunk_count, "refs": ref_count}

    # Why: 真实 Logseq 库里偶尔会出现被复制或冲突合并过的重复 id:: UUID。
    # 如果直接硬插入，整轮建库会因为唯一键崩掉。这里保住首个块的 block_id，
    # 把后续重复块降级为普通 chunk，确保索引继续可用，同时让引用解析仍有唯一目标。
    def _demote_duplicate_block_ids(self, parsed_file: ParsedFile) -> list[str]:
        candidate_ids = [chunk.block_id for chunk in parsed_file.chunks if chunk.block_id]
        if not candidate_ids:
            return []
        existing_ids = self._find_existing_block_ids(candidate_ids)
        seen_ids: set[str] = set()
        duplicates: list[str] = []
        for chunk in parsed_file.chunks:
            block_id = chunk.block_id
            if not block_id:
                continue
            if block_id in seen_ids or block_id in existing_ids:
                duplicates.append(block_id)
                chunk.properties.setdefault('_duplicate_block_id', block_id)
                chunk.properties.setdefault('_duplicate_block_resolution', 'demoted_to_plain_chunk')
                chunk.chunk_id = self._fallback_chunk_id(parsed_file.relative_path, chunk.kind, chunk.position, block_id)
                chunk.block_id = None
                continue
            seen_ids.add(block_id)
        return duplicates

    def _find_existing_block_ids(self, block_ids: list[str]) -> set[str]:
        unique_ids = sorted({item for item in block_ids if item})
        if not unique_ids:
            return set()
        placeholders = ','.join('?' for _ in unique_ids)
        rows = self.connection.execute(
            f'SELECT block_id FROM chunks WHERE block_id IN ({placeholders})',
            unique_ids,
        ).fetchall()
        return {row['block_id'] for row in rows if row['block_id']}

    def _fallback_chunk_id(self, source_path: str, kind: str, position: int, block_id: str) -> str:
        digest = hashlib.sha1(f'{source_path}|{kind}|{position}|{block_id}'.encode('utf-8')).hexdigest()[:20]
        return f'dedup:{digest}'

    def _search_fts(self, query_text: str, limit: int) -> list[sqlite3.Row]:
        fts_query = _build_fts_query(query_text)
        if not fts_query:
            return []
        try:
            return self.connection.execute(
                """
                SELECT
                    chunks.chunk_id,
                    chunks.title,
                    chunks.anchor,
                    chunks.source_path,
                    chunks.rendered_text,
                    bm25(chunks_fts, 8.0, 4.0, 1.0) AS fts_rank,
                    0 AS like_hits
                FROM chunks_fts
                JOIN chunks ON chunks.chunk_id = chunks_fts.chunk_id
                WHERE chunks_fts MATCH ?
                ORDER BY fts_rank
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []

    def _search_like(self, query_text: str, limit: int) -> list[sqlite3.Row]:
        normalized = query_text.strip()
        if not normalized:
            return []
        like = f"%{normalized}%"
        return self.connection.execute(
            """
            SELECT
                chunk_id,
                title,
                anchor,
                source_path,
                rendered_text,
                NULL AS fts_rank,
                (
                    CASE WHEN title LIKE ? THEN 3 ELSE 0 END +
                    CASE WHEN anchor LIKE ? THEN 2 ELSE 0 END +
                    CASE WHEN rendered_text LIKE ? THEN 1 ELSE 0 END
                ) AS like_hits
            FROM chunks
            WHERE title LIKE ? OR anchor LIKE ? OR rendered_text LIKE ?
            ORDER BY like_hits DESC, source_path, position
            LIMIT ?
            """,
            (like, like, like, like, like, like, limit),
        ).fetchall()

    def _row_from_dict(self, payload: dict[str, object]) -> sqlite3.Row:
        cursor = self.connection.execute(
            """
            SELECT
                ? AS chunk_id,
                ? AS title,
                ? AS anchor,
                ? AS source_path,
                ? AS rendered_text,
                ? AS fts_rank,
                ? AS like_hits
            """,
            (
                payload.get("chunk_id"),
                payload.get("title"),
                payload.get("anchor"),
                payload.get("source_path"),
                payload.get("rendered_text"),
                payload.get("fts_rank"),
                payload.get("like_hits"),
            ),
        )
        return cursor.fetchone()


def _build_fts_query(query_text: str) -> str | None:
    terms = [term.replace('"', "") for term in QUERY_TOKEN_RE.findall(query_text.strip()) if term.strip()]
    if not terms:
        return None
    return " OR ".join(f'"{term}"' for term in terms[:8])
