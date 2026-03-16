from __future__ import annotations

import gc
import hashlib
import itertools
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import traceback
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .app_logging import clear_log_files, configure_file_logging
from .clipboard import copy_text
from .config import AppConfig, DataPaths, normalize_watch_resource_peak_percent
from .errors import BuildCancelledError, RuntimeDependencyError
from .extensions.query import ExtensionQueryBroker, normalize_markdown_hit
from .extensions.registry import ExtensionRegistry
from .extensions.models import ExtensionIndexState
from .models import QueryInsights, QueryResult, RerankOutcome, SearchHit, SpaceEstimate
from .parser import BLOCK_REF_RE, BULLET_RE, EMBED_RE, PAGE_REF_RE, PROPERTY_RE
from .query_runtime import QueryRuntimeAdvisor, select_query_hits
from .retrieval_policy import QueryProfile, build_query_profile, rank_candidates
from .reranker import CrossEncoderReranker, create_reranker, is_local_reranker_ready
from .preflight import estimate_storage_for_vault
from .runtime_recovery import record_runtime_incident
from .storage import MetadataStore, _build_fts_query
from .timing import BuildEtaTracker, append_build_history, build_history_file, estimate_remaining_build_seconds, find_matching_history
from .vector_index import create_vector_index, detect_acceleration, inspect_runtime_environment, is_local_model_ready, prepare_local_model_snapshot, release_process_vector_resources, resolve_vector_device, runtime_dependency_issue
from .runtime_layout import list_pending_runtime_updates, load_runtime_component_registry, runtime_component_registry_path

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
REBUILD_STATE_VERSION = 2
INDEX_STATE_VERSION = 1
REBUILD_PROGRESS_EMIT_INTERVAL_SECONDS = 0.03
REBUILD_PROGRESS_EMIT_ROW_INTERVAL = 1
REBUILD_STATE_CHECKPOINT_SECONDS = 0.8
REBUILD_INDEXING_CHECKPOINT_ROWS = 32
REBUILD_RENDERING_CHECKPOINT_ROWS = 1024
REBUILD_VECTOR_RESUME_REWIND = 512
REBUILD_WATCHDOG_INTERVAL_SECONDS = 5.0
REBUILD_WATCHDOG_STALL_SECONDS = 120.0
REBUILD_WATCHDOG_REPEAT_SECONDS = 60.0
REBUILD_DIAGNOSTIC_MAX_FILES = 12
RENDER_UPDATE_BATCH_SIZE = 1024
VECTOR_UPSERT_BATCH_SIZE = 256
WATCH_READY_BATCH_LIMITS = {
    5: 1,
    10: 2,
    15: 4,
    20: 6,
    30: 8,
    40: 12,
    50: 16,
    60: 24,
    70: 32,
    80: 48,
    90: 64,
}
WATCH_BATCH_COOLDOWNS = {
    5: 1.1,
    10: 0.9,
    15: 0.75,
    20: 0.6,
    30: 0.45,
    40: 0.32,
    50: 0.24,
    60: 0.16,
    70: 0.1,
    80: 0.05,
    90: 0.0,
}
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

LOGGER = logging.getLogger(__name__)


class _LazyBlockLookup:
    def __init__(self, store: MetadataStore) -> None:
        self._store = store
        self._cache: dict[str, object] = {}

    def get(self, block_id: str):
        if not block_id:
            return None
        if block_id not in self._cache:
            self._cache[block_id] = self._store.fetch_block_row(block_id)
        return self._cache[block_id]


class _LazyChunkLookup:
    def __init__(self, store: MetadataStore, initial_rows: dict[str, object] | None = None) -> None:
        self._store = store
        self._cache: dict[str, object] = dict(initial_rows or {})

    def get(self, chunk_id: str):
        if not chunk_id:
            return None
        if chunk_id not in self._cache:
            self._cache[chunk_id] = self._store.fetch_chunk_row(chunk_id)
        return self._cache[chunk_id]


