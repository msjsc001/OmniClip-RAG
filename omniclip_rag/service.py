from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .clipboard import copy_text
from .config import AppConfig, DataPaths
from .errors import BuildCancelledError
from .models import QueryInsights, QueryResult, SearchHit, SpaceEstimate
from .parser import BLOCK_REF_RE, BULLET_RE, EMBED_RE, PAGE_REF_RE, PROPERTY_RE
from .query_runtime import QueryRuntimeAdvisor, select_query_hits
from .retrieval_policy import QueryProfile, build_query_profile, rank_candidates
from .reranker import CrossEncoderReranker, create_reranker, is_local_reranker_ready
from .preflight import estimate_storage_for_vault
from .storage import MetadataStore
from .timing import BuildEtaTracker, append_build_history, build_history_file, estimate_remaining_build_seconds, find_matching_history
from .vector_index import create_vector_index, is_local_model_ready, resolve_vector_device

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    WATCHDOG_AVAILABLE = True
except ImportError:
    FileSystemEvent = object  # type: ignore[assignment]
    FileSystemEventHandler = object  # type: ignore[assignment]
    Observer = None  # type: ignore[assignment]
    WATCHDOG_AVAILABLE = False


IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
TAG_RE = re.compile(r"(?<!\w)#([\w\-\u4e00-\u9fff/]+)")
QUERY_TERM_RE = re.compile(r"[\w\u4e00-\u9fff-]+", re.UNICODE)
MAX_RENDER_DEPTH = 4
MAX_EXPANDED_LENGTH = 480
WATCH_DEBOUNCE_SECONDS = 0.8
WATCH_STABLE_FILE_SECONDS = 1.2
WATCH_DELETE_CONFIRM_SECONDS = 3.0
WATCH_REPAIR_INTERVAL_SECONDS = 20.0
WATCH_STATE_VERSION = 1
REBUILD_STATE_VERSION = 1
REBUILD_PROGRESS_EMIT_INTERVAL_SECONDS = 0.25
RENDER_UPDATE_BATCH_SIZE = 1024
VECTOR_UPSERT_BATCH_SIZE = 256
SENSITIVE_PLACEHOLDER = '[被RAG过滤/Filtered by RAG]'
LOGSEQ_HIDDEN_PROPERTIES = {'id', 'collapsed'}
LABELED_SECRET_PATTERNS = [
    re.compile(r'(?i)(?P<label>密码|password|passwd|pwd)(?P<sep>\s*[:：=]\s*)(?P<secret>[^\s`]+)'),
    re.compile(r'(?i)(?P<label>api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|private[_ -]?key|secret|token|2fa|otp|私钥|密钥|令牌)(?P<sep>\s*[:：=]\s*)(?P<secret>[^\s`]+)'),
]
RAW_SECRET_PATTERNS = [
    re.compile(r'\bsk-[A-Za-z0-9_-]{8,}\b'),
    re.compile(r'\bAIza[0-9A-Za-z_-]{20,}\b'),
    re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._-]{10,}\b'),
]
EXTENDED_REDACTION_PATTERNS = [
    re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
    re.compile(r'(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)'),
    re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)'),
]


