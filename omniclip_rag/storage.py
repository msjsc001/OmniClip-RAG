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
        self.connection.execute("PRAGMA busy_timeout=5000;")
        self.connection.execute("PRAGMA foreign_keys=ON;")
        self._variable_limit = self._resolve_variable_limit()
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
                parent_chunk_id TEXT,
                title TEXT NOT NULL,
                anchor TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                rendered_text TEXT NOT NULL DEFAULT '',
                properties_json TEXT NOT NULL,
                position INTEGER NOT NULL,
                depth INTEGER NOT NULL DEFAULT 0,
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
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(chunks)").fetchall()
        }
        if 'parent_chunk_id' not in columns:
            self.connection.execute("ALTER TABLE chunks ADD COLUMN parent_chunk_id TEXT")
        if 'depth' not in columns:
            self.connection.execute("ALTER TABLE chunks ADD COLUMN depth INTEGER NOT NULL DEFAULT 0")
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
        duplicate_block_ids = self._demote_duplicate_block_ids(parsed_file)
        with self.connection:
            self._delete_files_tx([parsed_file.relative_path])
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
                        chunk_id, source_path, kind, block_id, parent_chunk_id, title, anchor,
                        raw_text, rendered_text, properties_json,
                        position, depth, line_start, line_end
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.chunk_id,
                        chunk.source_path,
                        chunk.kind,
                        chunk.block_id,
                        chunk.parent_chunk_id,
                        chunk.title,
                        chunk.anchor,
                        chunk.raw_text,
                        json.dumps(chunk.properties, ensure_ascii=False),
                        chunk.position,
                        chunk.depth,
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
        return duplicate_block_ids

    def delete_files(self, relative_paths: Iterable[str]) -> None:
        paths = [item for item in relative_paths if item]
        if not paths:
            return
        with self.connection:
            self._delete_files_tx(paths)

    def _delete_files_tx(self, paths: list[str]) -> None:
        if not paths:
            return
        for batch in self._batched_values(paths):
            placeholders = ",".join("?" for _ in batch)
            self.connection.execute(
                f"DELETE FROM chunks_fts WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE source_path IN ({placeholders}))",
                batch,
            )
            self.connection.execute(
                f"DELETE FROM files WHERE source_path IN ({placeholders})",
                batch,
            )

    def get_block_ids_for_paths(self, relative_paths: Iterable[str]) -> set[str]:
        paths = [item for item in relative_paths if item]
        if not paths:
            return set()
        block_ids: set[str] = set()
        for batch in self._batched_values(paths):
            placeholders = ",".join("?" for _ in batch)
            rows = self.connection.execute(
                f"""
                SELECT block_id
                FROM chunks
                WHERE source_path IN ({placeholders}) AND block_id IS NOT NULL
                """,
                batch,
            ).fetchall()
            block_ids.update(row["block_id"] for row in rows if row["block_id"])
        return block_ids

    def get_chunk_ids_for_paths(self, relative_paths: Iterable[str]) -> list[str]:
        paths = [item for item in relative_paths if item]
        if not paths:
            return []
        chunk_ids: list[str] = []
        for batch in self._batched_values(paths):
            placeholders = ",".join("?" for _ in batch)
            rows = self.connection.execute(
                f"SELECT chunk_id FROM chunks WHERE source_path IN ({placeholders})",
                batch,
            ).fetchall()
            chunk_ids.extend(row["chunk_id"] for row in rows)
        return chunk_ids

    def get_transitive_dependent_paths(self, block_ids: set[str]) -> set[str]:
        frontier = set(block_ids)
        seen_ids = set(block_ids)
        paths: set[str] = set()
        while frontier:
            rows: list[sqlite3.Row] = []
            for batch in self._batched_values(sorted(frontier)):
                placeholders = ",".join("?" for _ in batch)
                rows.extend(
                    self.connection.execute(
                        f"""
                        SELECT DISTINCT chunks.block_id, chunks.source_path
                        FROM refs
                        JOIN chunks ON chunks.chunk_id = refs.source_chunk_id
                        WHERE refs.target_block_id IN ({placeholders})
                        """,
                        batch,
                    ).fetchall()
                )
            next_frontier: set[str] = set()
            for row in rows:
                paths.add(row["source_path"])
                block_id = row["block_id"]
                if block_id and block_id not in seen_ids:
                    seen_ids.add(block_id)
                    next_frontier.add(block_id)
            frontier = next_frontier
        return paths

    def count_render_rows(self, source_paths: Iterable[str] | None = None) -> int:
        if source_paths is None:
            return int(self.connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"])
        paths = [item for item in source_paths if item]
        if not paths:
            return 0
        total = 0
        for batch in self._batched_values(sorted(paths)):
            placeholders = ",".join("?" for _ in batch)
            total += int(
                self.connection.execute(
                    f"SELECT COUNT(*) AS count FROM chunks WHERE source_path IN ({placeholders})",
                    batch,
                ).fetchone()["count"]
            )
        return total

    def iter_render_rows(self, source_paths: Iterable[str] | None = None):
        if source_paths is None:
            cursor = self.connection.execute(
                """
                SELECT chunks.*, files.page_properties_json
                FROM chunks
                JOIN files ON files.source_path = chunks.source_path
                ORDER BY chunks.source_path, chunks.position
                """
            )
            for row in cursor:
                yield row
            return

        paths = [item for item in source_paths if item]
        if not paths:
            return
        for batch in self._batched_values(sorted(paths)):
            placeholders = ",".join("?" for _ in batch)
            cursor = self.connection.execute(
                f"""
                SELECT chunks.*, files.page_properties_json
                FROM chunks
                JOIN files ON files.source_path = chunks.source_path
                WHERE chunks.source_path IN ({placeholders})
                ORDER BY chunks.source_path, chunks.position
                """,
                batch,
            )
            for row in cursor:
                yield row

    def fetch_render_rows(self, source_paths: Iterable[str] | None = None) -> list[sqlite3.Row]:
        return list(self.iter_render_rows(source_paths))

    def list_source_paths(self) -> list[str]:
        rows = self.connection.execute(
            """
            SELECT source_path
            FROM files
            ORDER BY source_path
            """
        ).fetchall()
        return [str(row["source_path"]) for row in rows if row["source_path"]]

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

    def fetch_block_row(self, block_id: str) -> sqlite3.Row | None:
        if not block_id:
            return None
        return self.connection.execute(
            """
            SELECT chunks.*, files.page_properties_json
            FROM chunks
            JOIN files ON files.source_path = chunks.source_path
            WHERE chunks.block_id = ?
            LIMIT 1
            """
            ,(block_id,),
        ).fetchone()

    def fetch_chunk_row(self, chunk_id: str) -> sqlite3.Row | None:
        if not chunk_id:
            return None
        return self.connection.execute(
            """
            SELECT chunks.*, files.page_properties_json
            FROM chunks
            JOIN files ON files.source_path = chunks.source_path
            WHERE chunks.chunk_id = ?
            LIMIT 1
            """
            ,(chunk_id,),
        ).fetchone()

    def fetch_chunk_lookup(self, chunk_ids: Iterable[str] | None = None) -> dict[str, sqlite3.Row]:
        query = """
            SELECT chunks.*, files.page_properties_json
            FROM chunks
            JOIN files ON files.source_path = chunks.source_path
        """
        if chunk_ids is not None:
            values = [item for item in chunk_ids if item]
            if not values:
                return {}
            rows: list[sqlite3.Row] = []
            for batch in self._batched_values(values):
                placeholders = ",".join("?" for _ in batch)
                rows.extend(
                    self.connection.execute(
                        query + f" WHERE chunks.chunk_id IN ({placeholders})",
                        batch,
                    ).fetchall()
                )
            return {row["chunk_id"]: row for row in rows}
        rows = self.connection.execute(query).fetchall()
        return {row["chunk_id"]: row for row in rows}

    def fetch_rows_by_chunk_ids(self, chunk_ids: Iterable[str]) -> list[sqlite3.Row]:
        values = [item for item in chunk_ids if item]
        if not values:
            return []
        rows: list[sqlite3.Row] = []
        for batch in self._batched_values(values):
            placeholders = ",".join("?" for _ in batch)
            rows.extend(
                self.connection.execute(
                    f"""
                    SELECT chunks.*, files.page_properties_json, NULL AS fts_rank, 0 AS like_hits
                    FROM chunks
                    JOIN files ON files.source_path = chunks.source_path
                    WHERE chunks.chunk_id IN ({placeholders})
                    """,
                    batch,
                ).fetchall()
            )
        return rows

    def update_rendered_chunks(self, payloads: list[tuple[str, str]]) -> None:
        if not payloads:
            return
        with self.connection:
            self.connection.executemany(
                "UPDATE chunks SET rendered_text = ? WHERE chunk_id = ?",
                [(rendered, chunk_id) for chunk_id, rendered in payloads],
            )
            chunk_ids = [chunk_id for chunk_id, _ in payloads if chunk_id]
            for batch in self._batched_values(chunk_ids):
                placeholders = ",".join("?" for _ in batch)
                self.connection.execute(
                    f"DELETE FROM chunks_fts WHERE chunk_id IN ({placeholders})",
                    batch,
                )
                rows = self.connection.execute(
                    f"""
                    SELECT chunk_id, title, anchor, rendered_text
                    FROM chunks
                    WHERE chunk_id IN ({placeholders})
                    """,
                    batch,
                ).fetchall()
                self.connection.executemany(
                    """
                    INSERT INTO chunks_fts (chunk_id, title, anchor, rendered_text)
                    VALUES (?, ?, ?, ?)
                    """,
                    [(row["chunk_id"], row["title"], row["anchor"], row["rendered_text"]) for row in rows],
                )

    def fetch_all_rendered_chunks(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT chunk_id, title, anchor, source_path, rendered_text
            FROM chunks
            ORDER BY source_path, position
            """
        ).fetchall()

    def count_vector_documents(self, source_paths: Iterable[str] | None = None) -> int:
        if source_paths is None:
            return int(self.connection.execute("SELECT COUNT(*) AS count FROM chunks WHERE rendered_text <> ''").fetchone()["count"])
        paths = [item for item in source_paths if item]
        if not paths:
            return 0
        total = 0
        for batch in self._batched_values(sorted(paths)):
            placeholders = ",".join("?" for _ in batch)
            total += int(
                self.connection.execute(
                    f"SELECT COUNT(*) AS count FROM chunks WHERE source_path IN ({placeholders}) AND rendered_text <> ''",
                    batch,
                ).fetchone()["count"]
            )
        return total

    def iter_vector_documents(self, source_paths: Iterable[str] | None = None):
        for row in self.iter_render_rows(source_paths):
            if not row["rendered_text"]:
                continue
            yield {
                "chunk_id": row["chunk_id"],
                "source_path": row["source_path"],
                "title": row["title"],
                "anchor": row["anchor"],
                "rendered_text": row["rendered_text"],
            }

    def fetch_vector_documents(self, source_paths: Iterable[str] | None = None) -> list[dict[str, str]]:
        return list(self.iter_vector_documents(source_paths))

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

    def fetch_file_manifest(self) -> dict[str, tuple[float, int]]:
        rows = self.connection.execute(
            """
            SELECT source_path, mtime, size
            FROM files
            """
        ).fetchall()
        return {
            row["source_path"]: (float(row["mtime"]), int(row["size"]))
            for row in rows
            if row["source_path"]
        }

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
        existing_ids: set[str] = set()
        for batch in self._batched_values(unique_ids):
            placeholders = ','.join('?' for _ in batch)
            rows = self.connection.execute(
                f'SELECT block_id FROM chunks WHERE block_id IN ({placeholders})',
                batch,
            ).fetchall()
            existing_ids.update(row['block_id'] for row in rows if row['block_id'])
        return existing_ids

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
                    chunks.*,
                    files.page_properties_json,
                    bm25(chunks_fts, 8.0, 4.0, 1.0) AS fts_rank,
                    0 AS like_hits
                FROM chunks_fts
                JOIN chunks ON chunks.chunk_id = chunks_fts.chunk_id
                JOIN files ON files.source_path = chunks.source_path
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
                chunks.*,
                files.page_properties_json,
                NULL AS fts_rank,
                (
                    CASE WHEN chunks.title LIKE ? THEN 3 ELSE 0 END +
                    CASE WHEN chunks.anchor LIKE ? THEN 2 ELSE 0 END +
                    CASE WHEN chunks.rendered_text LIKE ? THEN 1 ELSE 0 END
                ) AS like_hits
            FROM chunks
            JOIN files ON files.source_path = chunks.source_path
            WHERE chunks.title LIKE ? OR chunks.anchor LIKE ? OR chunks.rendered_text LIKE ?
            ORDER BY like_hits DESC, chunks.source_path, chunks.position
            LIMIT ?
            """,
            (like, like, like, like, like, like, limit),
        ).fetchall()

    def _row_from_dict(self, payload: dict[str, object]) -> sqlite3.Row:
        cursor = self.connection.execute(
            """
            SELECT
                ? AS chunk_id,
                ? AS source_path,
                ? AS kind,
                ? AS block_id,
                ? AS parent_chunk_id,
                ? AS title,
                ? AS anchor,
                ? AS raw_text,
                ? AS rendered_text,
                ? AS properties_json,
                ? AS position,
                ? AS depth,
                ? AS line_start,
                ? AS line_end,
                ? AS page_properties_json,
                ? AS fts_rank,
                ? AS like_hits
            """,
            (
                payload.get("chunk_id"),
                payload.get("source_path"),
                payload.get("kind"),
                payload.get("block_id"),
                payload.get("parent_chunk_id"),
                payload.get("title"),
                payload.get("anchor"),
                payload.get("raw_text"),
                payload.get("rendered_text"),
                payload.get("properties_json"),
                payload.get("position"),
                payload.get("depth"),
                payload.get("line_start"),
                payload.get("line_end"),
                payload.get("page_properties_json"),
                payload.get("fts_rank"),
                payload.get("like_hits"),
            ),
        )
        return cursor.fetchone()

    def _resolve_variable_limit(self) -> int:
        if hasattr(self.connection, 'getlimit') and hasattr(sqlite3, 'SQLITE_LIMIT_VARIABLE_NUMBER'):
            try:
                limit = int(self.connection.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER))
            except Exception:
                limit = 0
            if limit > 32:
                return max(limit - 8, 128)
        return 900

    def _batched_values(self, values: Iterable[str]):
        batch: list[str] = []
        for value in values:
            if not value:
                continue
            batch.append(value)
            if len(batch) >= self._variable_limit:
                yield batch
                batch = []
        if batch:
            yield batch



def _build_fts_query(query_text: str) -> str | None:
    terms = [term.replace('"', "") for term in QUERY_TOKEN_RE.findall(query_text.strip()) if term.strip()]
    if not terms:
        return None
    return " OR ".join(f'"{term}"' for term in terms[:8])