class OmniClipService:
    def __init__(self, config: AppConfig, paths: DataPaths) -> None:
        self.config = config
        self.paths = paths
        configure_file_logging(paths, config)
        self.store = MetadataStore(paths.sqlite_file)
        self.vector_index = create_vector_index(config, paths)
        self._rebuild_state_file = self.paths.state_dir / 'rebuild_state.json'
        self._watch_state_file = self.paths.state_dir / 'watch_state.json'
        self._index_state_file = self.paths.state_dir / 'index_state.json'
        self._build_history_file = build_history_file(self.paths.state_dir)
        self._query_runtime_file = self.paths.state_dir / 'query_runtime.json'
        self._query_runtime_advisor = QueryRuntimeAdvisor(self._query_runtime_file)
        self.reranker = create_reranker(config, paths)
        self.extension_query_broker = ExtensionQueryBroker(config=config, paths=paths)

    def close(self) -> None:
        try:
            release_process_vector_resources(clear_cuda=True, reset_acceleration=False)
        except Exception:
            pass
        try:
            self.extension_query_broker.close()
        except Exception:
            pass
        self.store.close()

    def save_runtime_config(self) -> None:
        payload = asdict(self.config)
        self.paths.config_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    def _watch_peak_percent(self) -> int:
        return normalize_watch_resource_peak_percent(getattr(self.config, 'watch_resource_peak_percent', 15), 15)

    def _watch_batch_limit(self) -> int:
        return WATCH_READY_BATCH_LIMITS.get(self._watch_peak_percent(), 16)

    def _watch_post_batch_cooldown(self) -> float:
        return WATCH_BATCH_COOLDOWNS.get(self._watch_peak_percent(), 0.24)

    def _index_state(self, *, pending: dict[str, object] | None = None) -> str:
        active_pending = self.pending_rebuild() if pending is None else pending
        if isinstance(active_pending, dict):
            return 'pending'
        return 'ready' if self._read_index_state() is not None else 'missing'

    def _index_ready(self, *, pending: dict[str, object] | None = None) -> bool:
        return self._index_state(pending=pending) == 'ready'

    def _require_ready_index(self, *, action: str) -> None:
        state = self._index_state()
        if state == 'ready':
            return
        if action == 'watch':
            if state == 'pending':
                raise RuntimeError('热监听不能接在未完成的全量建库后面。请先继续或重新完成全量建库。')
            raise RuntimeError('索引还没建立，暂时不能启动热监听。请先完成一次全量建库。')
        if state == 'pending':
            raise RuntimeError('当前索引未完成，暂时不能查询。请先继续或重新完成全量建库。')
        raise RuntimeError('索引还没建立，暂时不能查询。请先完成一次全量建库。')

    # Why: 百万级文件时不能把所有 Path 长时间常驻内存；用稳定顺序的流式遍历更稳。
    def iter_vault_files(self):
        if not self.config.vault_path:
            return
        ignore = set(self.config.ignore_dirs)
        for root, dirnames, filenames in os.walk(self.config.vault_dir, topdown=True):
            dirnames[:] = sorted(name for name in dirnames if name not in ignore)
            current_root = Path(root)
            for filename in sorted(filenames):
                if not filename.lower().endswith('.md'):
                    continue
                yield (current_root / filename).resolve()

    def scan_vault(self) -> list[Path]:
        return list(self.iter_vault_files() or [])

    def _scan_manifest_signature_and_count(self) -> tuple[str, int]:
        digest = hashlib.sha1()
        total = 0
        for path in self.iter_vault_files() or []:
            try:
                stat = path.stat()
            except OSError:
                continue
            relative = path.relative_to(self.config.vault_dir).as_posix()
            digest.update(relative.encode('utf-8', errors='ignore'))
            digest.update(b'\0')
            digest.update(f"{stat.st_mtime_ns}:{stat.st_size}".encode('ascii', errors='ignore'))
            digest.update(b'\n')
            total += 1
        return digest.hexdigest(), total

    def estimate_space(
        self,
        *,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SpaceEstimate:
        report = estimate_storage_for_vault(
            self.config,
            self.paths,
            on_progress=on_progress,
            pause_event=pause_event,
            cancel_event=cancel_event,
        )
        self.store.record_preflight(report, str(self.config.vault_dir))
        return report

    def _ensure_vector_runtime_ready(self) -> None:
        message = runtime_dependency_issue(self.config)
        if message:
            raise RuntimeDependencyError(message)

    def bootstrap_model(self) -> dict[str, object]:
        result = prepare_local_model_snapshot(self.config, self.paths, allow_download=True)
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

        vector_enabled = (self.config.vector_backend or 'disabled').strip().lower() not in {'', 'disabled', 'none', 'off'}
        if vector_enabled:
            self._ensure_vector_runtime_ready()
        manifest_signature, total_files = self._scan_manifest_signature_and_count()
        state = self._read_rebuild_state() if resume else None
        if state is None or not self._can_resume_rebuild_state(state, manifest_signature, total_files):
            if resume and self._rebuild_state_file.exists():
                self.discard_pending_rebuild()
            self._clear_index_state()
            state = self._start_fresh_rebuild_state(manifest_signature, total_files)
            self.store.reset_all()
            self.vector_index.reset()
        else:
            state['manifest_signature'] = manifest_signature
            state['total_files'] = total_files
            state.setdefault('indexing_cursor', 0)
            state.setdefault('readable_count', 0)
            state.setdefault('skipped_count', 0)
            state.setdefault('parsed_chunk_count', int(self.store.stats().get('chunks', 0)))
            state.setdefault('duplicate_block_ids', 0)
            state.setdefault('rendering_cursor', 0)
            state.setdefault('total_render_rows', 0)
            state.setdefault('vector_encoded_count', 0)
            state.setdefault('vector_written_count', 0)
            state.setdefault('total_vector_documents', 0)
            self._write_rebuild_state(state)

        model_ready = (not vector_enabled) or is_local_model_ready(self.config, self.paths)
        history_entry = find_matching_history(self._build_history_file, self.config)
        eta_tracker = BuildEtaTracker(self.config, history_entry=history_entry, vector_enabled=vector_enabled, model_ready=model_ready)
        resolved_device = resolve_vector_device(self.config.vector_device)
        LOGGER.info(
            'Starting full rebuild: resume=%s requested_device=%s resolved_device=%s build_profile=%s vector_backend=%s files=%s.',
            resume,
            getattr(self.config, 'vector_device', 'auto'),
            resolved_device,
            getattr(self.config, 'build_resource_profile', 'balanced'),
            getattr(self.config, 'vector_backend', 'disabled'),
            total_files,
        )
        if str(getattr(self.config, 'vector_device', '') or '').strip().lower() == 'cuda' and resolved_device != 'cuda':
            LOGGER.warning('CUDA was requested for rebuild, but OmniClip fell back to %s.', resolved_device)

        phase_on_resume = str(state.get('phase', 'indexing') or 'indexing').strip().lower() or 'indexing'
        indexing_cursor = min(max(int(state.get('indexing_cursor', 0) or 0), 0), total_files)
        readable_count = max(int(state.get('readable_count', 0) or 0), 0)
        skipped_count = max(int(state.get('skipped_count', 0) or 0), 0)
        duplicate_block_ids = int(state.get('duplicate_block_ids', 0) or 0)
        parsed_chunk_count = int(state.get('parsed_chunk_count', self.store.stats().get('chunks', 0)) or 0)

        rebuild_started_at = time.time()
        indexing_started_at = rebuild_started_at
        rendering_started_at = 0.0
        vectorizing_started_at = 0.0
        last_state_write_at = 0.0
        last_state_write_phase = ''
        last_state_write_cursor = -1

        def state_cursor(payload: dict[str, object]) -> int:
            phase = str(payload.get('phase', 'indexing') or 'indexing').strip().lower() or 'indexing'
            if phase == 'indexing':
                return int(payload.get('indexing_cursor', 0) or 0)
            if phase == 'rendering':
                return int(payload.get('rendering_cursor', 0) or 0)
            return int(payload.get('vector_written_count', 0) or 0)

        def checkpoint_state(*, force: bool = False) -> None:
            nonlocal last_state_write_at, last_state_write_phase, last_state_write_cursor
            phase = str(state.get('phase', 'indexing') or 'indexing').strip().lower() or 'indexing'
            cursor = state_cursor(state)
            now = time.time()
            row_threshold = {
                'indexing': REBUILD_INDEXING_CHECKPOINT_ROWS,
                'rendering': REBUILD_RENDERING_CHECKPOINT_ROWS,
                'vectorizing': max(VECTOR_UPSERT_BATCH_SIZE, 64),
            }.get(phase, REBUILD_INDEXING_CHECKPOINT_ROWS)
            if (
                not force
                and phase == last_state_write_phase
                and (now - last_state_write_at) < REBUILD_STATE_CHECKPOINT_SECONDS
                and (cursor - last_state_write_cursor) < row_threshold
            ):
                return
            state['updated_at'] = _utc_now()
            self._write_rebuild_state(state)
            last_state_write_at = now
            last_state_write_phase = phase
            last_state_write_cursor = cursor

        watchdog_lock = threading.Lock()
        watchdog_state: dict[str, object] = {
            'phase': str(state.get('phase', 'indexing') or 'indexing'),
            'current': int(indexing_cursor),
            'total': int(total_files),
            'encoded_count': 0,
            'written_count': 0,
            'current_path': str(state.get('current_path', '') or ''),
            'stage_status': '',
            'overall_percent': 0.0,
            'last_progress_at': time.time(),
            'last_forward_at': time.time(),
            'last_report_at': 0.0,
            'forward_signature': ('indexing', int(indexing_cursor), 0, 0, str(state.get('current_path', '') or '')),
        }
        watchdog_stop = threading.Event()

        def update_watchdog(payload: dict[str, object]) -> None:
            phase = str(payload.get('stage', state.get('phase', 'indexing')) or 'indexing').strip().lower() or 'indexing'
            current = int(payload.get('current', 0) or 0)
            total = int(payload.get('total', 0) or 0)
            encoded_count = int(payload.get('encoded_count', current) or current)
            written_count = int(payload.get('written_count', current) or current)
            current_path = str(payload.get('current_path') or state.get('current_path', '') or '')
            signature = (phase, current, encoded_count, written_count, current_path)
            now = time.time()
            with watchdog_lock:
                previous_signature = watchdog_state.get('forward_signature')
                watchdog_state.update(
                    {
                        'phase': phase,
                        'current': current,
                        'total': total,
                        'encoded_count': encoded_count,
                        'written_count': written_count,
                        'current_path': current_path,
                        'stage_status': str(payload.get('stage_status') or ''),
                        'overall_percent': float(payload.get('overall_percent', 0.0) or 0.0),
                        'last_progress_at': now,
                    }
                )
                if previous_signature != signature:
                    watchdog_state['forward_signature'] = signature
                    watchdog_state['last_forward_at'] = now

        def emit_rebuild_progress(payload: dict[str, object]) -> None:
            update_watchdog(payload)
            _emit_progress(on_progress, payload)

        def watchdog_loop() -> None:
            while not watchdog_stop.wait(REBUILD_WATCHDOG_INTERVAL_SECONDS):
                now = time.time()
                with watchdog_lock:
                    snapshot = dict(watchdog_state)
                stalled_seconds = max(now - float(snapshot.get('last_forward_at', now) or now), 0.0)
                last_report_at = float(snapshot.get('last_report_at', 0.0) or 0.0)
                if stalled_seconds < REBUILD_WATCHDOG_STALL_SECONDS:
                    continue
                if last_report_at > 0.0 and (now - last_report_at) < REBUILD_WATCHDOG_REPEAT_SECONDS:
                    continue
                actions = ['force_checkpoint']
                checkpoint_state(force=True)
                try:
                    gc.collect()
                    actions.append('gc_collect')
                except Exception:
                    pass
                phase = str(snapshot.get('phase', 'indexing') or 'indexing')
                if phase == 'vectorizing':
                    try:
                        record_runtime_incident(self.paths, kind='memory_pressure', detail='watchdog detected stalled rebuild progress', phase=phase)
                        actions.append('record_runtime_incident')
                    except Exception:
                        pass
                report_path = _write_rebuild_diagnostic(
                    self.paths,
                    {
                        'generated_at': _utc_now(),
                        'reason': 'watchdog_stall',
                        'stalled_seconds': round(stalled_seconds, 1),
                        'state': snapshot,
                        'actions': actions,
                        'rebuild_state': dict(state),
                        'thread_stacks': _collect_thread_stack_snapshots(),
                    },
                )
                actions.append('write_diagnostic_report')
                warning_payload = {
                    'stage': phase,
                    'current': int(snapshot.get('current', 0) or 0),
                    'total': int(snapshot.get('total', 0) or 0),
                    'encoded_count': int(snapshot.get('encoded_count', 0) or 0),
                    'written_count': int(snapshot.get('written_count', 0) or 0),
                    'overall_percent': float(snapshot.get('overall_percent', 0.0) or 0.0),
                    'stage_status': str(snapshot.get('stage_status') or ''),
                    'watchdog_stalled': True,
                    'watchdog_wait_seconds': round(stalled_seconds, 1),
                    'watchdog_report_path': str(report_path),
                    'watchdog_actions': list(actions),
                    'current_path': str(snapshot.get('current_path', '') or ''),
                }
                emit_rebuild_progress(warning_payload)
                LOGGER.warning('Rebuild watchdog detected no forward progress for %.1fs during %s. Diagnostic report: %s', stalled_seconds, phase, report_path)
                with watchdog_lock:
                    watchdog_state['last_report_at'] = now

        watchdog_thread = threading.Thread(target=watchdog_loop, daemon=True, name='rebuild-watchdog')
        watchdog_thread.start()

        try:
            for index, path in enumerate(self.iter_vault_files() or [], start=1):
                _wait_for_worker_controls(pause_event, cancel_event)
                if index <= indexing_cursor:
                    continue
                relative_path = path.relative_to(self.config.vault_dir).as_posix()
                try:
                    parsed = parse_markdown_file(self.config.vault_dir, path)
                except OSError:
                    skipped_count += 1
                else:
                    duplicate_block_ids += len(self.store.replace_file(parsed))
                    readable_count += 1
                    parsed_chunk_count += len(parsed.chunks)
                indexing_cursor = index
                state.update(
                    {
                        'phase': 'indexing',
                        'indexing_cursor': indexing_cursor,
                        'readable_count': readable_count,
                        'skipped_count': skipped_count,
                        'duplicate_block_ids': duplicate_block_ids,
                        'parsed_chunk_count': parsed_chunk_count,
                        'current_path': relative_path,
                    }
                )
                checkpoint_state(force=indexing_cursor >= total_files)
                completed_count = indexing_cursor
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
                emit_rebuild_progress(
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
            _wait_for_worker_controls(pause_event, cancel_event)
            rendering_start_offset = 0
            if phase_on_resume in {'rendering', 'vectorizing'}:
                rendering_start_offset = max(int(state.get('rendering_cursor', 0) or 0), 0)
            state.update({'phase': 'rendering', 'current_path': '', 'total_render_rows': int(state.get('total_render_rows', 0) or 0)})
            checkpoint_state(force=True)
            rendering_started_at = time.time()

            def update_render_checkpoint(current: int, total_rows: int, source_path: str) -> None:
                state.update(
                    {
                        'phase': 'rendering',
                        'rendering_cursor': current,
                        'total_render_rows': total_rows,
                        'current_path': source_path,
                    }
                )
                checkpoint_state(force=current >= total_rows)

            total_render_rows = self._refresh_rendered(
                None,
                pause_event=pause_event,
                cancel_event=cancel_event,
                on_progress=emit_rebuild_progress,
                rebuild_started_at=rebuild_started_at,
                history_entry=history_entry,
                vector_enabled=vector_enabled,
                model_ready=model_ready,
                eta_tracker=eta_tracker,
                start_offset=rendering_start_offset,
                checkpoint=update_render_checkpoint,
            )
            state.update({'phase': 'rendering', 'rendering_cursor': total_render_rows, 'total_render_rows': total_render_rows, 'current_path': ''})
            checkpoint_state(force=True)
            rendering_seconds = max(time.time() - rendering_started_at, 0.0)

            _wait_for_worker_controls(pause_event, cancel_event)
            total_documents = self.store.count_vector_documents()
            resume_vector_from = 0
            reset_vector_index = True
            if phase_on_resume == 'vectorizing':
                durable_written = min(max(int(state.get('vector_written_count', 0) or 0), 0), total_documents)
                if durable_written > 0:
                    rewind = min(REBUILD_VECTOR_RESUME_REWIND, durable_written)
                    resume_vector_from = max(durable_written - rewind, 0)
                    LOGGER.info('Resuming vector stage from cursor=%s (durable_written=%s rewind=%s total=%s).', resume_vector_from, durable_written, rewind, total_documents)
                    self._trim_vector_suffix(resume_vector_from)
                    reset_vector_index = resume_vector_from <= 0
            if resume_vector_from <= 0 and reset_vector_index:
                self.vector_index.reset()
            documents = itertools.islice(self.store.iter_vector_documents(), resume_vector_from, None)
            state.update(
                {
                    'phase': 'vectorizing',
                    'current_path': '',
                    'total_vector_documents': total_documents,
                    'vector_encoded_count': resume_vector_from,
                    'vector_written_count': resume_vector_from,
                }
            )
            checkpoint_state(force=True)
            vectorizing_started_at = time.time()
            eta_seconds, overall_percent = eta_tracker.estimate(
                stage='vectorizing',
                current=resume_vector_from,
                total=total_documents,
                elapsed_total=time.time() - rebuild_started_at,
                stage_elapsed=0.0,
                parsed_chunks=parsed_chunk_count,
                estimated_total_chunks=max(total_documents, parsed_chunk_count),
            )
            emit_rebuild_progress(
                {
                    'stage': 'vectorizing',
                    'current': resume_vector_from,
                    'total': total_documents,
                    'eta_seconds': eta_seconds,
                    'overall_percent': overall_percent,
                    'stage_status': 'loading_model',
                    'encoded_count': resume_vector_from,
                    'written_count': resume_vector_from,
                },
            )

            LOGGER.info('Vector stage is starting with %s documents (resume_from=%s).', total_documents, resume_vector_from)
            vector_metrics: dict[str, object] = {}

            def emit_vector_progress(progress: dict[str, object]) -> None:
                current = max(0, int(progress.get('current', resume_vector_from) or resume_vector_from))
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
                enriched = dict(progress)
                enriched['eta_seconds'] = eta_seconds
                enriched['overall_percent'] = overall_percent
                state.update(
                    {
                        'phase': 'vectorizing',
                        'vector_encoded_count': int(enriched.get('encoded_count', current) or current),
                        'vector_written_count': int(enriched.get('written_count', current) or current),
                        'total_vector_documents': total_documents,
                    }
                )
                checkpoint_state(force=int(state.get('vector_written_count', 0) or 0) >= total_documents)
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
                emit_rebuild_progress(enriched)

            self.vector_index.rebuild(
                documents,
                total=total_documents,
                on_progress=emit_vector_progress,
                pause_event=pause_event,
                cancel_event=cancel_event,
                progress_offset=resume_vector_from,
                reset_index=not bool(resume_vector_from),
            )
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
            self._write_index_state(stats)
            LOGGER.info(
                'Full rebuild finished: files=%s chunks=%s refs=%s duplicate_block_ids=%s.',
                stats.get('files', 0),
                stats.get('chunks', 0),
                stats.get('refs', 0),
                stats.get('duplicate_block_ids', 0),
            )
            return stats
        except BuildCancelledError:
            checkpoint_state(force=True)
            raise
        except Exception as exc:
            checkpoint_state(force=True)
            if _is_memory_pressure_error(exc):
                record_runtime_incident(self.paths, kind='vector_oom', detail=str(exc), phase=str(state.get('phase', 'indexing') or 'indexing'))
            raise
        finally:
            watchdog_stop.set()
            if watchdog_thread.is_alive():
                watchdog_thread.join(timeout=0.3)
    def reindex_paths(self, changed_relative_paths: list[str], deleted_relative_paths: list[str]) -> dict[str, object]:
        from .parser import parse_markdown_file

        self._ensure_vector_runtime_ready()
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

    def _trim_vector_suffix(self, start_offset: int) -> None:
        safe_start = max(int(start_offset or 0), 0)
        if safe_start <= 0:
            self.vector_index.reset()
            return
        chunk_ids: list[str] = []
        for document in itertools.islice(self.store.iter_vector_documents(), safe_start, None):
            chunk_id = str(document.get('chunk_id') or '').strip()
            if not chunk_id:
                continue
            chunk_ids.append(chunk_id)
            if len(chunk_ids) >= VECTOR_UPSERT_BATCH_SIZE:
                self.vector_index.delete(chunk_ids)
                chunk_ids = []
        if chunk_ids:
            self.vector_index.delete(chunk_ids)

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

    def _normalize_query_families(self, allowed_families) -> set[str]:
        supported = {'markdown', 'pdf', 'tika'}
        if allowed_families is None:
            return set(supported)
        normalized = {str(item).strip().lower() for item in allowed_families if str(item).strip()}
        return {item for item in normalized if item in supported}

    def _vector_index_status(self) -> dict[str, object]:
        status_fn = getattr(self.vector_index, 'status', None)
        if not callable(status_fn):
            return {}
        try:
            payload = status_fn()
        except Exception:
            LOGGER.debug('Vector index status probe failed.', exc_info=True)
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

    @staticmethod
    def _normalize_query_mode(query_mode: str | None) -> str:
        normalized = str(query_mode or 'hybrid').strip().lower().replace('_', '-').replace(' ', '-')
        aliases = {
            '': 'hybrid',
            'auto': 'hybrid',
            'default': 'hybrid',
            'hybrid': 'hybrid',
            'hybrid-no-rerank': 'hybrid_no_rerank',
            'hybrid-norerank': 'hybrid_no_rerank',
            'hybrid_no_rerank': 'hybrid_no_rerank',
            'lexical': 'lexical-only',
            'lexical-only': 'lexical-only',
            'lexical_only': 'lexical-only',
            'vector': 'vector-only',
            'vector-only': 'vector-only',
            'vector_only': 'vector-only',
        }
        return aliases.get(normalized, 'hybrid')

    @staticmethod
    def _trace_json_line(label: str, payload: dict[str, object]) -> str:
        return f"{label} " + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _trace_digest(payload: object, prefix: str) -> str:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return f"{prefix}:{hashlib.sha1(serialized.encode('utf-8')).hexdigest()[:12]}"

    @staticmethod
    def _safe_realpath(value: str | Path | None) -> str:
        if value is None:
            return ''
        try:
            return str(Path(value).resolve(strict=False))
        except Exception:
            return str(value)

    @staticmethod
    def _app_root_dir() -> Path:
        if getattr(sys, 'frozen', False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent.parent

    def _runtime_trace_metadata(self) -> dict[str, object]:
        runtime_state = inspect_runtime_environment()
        runtime_dir = Path(runtime_state.get('runtime_dir') or '')
        registry_path = runtime_component_registry_path(runtime_dir)
        registry_payload = load_runtime_component_registry(runtime_dir) if runtime_dir.exists() else {}
        pending_updates = list_pending_runtime_updates(runtime_dir) if runtime_dir.exists() else []
        manifest_payload: object = {}
        if registry_path.exists():
            try:
                manifest_payload = json.loads(registry_path.read_text(encoding='utf-8'))
            except Exception:
                manifest_payload = {'registry_path': str(registry_path), 'invalid': True}
        elif registry_payload:
            manifest_payload = registry_payload
        else:
            manifest_payload = {'layout': 'legacy-or-empty'}
        runtime_manifest_version = self._trace_digest(manifest_payload, 'runtime-manifest')
        live_runtime_id = self._trace_digest(
            {
                'runtime_dir': self._safe_realpath(runtime_dir),
                'registry': registry_payload,
            },
            'runtime-live',
        )
        pending_runtime_id = ''
        if pending_updates:
            pending_runtime_id = self._trace_digest(pending_updates, 'runtime-pending')
        runtime_instance_id = self._trace_digest(
            {
                'runtime_manifest_version': runtime_manifest_version,
                'live_runtime_id': live_runtime_id,
                'exe_version': __version__,
            },
            'runtime-instance',
        )
        return {
            'runtime_root': self._safe_realpath(runtime_dir),
            'runtime_manifest_version': runtime_manifest_version,
            'live_runtime_id': live_runtime_id,
            'pending_runtime_id': pending_runtime_id,
            'runtime_instance_id': runtime_instance_id,
        }

    def _index_trace_metadata(self) -> dict[str, object]:
        index_state = self._read_index_state() or {}
        stats = self.store.stats()
        index_payload = {
            'workspace_id': self.paths.workspace_id,
            'vault_path': str(self.config.vault_dir) if self.config.vault_path else '',
            'index_completed_at': str(index_state.get('completed_at') or ''),
            'sqlite_realpath': self._safe_realpath(self.paths.sqlite_file),
            'stats': stats,
        }
        return {
            'workspace_id': self.paths.workspace_id,
            'index_generation_id': self._trace_digest(index_payload, 'index-generation'),
            'index_built_for_workspace': index_payload['vault_path'],
            'index_built_at': index_payload['index_completed_at'],
            'sqlite_realpath': index_payload['sqlite_realpath'],
            'fts_rows_in_scope': int(stats.get('chunks', 0) or 0),
            'vector_rows_in_scope': int(self.store.count_vector_documents() or 0),
        }

    @staticmethod
    def _query_expected_steps(*, markdown_requested: bool, query_mode: str, profile: QueryProfile, requested_families: set[str], reranker_enabled: bool) -> tuple[str, ...]:
        steps: list[str] = []
        lexical_enabled = markdown_requested and query_mode != 'vector-only'
        vector_enabled = markdown_requested and profile.use_vector and query_mode != 'lexical-only'
        if lexical_enabled:
            steps.append('Markdown 基础候选召回')
        if vector_enabled:
            steps.append('Markdown 语义向量召回')
        if 'pdf' in requested_families:
            steps.append('PDF 扩展检索')
        if 'tika' in requested_families:
            steps.append('Tika 扩展检索')
        steps.append('跨来源融合排序')
        if reranker_enabled:
            steps.append('Reranker')
        steps.append('上下文组装')
        return tuple(steps)

    def _build_query_plan_payload(
        self,
        *,
        query_id: str,
        query_text: str,
        query_mode: str,
        requested_families: set[str],
        profile: QueryProfile,
        threshold: float,
        topk: int,
        reranker_enabled: bool,
        expected_steps: tuple[str, ...],
    ) -> dict[str, object]:
        profile_payload = {
            'kind': profile.kind,
            'terms': profile.terms,
            'use_vector': profile.use_vector,
            'candidate_limit': profile.candidate_limit,
            'hydration_pool_size': profile.hydration_pool_size,
        }
        return {
            'query_id': query_id,
            'query_text': query_text,
            'query_mode': query_mode,
            'allowed_families': sorted(requested_families),
            'lexical_enabled': bool('markdown' in requested_families and query_mode != 'vector-only'),
            'vector_enabled': bool('markdown' in requested_families and profile.use_vector and query_mode != 'lexical-only'),
            'vector_mode': self.config.vector_backend,
            'seed_strategy': 'pure_vector' if query_mode == 'vector-only' else ('hybrid_parallel' if profile.use_vector else 'lexical_only'),
            'reranker_enabled': bool(reranker_enabled),
            'threshold': float(threshold),
            'topk': int(topk),
            'profile_hash': self._trace_digest(profile_payload, 'query-profile'),
            'expected_steps': expected_steps,
        }

    def _build_query_fingerprint_payload(
        self,
        *,
        vector_status: dict[str, object],
        fts_rows_in_scope: int,
        vector_rows_in_scope: int,
    ) -> dict[str, object]:
        runtime_meta = self._runtime_trace_metadata()
        index_meta = self._index_trace_metadata()
        build_payload = {
            'exe_version': __version__,
            'frozen': bool(getattr(sys, 'frozen', False)),
            'sys_executable': sys.executable,
        }
        return {
            'pid': os.getpid(),
            'thread_name': threading.current_thread().name,
            'sys_executable': sys.executable,
            'sys__meipass': str(getattr(sys, '_MEIPASS', '') or ''),
            'cwd': self._safe_realpath(Path.cwd()),
            'app_root': self._safe_realpath(self._app_root_dir()),
            'runtime_root': runtime_meta['runtime_root'],
            'runtime_manifest_version': runtime_meta['runtime_manifest_version'],
            'live_runtime_id': runtime_meta['live_runtime_id'],
            'pending_runtime_id': runtime_meta['pending_runtime_id'],
            'runtime_instance_id': runtime_meta['runtime_instance_id'],
            'appdata_root': self._safe_realpath(self.paths.global_root),
            'workspace_id': index_meta['workspace_id'],
            'workspace_realpath': str(self.config.vault_dir) if self.config.vault_path else '',
            'index_generation_id': index_meta['index_generation_id'],
            'index_built_for_workspace': index_meta['index_built_for_workspace'],
            'index_built_at': index_meta['index_built_at'],
            'sqlite_realpath': index_meta['sqlite_realpath'],
            'vector_db_realpath': self._safe_realpath(vector_status.get('db_dir') or ''),
            'fts_rows_in_scope': int(fts_rows_in_scope),
            'vector_rows_in_scope': int(vector_rows_in_scope),
            'embedding_model_id': self.config.vector_model,
            'reranker_model_id': getattr(self.config, 'reranker_model', ''),
            'build_id': self._trace_digest(build_payload, 'build'),
            'exe_version': __version__,
        }

    def _build_query_stage_payload(
        self,
        *,
        query_text: str,
        query_mode: str,
        vector_query_planned: bool,
        vector_query_executed: bool,
        vector_status: dict[str, object],
        storage_candidates: int,
        vector_candidates: int,
        vector_exception_class: str,
        vector_exception_message: str,
        fused_count: int,
        reranker_outcome: RerankOutcome | None,
        final_candidates_raw: int,
        final_after_filters: int,
        postfilter_drop_count: int,
        fallback_reason: str,
        stage_ms: dict[str, int],
    ) -> dict[str, object]:
        fts_query = _build_fts_query(query_text)
        reranker_applied = bool(getattr(reranker_outcome, 'applied', False)) if reranker_outcome is not None else False
        reranker_skip_reason = ''
        if reranker_outcome is not None:
            reranker_skip_reason = str(getattr(reranker_outcome, 'skipped_reason', '') or '')
        return {
            'query_text_normalized': query_text.strip(),
            'fts_query_normalized': fts_query or '',
            'query_mode': query_mode,
            'lexical_candidates_raw': int(storage_candidates),
            'vector_query_planned': bool(vector_query_planned),
            'vector_query_executed': bool(vector_query_executed),
            'vector_table_ready': vector_status.get('table_ready'),
            'vector_backend': vector_status.get('backend') or self.config.vector_backend,
            'vector_candidates_raw': int(vector_candidates),
            'vector_exception_class': vector_exception_class,
            'vector_exception_message': vector_exception_message,
            'fusion_candidates_raw': int(fused_count),
            'reranker_applied': reranker_applied,
            'reranker_skip_reason': reranker_skip_reason,
            'final_candidates_raw': int(final_candidates_raw),
            'final_after_filters': int(final_after_filters),
            'postfilter_drop_count': int(postfilter_drop_count),
            'fallback_reason': fallback_reason,
            'stage_ms': stage_ms,
        }

    def query(
        self,
        query_text: str,
        limit: int | None = None,
        copy_result: bool = False,
        score_threshold: float | None = None,
        allowed_families: list[str] | tuple[str, ...] | set[str] | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        query_mode: str | None = None,
    ) -> QueryResult:
        requested_families = self._normalize_query_families(allowed_families)
        if not requested_families:
            raise RuntimeError('query_family_none_selected')
        normalized_query_mode = self._normalize_query_mode(query_mode)
        started_at = time.perf_counter()
        limit = max(int(limit or self.config.query_limit or 0), 1)
        profile = build_query_profile(query_text, limit)
        candidate_limit = profile.candidate_limit
        reranker_enabled = bool(getattr(self.config, 'reranker_enabled', False)) and normalized_query_mode == 'hybrid'
        expected_steps = self._query_expected_steps(
            markdown_requested='markdown' in requested_families,
            query_mode=normalized_query_mode,
            profile=profile,
            requested_families=requested_families,
            reranker_enabled=reranker_enabled,
        )
        query_id = self._trace_digest(
            {
                'query_text': query_text,
                'workspace_id': self.paths.workspace_id,
                'mode': normalized_query_mode,
                'limit': limit,
                'at': time.time_ns(),
            },
            'query',
        )
        _emit_query_progress(on_progress, 'prepare', percent=5, query_text=query_text, limit=limit, families=sorted(requested_families))

        family_hits: dict[str, list[SearchHit]] = {}
        markdown_hits: list[SearchHit] = []
        runtime_warnings: list[str] = []
        page_block_patterns = _compile_page_blocklist_patterns(getattr(self.config, 'page_blocklist_rules', ''))
        trace_storage_candidates = 0
        trace_vector_candidates = 0
        trace_vector_error = ''
        trace_vector_error_class = ''
        trace_vector_table_ready: object = None
        trace_vector_query_executed = False
        fallback_reason = ''
        stage_ms = {
            'lexical': 0,
            'vector': 0,
            'fusion': 0,
            'rerank': 0,
            'finalize': 0,
        }

        markdown_requested = 'markdown' in requested_families
        lexical_enabled = markdown_requested and normalized_query_mode != 'vector-only'
        vector_query_planned = bool(markdown_requested and profile.use_vector and normalized_query_mode != 'lexical-only')
        vector_status = self._vector_index_status() if markdown_requested else {}
        trace_vector_table_ready = vector_status.get('table_ready') if vector_status else None
        fts_rows_in_scope = int(self.store.stats().get('chunks', 0) if markdown_requested else 0)
        vector_rows_in_scope = int(self.store.count_vector_documents() if markdown_requested else 0)

        if markdown_requested:
            if self._index_ready():
                storage_candidates = []
                lexical_started_at = time.perf_counter()
                if lexical_enabled:
                    storage_candidates = _filter_candidate_rows_by_page_blocklist(self.store.search_candidates(query_text, candidate_limit), page_block_patterns)
                    trace_storage_candidates = len(storage_candidates)
                    _emit_query_progress(on_progress, 'candidate', percent=22, candidates=len(storage_candidates), limit=candidate_limit)
                stage_ms['lexical'] = max(int((time.perf_counter() - lexical_started_at) * 1000), 0)

                vector_candidates: dict[str, float] = {}
                vector_runtime_ready = True
                if vector_query_planned:
                    vector_started_at = time.perf_counter()
                    try:
                        self._ensure_vector_runtime_ready()
                    except RuntimeDependencyError as exc:
                        vector_runtime_ready = False
                        trace_vector_error_class = exc.__class__.__name__
                        trace_vector_error = str(exc).strip() or exc.__class__.__name__
                        runtime_warnings.append('markdown_vector_runtime_unavailable')
                        fallback_reason = 'vector_runtime_unavailable'
                        LOGGER.warning(
                            'Markdown query degraded to lexical-only retrieval because vector runtime is not ready: %s',
                            trace_vector_error,
                        )
                    if vector_runtime_ready:
                        if trace_vector_table_ready is False:
                            runtime_warnings.append('markdown_vector_index_missing')
                            fallback_reason = 'vector_index_missing'
                        else:
                            vector_limit = max(self.config.vector_candidate_limit, candidate_limit)
                            try:
                                trace_vector_query_executed = True
                                vector_candidates = {item.chunk_id: item.score for item in self.vector_index.search(query_text, vector_limit)}
                                trace_vector_candidates = len(vector_candidates)
                                _emit_query_progress(on_progress, 'vector', percent=35, candidates=len(vector_candidates), limit=vector_limit)
                                if resolve_vector_device(self.config.vector_device) == 'cpu':
                                    runtime_warnings.append('markdown_vector_cpu_ready')
                            except Exception as exc:
                                trace_vector_error_class = exc.__class__.__name__
                                trace_vector_error = str(exc).strip() or exc.__class__.__name__
                                runtime_warnings.append('markdown_vector_query_failed')
                                fallback_reason = 'vector_query_failed'
                                LOGGER.exception('Markdown vector search failed during query and was isolated from lexical retrieval.')
                                vector_candidates = {}
                    stage_ms['vector'] = max(int((time.perf_counter() - vector_started_at) * 1000), 0)

                if normalized_query_mode == 'vector-only':
                    candidate_rows = _filter_candidate_rows_by_page_blocklist(
                        self.store.fetch_rows_by_chunk_ids(vector_candidates.keys()),
                        page_block_patterns,
                    ) if vector_candidates else []
                else:
                    candidate_rows = _filter_candidate_rows_by_page_blocklist(self._merge_candidate_rows(storage_candidates, vector_candidates), page_block_patterns)
                if candidate_rows:
                    markdown_hits = rank_candidates(query_text, candidate_rows, vector_candidates, profile)
                elif normalized_query_mode != 'vector-only':
                    rows = _filter_candidate_rows_by_page_blocklist(self.store.fetch_all_rendered_chunks(), page_block_patterns)
                    markdown_hits = rank_candidates(query_text, rows, vector_candidates, profile)
                markdown_hits = [normalize_markdown_hit(hit) for hit in markdown_hits]
                if markdown_hits:
                    family_hits['markdown'] = markdown_hits
            elif requested_families == {'markdown'}:
                self._require_ready_index(action='query')
            else:
                fallback_reason = 'markdown_index_not_ready'

        fusion_started_at = time.perf_counter()
        extension_hits = self.extension_query_broker.collect_extension_hits(
            query_text,
            limit=candidate_limit,
            profile=profile,
            allowed_families=requested_families,
        )
        family_hits.update(extension_hits)
        fused_hits = self.extension_query_broker.fuse_family_hits(family_hits, limit=max(limit, profile.hydration_pool_size))
        extension_counts = {
            'pdf': len(extension_hits.get('pdf', [])),
            'tika': len(extension_hits.get('tika', [])),
        }
        stage_ms['fusion'] = max(int((time.perf_counter() - fusion_started_at) * 1000), 0)
        _emit_query_progress(
            on_progress,
            'rank',
            percent=52,
            candidates=len(fused_hits),
            limit=limit,
            markdown_candidates=len(markdown_hits),
            pdf_candidates=extension_counts['pdf'],
            tika_candidates=extension_counts['tika'],
            families=sorted(family_hits),
        )

        effective_threshold = self.config.query_score_threshold if score_threshold is None else float(score_threshold or 0.0)
        threshold_floor = max(effective_threshold, 0.0)
        rerank_limit = min(len(fused_hits), max(limit, profile.hydration_pool_size))
        rerank_started_at = time.perf_counter()
        if reranker_enabled:
            _emit_query_progress(on_progress, 'rerank', percent=68, candidates=rerank_limit, limit=limit)
            reranked_hits, rerank_outcome = self.reranker.rerank(query_text, fused_hits, rerank_limit)
        else:
            reranked_hits = fused_hits
            rerank_outcome = RerankOutcome(enabled=bool(getattr(self.config, 'reranker_enabled', False)), applied=False, skipped_reason='disabled' if normalized_query_mode != 'hybrid' else 'disabled')
        stage_ms['rerank'] = max(int((time.perf_counter() - rerank_started_at) * 1000), 0)
        filtered_hits = [hit for hit in reranked_hits if hit.score >= threshold_floor]
        postfilter_drop_count = max(len(reranked_hits) - len(filtered_hits), 0)

        finalize_started_at = time.perf_counter()
        finalized_hits, insights = self._finalize_query_hits(query_text, filtered_hits, limit, profile, on_progress=on_progress)
        stage_ms['finalize'] = max(int((time.perf_counter() - finalize_started_at) * 1000), 0)
        insights.elapsed_ms = max(int((time.perf_counter() - started_at) * 1000), 0)
        insights.runtime_warnings = tuple(dict.fromkeys(runtime_warnings))
        insights.reranker = rerank_outcome
        insights.recommendation = self._query_runtime_advisor.record_and_recommend(
            resolved_device=resolve_vector_device(self.config.vector_device),
            query_limit=limit,
            elapsed_ms=insights.elapsed_ms,
            selected_hits=len(finalized_hits),
            hydrated_candidates=insights.hydrated_candidates,
            reranker_enabled=reranker_enabled,
            reranker_degraded=bool(rerank_outcome.degraded_to_cpu if rerank_outcome else False),
            reranker_oom=bool(rerank_outcome.oom_recovered if rerank_outcome else False),
        )
        insights.query_plan = self._build_query_plan_payload(
            query_id=query_id,
            query_text=query_text,
            query_mode=normalized_query_mode,
            requested_families=requested_families,
            profile=profile,
            threshold=threshold_floor,
            topk=limit,
            reranker_enabled=reranker_enabled,
            expected_steps=expected_steps,
        )
        insights.query_fingerprint = self._build_query_fingerprint_payload(
            vector_status=vector_status,
            fts_rows_in_scope=fts_rows_in_scope,
            vector_rows_in_scope=vector_rows_in_scope,
        )
        insights.query_stage = self._build_query_stage_payload(
            query_text=query_text,
            query_mode=normalized_query_mode,
            vector_query_planned=vector_query_planned,
            vector_query_executed=trace_vector_query_executed,
            vector_status=vector_status,
            storage_candidates=trace_storage_candidates,
            vector_candidates=trace_vector_candidates,
            vector_exception_class=trace_vector_error_class,
            vector_exception_message=trace_vector_error,
            fused_count=len(fused_hits),
            reranker_outcome=rerank_outcome,
            final_candidates_raw=len(filtered_hits),
            final_after_filters=len(finalized_hits),
            postfilter_drop_count=postfilter_drop_count,
            fallback_reason=fallback_reason,
            stage_ms=stage_ms,
        )
        insights.trace_lines = (
            self._trace_json_line('QUERY_PLAN', insights.query_plan),
            self._trace_json_line('QUERY_FINGERPRINT', insights.query_fingerprint),
            self._trace_json_line('QUERY_STAGE', insights.query_stage),
        )
        if getattr(self.config, 'query_trace_logging_enabled', False):
            for line in insights.trace_lines:
                LOGGER.info('%s', line)
        _emit_query_progress(on_progress, 'context', percent=96, hits=len(finalized_hits), limit=limit)
        context_pack = self.compose_context_pack_text(query_text, finalized_hits, export_mode=getattr(self.config, 'context_export_mode', 'standard'), language=getattr(self.config, 'ui_language', 'zh-CN'))
        if copy_result:
            _emit_query_progress(on_progress, 'copy', percent=98, hits=len(finalized_hits), limit=limit)
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
        block_lookup = _LazyBlockLookup(self.store)
        chunk_lookup = _LazyChunkLookup(self.store, self.store.fetch_chunk_lookup([hit.chunk_id for hit in hits]))
        file_cache: dict[str, list[str]] = {}
        for hit in hits:
            row = chunk_lookup.get(hit.chunk_id)
            display_text = ''
            if row is not None:
                display_text = _build_display_text(self.config.vault_dir, row, block_lookup, chunk_lookup, file_cache, self.config)
            hit.display_text = display_text.strip() or _apply_output_redaction(_normalize_markup(hit.rendered_text), self.config)
            hit.preview_text = _build_preview_text(query_text, hit.display_text or hit.rendered_text)

    def _finalize_query_hits(
        self,
        query_text: str,
        hits: list[SearchHit],
        limit: int,
        profile: QueryProfile,
        *,
        on_progress: Callable[[dict[str, object]], None] | None = None,
    ) -> tuple[list[SearchHit], QueryInsights]:
        if not hits or limit <= 0:
            return [], QueryInsights(hydrated_candidates=len(hits))
        hydrated_pool: list[SearchHit] = []
        step = profile.hydration_pool_size
        offset = 0
        selected: list[SearchHit] = []
        insights = QueryInsights(hydrated_candidates=len(hits))
        total_batches = max((len(hits) + max(step, 1) - 1) // max(step, 1), 1)
        batch_index = 0
        while offset < len(hits):
            batch = hits[offset: offset + step]
            self._hydrate_display_hits(query_text, batch)
            hydrated_pool.extend(batch)
            selected, insights = select_query_hits(hydrated_pool, limit)
            offset += len(batch)
            batch_index += 1
            percent = min(90, 68 + int(round((batch_index / max(total_batches, 1)) * 22)))
            _emit_query_progress(
                on_progress,
                'hydrate',
                percent=percent,
                current=batch_index,
                total=total_batches,
                hydrated=len(hydrated_pool),
                selected=len(selected),
            )
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
        self._require_ready_index(action='watch')
        self._ensure_vector_runtime_ready()
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
        index_state = self._index_state(pending=pending)
        index_ready = index_state == 'ready'
        index_status = self._read_index_state()
        extension_registry = ExtensionRegistry().load(self.paths)
        pdf_ready = bool(extension_registry.pdf_config.enabled and extension_registry.snapshot.pdf.index_state == ExtensionIndexState.READY)
        tika_ready = bool(extension_registry.tika_config.enabled and extension_registry.snapshot.tika.index_state == ExtensionIndexState.READY)
        available_query_families = [family for family, enabled in (('markdown', index_ready), ('pdf', pdf_ready), ('tika', tika_ready)) if enabled]
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
            'index_state': index_state,
            'index_ready': index_ready,
            'watch_allowed': index_ready,
            'query_allowed': bool(available_query_families),
            'query_available_families': available_query_families,
            'index_completed_at': str((index_status or {}).get('completed_at') or ''),
            'query_limit_recommendation': query_recommendation,
        }

    def pending_rebuild(self) -> dict[str, object] | None:
        state = self._read_rebuild_state()
        if state is None:
            return None
        phase = str(state.get('phase', 'indexing') or 'indexing').strip().lower() or 'indexing'
        if phase == 'indexing':
            completed = int(state.get('indexing_cursor', 0) or 0)
            total = int(state.get('total_files', 0) or 0)
        elif phase == 'rendering':
            completed = int(state.get('rendering_cursor', 0) or 0)
            total = int(state.get('total_render_rows', 0) or 0)
        else:
            completed = int(state.get('vector_written_count', 0) or 0)
            total = int(state.get('total_vector_documents', 0) or 0)
        return {
            'phase': phase,
            'completed': completed,
            'total': total,
            'started_at': state.get('started_at', ''),
            'updated_at': state.get('updated_at', ''),
            'current_path': state.get('current_path', ''),
        }

    def discard_pending_rebuild(self) -> None:
        self.vector_index.reset()
        self.store.reset_all()
        self._clear_rebuild_state()
        self._clear_watch_state()
        self._clear_index_state()

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
            self._clear_index_state()
            self.store = MetadataStore(self.paths.sqlite_file)
        if clear_logs:
            clear_log_files(self.paths, self.config)
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
        batch_limit = max(self._watch_batch_limit(), 1)
        batch_cooldown = max(self._watch_post_batch_cooldown(), 0.0)

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
                deferred_changed: list[str] = []
                deferred_deleted: list[str] = []
                if (len(ready_changed) + len(ready_deleted)) > batch_limit:
                    budget = batch_limit
                    limited_changed = ready_changed[:budget]
                    budget = max(budget - len(limited_changed), 0)
                    limited_deleted = ready_deleted[:budget]
                    deferred_changed = ready_changed[len(limited_changed):]
                    deferred_deleted = ready_deleted[len(limited_deleted):]
                    ready_changed = limited_changed
                    ready_deleted = limited_deleted
                    if deferred_changed or deferred_deleted:
                        buffer.requeue(deferred_changed, deferred_deleted, current_snapshot, now, ready=True)
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
            if batch_cooldown > 0.0 and (ready_changed or ready_deleted):
                if stop_event.wait(batch_cooldown):
                    break

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
        relative_paths: list[str] | None,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        rebuild_started_at: float = 0.0,
        history_entry: dict[str, object] | None = None,
        vector_enabled: bool = False,
        model_ready: bool = False,
        eta_tracker: BuildEtaTracker | None = None,
        start_offset: int = 0,
        checkpoint: Callable[[int, int, str], None] | None = None,
    ) -> int:
        block_lookup = _LazyBlockLookup(self.store)
        payloads: list[tuple[str, str]] = []
        total_rows = self.store.count_render_rows(relative_paths)
        stage_started_at = time.time()
        last_emit_at = 0.0
        current = min(max(int(start_offset or 0), 0), total_rows)
        committed = current
        last_source_path = ''

        if total_rows > 0:
            eta_seconds, overall_percent = (
                eta_tracker.estimate(
                    stage='rendering',
                    current=current,
                    total=total_rows,
                    elapsed_total=max(time.time() - rebuild_started_at, 0.1),
                    stage_elapsed=0.0,
                    parsed_chunks=total_rows,
                    estimated_total_chunks=total_rows,
                )
                if eta_tracker is not None
                else estimate_remaining_build_seconds(
                    self.config,
                    stage='rendering',
                    current=current,
                    total=total_rows,
                    elapsed_total=max(time.time() - rebuild_started_at, 0.1),
                    stage_elapsed=0.0,
                    parsed_chunks=total_rows,
                    estimated_total_chunks=total_rows,
                    history_entry=history_entry,
                    vector_enabled=vector_enabled,
                    model_ready=model_ready,
                )
            )
            _emit_progress(
                on_progress,
                {
                    'stage': 'rendering',
                    'current': current,
                    'total': total_rows,
                    'eta_seconds': eta_seconds,
                    'overall_percent': overall_percent,
                },
            )

        for index, row in enumerate(self.store.iter_render_rows(relative_paths), start=1):
            _wait_for_worker_controls(pause_event, cancel_event)
            if index <= current:
                continue
            payloads.append((row['chunk_id'], _render_row(row, block_lookup)))
            current = index
            last_source_path = str(row['source_path'] or '')
            if len(payloads) >= RENDER_UPDATE_BATCH_SIZE:
                self.store.update_rendered_chunks(payloads)
                payloads = []
                committed = current
                if checkpoint is not None:
                    checkpoint(committed, total_rows, last_source_path)
            now = time.time()
            if current == total_rows or (current % REBUILD_PROGRESS_EMIT_ROW_INTERVAL) == 0 or (now - last_emit_at) >= REBUILD_PROGRESS_EMIT_INTERVAL_SECONDS:
                last_emit_at = now
                eta_seconds, overall_percent = (
                    eta_tracker.estimate(
                        stage='rendering',
                        current=current,
                        total=total_rows,
                        elapsed_total=max(now - rebuild_started_at, 0.1),
                        stage_elapsed=max(now - stage_started_at, 0.0),
                        parsed_chunks=total_rows,
                        estimated_total_chunks=total_rows,
                        timestamp=now,
                    )
                    if eta_tracker is not None
                    else estimate_remaining_build_seconds(
                        self.config,
                        stage='rendering',
                        current=current,
                        total=total_rows,
                        elapsed_total=max(now - rebuild_started_at, 0.1),
                        stage_elapsed=max(now - stage_started_at, 0.0),
                        parsed_chunks=total_rows,
                        estimated_total_chunks=total_rows,
                        history_entry=history_entry,
                        vector_enabled=vector_enabled,
                        model_ready=model_ready,
                    )
                )
                _emit_progress(
                    on_progress,
                    {
                        'stage': 'rendering',
                        'current': current,
                        'total': total_rows,
                        'eta_seconds': eta_seconds,
                        'overall_percent': overall_percent,
                    },
                )
        if payloads:
            self.store.update_rendered_chunks(payloads)
            committed = total_rows
        if checkpoint is not None and total_rows > 0:
            checkpoint(committed, total_rows, '' if committed >= total_rows else last_source_path)
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

    def _start_fresh_rebuild_state(self, manifest_signature: str, total_files: int) -> dict[str, object]:
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
            'manifest_signature': manifest_signature,
            'total_files': int(total_files),
            'indexing_cursor': 0,
            'readable_count': 0,
            'skipped_count': 0,
            'duplicate_block_ids': 0,
            'parsed_chunk_count': 0,
            'rendering_cursor': 0,
            'total_render_rows': 0,
            'vector_encoded_count': 0,
            'vector_written_count': 0,
            'total_vector_documents': 0,
            'current_path': '',
        }
        self._write_rebuild_state(state)
        return state

    def _can_resume_rebuild_state(self, state: dict[str, object], manifest_signature: str, total_files: int) -> bool:
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
        if str(state.get('manifest_signature') or '') != manifest_signature:
            return False
        return int(state.get('total_files', 0) or 0) == int(total_files)

    def _read_rebuild_state(self) -> dict[str, object] | None:
        if not self._rebuild_state_file.exists():
            return None
        try:
            return json.loads(self._rebuild_state_file.read_text(encoding='utf-8'))
        except Exception:
            return None

    def _write_rebuild_state(self, state: dict[str, object]) -> None:
        _write_json_atomic(self._rebuild_state_file, state)

    def _default_index_state(self) -> dict[str, object]:
        return {
            'version': INDEX_STATE_VERSION,
            'vault_path': str(self.config.vault_dir),
            'completed_at': _utc_now(),
        }

    def _read_index_state(self) -> dict[str, object] | None:
        if not self._index_state_file.exists():
            return None
        try:
            state = json.loads(self._index_state_file.read_text(encoding='utf-8'))
        except Exception:
            return None
        if int(state.get('version', 0) or 0) != INDEX_STATE_VERSION:
            return None
        if state.get('vault_path') != str(self.config.vault_dir):
            return None
        return state

    def _write_index_state(self, stats: dict[str, object] | None = None) -> None:
        state = self._default_index_state()
        if isinstance(stats, dict):
            state['stats'] = {
                'files': int(stats.get('files', 0) or 0),
                'chunks': int(stats.get('chunks', 0) or 0),
                'refs': int(stats.get('refs', 0) or 0),
            }
        _write_json_atomic(self._index_state_file, state)

    def _clear_index_state(self) -> None:
        if self._index_state_file.exists():
            try:
                self._index_state_file.unlink()
            except OSError:
                pass

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
        *,
        ready: bool = False,
    ) -> None:
        recorded_at = time.time() if now is None else now
        changed_seen_at = recorded_at - self.stable_seconds if ready else recorded_at
        deleted_seen_at = recorded_at - self.delete_confirm_seconds if ready else recorded_at
        for relative_path in changed:
            metadata = snapshot.get(relative_path)
            if metadata is None:
                continue
            self._changed[relative_path] = {'first_seen': changed_seen_at, 'metadata': metadata}
            self._deleted.pop(relative_path, None)
        for relative_path in deleted:
            if relative_path in snapshot:
                continue
            self._deleted[relative_path] = deleted_seen_at
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