class OmniClipService:
    def __init__(self, config: AppConfig, paths: DataPaths) -> None:
        self.config = config
        self.paths = paths
        self.store = MetadataStore(paths.sqlite_file)
        self.vector_index = create_vector_index(config, paths)
        self._rebuild_state_file = self.paths.state_dir / 'rebuild_state.json'
        self._watch_state_file = self.paths.state_dir / 'watch_state.json'
        self._build_history_file = build_history_file(self.paths.state_dir)
        self._query_runtime_file = self.paths.state_dir / 'query_runtime.json'
        self._query_runtime_advisor = QueryRuntimeAdvisor(self._query_runtime_file)
        self.reranker = create_reranker(config, paths)

    def close(self) -> None:
        self.store.close()

    def save_runtime_config(self) -> None:
        payload = asdict(self.config)
        self.paths.config_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    # Why: 真正影响速度的是目录遍历是否剪枝，而不是后面解析器里再过滤一次。
    def scan_vault(self) -> list[Path]:
        if not self.config.vault_path:
            return []
        ignore = set(self.config.ignore_dirs)
        files: list[Path] = []
        for root, dirnames, filenames in os.walk(self.config.vault_dir, topdown=True):
            dirnames[:] = [name for name in dirnames if name not in ignore]
            current_root = Path(root)
            for filename in filenames:
                if not filename.lower().endswith('.md'):
                    continue
                files.append((current_root / filename).resolve())
        return sorted(files)

    def estimate_space(self) -> SpaceEstimate:
        report = estimate_storage_for_vault(self.config, self.paths, files=self.scan_vault())
        self.store.record_preflight(report, str(self.config.vault_dir))
        return report

    def bootstrap_model(self) -> dict[str, object]:
        result = self.vector_index.warmup()
        result['cache_bytes'] = _directory_size(self.paths.cache_dir / 'models')
        return result

    def bootstrap_reranker(self) -> dict[str, object]:
        bootstrapper = CrossEncoderReranker(self.config, self.paths)
        result = bootstrapper.warmup(allow_download=True)
        result['cache_bytes'] = _directory_size(self.paths.cache_dir / 'models')
        return result

    def rebuild_index(
        self,
        *,
        resume: bool = False,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, int]:
        from .parser import parse_markdown_file

        files = self.scan_vault()
        manifest = self._build_file_manifest(files)
        state = self._read_rebuild_state() if resume else None
        if state is None or not self._can_resume_rebuild_state(state, manifest):
            if resume and self._rebuild_state_file.exists():
                self.discard_pending_rebuild()
            self._start_fresh_rebuild_state(manifest)
            self.store.reset_all()
            self.vector_index.reset()
            state = self._read_rebuild_state() or self._start_fresh_rebuild_state(manifest)
        else:
            state['file_manifest'] = manifest
            state['total_files'] = len(manifest)
            self._write_rebuild_state(state)

        vector_enabled = (self.config.vector_backend or 'disabled').strip().lower() not in {'', 'disabled', 'none', 'off'}
        model_ready = (not vector_enabled) or is_local_model_ready(self.config, self.paths)
        history_entry = find_matching_history(self._build_history_file, self.config)
        eta_tracker = BuildEtaTracker(self.config, history_entry=history_entry, vector_enabled=vector_enabled, model_ready=model_ready)
        resolved_device = resolve_vector_device(self.config.vector_device)

        completed_paths = set(state.get('completed_paths', []))
        readable_paths = list(dict.fromkeys(state.get('readable_paths', [])))
        skipped_paths = list(dict.fromkeys(state.get('skipped_paths', [])))
        duplicate_block_ids = int(state.get('duplicate_block_ids', 0))
        parsed_chunk_count = int(state.get('parsed_chunk_count', self.store.stats().get('chunks', 0)))
        total_files = len(manifest)

        rebuild_started_at = time.time()
        indexing_started_at = rebuild_started_at
        rendering_started_at = 0.0
        vectorizing_started_at = 0.0

        for path in files:
            _wait_for_worker_controls(pause_event, cancel_event)
            relative_path = path.relative_to(self.config.vault_dir).as_posix()
            if relative_path in completed_paths:
                continue
            try:
                parsed = parse_markdown_file(self.config.vault_dir, path)
            except OSError:
                skipped_paths.append(relative_path)
            else:
                duplicate_block_ids += len(self.store.replace_file(parsed))
                readable_paths.append(relative_path)
                parsed_chunk_count += len(parsed.chunks)
            completed_paths.add(relative_path)
            state.update(
                {
                    'phase': 'indexing',
                    'completed_paths': sorted(completed_paths),
                    'readable_paths': list(dict.fromkeys(readable_paths)),
                    'skipped_paths': sorted(set(skipped_paths)),
                    'duplicate_block_ids': duplicate_block_ids,
                    'parsed_chunk_count': parsed_chunk_count,
                    'current_path': relative_path,
                    'updated_at': _utc_now(),
                }
            )
            self._write_rebuild_state(state)
            completed_count = len(completed_paths)
            estimated_total_chunks = parsed_chunk_count
            if completed_count > 0:
                estimated_total_chunks = max(parsed_chunk_count, int(parsed_chunk_count / completed_count * max(total_files, 1)))
            eta_seconds, overall_percent = eta_tracker.estimate(
                stage='indexing',
                current=completed_count,
                total=total_files,
                elapsed_total=time.time() - rebuild_started_at,
                stage_elapsed=time.time() - indexing_started_at,
                parsed_chunks=parsed_chunk_count,
                estimated_total_chunks=estimated_total_chunks,
            )
            _emit_progress(
                on_progress,
                {
                    'stage': 'indexing',
                    'current': completed_count,
                    'total': total_files,
                    'current_path': relative_path,
                    'duplicate_block_ids': duplicate_block_ids,
                    'parsed_chunks': parsed_chunk_count,
                    'estimated_total_chunks': estimated_total_chunks,
                    'eta_seconds': eta_seconds,
                    'overall_percent': overall_percent,
                },
            )

        indexing_seconds = max(time.time() - indexing_started_at, 0.0)
        readable_paths = list(dict.fromkeys(readable_paths))
        _wait_for_worker_controls(pause_event, cancel_event)
        state.update({'phase': 'rendering', 'updated_at': _utc_now(), 'current_path': ''})
        self._write_rebuild_state(state)
        rendering_started_at = time.time()
        total_render_rows = self._refresh_rendered(
            readable_paths,
            pause_event=pause_event,
            cancel_event=cancel_event,
            on_progress=on_progress,
            rebuild_started_at=rebuild_started_at,
            history_entry=history_entry,
            vector_enabled=vector_enabled,
            model_ready=model_ready,
            eta_tracker=eta_tracker,
        )
        rendering_seconds = max(time.time() - rendering_started_at, 0.0)

        _wait_for_worker_controls(pause_event, cancel_event)
        total_documents = self.store.count_vector_documents()
        documents = self.store.iter_vector_documents()
        state.update({'phase': 'vectorizing', 'updated_at': _utc_now(), 'current_path': ''})
        self._write_rebuild_state(state)
        vectorizing_started_at = time.time()
        eta_seconds, overall_percent = eta_tracker.estimate(
            stage='vectorizing',
            current=0,
            total=total_documents,
            elapsed_total=time.time() - rebuild_started_at,
            stage_elapsed=0.0,
            parsed_chunks=parsed_chunk_count,
            estimated_total_chunks=max(total_documents, parsed_chunk_count),
        )
        _emit_progress(
            on_progress,
            {
                'stage': 'vectorizing',
                'current': 0,
                'total': total_documents,
                'eta_seconds': eta_seconds,
                'overall_percent': overall_percent,
                'stage_status': 'loading_model',
            },
        )

        vector_metrics: dict[str, object] = {}

        def emit_vector_progress(progress: dict[str, object]) -> None:
            current = max(0, int(progress.get('current', 0) or 0))
            total = max(0, int(progress.get('total', total_documents) or total_documents))
            eta_seconds, overall_percent = eta_tracker.estimate(
                stage='vectorizing',
                current=current,
                total=total,
                elapsed_total=time.time() - rebuild_started_at,
                stage_elapsed=time.time() - vectorizing_started_at,
                parsed_chunks=parsed_chunk_count,
                estimated_total_chunks=max(total_documents, parsed_chunk_count),
            )
            state['updated_at'] = _utc_now()
            self._write_rebuild_state(state)
            enriched = dict(progress)
            enriched['eta_seconds'] = eta_seconds
            enriched['overall_percent'] = overall_percent
            for key in (
                'encode_elapsed_total_ms',
                'prepare_elapsed_total_ms',
                'write_elapsed_total_ms',
                'write_flush_count',
                'encoded_count',
                'written_count',
                'write_queue_depth',
                'write_queue_capacity',
                'staged_write_rows',
            ):
                if key in enriched:
                    vector_metrics[key] = enriched[key]
            _emit_progress(on_progress, enriched)

        self.vector_index.rebuild(documents, total=total_documents, on_progress=emit_vector_progress, pause_event=pause_event, cancel_event=cancel_event)
        vectorizing_seconds = max(time.time() - vectorizing_started_at, 0.0)

        stats = {**self.store.stats(), 'duplicate_block_ids': duplicate_block_ids}
        self._record_build_history(
            files=stats.get('files', total_files),
            chunks=stats.get('chunks', parsed_chunk_count),
            refs=stats.get('refs', 0),
            indexing_seconds=indexing_seconds,
            rendering_seconds=rendering_seconds,
            vectorizing_seconds=vectorizing_seconds,
            vector_tail_seconds_per_chunk=eta_tracker.recent_rate('vectorizing') or 0.0,
            resolved_device=resolved_device,
            total_seconds=max(time.time() - rebuild_started_at, 0.0),
            vector_prepare_seconds=float(vector_metrics.get('prepare_elapsed_total_ms', 0.0) or 0.0) / 1000.0,
            vector_write_seconds=float(vector_metrics.get('write_elapsed_total_ms', 0.0) or 0.0) / 1000.0,
            vector_write_flush_count=int(vector_metrics.get('write_flush_count', 0) or 0),
        )
        self._clear_rebuild_state()
        self._clear_watch_state()
        return stats

    def reindex_paths(self, changed_relative_paths: list[str], deleted_relative_paths: list[str]) -> dict[str, object]:
        from .parser import parse_markdown_file

        changed_paths = sorted({item for item in changed_relative_paths if item})
        deleted_paths = sorted({item for item in deleted_relative_paths if item})
        parsed_by_path = {}
        skipped_changed_paths: list[str] = []
        for relative_path in changed_paths:
            absolute_path = self.config.vault_dir / relative_path
            if not absolute_path.exists():
                continue
            try:
                parsed_by_path[relative_path] = parse_markdown_file(self.config.vault_dir, absolute_path)
            except (OSError, UnicodeError):
                skipped_changed_paths.append(relative_path)

        replaced_paths = sorted(parsed_by_path)
        mutated_paths = sorted(set(deleted_paths) | set(replaced_paths))
        previous_block_ids = self.store.get_block_ids_for_paths(mutated_paths)
        previous_chunk_ids = self.store.get_chunk_ids_for_paths(mutated_paths)
        dependent_paths = self.store.get_transitive_dependent_paths(previous_block_ids) if previous_block_ids else set()

        if deleted_paths:
            self.store.delete_files(deleted_paths)

        duplicate_block_ids = 0
        new_block_ids: set[str] = set()
        for relative_path in replaced_paths:
            parsed = parsed_by_path[relative_path]
            duplicate_block_ids += len(self.store.replace_file(parsed))
            new_block_ids.update(chunk.block_id for chunk in parsed.chunks if chunk.block_id)

        affected_paths = set(replaced_paths) | dependent_paths
        if new_block_ids:
            affected_paths |= self.store.get_transitive_dependent_paths(new_block_ids)
        affected_list = sorted(affected_paths)
        if affected_list:
            self._update_watch_state(add_paths=affected_list)
            self._refresh_rendered(affected_list)
            self._update_watch_state(remove_paths=affected_list)

        vector_error = self._sync_vector_documents(affected_paths=affected_list, deleted_chunk_ids=previous_chunk_ids)
        stats: dict[str, object] = {**self.store.stats(), 'duplicate_block_ids': duplicate_block_ids}
        if skipped_changed_paths:
            stats['skipped_changed_paths'] = skipped_changed_paths
        if vector_error:
            stats['vector_dirty'] = 1
            stats['vector_error'] = vector_error
        return stats

    def _sync_vector_documents(self, *, affected_paths: list[str], deleted_chunk_ids: list[str]) -> str | None:
        vector_paths = sorted({item for item in affected_paths if item})
        vector_chunk_ids = sorted({item for item in deleted_chunk_ids if item})
        if not vector_paths and not vector_chunk_ids:
            return None
        self._update_watch_state(add_vector_paths=vector_paths, add_vector_chunk_ids=vector_chunk_ids)
        try:
            if vector_chunk_ids:
                self.vector_index.delete(vector_chunk_ids)
            if vector_paths:
                self._upsert_vector_documents_for_paths(vector_paths)
        except Exception as exc:
            return str(exc)
        self._update_watch_state(remove_vector_paths=vector_paths, remove_vector_chunk_ids=vector_chunk_ids)
        return None

    def _repair_watch_state(self, current_snapshot: dict[str, tuple[float, int]]) -> list[dict[str, object]]:
        state = self._read_watch_state()
        if state is None:
            return []

        repaired_paths = sorted(path for path in state.get('dirty_paths', []) if path in current_snapshot)
        repaired_vector_paths = sorted(path for path in state.get('dirty_vector_paths', []) if path in current_snapshot)
        repaired_vector_chunk_ids = sorted({item for item in state.get('dirty_vector_chunk_ids', []) if item})
        if repaired_paths:
            self._refresh_rendered(repaired_paths)
            self._update_watch_state(remove_paths=repaired_paths)
        if repaired_vector_chunk_ids or repaired_vector_paths:
            if repaired_vector_chunk_ids:
                self.vector_index.delete(repaired_vector_chunk_ids)
            if repaired_vector_paths:
                self._upsert_vector_documents_for_paths(repaired_vector_paths)
            self._update_watch_state(
                remove_vector_paths=repaired_vector_paths,
                remove_vector_chunk_ids=repaired_vector_chunk_ids,
            )
        if not repaired_paths and not repaired_vector_paths and not repaired_vector_chunk_ids:
            return []
        return [
            {
                'kind': 'repair',
                'paths': len(repaired_paths),
                'vector_paths': len(repaired_vector_paths),
                'vector_chunk_ids': len(repaired_vector_chunk_ids),
            }
        ]

    def _upsert_vector_documents_for_paths(self, source_paths: list[str]) -> None:
        if not source_paths:
            return
        batch_size = max(int(self.config.vector_batch_size or 16) * 8, VECTOR_UPSERT_BATCH_SIZE)
        buffer: list[dict[str, str]] = []
        for document in self.store.iter_vector_documents(source_paths):
            buffer.append(document)
            if len(buffer) >= batch_size:
                self.vector_index.upsert(buffer)
                buffer = []
        if buffer:
            self.vector_index.upsert(buffer)

    def query(
        self,
        query_text: str,
        limit: int | None = None,
        copy_result: bool = False,
        score_threshold: float | None = None,
    ) -> QueryResult:
        started_at = time.perf_counter()
        limit = max(int(limit or self.config.query_limit or 0), 1)
        profile = build_query_profile(query_text, limit)
        candidate_limit = profile.candidate_limit
        page_block_patterns = _compile_page_blocklist_patterns(getattr(self.config, 'page_blocklist_rules', ''))
        storage_candidates = _filter_candidate_rows_by_page_blocklist(self.store.search_candidates(query_text, candidate_limit), page_block_patterns)
        vector_candidates = {}
        if profile.use_vector:
            vector_limit = max(self.config.vector_candidate_limit, candidate_limit)
            vector_candidates = {item.chunk_id: item.score for item in self.vector_index.search(query_text, vector_limit)}
        candidate_rows = _filter_candidate_rows_by_page_blocklist(self._merge_candidate_rows(storage_candidates, vector_candidates), page_block_patterns)

        if candidate_rows:
            hits = rank_candidates(query_text, candidate_rows, vector_candidates, profile)
        else:
            rows = _filter_candidate_rows_by_page_blocklist(self.store.fetch_all_rendered_chunks(), page_block_patterns)
            hits = rank_candidates(query_text, rows, vector_candidates, profile)

        effective_threshold = self.config.query_score_threshold if score_threshold is None else float(score_threshold or 0.0)
        threshold_floor = max(effective_threshold, 0.0)
        rerank_limit = min(len(hits), max(limit, profile.hydration_pool_size))
        reranked_hits, rerank_outcome = self.reranker.rerank(query_text, hits, rerank_limit)
        filtered_hits = [hit for hit in reranked_hits if hit.score >= threshold_floor]
        finalized_hits, insights = self._finalize_query_hits(query_text, filtered_hits, limit, profile)
        insights.elapsed_ms = max(int((time.perf_counter() - started_at) * 1000), 0)
        insights.reranker = rerank_outcome
        insights.recommendation = self._query_runtime_advisor.record_and_recommend(
            resolved_device=resolve_vector_device(self.config.vector_device),
            query_limit=limit,
            elapsed_ms=insights.elapsed_ms,
            selected_hits=len(finalized_hits),
            hydrated_candidates=insights.hydrated_candidates,
            reranker_enabled=getattr(self.config, 'reranker_enabled', False),
            reranker_degraded=bool(rerank_outcome.degraded_to_cpu if rerank_outcome else False),
            reranker_oom=bool(rerank_outcome.oom_recovered if rerank_outcome else False),
        )
        context_pack = self.compose_context_pack_text(query_text, finalized_hits, export_mode=getattr(self.config, 'context_export_mode', 'standard'), language=getattr(self.config, 'ui_language', 'zh-CN'))
        if copy_result:
            copy_text(context_pack)
        export_name = f"context_{int(time.time())}.md"
        (self.paths.exports_dir / export_name).write_text(context_pack, encoding='utf-8')
        return QueryResult(hits=finalized_hits, context_text=context_pack, insights=insights)

    def _merge_candidate_rows(self, storage_candidates, vector_candidates: dict[str, float]):
        candidate_map = {row['chunk_id']: row for row in storage_candidates}
        missing_vector_ids = [chunk_id for chunk_id in vector_candidates if chunk_id not in candidate_map]
        if missing_vector_ids:
            for row in self.store.fetch_rows_by_chunk_ids(missing_vector_ids):
                candidate_map[row['chunk_id']] = row
        return list(candidate_map.values())

    def _hydrate_display_hits(self, query_text: str, hits: list[SearchHit]) -> None:
        if not hits:
            return
        block_lookup = self.store.fetch_block_lookup()
        chunk_lookup = self.store.fetch_chunk_lookup()
        file_cache: dict[str, list[str]] = {}
        for hit in hits:
            row = chunk_lookup.get(hit.chunk_id)
            display_text = ''
            if row is not None:
                display_text = _build_display_text(self.config.vault_dir, row, block_lookup, chunk_lookup, file_cache, self.config)
            hit.display_text = display_text.strip() or _apply_output_redaction(_normalize_markup(hit.rendered_text), self.config)
            hit.preview_text = _build_preview_text(query_text, hit.display_text or hit.rendered_text)

    def _finalize_query_hits(self, query_text: str, hits: list[SearchHit], limit: int, profile: QueryProfile) -> tuple[list[SearchHit], QueryInsights]:
        if not hits or limit <= 0:
            return [], QueryInsights(hydrated_candidates=len(hits))
        hydrated_pool: list[SearchHit] = []
        step = profile.hydration_pool_size
        offset = 0
        selected: list[SearchHit] = []
        insights = QueryInsights(hydrated_candidates=len(hits))
        while offset < len(hits):
            batch = hits[offset: offset + step]
            self._hydrate_display_hits(query_text, batch)
            hydrated_pool.extend(batch)
            selected, insights = select_query_hits(hydrated_pool, limit)
            offset += len(batch)
            if len(selected) >= limit or offset >= len(hits):
                break
        insights.hydrated_candidates = len(hydrated_pool)
        insights.selected_hits = len(selected)
        return selected[:limit], insights

    @staticmethod
    def compose_context_pack_text(query_text: str, hits: list[SearchHit], *, export_mode: str = 'standard', language: str = 'zh-CN') -> str:
        resolved_language = _resolve_context_language(language, query_text, hits)
        lines = ['# RAG结果']
        if query_text.strip():
            lines.extend(['', f'搜索词：{query_text.strip()}'])
        if str(export_mode or 'standard').strip().lower() == 'ai-collab':
            lines.extend(_ai_collaboration_lines(resolved_language))

        if not hits:
            lines.extend(['', '未找到足够相关的笔记片段。'])
            return '\n'.join(lines).strip() + '\n'

        grouped: dict[tuple[str, str], list[SearchHit]] = {}
        for hit in hits:
            key = (hit.title, hit.source_path)
            grouped.setdefault(key, []).append(hit)

        total_groups = len(grouped)
        for page_index, ((title, _source_path), page_hits) in enumerate(grouped.items()):
            lines.extend(['', f'# 笔记名：{title}'])
            deduped_fragments = _collect_context_fragments(page_hits)

            for fragment_index, fragment in enumerate(deduped_fragments, start=1):
                lines.append(f'笔记片段{fragment_index}：')
                lines.append(fragment)
                lines.append('')
            if page_index < total_groups - 1:
                lines.extend(['---', ''])

        return '\n'.join(lines).strip() + '\n'

    def compose_context_pack(self, query_text: str, hits: list[SearchHit]) -> str:
        return self.compose_context_pack_text(query_text, hits, export_mode=getattr(self.config, 'context_export_mode', 'standard'), language=getattr(self.config, 'ui_language', 'zh-CN'))

    def watch(self, interval: float | None = None, force_polling: bool = False) -> None:
        stop_event = threading.Event()
        self.watch_until_stopped(stop_event, interval=interval, force_polling=force_polling)

    def watch_until_stopped(
        self,
        stop_event: threading.Event,
        interval: float | None = None,
        force_polling: bool = False,
        on_update: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        interval = interval or self.config.poll_interval_seconds
        if not force_polling and WATCHDOG_AVAILABLE:
            self._watch_with_watchdog(interval, stop_event, on_update)
            return
        self._watch_with_polling(interval, stop_event, on_update)

    def status_snapshot(self) -> dict[str, object]:
        latest = self.store.fetch_latest_preflight()
        stats = self.store.stats()
        latest_preflight = None
        if latest is not None:
            latest_preflight = {
                'risk_level': latest['risk_level'],
                'required_free_bytes': latest['required_free_bytes'],
                'available_free_bytes': latest['available_free_bytes'],
                'run_at': latest['run_at'],
            }
        pending = self.pending_rebuild()
        resolved_vault = str(self.config.vault_dir) if self.config.vault_path else ''
        query_recommendation = asdict(self._query_runtime_advisor.current_recommendation(resolve_vector_device(self.config.vector_device), getattr(self.config, 'reranker_enabled', False)))
        return {
            'vault_path': resolved_vault,
            'data_root': str(self.paths.global_root),
            'shared_root': str(self.paths.shared_root),
            'workspace_root': str(self.paths.root),
            'workspace_id': self.paths.root.name,
            'vector_backend': self.config.vector_backend,
            'reranker_enabled': getattr(self.config, 'reranker_enabled', False),
            'reranker_model': getattr(self.config, 'reranker_model', ''),
            'reranker_ready': is_local_reranker_ready(self.config, self.paths),
            'stats': stats,
            'latest_preflight': latest_preflight,
            'watchdog_available': WATCHDOG_AVAILABLE,
            'pending_rebuild': pending,
            'query_limit_recommendation': query_recommendation,
        }

    def pending_rebuild(self) -> dict[str, object] | None:
        state = self._read_rebuild_state()
        if state is None:
            return None
        completed = len(state.get('completed_paths', []))
        return {
            'phase': state.get('phase', 'indexing'),
            'completed': completed,
            'total': int(state.get('total_files', 0)),
            'started_at': state.get('started_at', ''),
            'updated_at': state.get('updated_at', ''),
            'current_path': state.get('current_path', ''),
        }

    def discard_pending_rebuild(self) -> None:
        self.vector_index.reset()
        self.store.reset_all()
        self._clear_rebuild_state()
        self._clear_watch_state()

    def open_data_dir(self) -> None:
        import os as _os

        _os.startfile(self.paths.root)  # type: ignore[attr-defined]

    def open_exports_dir(self) -> None:
        import os as _os

        _os.startfile(self.paths.exports_dir)  # type: ignore[attr-defined]

    def open_vault_dir(self) -> None:
        import os as _os

        _os.startfile(self.config.vault_dir)  # type: ignore[attr-defined]

    def clear_data(
        self,
        clear_index: bool = False,
        clear_logs: bool = False,
        clear_cache: bool = False,
        clear_exports: bool = False,
    ) -> None:
        if clear_index:
            self.vector_index.reset()
            self.store.close()
            if self.paths.sqlite_file.exists():
                self.paths.sqlite_file.unlink()
            self._clear_rebuild_state()
            self._clear_watch_state()
            self.store = MetadataStore(self.paths.sqlite_file)
        if clear_logs:
            _clear_directory(self.paths.logs_dir)
        if clear_cache:
            _clear_directory(self.paths.cache_dir)
        if clear_exports:
            _clear_directory(self.paths.exports_dir)


    def _watch_with_polling(
        self,
        interval: float,
        stop_event: threading.Event,
        on_update: Callable[[dict[str, object]], None] | None,
    ) -> None:
        self._watch_loop('polling', interval, stop_event, on_update)

    def _watch_with_watchdog(
        self,
        interval: float,
        stop_event: threading.Event,
        on_update: Callable[[dict[str, object]], None] | None,
    ) -> None:
        handler = _VaultEventHandler(self.config.vault_dir, set(self.config.ignore_dirs))
        observer = Observer()
        observer.schedule(handler, str(self.config.vault_dir), recursive=True)
        observer.start()
        try:
            self._watch_loop(
                'watchdog',
                interval,
                stop_event,
                on_update,
                event_provider=lambda: handler.pop_due_changes(WATCH_DEBOUNCE_SECONDS),
            )
        finally:
            observer.stop()
            observer.join(timeout=5)

    def _watch_loop(
        self,
        mode: str,
        interval: float,
        stop_event: threading.Event,
        on_update: Callable[[dict[str, object]], None] | None,
        event_provider: Callable[[], tuple[list[str], list[str]]] | None = None,
    ) -> None:
        previous_snapshot, offline_reason = self._snapshot_safe()
        buffer = _LiveWatchBuffer(WATCH_STABLE_FILE_SECONDS, WATCH_DELETE_CONFIRM_SECONDS)
        last_repair_at = 0.0

        if previous_snapshot is None:
            previous_snapshot = self.store.fetch_file_manifest()
            self._update_watch_state(vault_offline=True, offline_reason=offline_reason or '')
            _emit_watch_update(
                on_update,
                mode,
                [],
                [],
                self.store.stats(),
                events=[{'kind': 'vault_offline', 'reason': offline_reason or ''}],
                note_only=True,
            )
        else:
            self._update_watch_state(vault_offline=False, offline_reason='')
            reconcile_changed, reconcile_deleted = _diff_snapshot(self.store.fetch_file_manifest(), previous_snapshot)
            buffer.record(reconcile_changed, reconcile_deleted, previous_snapshot)
            try:
                repair_events = self._repair_watch_state(previous_snapshot)
            except Exception as exc:
                repair_events = [{'kind': 'batch_retry', 'changed': [], 'deleted': [], 'error': str(exc)}]
            if repair_events:
                _emit_watch_update(on_update, mode, [], [], self.store.stats(), events=repair_events, note_only=True)
            last_repair_at = time.time()

        while not stop_event.wait(interval):
            now = time.time()
            current_snapshot, offline_reason = self._snapshot_safe()
            if current_snapshot is None:
                watch_state = self._read_watch_state() or {}
                if not watch_state.get('vault_offline'):
                    self._update_watch_state(vault_offline=True, offline_reason=offline_reason or '')
                    _emit_watch_update(
                        on_update,
                        mode,
                        [],
                        [],
                        self.store.stats(),
                        events=[{'kind': 'vault_offline', 'reason': offline_reason or ''}],
                        note_only=True,
                    )
                continue

            events: list[dict[str, object]] = []
            watch_state = self._read_watch_state() or {}
            if watch_state.get('vault_offline'):
                self._update_watch_state(vault_offline=False, offline_reason='')
                events.append({'kind': 'vault_recovered'})

            hinted_changed, hinted_deleted = event_provider() if event_provider is not None else ([], [])
            changed, deleted = _diff_snapshot(previous_snapshot, current_snapshot)
            reconcile_changed, reconcile_deleted = _diff_snapshot(self.store.fetch_file_manifest(), current_snapshot)
            buffer.record(
                _merge_relative_paths(changed, hinted_changed, reconcile_changed),
                _merge_relative_paths(deleted, hinted_deleted, reconcile_deleted),
                current_snapshot,
                now,
            )

            if last_repair_at <= 0.0 or (now - last_repair_at) >= WATCH_REPAIR_INTERVAL_SECONDS:
                try:
                    events.extend(self._repair_watch_state(current_snapshot))
                except Exception as exc:
                    events.append({'kind': 'batch_retry', 'changed': [], 'deleted': [], 'error': str(exc)})
                last_repair_at = now

            ready_changed, ready_deleted = buffer.pop_ready(current_snapshot, now)
            if ready_changed or ready_deleted:
                try:
                    stats = self.reindex_paths(ready_changed, ready_deleted)
                except Exception as exc:
                    buffer.requeue(ready_changed, ready_deleted, current_snapshot, now)
                    events.append(
                        {
                            'kind': 'batch_retry',
                            'changed': ready_changed[:5],
                            'deleted': ready_deleted[:5],
                            'error': str(exc),
                        }
                    )
                    _emit_watch_update(on_update, mode, [], [], self.store.stats(), events=events, note_only=True)
                else:
                    skipped_changed = [
                        item
                        for item in stats.get('skipped_changed_paths', [])
                        if isinstance(item, str) and item in current_snapshot
                    ]
                    if skipped_changed:
                        buffer.requeue(skipped_changed, [], current_snapshot, now)
                    _emit_watch_update(on_update, mode, ready_changed, ready_deleted, stats, events=events)
            elif events:
                _emit_watch_update(on_update, mode, [], [], self.store.stats(), events=events, note_only=True)

            previous_snapshot = current_snapshot

    def _snapshot(self) -> dict[str, tuple[float, int]]:
        snapshot, _ = self._snapshot_safe()
        return snapshot or {}

    def _snapshot_safe(self) -> tuple[dict[str, tuple[float, int]] | None, str | None]:
        if not self.config.vault_path:
            return {}, None
        vault_dir = self.config.vault_dir
        try:
            if not vault_dir.exists():
                return None, f'vault missing: {vault_dir}'
            if not vault_dir.is_dir():
                return None, f'vault not directory: {vault_dir}'
        except OSError as exc:
            return None, str(exc)

        snapshot: dict[str, tuple[float, int]] = {}
        errors: list[str] = []
        ignore = set(self.config.ignore_dirs)

        def onerror(exc: OSError) -> None:
            errors.append(str(exc))

        try:
            for root, dirnames, filenames in os.walk(vault_dir, topdown=True, onerror=onerror):
                dirnames[:] = [name for name in dirnames if name not in ignore]
                current_root = Path(root)
                for filename in filenames:
                    if not filename.lower().endswith('.md'):
                        continue
                    absolute_path = (current_root / filename).resolve()
                    try:
                        stat = absolute_path.stat()
                    except OSError as exc:
                        errors.append(f'{absolute_path}: {exc}')
                        continue
                    snapshot[absolute_path.relative_to(vault_dir).as_posix()] = (stat.st_mtime, stat.st_size)
        except OSError as exc:
            return None, str(exc)
        if errors:
            return None, errors[0]
        return snapshot, None

    def _refresh_rendered(
        self,
        relative_paths: list[str],
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        rebuild_started_at: float = 0.0,
        history_entry: dict[str, object] | None = None,
        vector_enabled: bool = False,
        model_ready: bool = False,
        eta_tracker: BuildEtaTracker | None = None,
    ) -> int:
        block_lookup = self.store.fetch_block_lookup()
        payloads: list[tuple[str, str]] = []
        total_rows = self.store.count_render_rows(relative_paths)
        stage_started_at = time.time()
        last_emit_at = 0.0

        if total_rows > 0:
            eta_seconds, overall_percent = (eta_tracker.estimate(
                stage='rendering',
                current=0,
                total=total_rows,
                elapsed_total=max(time.time() - rebuild_started_at, 0.1),
                stage_elapsed=0.0,
                parsed_chunks=total_rows,
                estimated_total_chunks=total_rows,
            ) if eta_tracker is not None else estimate_remaining_build_seconds(
                self.config,
                stage='rendering',
                current=0,
                total=total_rows,
                elapsed_total=max(time.time() - rebuild_started_at, 0.1),
                stage_elapsed=0.0,
                parsed_chunks=total_rows,
                estimated_total_chunks=total_rows,
                history_entry=history_entry,
                vector_enabled=vector_enabled,
                model_ready=model_ready,
            ))
            _emit_progress(
                on_progress,
                {
                    'stage': 'rendering',
                    'current': 0,
                    'total': total_rows,
                    'eta_seconds': eta_seconds,
                    'overall_percent': overall_percent,
                },
            )

        for index, row in enumerate(self.store.iter_render_rows(relative_paths), start=1):
            _wait_for_worker_controls(pause_event, cancel_event)
            payloads.append((row['chunk_id'], _render_row(row, block_lookup)))
            if len(payloads) >= RENDER_UPDATE_BATCH_SIZE:
                self.store.update_rendered_chunks(payloads)
                payloads = []
            now = time.time()
            if index == total_rows or (now - last_emit_at) >= REBUILD_PROGRESS_EMIT_INTERVAL_SECONDS:
                last_emit_at = now
                eta_seconds, overall_percent = (eta_tracker.estimate(
                    stage='rendering',
                    current=index,
                    total=total_rows,
                    elapsed_total=max(now - rebuild_started_at, 0.1),
                    stage_elapsed=max(now - stage_started_at, 0.0),
                    parsed_chunks=total_rows,
                    estimated_total_chunks=total_rows,
                    timestamp=now,
                ) if eta_tracker is not None else estimate_remaining_build_seconds(
                    self.config,
                    stage='rendering',
                    current=index,
                    total=total_rows,
                    elapsed_total=max(now - rebuild_started_at, 0.1),
                    stage_elapsed=max(now - stage_started_at, 0.0),
                    parsed_chunks=total_rows,
                    estimated_total_chunks=total_rows,
                    history_entry=history_entry,
                    vector_enabled=vector_enabled,
                    model_ready=model_ready,
                ))
                _emit_progress(
                    on_progress,
                    {
                        'stage': 'rendering',
                        'current': index,
                        'total': total_rows,
                        'eta_seconds': eta_seconds,
                        'overall_percent': overall_percent,
                    },
                )
        if payloads:
            self.store.update_rendered_chunks(payloads)
        return total_rows

    def _record_build_history(
        self,
        *,
        files: int,
        chunks: int,
        refs: int,
        indexing_seconds: float,
        rendering_seconds: float,
        vectorizing_seconds: float,
        vector_tail_seconds_per_chunk: float,
        resolved_device: str,
        total_seconds: float,
        vector_prepare_seconds: float = 0.0,
        vector_write_seconds: float = 0.0,
        vector_write_flush_count: int = 0,
    ) -> None:
        append_build_history(
            self._build_history_file,
            {
                'recorded_at': _utc_now(),
                'vault_path': str(self.config.vault_dir),
                'vector_backend': self.config.vector_backend,
                'vector_model': self.config.vector_model,
                'vector_runtime': self.config.vector_runtime,
                'resolved_device': resolved_device,
                'files': int(files),
                'chunks': int(chunks),
                'refs': int(refs),
                'indexing_seconds': float(indexing_seconds),
                'rendering_seconds': float(rendering_seconds),
                'vectorizing_seconds': float(vectorizing_seconds),
                'vector_tail_seconds_per_chunk': float(vector_tail_seconds_per_chunk),
                'vector_prepare_seconds': float(vector_prepare_seconds),
                'vector_write_seconds': float(vector_write_seconds),
                'vector_write_flush_count': int(vector_write_flush_count),
                'vector_load_seconds': 0.0,
                'total_seconds': float(total_seconds),
            },
        )

    def _build_file_manifest(self, files: list[Path]) -> dict[str, dict[str, float | int]]:
        manifest: dict[str, dict[str, float | int]] = {}
        for path in files:
            try:
                stat = path.stat()
            except OSError:
                continue
            manifest[path.relative_to(self.config.vault_dir).as_posix()] = {'mtime': stat.st_mtime, 'size': stat.st_size}
        return manifest

    def _start_fresh_rebuild_state(self, manifest: dict[str, dict[str, float | int]]) -> dict[str, object]:
        state = {
            'version': REBUILD_STATE_VERSION,
            'vault_path': str(self.config.vault_dir),
            'vector_backend': self.config.vector_backend,
            'vector_model': self.config.vector_model,
            'vector_runtime': self.config.vector_runtime,
            'vector_device': self.config.vector_device,
            'started_at': _utc_now(),
            'updated_at': _utc_now(),
            'phase': 'indexing',
            'total_files': len(manifest),
            'file_manifest': manifest,
            'completed_paths': [],
            'readable_paths': [],
            'skipped_paths': [],
            'duplicate_block_ids': 0,
            'parsed_chunk_count': 0,
            'current_path': '',
        }
        self._write_rebuild_state(state)
        return state

    def _can_resume_rebuild_state(self, state: dict[str, object], manifest: dict[str, dict[str, float | int]]) -> bool:
        if not state:
            return False
        if int(state.get('version', 0) or 0) != REBUILD_STATE_VERSION:
            return False
        if state.get('vault_path') != str(self.config.vault_dir):
            return False
        for key, expected in (
            ('vector_backend', self.config.vector_backend),
            ('vector_model', self.config.vector_model),
            ('vector_runtime', self.config.vector_runtime),
            ('vector_device', self.config.vector_device),
        ):
            if (state.get(key) or '') != (expected or ''):
                return False
        return state.get('file_manifest') == manifest

    def _read_rebuild_state(self) -> dict[str, object] | None:
        if not self._rebuild_state_file.exists():
            return None
        try:
            return json.loads(self._rebuild_state_file.read_text(encoding='utf-8'))
        except Exception:
            return None

    def _write_rebuild_state(self, state: dict[str, object]) -> None:
        _write_json_atomic(self._rebuild_state_file, state)

    def _default_watch_state(self) -> dict[str, object]:
        return {
            'version': WATCH_STATE_VERSION,
            'vault_path': str(self.config.vault_dir),
            'updated_at': _utc_now(),
            'vault_offline': False,
            'vault_offline_reason': '',
            'dirty_paths': [],
            'dirty_vector_paths': [],
            'dirty_vector_chunk_ids': [],
        }

    def _read_watch_state(self) -> dict[str, object] | None:
        if not self._watch_state_file.exists():
            return None
        try:
            state = json.loads(self._watch_state_file.read_text(encoding='utf-8'))
        except Exception:
            return None
        if int(state.get('version', 0) or 0) != WATCH_STATE_VERSION:
            return None
        if state.get('vault_path') != str(self.config.vault_dir):
            return None
        return state

    def _write_watch_state(self, state: dict[str, object]) -> None:
        normalized = self._default_watch_state()
        normalized.update(state)
        normalized['updated_at'] = _utc_now()
        normalized['dirty_paths'] = _merge_relative_paths(normalized.get('dirty_paths', []))
        normalized['dirty_vector_paths'] = _merge_relative_paths(normalized.get('dirty_vector_paths', []))
        normalized['dirty_vector_chunk_ids'] = sorted({item for item in normalized.get('dirty_vector_chunk_ids', []) if item})
        normalized['vault_offline'] = bool(normalized.get('vault_offline'))
        normalized['vault_offline_reason'] = str(normalized.get('vault_offline_reason') or '')
        if (
            not normalized['vault_offline']
            and not normalized['dirty_paths']
            and not normalized['dirty_vector_paths']
            and not normalized['dirty_vector_chunk_ids']
        ):
            self._clear_watch_state()
            return
        _write_json_atomic(self._watch_state_file, normalized)

    def _clear_watch_state(self) -> None:
        if self._watch_state_file.exists():
            try:
                self._watch_state_file.unlink()
            except OSError:
                pass

    def _update_watch_state(
        self,
        *,
        add_paths: list[str] | None = None,
        remove_paths: list[str] | None = None,
        add_vector_paths: list[str] | None = None,
        remove_vector_paths: list[str] | None = None,
        add_vector_chunk_ids: list[str] | None = None,
        remove_vector_chunk_ids: list[str] | None = None,
        vault_offline: bool | None = None,
        offline_reason: str | None = None,
    ) -> None:
        state = self._read_watch_state() or self._default_watch_state()
        dirty_paths = set(state.get('dirty_paths', []))
        dirty_paths.update(item for item in add_paths or [] if item)
        dirty_paths.difference_update(item for item in remove_paths or [] if item)
        state['dirty_paths'] = sorted(dirty_paths)

        dirty_vector_paths = set(state.get('dirty_vector_paths', []))
        dirty_vector_paths.update(item for item in add_vector_paths or [] if item)
        dirty_vector_paths.difference_update(item for item in remove_vector_paths or [] if item)
        state['dirty_vector_paths'] = sorted(dirty_vector_paths)

        dirty_vector_chunk_ids = set(state.get('dirty_vector_chunk_ids', []))
        dirty_vector_chunk_ids.update(item for item in add_vector_chunk_ids or [] if item)
        dirty_vector_chunk_ids.difference_update(item for item in remove_vector_chunk_ids or [] if item)
        state['dirty_vector_chunk_ids'] = sorted(dirty_vector_chunk_ids)

        if vault_offline is not None:
            state['vault_offline'] = vault_offline
        if offline_reason is not None:
            state['vault_offline_reason'] = offline_reason
        self._write_watch_state(state)

    def _clear_rebuild_state(self) -> None:
        if self._rebuild_state_file.exists():
            try:
                self._rebuild_state_file.unlink()
            except OSError:
                pass


class _LiveWatchBuffer:
    def __init__(self, stable_seconds: float, delete_confirm_seconds: float) -> None:
        self.stable_seconds = max(float(stable_seconds), 0.0)
        self.delete_confirm_seconds = max(float(delete_confirm_seconds), 0.0)
        self._changed: dict[str, dict[str, object]] = {}
        self._deleted: dict[str, float] = {}

    def record(
        self,
        changed: list[str],
        deleted: list[str],
        snapshot: dict[str, tuple[float, int]],
        now: float | None = None,
    ) -> None:
        recorded_at = time.time() if now is None else now
        for relative_path in changed:
            metadata = snapshot.get(relative_path)
            if metadata is None:
                self._deleted.setdefault(relative_path, recorded_at)
                self._changed.pop(relative_path, None)
                continue
            existing = self._changed.get(relative_path)
            if existing is None or existing.get('metadata') != metadata:
                self._changed[relative_path] = {'first_seen': recorded_at, 'metadata': metadata}
            self._deleted.pop(relative_path, None)
        for relative_path in deleted:
            if relative_path in snapshot:
                continue
            self._deleted.setdefault(relative_path, recorded_at)
            self._changed.pop(relative_path, None)

    def pop_ready(self, snapshot: dict[str, tuple[float, int]], now: float | None = None) -> tuple[list[str], list[str]]:
        current_time = time.time() if now is None else now
        ready_changed: list[str] = []
        ready_deleted: list[str] = []

        for relative_path, payload in list(self._changed.items()):
            metadata = snapshot.get(relative_path)
            if metadata is None:
                self._deleted.setdefault(relative_path, float(payload.get('first_seen', current_time)))
                self._changed.pop(relative_path, None)
                continue
            if payload.get('metadata') != metadata:
                self._changed[relative_path] = {'first_seen': current_time, 'metadata': metadata}
                continue
            if current_time - float(payload.get('first_seen', current_time)) >= self.stable_seconds:
                ready_changed.append(relative_path)
                self._changed.pop(relative_path, None)

        for relative_path, first_seen in list(self._deleted.items()):
            if relative_path in snapshot:
                self._deleted.pop(relative_path, None)
                continue
            if current_time - float(first_seen) >= self.delete_confirm_seconds:
                ready_deleted.append(relative_path)
                self._deleted.pop(relative_path, None)

        ready_deleted_set = set(ready_deleted)
        ready_changed = sorted(path for path in ready_changed if path not in ready_deleted_set)
        return ready_changed, sorted(ready_deleted)

    def requeue(
        self,
        changed: list[str],
        deleted: list[str],
        snapshot: dict[str, tuple[float, int]],
        now: float | None = None,
    ) -> None:
        recorded_at = time.time() if now is None else now
        for relative_path in changed:
            metadata = snapshot.get(relative_path)
            if metadata is None:
                continue
            self._changed[relative_path] = {'first_seen': recorded_at, 'metadata': metadata}
            self._deleted.pop(relative_path, None)
        for relative_path in deleted:
            if relative_path in snapshot:
                continue
            self._deleted[relative_path] = recorded_at
            self._changed.pop(relative_path, None)


class _VaultEventHandler(FileSystemEventHandler):
    def __init__(self, vault_dir: Path, ignore_dirs: set[str]) -> None:
        super().__init__()
        self.vault_dir = vault_dir.resolve()
        self.ignore_dirs = ignore_dirs
        self._changed: dict[str, float] = {}
        self._deleted: dict[str, float] = {}
        self._lock = threading.Lock()

    def on_any_event(self, event: FileSystemEvent) -> None:
        if getattr(event, 'is_directory', False):
            return
        src_rel = self._to_relative_path(Path(event.src_path))
        dest_rel = self._to_relative_path(Path(getattr(event, 'dest_path', ''))) if getattr(event, 'dest_path', None) else None

        with self._lock:
            if src_rel:
                self._record_event(src_rel, getattr(event, 'event_type', 'modified'))
            if dest_rel:
                self._record_event(dest_rel, 'created')

    def pop_due_changes(self, debounce_seconds: float) -> tuple[list[str], list[str]]:
        deadline = time.time() - debounce_seconds
        with self._lock:
            changed = sorted(path for path, ts in self._changed.items() if ts <= deadline)
            deleted = sorted(path for path, ts in self._deleted.items() if ts <= deadline)
            for path in changed:
                self._changed.pop(path, None)
            for path in deleted:
                self._deleted.pop(path, None)
        changed = [path for path in changed if path not in deleted]
        return changed, deleted

    def _record_event(self, relative_path: str, event_type: str) -> None:
        if event_type in {'deleted', 'moved'}:
            self._deleted[relative_path] = time.time()
            self._changed.pop(relative_path, None)
            return
        self._changed[relative_path] = time.time()
        self._deleted.pop(relative_path, None)

    def _to_relative_path(self, path: Path) -> str | None:
        if not path:
            return None
        try:
            resolved = path.resolve()
        except OSError:
            return None
        if resolved.suffix.lower() != '.md':
            return None
        try:
            relative = resolved.relative_to(self.vault_dir).as_posix()
        except ValueError:
            return None
        if any(part in self.ignore_dirs for part in Path(relative).parts):
            return None
        return relative


def _render_row(row, block_lookup) -> str:
    chunk_properties = json.loads(row['properties_json'] or '{}')
    raw_text = row['raw_text'] or ''
    block_id = row['block_id']
    visited = {block_id} if block_id else set()
    expanded = _expand_refs(raw_text, block_lookup, depth=0, visited=visited)
    expanded = _normalize_markup(expanded)

    sections = [row['title']]
    if row['anchor'] and row['anchor'] != row['title']:
        sections.append(row['anchor'])
    if chunk_properties:
        sections.append(_format_properties(chunk_properties))
    if expanded:
        sections.append(expanded)
    return '\n'.join(section.strip() for section in sections if section and section.strip())


def _expand_refs(text: str, block_lookup, depth: int, visited: set[str]) -> str:
    if depth >= MAX_RENDER_DEPTH:
        return _truncate(text)

    def replace_embed(match: re.Match[str]) -> str:
        return _resolve_block_ref(match.group(1), block_lookup, depth + 1, visited, embed=True)

    def replace_block(match: re.Match[str]) -> str:
        return _resolve_block_ref(match.group(1), block_lookup, depth + 1, visited, embed=False)

    text = EMBED_RE.sub(replace_embed, text)
    text = BLOCK_REF_RE.sub(replace_block, text)
    return text


def _resolve_block_ref(block_id: str, block_lookup, depth: int, visited: set[str], embed: bool) -> str:
    if block_id in visited:
        return f'[循环引用:{block_id}]'
    target = block_lookup.get(block_id)
    if target is None:
        return f'[缺失引用:{block_id}]'
    next_visited = set(visited)
    next_visited.add(block_id)
    target_text = _expand_refs(target['raw_text'] or '', block_lookup, depth, next_visited)
    target_text = _normalize_markup(target_text)
    prefix = target['anchor'] or target['title']
    merged = f'{prefix}: {target_text}'.strip(': ')
    limit = MAX_EXPANDED_LENGTH if embed else MAX_EXPANDED_LENGTH // 2
    return _truncate(merged, limit=limit)


def _normalize_markup(text: str) -> str:
    normalized = PAGE_REF_RE.sub(r'\1', text)
    normalized = IMAGE_RE.sub(lambda match: match.group(1) or Path(match.group(2)).name, normalized)
    normalized = LINK_RE.sub(r'\1', normalized)
    normalized = TAG_RE.sub(r'\1', normalized)
    normalized = re.sub(r'\s+', ' ', normalized)
    return normalized.strip()


def _format_properties(properties: dict[str, str]) -> str:
    return '; '.join(f"{key}: {_normalize_markup(value)}" for key, value in properties.items())


def _truncate(text: str, limit: int = MAX_EXPANDED_LENGTH) -> str:
    normalized = re.sub(r'\s+', ' ', text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + '…'


def _score_query(query_text: str, title: str, anchor: str, rendered_text: str) -> float:
    normalized_query = query_text.strip().lower()
    if not normalized_query:
        return 0.0
    score = 0.0
    title_lower = title.lower()
    anchor_lower = anchor.lower()
    text_lower = rendered_text.lower()
    terms = _tokenize_query(normalized_query)

    if normalized_query in title_lower:
        score += 64
    if normalized_query in anchor_lower:
        score += 52
    if normalized_query in text_lower:
        score += 28

    matched_terms = 0
    for term in terms:
        if len(term) == 1 and term.isascii():
            continue
        term_matched = False
        if term in title_lower:
            score += 16
            term_matched = True
        if term in anchor_lower:
            score += 12
            term_matched = True
        count = min(text_lower.count(term), 6)
        if count:
            score += count * 4.5
            term_matched = True
        if term_matched:
            matched_terms += 1

    if terms:
        coverage = matched_terms / len(terms)
        if coverage >= 1.0:
            score += 10.0
        elif coverage >= 0.66:
            score += 4.0
        else:
            score -= 4.0

    if _contains_cjk(normalized_query) and normalized_query in text_lower:
        score += 10

    return score


def _score_fts_rank(fts_rank: object) -> float:
    if fts_rank is None:
        return 0.0
    try:
        rank = float(fts_rank)
    except (TypeError, ValueError):
        return 0.0
    if rank <= 0:
        return 24.0
    return 24.0 / (1.0 + rank)


def _contains_cjk(text: str) -> bool:
    return any('\u4e00' <= char <= '\u9fff' for char in text)


def _tokenize_query(query_text: str) -> list[str]:
    terms = [term.lower() for term in QUERY_TERM_RE.findall(query_text.strip()) if term.strip()]
    return terms or ([query_text.strip().lower()] if query_text.strip() else [])


def _query_coverage(query_text: str, title: str, anchor: str, rendered_text: str) -> float:
    terms = _tokenize_query(query_text)
    if not terms:
        return 0.0
    combined = f"{title}\n{anchor}\n{rendered_text}".lower()
    matched = sum(1 for term in terms if term in combined)
    return matched / len(terms)


def _length_penalty(rendered_text: str, coverage: float) -> float:
    normalized_length = len(re.sub(r'\s+', ' ', rendered_text).strip())
    overflow = max(normalized_length - 640, 0)
    if overflow <= 0:
        return 0.0
    base_penalty = min(overflow / 220.0, 12.0)
    if coverage >= 1.0:
        return base_penalty * 0.25
    if coverage >= 0.66:
        return base_penalty * 0.5
    return base_penalty


def _normalize_score(raw_score: float) -> float:
    return max(0.0, min(float(raw_score), 100.0))


def _is_short_query(query_text: str) -> bool:
    stripped = query_text.strip()
    terms = _tokenize_query(stripped)
    return len(terms) <= 1 and len(stripped) <= 4


def _should_use_vector_search(query_text: str) -> bool:
    stripped = query_text.strip()
    if not stripped:
        return False
    if len(stripped) <= 1:
        return False
    return True


def _candidate_limit_for_query(query_text: str, limit: int) -> int:
    base = max(int(limit or 0), 1)
    stripped = query_text.strip()
    if not stripped:
        return max(base * 8, 64)
    if len(stripped) <= 1:
        return max(base * 24, 240)
    if _is_short_query(query_text):
        return max(base * 16, 160)
    return max(base * 10, 96)


def _hydration_pool_size(query_text: str, limit: int) -> int:
    base = max(int(limit or 0), 1)
    stripped = query_text.strip()
    if len(stripped) <= 1:
        return max(base * 8, 48)
    if _is_short_query(query_text):
        return max(base * 6, 36)
    return max(base * 4, 24)


def _compile_page_blocklist_patterns(raw_rules: str) -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for raw_line in (raw_rules or '').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        enabled = True
        rule = line
        if '\t' in line:
            flag, rest = line.split('\t', 1)
            if flag in {'0', '1'}:
                enabled = flag == '1'
                rule = rest.strip()
        if not enabled or not rule:
            continue
        try:
            patterns.append(re.compile(rule, re.IGNORECASE))
        except re.error:
            continue
    return patterns


def _page_matches_blocklist(title: str, source_path: str, patterns: list[re.Pattern[str]]) -> bool:
    if not patterns:
        return False
    title_text = str(title or '')
    source_text = str(source_path or '')
    return any(pattern.search(title_text) or pattern.search(source_text) for pattern in patterns)


def _filter_candidate_rows_by_page_blocklist(rows, patterns: list[re.Pattern[str]]):
    if not patterns:
        return list(rows)
    return [row for row in rows if not _page_matches_blocklist(row['title'], row['source_path'], patterns)]



def _semantic_only_score(vector_similarity: float) -> float:
    similarity = max(float(vector_similarity or 0.0), 0.0)
    if similarity <= 0.0:
        return 0.0
    if similarity <= 0.15:
        return min(10.0 + similarity * 12.0, 12.0)
    return min(12.0 + (similarity - 0.15) * 60.0, 40.0)


def _build_hit_reason(
    query_text: str,
    title: str,
    anchor: str,
    rendered_text: str,
    fts_rank: object,
    like_hits: object,
    vector_score: float,
) -> str:
    normalized_query = query_text.strip().lower()
    title_lower = title.lower()
    anchor_lower = anchor.lower()
    text_lower = rendered_text.lower()
    reasons: list[str] = []
    if normalized_query and normalized_query in title_lower:
        reasons.append('标题直达')
    elif normalized_query and normalized_query in anchor_lower:
        reasons.append('语义路径直达')
    if normalized_query and normalized_query in text_lower:
        reasons.append('正文命中')
    if fts_rank is not None:
        reasons.append('全文检索')
    if float(like_hits or 0) > 0:
        reasons.append('关键词匹配')
    if vector_score > 0.15:
        reasons.append('语义相似')
    if not reasons:
        reasons.append('综合相关')
    return ' + '.join(dict.fromkeys(reasons))


def _preview_source_text(rendered_text: str) -> str:
    return ' '.join(line.strip() for line in rendered_text.splitlines() if line.strip())


def _build_preview_text(query_text: str, rendered_text: str, limit: int = 220) -> str:
    source = _preview_source_text(rendered_text)
    if not source:
        return ''
    lowered = source.lower()
    positions = [lowered.find(term) for term in _tokenize_query(query_text) if term and lowered.find(term) >= 0]
    if not positions:
        return _truncate(source, limit=limit)
    start = max(min(positions) - 48, 0)
    end = min(start + limit, len(source))
    snippet = source[start:end].strip()
    if start > 0:
        snippet = '…' + snippet
    if end < len(source):
        snippet = snippet.rstrip() + '…'
    return snippet


def _build_display_text(vault_dir: Path, row, block_lookup, chunk_lookup, file_cache: dict[str, list[str]], config: AppConfig) -> str:
    if row['kind'] == 'logseq_block':
        visited = {row['block_id']} if row['block_id'] else set()
        lines = _build_logseq_tree_lines(vault_dir, row, block_lookup, chunk_lookup, file_cache, config, visited, include_ancestors=True)
    elif row['kind'] == 'md_section':
        source_lines = _load_source_range(vault_dir, row['source_path'], row['line_start'], row['line_end'], file_cache)
        lines = _render_source_lines(vault_dir, source_lines, block_lookup, chunk_lookup, file_cache, config, set())
    else:
        fallback = _apply_output_redaction(_normalize_markup(row['rendered_text'] or row['title']), config).strip()
        return fallback
    cleaned = _trim_blank_lines(lines)
    return '\n'.join(cleaned).strip()


def _build_logseq_tree_lines(
    vault_dir: Path,
    row,
    block_lookup,
    chunk_lookup,
    file_cache: dict[str, list[str]],
    config: AppConfig,
    visited: set[str],
    *,
    include_ancestors: bool,
) -> list[str]:
    subtree_lines = _load_source_range(vault_dir, row['source_path'], row['line_start'], row['line_end'], file_cache)
    rendered = _render_source_lines(vault_dir, subtree_lines, block_lookup, chunk_lookup, file_cache, config, visited)
    if not include_ancestors:
        return rendered
    ancestors = _collect_ancestor_lines(vault_dir, row, chunk_lookup, block_lookup, file_cache, config, visited)
    return ancestors + rendered


def _collect_ancestor_lines(vault_dir: Path, row, chunk_lookup, block_lookup, file_cache: dict[str, list[str]], config: AppConfig, visited: set[str]) -> list[str]:
    parent_chunk_id = row['parent_chunk_id']
    if parent_chunk_id:
        chain = []
        seen: set[str] = set()
        current = parent_chunk_id
        while current and current not in seen:
            seen.add(current)
            parent = chunk_lookup.get(current)
            if parent is None:
                break
            chain.append(parent)
            current = parent['parent_chunk_id']
        chain.reverse()
        lines: list[str] = []
        for parent in chain:
            source_line = _load_source_line(vault_dir, parent['source_path'], parent['line_start'], file_cache)
            rendered = _render_source_lines(vault_dir, [source_line], block_lookup, chunk_lookup, file_cache, config, visited)
            if rendered:
                lines.append(rendered[0])
        return lines

    parts = [part.strip() for part in str(row['anchor'] or '').split(' > ') if part.strip()]
    return [f"{'  ' * index}- {part}" for index, part in enumerate(parts[:-1])]


def _render_source_lines(
    vault_dir: Path,
    lines: list[str],
    block_lookup,
    chunk_lookup,
    file_cache: dict[str, list[str]],
    config: AppConfig,
    visited: set[str],
) -> list[str]:
    rendered: list[str] = []
    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip():
            rendered.append('')
            continue

        expanded = line.expandtabs(4)
        property_match = PROPERTY_RE.match(expanded)
        if property_match and _should_skip_property(property_match.group('key').strip()):
            continue

        bullet_match = BULLET_RE.match(expanded)
        if bullet_match:
            indent = bullet_match.group('indent')
            value = bullet_match.group('value').strip()
            embed_target = _match_embed_only(value)
            if embed_target:
                embed_lines = _render_embedded_block(vault_dir, embed_target, block_lookup, chunk_lookup, file_cache, config, visited)
                if embed_lines:
                    rendered.extend(_indent_lines(_normalize_indentation(embed_lines), len(indent)))
                continue
            block_target = _match_block_ref_only(value)
            if block_target:
                replacement = _resolve_block_ref_inline(vault_dir, block_target, block_lookup, chunk_lookup, file_cache, config, visited)
                rendered.append(f"{indent}- {replacement}".rstrip())
                continue
            processed_value = _replace_inline_refs(vault_dir, value, block_lookup, chunk_lookup, file_cache, config, visited)
            rendered.append(f"{indent}- {_apply_output_redaction(processed_value, config)}".rstrip())
            continue

        if property_match:
            indent = property_match.group('indent')
            key = property_match.group('key').strip()
            value = property_match.group('value').strip()
            processed_value = _replace_inline_refs(vault_dir, value, block_lookup, chunk_lookup, file_cache, config, visited)
            rendered.append(f"{indent}{key}:: {_apply_output_redaction(processed_value, config)}".rstrip())
            continue

        embed_target = _match_embed_only(line.strip())
        if embed_target:
            rendered.extend(_render_embedded_block(vault_dir, embed_target, block_lookup, chunk_lookup, file_cache, config, visited))
            continue

        processed_line = _replace_inline_refs(vault_dir, line, block_lookup, chunk_lookup, file_cache, config, visited)
        rendered.append(_apply_output_redaction(processed_line, config).rstrip())

    return _trim_blank_lines(rendered)


def _render_embedded_block(vault_dir: Path, block_id: str, block_lookup, chunk_lookup, file_cache: dict[str, list[str]], config: AppConfig, visited: set[str]) -> list[str]:
    if block_id in visited:
        return [f'- [循环引用]']
    target = block_lookup.get(block_id)
    if target is None:
        return [f'- [缺失引用]']
    next_visited = set(visited)
    next_visited.add(block_id)
    return _build_logseq_tree_lines(vault_dir, target, block_lookup, chunk_lookup, file_cache, config, next_visited, include_ancestors=True)


def _replace_inline_refs(vault_dir: Path, text: str, block_lookup, chunk_lookup, file_cache: dict[str, list[str]], config: AppConfig, visited: set[str]) -> str:
    replaced = PAGE_REF_RE.sub(r'\1', text)
    replaced = EMBED_RE.sub(lambda match: _resolve_block_ref_inline(vault_dir, match.group(1), block_lookup, chunk_lookup, file_cache, config, visited), replaced)
    replaced = BLOCK_REF_RE.sub(lambda match: _resolve_block_ref_inline(vault_dir, match.group(1), block_lookup, chunk_lookup, file_cache, config, visited), replaced)
    return replaced


def _resolve_block_ref_inline(vault_dir: Path, block_id: str, block_lookup, chunk_lookup, file_cache: dict[str, list[str]], config: AppConfig, visited: set[str]) -> str:
    if block_id in visited:
        return '[循环引用]'
    target = block_lookup.get(block_id)
    if target is None:
        return '[缺失引用]'
    next_visited = set(visited)
    next_visited.add(block_id)
    source_line = _load_source_line(vault_dir, target['source_path'], target['line_start'], file_cache)
    expanded = source_line.expandtabs(4)
    bullet_match = BULLET_RE.match(expanded)
    property_match = PROPERTY_RE.match(expanded)
    if bullet_match:
        candidate = bullet_match.group('value').strip()
    elif property_match:
        candidate = property_match.group('value').strip()
    else:
        candidate = source_line.strip()
    candidate = PAGE_REF_RE.sub(r'\1', candidate)
    candidate = EMBED_RE.sub(lambda match: _resolve_block_ref_inline(vault_dir, match.group(1), block_lookup, chunk_lookup, file_cache, config, next_visited), candidate)
    candidate = BLOCK_REF_RE.sub(lambda match: _resolve_block_ref_inline(vault_dir, match.group(1), block_lookup, chunk_lookup, file_cache, config, next_visited), candidate)
    return _apply_output_redaction(candidate, config).strip() or _normalize_markup(target['anchor'] or target['title'])


def _match_embed_only(text: str) -> str | None:
    match = EMBED_RE.fullmatch(text.strip())
    return match.group(1) if match else None


def _match_block_ref_only(text: str) -> str | None:
    match = BLOCK_REF_RE.fullmatch(text.strip())
    return match.group(1) if match else None


def _should_skip_property(key: str) -> bool:
    return key.strip().lower() in LOGSEQ_HIDDEN_PROPERTIES


def _load_source_lines(vault_dir: Path, source_path: str, file_cache: dict[str, list[str]]) -> list[str]:
    cached = file_cache.get(source_path)
    if cached is not None:
        return cached
    absolute_path = (vault_dir / source_path).resolve()
    try:
        lines = absolute_path.read_text(encoding='utf-8', errors='ignore').splitlines()
    except OSError:
        lines = []
    file_cache[source_path] = lines
    return lines


def _load_source_line(vault_dir: Path, source_path: str, line_number: int, file_cache: dict[str, list[str]]) -> str:
    lines = _load_source_lines(vault_dir, source_path, file_cache)
    index = max(line_number - 1, 0)
    if index >= len(lines):
        return ''
    return lines[index]


def _load_source_range(vault_dir: Path, source_path: str, line_start: int, line_end: int, file_cache: dict[str, list[str]]) -> list[str]:
    lines = _load_source_lines(vault_dir, source_path, file_cache)
    start = max(line_start - 1, 0)
    end = max(line_end, line_start)
    return lines[start:end]


def _normalize_indentation(lines: list[str]) -> list[str]:
    expanded = [line.expandtabs(4).rstrip() for line in lines]
    indents = [len(line) - len(line.lstrip(' ')) for line in expanded if line.strip()]
    if not indents:
        return expanded
    base_indent = min(indents)
    normalized: list[str] = []
    for line in expanded:
        if not line.strip():
            normalized.append('')
            continue
        normalized.append(line[base_indent:])
    return normalized


def _indent_lines(lines: list[str], indent: int) -> list[str]:
    prefix = ' ' * max(indent, 0)
    return [f'{prefix}{line}' if line else '' for line in lines]


def _trim_blank_lines(lines: list[str]) -> list[str]:
    trimmed = list(lines)
    while trimmed and not trimmed[0].strip():
        trimmed.pop(0)
    while trimmed and not trimmed[-1].strip():
        trimmed.pop()
    return trimmed


def _apply_output_redaction(text: str, config: AppConfig) -> str:
    redacted = text
    if getattr(config, 'rag_filter_core_enabled', True):
        for pattern in LABELED_SECRET_PATTERNS:
            redacted = pattern.sub(lambda match: f"{match.group('label')}{match.group('sep')}{SENSITIVE_PLACEHOLDER}", redacted)
        for pattern in RAW_SECRET_PATTERNS:
            redacted = pattern.sub(SENSITIVE_PLACEHOLDER, redacted)
    if getattr(config, 'rag_filter_extended_enabled', False):
        for pattern in EXTENDED_REDACTION_PATTERNS:
            redacted = pattern.sub(SENSITIVE_PLACEHOLDER, redacted)
    custom_rules = getattr(config, 'rag_filter_custom_rules', '') or ''
    for raw_rule in re.split(r'[\n,]+', custom_rules):
        rule = raw_rule.strip()
        if not rule:
            continue
        if len(rule) >= 2 and rule.startswith('/') and rule.endswith('/'):
            try:
                redacted = re.sub(rule[1:-1], SENSITIVE_PLACEHOLDER, redacted)
            except re.error:
                continue
            continue
        redacted = redacted.replace(rule, SENSITIVE_PLACEHOLDER)
    return redacted


def _fragment_is_covered(fragment: str, other: str) -> bool:
    fragment_lines = [line.rstrip() for line in fragment.splitlines() if line.strip()]
    other_lines = [line.rstrip() for line in other.splitlines() if line.strip()]
    if not fragment_lines or len(fragment_lines) > len(other_lines):
        return False
    start = 0
    for line in fragment_lines:
        matched = False
        for index in range(start, len(other_lines)):
            if other_lines[index] == line:
                start = index + 1
                matched = True
                break
        if not matched:
            return False
    return True


def _collect_context_fragments(page_hits: list[SearchHit]) -> list[str]:
    grouped: dict[tuple[str, ...], list[tuple[SearchHit, str]]] = {}
    ordered_keys: list[tuple[str, ...]] = []
    for hit in page_hits:
        fragment = (hit.display_text or hit.rendered_text).strip()
        if not fragment or fragment in {'-', '- '}:
            continue
        key = _context_sibling_group_key(hit, fragment)
        if key not in grouped:
            grouped[key] = []
            ordered_keys.append(key)
        grouped[key].append((hit, fragment))

    merged_fragments: list[str] = []
    for key in ordered_keys:
        merged = _merge_context_group(grouped[key])
        if merged:
            merged_fragments.append(merged)

    deduped_fragments: list[str] = []
    for fragment in merged_fragments:
        skip_fragment = False
        for index, existing in enumerate(deduped_fragments):
            if fragment == existing or fragment in existing or _fragment_is_covered(fragment, existing):
                skip_fragment = True
                break
            if existing in fragment or _fragment_is_covered(existing, fragment):
                deduped_fragments[index] = fragment
                skip_fragment = True
                break
        if not skip_fragment:
            deduped_fragments.append(fragment)

    return [
        fragment
        for fragment in deduped_fragments
        if not any(fragment != other and _fragment_is_covered(fragment, other) for other in deduped_fragments)
    ]


def _context_sibling_group_key(hit: SearchHit, fragment: str) -> tuple[str, ...]:
    parts = _context_anchor_parts(hit.anchor)
    if len(parts) >= 2:
        parent_parts = [_normalize_anchor_segment(part) for part in parts[:-1] if _normalize_anchor_segment(part)]
        if parent_parts:
            return ('anchor-parent', hit.source_path, *parent_parts[-4:])
    fragment_lines = [line.rstrip() for line in fragment.splitlines() if line.strip()]
    if len(fragment_lines) >= 2:
        return ('line-parent', hit.source_path, *_normalize_context_lines(fragment_lines[:-1])[-4:])
    return ('single', hit.source_path, hit.chunk_id)


def _merge_context_group(items: list[tuple[SearchHit, str]]) -> str:
    if not items:
        return ''
    unique_fragments: list[str] = []
    for _hit, fragment in items:
        if any(fragment == existing or fragment in existing for existing in unique_fragments):
            continue
        replaced = False
        for index, existing in enumerate(unique_fragments):
            if existing in fragment:
                unique_fragments[index] = fragment
                replaced = True
                break
        if not replaced:
            unique_fragments.append(fragment)

    if len(unique_fragments) <= 1:
        return unique_fragments[0] if unique_fragments else ''

    longest = max(unique_fragments, key=len)
    if all(fragment == longest or _fragment_is_covered(fragment, longest) for fragment in unique_fragments):
        return longest

    merged = _merge_sibling_fragments(unique_fragments)
    if merged:
        return merged
    return longest


def _merge_sibling_fragments(fragments: list[str]) -> str:
    if len(fragments) <= 1:
        return fragments[0] if fragments else ''
    line_groups = [[line.rstrip() for line in fragment.splitlines() if line.strip()] for fragment in fragments]
    if any(not group for group in line_groups):
        return ''

    common_prefix: list[str] = []
    for candidate_lines in zip(*line_groups):
        head = candidate_lines[0]
        if all(line == head for line in candidate_lines[1:]):
            common_prefix.append(head)
            continue
        break

    if not common_prefix:
        return ''

    merged_lines = list(common_prefix)
    seen_tails: set[tuple[str, ...]] = set()
    for lines in line_groups:
        tail = tuple(lines[len(common_prefix):])
        if not tail or tail in seen_tails:
            continue
        seen_tails.add(tail)
        merged_lines.extend(tail)

    merged_text = '\n'.join(merged_lines).strip()
    if len(merged_text) > 3200:
        return ''
    return merged_text


def _context_anchor_parts(anchor: str) -> list[str]:
    return [part.strip() for part in str(anchor or '').split(' > ') if part.strip()]


def _normalize_anchor_segment(text: str) -> str:
    cleaned = _normalize_markup(text or '')
    cleaned = re.sub(r'\s+', ' ', cleaned).strip().lower()
    return cleaned


def _normalize_context_lines(lines: list[str]) -> list[str]:
    normalized: list[str] = []
    for line in lines:
        cleaned = re.sub(r'\s+', ' ', line.strip()).lower()
        if cleaned:
            normalized.append(cleaned)
    return normalized


def _diff_snapshot(previous: dict[str, tuple[float, int]], current: dict[str, tuple[float, int]]) -> tuple[list[str], list[str]]:
    changed: list[str] = []
    deleted: list[str] = []
    for relative_path, metadata in current.items():
        if relative_path not in previous or previous[relative_path] != metadata:
            changed.append(relative_path)
    for relative_path in previous:
        if relative_path not in current:
            deleted.append(relative_path)
    return changed, deleted


def _merge_relative_paths(*groups) -> list[str]:
    merged: set[str] = set()
    for group in groups:
        for item in group or []:
            if isinstance(item, str) and item:
                merged.add(item)
    return sorted(merged)


def _resolve_context_language(language: str, query_text: str, hits: list[SearchHit]) -> str:
    normalized = str(language or 'zh-CN').strip() or 'zh-CN'
    if normalized.lower().startswith('zh'):
        return normalized
    samples = [query_text] + [hit.title for hit in hits[:3]] + [hit.anchor for hit in hits[:3]]
    if any(re.search(r'[\u4e00-\u9fff]', sample or '') for sample in samples):
        return 'zh-CN'
    return normalized


def _ai_collaboration_lines(language: str) -> list[str]:
    normalized = str(language or 'zh-CN').strip().lower()
    if normalized.startswith('en'):
        return [
            '',
            'AI collaboration mode:',
            '- These are candidate snippets from my local notes.',
            '- Ignore low-relevance fragments directly.',
            '- If the evidence is still insufficient, return only 1-3 more specific retrieval keywords.',
        ]
    return [
        '',
        'AI协作模式：',
        '- 下面是来自我本地笔记库的候选片段。',
        '- 低相关内容请直接忽略。',
        '- 如果上下文仍不足，请只返回 1-3 个更具体的检索关键词。',
    ]


def _clear_directory(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for item in directory.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for child in path.rglob('*'):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total


def _wait_for_worker_controls(pause_event: threading.Event | None, cancel_event: threading.Event | None) -> None:
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise BuildCancelledError('cancelled')
        if pause_event is None or not pause_event.is_set():
            return
        time.sleep(0.12)


def _emit_watch_update(
    on_update: Callable[[dict[str, object]], None] | None,
    mode: str,
    changed: list[str],
    deleted: list[str],
    stats: dict[str, object],
    *,
    events: list[dict[str, object]] | None = None,
    note_only: bool = False,
) -> None:
    if on_update is None:
        return
    on_update(
        {
            'mode': mode,
            'changed': changed,
            'deleted': deleted,
            'stats': stats,
            'events': events or [],
            'note_only': note_only,
        }
    )


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    with tempfile.NamedTemporaryFile('w', delete=False, dir=path.parent, prefix=f'{path.name}.', suffix='.tmp', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        temp_name = handle.name
    temp_path = Path(temp_name)
    last_error: PermissionError | None = None
    for _ in range(5):
        try:
            os.replace(temp_path, path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05)
    try:
        temp_path.unlink(missing_ok=True)
    except OSError:
        pass
    if last_error is not None:
        raise last_error


def _emit_progress(on_progress: Callable[[dict[str, object]], None] | None, payload: dict[str, object]) -> None:
    if on_progress is None:
        return
    on_progress(payload)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