def _is_memory_pressure_error(exc: BaseException) -> bool:
    if isinstance(exc, MemoryError):
        return True
    message = str(exc or '').strip().lower()
    return any(token in message for token in ('out of memory', 'cuda out of memory', 'allocator', 'bad alloc', 'not enough memory'))


def _collect_thread_stack_snapshots(limit: int = 10) -> dict[str, str]:
    frames = sys._current_frames()
    thread_names = {thread.ident: thread.name for thread in threading.enumerate() if thread.ident is not None}
    snapshots: dict[str, str] = {}
    for index, (thread_id, frame) in enumerate(frames.items()):
        if index >= max(int(limit), 1):
            break
        name = thread_names.get(thread_id, 'unknown')
        snapshots[f'{name}:{thread_id}'] = ''.join(traceback.format_stack(frame)[-12:])
    return snapshots


def _write_rebuild_diagnostic(paths: DataPaths, payload: dict[str, object]) -> Path:
    diagnostics_dir = paths.logs_dir / 'diagnostics'
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    target = diagnostics_dir / f'rebuild-watchdog-{timestamp}.json'
    _write_json_atomic(target, payload)
    files = sorted(diagnostics_dir.glob('rebuild-watchdog-*.json'))
    if len(files) > REBUILD_DIAGNOSTIC_MAX_FILES:
        for stale in files[:-REBUILD_DIAGNOSTIC_MAX_FILES]:
            try:
                stale.unlink()
            except OSError:
                continue
    return target


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


def _emit_query_progress(
    on_progress: Callable[[dict[str, object]], None] | None,
    stage_status: str,
    *,
    percent: int,
    **extra: object,
) -> None:
    if on_progress is None:
        return
    payload: dict[str, object] = {
        'stage': 'query',
        'stage_status': stage_status,
        'current': max(min(int(percent), 100), 0),
        'total': 100,
        'overall_percent': max(min(float(percent), 100.0), 0.0),
    }
    payload.update(extra)
    on_progress(payload)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()



