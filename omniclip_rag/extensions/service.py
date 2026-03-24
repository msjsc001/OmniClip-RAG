from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from importlib import import_module, metadata as importlib_metadata
from pathlib import Path

from ..config import AppConfig, DataPaths
from ..errors import BuildCancelledError
from ..models import SearchHit
from ..retrieval_policy import QueryProfile, build_query_profile, rank_candidates
from ..storage import MetadataStore
from ..vector_index import NullVectorIndex, create_vector_index, runtime_dependency_issue
from .build_state import (
    EXTENSION_BUILD_STATE_VERSION,
    acquire_extension_build_lease,
    clear_extension_build_state,
    file_fingerprint,
    fingerprint_matches,
    read_extension_build_state,
    release_extension_build_lease,
    touch_extension_build_lease,
    utc_now,
    write_extension_build_state,
    write_extension_diagnostic_report,
)
from .models import ExtensionDirectoryState, ExtensionIndexState, TikaFormatSupportTier
from .paths import build_extension_data_paths
from .registry import ExtensionRegistry, ExtensionRegistryState
from .runtimes import TikaParseError, TikaSidecarManager, parse_file_with_tika

LOGGER = logging.getLogger(__name__)

EXTENSION_BUILD_HEARTBEAT_SECONDS = 5.0
EXTENSION_BUILD_WATCHDOG_STALL_SECONDS = 120.0
EXTENSION_BUILD_WATCHDOG_REPEAT_SECONDS = 60.0
EXTENSION_TIKA_PARSE_TIMEOUT_SECONDS = 20.0
EXTENSION_MAX_PARSED_TEXT_CHARS = 2_000_000
EXTENSION_MAX_PARSED_CHUNKS = 5000
EXTENSION_PDF_PARSER_SCHEMA_VERSION = 'pdf-parser-v1'
EXTENSION_TIKA_PARSER_SCHEMA_VERSION = 'tika-parser-v1'
EXTENSION_SUPPORTED_PYPDF_RANGE = '>=5.0.0,<6.0.0'
EXTENSION_SUPPORTED_TIKA_SERVER_VERSION = '3.2.3'
EXTENSION_SUPPORTED_JAVA_RANGE = '21+'


def _extract_pdf_pages(pdf_path: Path) -> list[dict[str, object]]:
    # Why: Qt config pages and Markdown-only flows must stay importable even when
    # optional PDF parser dependencies are not installed in the source runtime.
    parser = import_module('omniclip_rag.extensions.parsers.pdf')
    return parser.extract_pdf_pages(pdf_path)


def _inspect_pdf_file(pdf_path: Path) -> dict[str, int]:
    parser = import_module('omniclip_rag.extensions.parsers.pdf')
    return parser.inspect_pdf_file(pdf_path)


def _parse_pdf_file(source_root: Path, absolute_path: Path):
    parser = import_module('omniclip_rag.extensions.parsers.pdf')
    return parser.parse_pdf_file(source_root, absolute_path)


def _detect_tika_format(path: Path) -> str:
    parser = import_module('omniclip_rag.extensions.parsers.tika')
    return parser.detect_tika_format(path)


def _enabled_tika_suffixes(enabled_formats: set[str]) -> set[str]:
    parser = import_module('omniclip_rag.extensions.parsers.tika')
    return parser.enabled_tika_suffixes(enabled_formats)


def _build_tika_suffix_matcher(enabled_formats: set[str]):
    parser = import_module('omniclip_rag.extensions.parsers.tika')
    return parser.build_tika_suffix_matcher(enabled_formats)


def _parse_tika_file(source_root: Path, absolute_path: Path, parsed_content, *, format_id: str):
    parser = import_module('omniclip_rag.extensions.parsers.tika')
    return parser.parse_tika_file(source_root, absolute_path, parsed_content, format_id=format_id)


class ExtensionTaskKind(str, Enum):
    """Task kinds owned by the isolated extension subsystem."""

    PRECHECK = 'precheck'
    SCAN_ONCE = 'scan_once'
    FULL_REBUILD = 'full_rebuild'
    DELETE_INDEX = 'delete_index'
    START_WATCH = 'start_watch'
    STOP_WATCH = 'stop_watch'


_HEAVY_TASK_KINDS = {
    ExtensionTaskKind.SCAN_ONCE,
    ExtensionTaskKind.FULL_REBUILD,
    ExtensionTaskKind.DELETE_INDEX,
}


@dataclass(slots=True)
class ExtensionTaskRequest:
    """One extension task the coordinator needs to arbitrate."""

    pipeline: str
    kind: ExtensionTaskKind


@dataclass(slots=True)
class ExtensionTaskDecision:
    """Coordinator result for whether an extension task may start."""

    allowed: bool
    reason: str = ''
    queued: bool = False


@dataclass(slots=True)
class PdfPreflightReport:
    """Preflight summary for the isolated PDF pipeline."""

    total_files: int
    total_pages: int
    total_bytes: int
    skipped_files: int = 0
    missing_directories: tuple[str, ...] = ()


@dataclass(slots=True)
class PdfBuildReport:
    """Result snapshot after a PDF build-like operation."""

    indexed_files: int
    indexed_chunks: int
    build_id: str = ''
    skipped_files: int = 0
    deleted_files: int = 0
    cancelled: bool = False
    resume_available: bool = False
    vector_ready: bool = False
    missing_directories: tuple[str, ...] = ()
    recent_issues: tuple[str, ...] = ()
    rebuilt: bool = False


@dataclass(slots=True)
class TikaPreflightReport:
    """Preflight summary for the isolated Tika pipeline."""

    total_files: int
    total_bytes: int
    enabled_formats: tuple[str, ...]
    skipped_files: int = 0
    missing_directories: tuple[str, ...] = ()
    recent_issues: tuple[str, ...] = ()


@dataclass(slots=True)
class TikaBuildReport:
    """Result snapshot after a Tika build-like operation."""

    indexed_files: int
    indexed_chunks: int
    enabled_formats: tuple[str, ...]
    build_id: str = ''
    skipped_files: int = 0
    expected_skips: int = 0
    failed_files: int = 0
    deleted_files: int = 0
    cancelled: bool = False
    resume_available: bool = False
    vector_ready: bool = False
    missing_directories: tuple[str, ...] = ()
    recent_issues: tuple[str, ...] = ()
    rebuilt: bool = False


@dataclass(slots=True)
class TikaFileOutcome:
    """One Tika file result after parse/normalize/storage handling."""

    status: str
    reason: str = ''
    path: str = ''
    chunk_count: int = 0


@dataclass(slots=True)
class TikaBuildStats:
    """Mutable counters shared across Tika build/update loops."""

    skipped_files: int = 0
    expected_skips: int = 0
    failed_files: int = 0
    recent_issues: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ExtensionBuildContext:
    """One isolated extension build session.

    Why: PDF/Tika need the same control-plane contract as Markdown builds
    without collapsing their parser/runtime/data-plane differences into one
    shared orchestration class too early.
    """

    pipeline: str
    build_id: str
    task_kind: ExtensionTaskKind
    manifest_signature: str
    parser_schema_version: str
    source_roots: tuple[str, ...]
    enabled_formats: tuple[str, ...] = ()
    on_progress: Callable[[dict[str, object]], None] | None = None
    pause_event: threading.Event | None = None
    cancel_event: threading.Event | None = None
    incremental: bool = False
    targeted: bool = False
    resume_requested: bool = False
    resume_state: dict[str, object] | None = None
    state_payload: dict[str, object] | None = None
    start_monotonic: float = field(default_factory=time.monotonic)


class ExtensionBuildProgressMonitor:
    """Heartbeat + watchdog wrapper for one extension build session."""

    def __init__(
        self,
        *,
        pipeline: str,
        build_id: str,
        extension_paths: DataPaths,
        on_progress: Callable[[dict[str, object]], None] | None,
    ) -> None:
        self.pipeline = pipeline
        self.build_id = build_id
        self.extension_paths = extension_paths
        self.on_progress = on_progress
        self.start_monotonic = time.monotonic()
        self._last_forward_at = self.start_monotonic
        self._last_report_at = 0.0
        self._latest_payload: dict[str, object] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name=f'extension-build-watchdog-{self.pipeline}')
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.3)

    def emit(self, payload: dict[str, object], *, material_progress: bool = True) -> None:
        now = time.monotonic()
        enriched = dict(payload)
        enriched['build_id'] = self.build_id
        enriched['pipeline'] = self.pipeline
        enriched['elapsed_seconds'] = round(now - self.start_monotonic, 1)
        touch_extension_build_lease(self.extension_paths, build_id=self.build_id)
        with self._lock:
            self._latest_payload = dict(enriched)
            if material_progress:
                self._last_forward_at = now
        _emit_progress(self.on_progress, enriched)

    def _run(self) -> None:
        while not self._stop_event.wait(EXTENSION_BUILD_HEARTBEAT_SECONDS):
            with self._lock:
                payload = dict(self._latest_payload)
                last_forward_at = self._last_forward_at
                last_report_at = self._last_report_at
            if not payload:
                continue
            now = time.monotonic()
            stalled_seconds = max(now - last_forward_at, 0.0)
            heartbeat = dict(payload)
            heartbeat['heartbeat_only'] = True
            heartbeat['elapsed_seconds'] = round(now - self.start_monotonic, 1)
            heartbeat['watchdog_wait_seconds'] = round(stalled_seconds, 1)
            if stalled_seconds >= EXTENSION_BUILD_WATCHDOG_STALL_SECONDS and (now - last_report_at) >= EXTENSION_BUILD_WATCHDOG_REPEAT_SECONDS:
                report_path = write_extension_diagnostic_report(
                    self.extension_paths,
                    prefix=f'{self.pipeline}-build-watchdog',
                    payload={
                        'pipeline': self.pipeline,
                        'build_id': self.build_id,
                        'reported_at': utc_now(),
                        'stalled_seconds': round(stalled_seconds, 1),
                        'last_payload': payload,
                    },
                )
                heartbeat['watchdog_stalled'] = True
                heartbeat['watchdog_report_path'] = str(report_path)
                with self._lock:
                    self._last_report_at = now
            self.emit(heartbeat, material_progress=False)


@dataclass(slots=True)
class ExtensionSourceIndexSummary:
    """Index summary for one extension source directory.

    Why: row-level UX needs a cheap, isolated way to answer whether one source
    already owns index data so the UI can choose between update vs. rebuild
    without touching the Markdown mainline state model.
    """

    source_path: str
    indexed_files: int = 0
    indexed_chunks: int = 0
    vector_documents: int = 0
    last_indexed_mtime: float = 0.0
    has_indexed_data: bool = False


class ExtensionTaskCoordinator:
    """Guards the Markdown mainline from extension task collisions.

    Execution, queue draining, and worker ownership stay outside this class so
    extension pipelines can share one central arbitration point without being
    tightly coupled to any specific worker implementation.
    """

    def __init__(self) -> None:
        self._active_request: ExtensionTaskRequest | None = None

    def can_start(
        self,
        request: ExtensionTaskRequest,
        *,
        markdown_rebuild_active: bool = False,
        markdown_watch_active: bool = False,
    ) -> ExtensionTaskDecision:
        """Return whether the requested extension task may start right now."""
        if self._active_request is not None and request.kind != ExtensionTaskKind.STOP_WATCH:
            return ExtensionTaskDecision(
                allowed=False,
                reason='another_extension_task_is_active',
                queued=request.kind in _HEAVY_TASK_KINDS,
            )
        if markdown_rebuild_active and request.kind in _HEAVY_TASK_KINDS:
            return ExtensionTaskDecision(
                allowed=False,
                reason='markdown_rebuild_active',
                queued=True,
            )
        if markdown_watch_active and request.kind == ExtensionTaskKind.FULL_REBUILD:
            return ExtensionTaskDecision(
                allowed=False,
                reason='markdown_watch_active',
                queued=True,
            )
        return ExtensionTaskDecision(allowed=True)

    def reserve(self, request: ExtensionTaskRequest) -> None:
        """Mark an extension task as active once it has really started."""
        self._active_request = request

    def release(self, request: ExtensionTaskRequest | None = None) -> None:
        """Release the active reservation after a task stops."""
        if request is None or self._active_request == request:
            self._active_request = None


class PdfExtensionService:
    """Own the isolated PDF parse/build/query lifecycle.

    Why: PDF is the first extension pipeline that touches real user data. This
    service keeps every byte inside the extension namespace and never reuses the
    Markdown store/vector table, while still reusing proven storage and ranking
    primitives.
    """

    def __init__(
        self,
        config: AppConfig,
        paths: DataPaths,
        *,
        coordinator: ExtensionTaskCoordinator | None = None,
        registry: ExtensionRegistry | None = None,
    ) -> None:
        self.config = config
        self.paths = paths
        self.coordinator = coordinator or ExtensionTaskCoordinator()
        self.registry = registry or ExtensionRegistry()
        self.extension_paths = build_extension_data_paths(paths, 'pdf')
        self.store = MetadataStore(self.extension_paths.sqlite_file)
        self._vector_runtime_issue = runtime_dependency_issue(config) if _vector_backend_enabled(config) else None
        self._vector_enabled = _vector_backend_enabled(config) and not self._vector_runtime_issue
        if self._vector_runtime_issue:
            LOGGER.warning('PDF extension is falling back to lexical-only mode because vector runtime is unavailable. %s', self._vector_runtime_issue)
        self.vector_index = create_vector_index(config, self.extension_paths) if self._vector_enabled else NullVectorIndex()
        self._state = self.registry.load(paths)

    def close(self) -> None:
        """Release isolated storage handles."""
        self.store.close()

    def preflight(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
    ) -> PdfPreflightReport:
        """Estimate the isolated PDF build scope without mutating any index data."""
        self._refresh_state()
        _ensure_pdf_parser_ready()
        source_dirs, missing_dirs = self._selected_source_directories(source_paths)
        files = list(self._iter_pdf_files(source_dirs))
        total_files = 0
        total_pages = 0
        total_bytes = 0
        skipped_files = 0
        started_at = time.monotonic()
        _emit_extension_stage(
            on_progress,
            stage='pdf_preflight',
            stage_status='prepare_runtime',
            close_safe=True,
            pipeline='pdf',
            elapsed_seconds=0.0,
        )
        _emit_extension_stage(
            on_progress,
            stage='pdf_preflight',
            stage_status='scan_sources',
            current=0,
            total=len(files),
            processed_files=0,
            skipped_files=0,
            close_safe=True,
            pipeline='pdf',
            elapsed_seconds=max(time.monotonic() - started_at, 0.0),
        )
        processed_files = 0
        for _source_root, pdf_path in files:
            current_path = str(pdf_path)
            _emit_extension_stage(
                on_progress,
                stage='pdf_preflight',
                stage_status='inspect_pdf',
                current=processed_files,
                total=len(files),
                current_path=current_path,
                processed_files=processed_files,
                skipped_files=skipped_files,
                error_count=skipped_files,
                close_safe=True,
                pipeline='pdf',
                elapsed_seconds=max(time.monotonic() - started_at, 0.0),
            )
            try:
                inspected = _inspect_pdf_file(pdf_path)
                total_files += 1
                total_bytes += int(inspected.get('size') or 0)
                total_pages += int(inspected.get('page_count') or 0)
            except Exception as exc:
                skipped_files += 1
                LOGGER.warning('PDF preflight skipped unreadable file: %s (%s: %s)', pdf_path, type(exc).__name__, exc)
            processed_files += 1
        _emit_extension_stage(
            on_progress,
            stage='pdf_preflight',
            stage_status='finalizing',
            current=processed_files,
            total=len(files),
            processed_files=processed_files,
            skipped_files=skipped_files,
            error_count=skipped_files,
            overall_percent=100.0,
            close_safe=True,
            pipeline='pdf',
            elapsed_seconds=max(time.monotonic() - started_at, 0.0),
        )
        self._persist_state()
        return PdfPreflightReport(
            total_files=total_files,
            total_pages=total_pages,
            total_bytes=total_bytes,
            skipped_files=skipped_files,
            missing_directories=tuple(str(item) for item in missing_dirs),
        )

    def full_rebuild(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        markdown_rebuild_active: bool = False,
        markdown_watch_active: bool = False,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        resume: bool = False,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
    ) -> PdfBuildReport:
        """Rebuild the isolated PDF index from scratch.

        The rebuild refuses to proceed if any selected source directory is only
        temporarily missing, because clearing the isolated store in that state
        would silently drop data for disconnected disks or locked volumes.
        """
        return self._run_build(
            kind=ExtensionTaskKind.FULL_REBUILD,
            incremental=False,
            source_paths=source_paths,
            markdown_rebuild_active=markdown_rebuild_active,
            markdown_watch_active=markdown_watch_active,
            on_progress=on_progress,
            resume=resume,
            pause_event=pause_event,
            cancel_event=cancel_event,
        )

    def scan_once(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        markdown_rebuild_active: bool = False,
        markdown_watch_active: bool = False,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
    ) -> PdfBuildReport:
        """Apply one-shot incremental PDF changes without enabling watch mode."""
        return self._run_build(
            kind=ExtensionTaskKind.SCAN_ONCE,
            incremental=True,
            source_paths=source_paths,
            markdown_rebuild_active=markdown_rebuild_active,
            markdown_watch_active=markdown_watch_active,
            on_progress=on_progress,
            pause_event=pause_event,
            cancel_event=cancel_event,
        )

    def delete_index(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        markdown_rebuild_active: bool = False,
        markdown_watch_active: bool = False,
    ) -> PdfBuildReport:
        """Delete only the isolated PDF index data, never the source files."""
        self._refresh_state()
        request = ExtensionTaskRequest(pipeline='pdf', kind=ExtensionTaskKind.DELETE_INDEX)
        decision = self.coordinator.can_start(
            request,
            markdown_rebuild_active=markdown_rebuild_active,
            markdown_watch_active=markdown_watch_active,
        )
        if not decision.allowed:
            raise RuntimeError(decision.reason)
        self.coordinator.reserve(request)
        try:
            clear_extension_build_state(self.extension_paths)
            release_extension_build_lease(self.extension_paths)
            filtered_roots, _missing_dirs = self._selected_source_directories(source_paths)
            if filtered_roots:
                target_paths = self._indexed_paths_under_roots(filtered_roots)
                if target_paths:
                    self._delete_paths_from_index(target_paths)
                stats = self.store.stats()
                vector_ready, query_ready = _vector_contract(self.store, self.vector_index, self._vector_enabled)
                self._update_pdf_status(
                    index_state=ExtensionIndexState.READY if int(stats.get('files', 0) or 0) > 0 else (ExtensionIndexState.NOT_BUILT if self._state.pdf_config.enabled else ExtensionIndexState.DISABLED),
                    build_in_progress=False,
                    build_id='',
                    vector_ready=vector_ready,
                    query_ready=query_ready,
                    resume_available=False,
                    indexed_document_count=int(stats.get('files', 0) or 0),
                    last_error='',
                )
                return PdfBuildReport(
                    indexed_files=int(stats.get('files', 0) or 0),
                    indexed_chunks=int(stats.get('chunks', 0) or 0),
                    deleted_files=len(target_paths),
                    rebuilt=False,
                    vector_ready=vector_ready,
                )
            self.store.reset_all()
            self.vector_index.reset()
            self._update_pdf_status(
                index_state=ExtensionIndexState.NOT_BUILT if self._state.pdf_config.enabled else ExtensionIndexState.DISABLED,
                build_in_progress=False,
                build_id='',
                vector_ready=False,
                query_ready=False,
                resume_available=False,
                indexed_document_count=0,
                last_error='',
            )
            return PdfBuildReport(indexed_files=0, indexed_chunks=0, rebuilt=False)
        finally:
            self.coordinator.release(request)
            self._persist_state()

    def query_hits(self, query_text: str, *, limit: int, profile: QueryProfile | None = None) -> list[SearchHit]:
        """Query only the isolated PDF index and annotate hits with PDF identity."""
        self._refresh_state()
        if not self._state.pdf_config.enabled:
            return []
        if self._state.snapshot.pdf.index_state != ExtensionIndexState.READY or not bool(self._state.snapshot.pdf.query_ready):
            return []
        limit = max(int(limit or 0), 1)
        query_profile = profile or build_query_profile(query_text, limit)
        candidate_limit = max(query_profile.candidate_limit, limit)
        storage_candidates = self.store.search_candidates(query_text, candidate_limit)
        vector_candidates: dict[str, float] = {}
        if query_profile.use_vector and self._vector_enabled:
            vector_limit = max(self.config.vector_candidate_limit, candidate_limit)
            vector_candidates = {item.chunk_id: item.score for item in self.vector_index.search(query_text, vector_limit)}
        candidate_rows = self._merge_candidate_rows(storage_candidates, vector_candidates)
        if candidate_rows:
            hits = rank_candidates(query_text, candidate_rows, vector_candidates, query_profile)
        else:
            hits = rank_candidates(query_text, self.store.fetch_all_rendered_chunks(), vector_candidates, query_profile)
        return [self._decorate_hit(hit) for hit in hits]

    def source_summaries(self, *, source_paths: list[str] | tuple[str, ...] | None = None) -> dict[str, ExtensionSourceIndexSummary]:
        """Return per-source index summaries for the isolated PDF pipeline."""
        self._refresh_state()
        requested = list(source_paths or [item.path for item in self._state.pdf_config.source_directories])
        manifest = self.store.fetch_file_manifest()
        summaries: dict[str, ExtensionSourceIndexSummary] = {}
        for source_path in requested:
            normalized = _normalize_source_path_text(source_path)
            if not normalized:
                continue
            summaries[normalized] = _build_source_index_summary(
                self.store,
                manifest,
                normalized,
            )
        return summaries

    def _run_build(
        self,
        *,
        kind: ExtensionTaskKind,
        incremental: bool,
        source_paths: list[str] | tuple[str, ...] | None,
        markdown_rebuild_active: bool,
        markdown_watch_active: bool,
        on_progress: Callable[[dict[str, object]], None] | None,
        resume: bool = False,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
    ) -> PdfBuildReport:
        self._refresh_state()
        parser_contract = _ensure_pdf_parser_ready()
        request = ExtensionTaskRequest(pipeline='pdf', kind=kind)
        decision = self.coordinator.can_start(
            request,
            markdown_rebuild_active=markdown_rebuild_active,
            markdown_watch_active=markdown_watch_active,
        )
        if not decision.allowed:
            raise RuntimeError(decision.reason)
        source_dirs, missing_dirs = self._selected_source_directories(source_paths)
        if not incremental and missing_dirs:
            self._update_pdf_status(
                index_state=ExtensionIndexState.STALE,
                build_in_progress=False,
                build_id='',
                resume_available=False,
                last_error='pdf_sources_missing',
            )
            self._persist_state()
            raise RuntimeError('pdf_sources_missing')
        files = list(self._iter_pdf_files(source_dirs))
        manifest_signature = _build_manifest_signature('pdf', source_roots=source_dirs, files=files)
        requested_resume = bool(resume and not incremental and not bool(source_paths))
        resume_state = read_extension_build_state(self.extension_paths) if requested_resume else None
        if resume_state and not _can_resume_extension_build(
            resume_state,
            pipeline='pdf',
            manifest_signature=manifest_signature,
            parser_schema_version=_parser_schema_version('pdf'),
            source_roots=tuple(str(item.resolve()) for item in source_dirs),
            enabled_formats=(),
        ):
            clear_extension_build_state(self.extension_paths)
            resume_state = None
        build_id = str((resume_state or {}).get('build_id') or _make_build_id('pdf'))
        build_payload: dict[str, object] = {
            'state_version': EXTENSION_BUILD_STATE_VERSION,
            'pipeline': 'pdf',
            'build_id': build_id,
            'task_kind': kind.value,
            'incremental': bool(incremental),
            'targeted': bool(source_paths),
            'resume_requested': bool(resume),
            'resume_available': bool(not incremental and not bool(source_paths)),
            'manifest_signature': manifest_signature,
            'parser_schema_version': parser_contract['parser_schema_version'],
            'source_roots': [str(item.resolve()) for item in source_dirs],
            'enabled_formats': [],
            'runtime_contract': parser_contract,
            'status': 'building',
            'phase': 'prepare_runtime',
            'started_at': str((resume_state or {}).get('started_at') or utc_now()),
            'updated_at': utc_now(),
            'current_path': '',
            'processed_files': int((resume_state or {}).get('processed_files') or 0),
            'skipped_files': int((resume_state or {}).get('skipped_files') or 0),
            'failed_files': int((resume_state or {}).get('failed_files') or 0),
            'deleted_files': 0,
            'current': int((resume_state or {}).get('current') or 0),
            'total': len(files),
            'completed_files': dict((resume_state or {}).get('completed_files') or {}),
        }
        if not source_dirs:
            clear_extension_build_state(self.extension_paths)
            self._update_pdf_status(
                index_state=ExtensionIndexState.NOT_BUILT if self._state.pdf_config.enabled else ExtensionIndexState.DISABLED,
                build_in_progress=False,
                build_id='',
                vector_ready=False,
                query_ready=False,
                resume_available=False,
                indexed_document_count=0,
                last_error='',
            )
            self._persist_state()
            return PdfBuildReport(
                indexed_files=0,
                indexed_chunks=0,
                build_id=build_id,
                missing_directories=tuple(str(item) for item in missing_dirs),
            )

        self.coordinator.reserve(request)
        try:
            acquire_extension_build_lease(
                self.extension_paths,
                pipeline='pdf',
                build_id=build_id,
                workspace_id=self.paths.workspace_id,
                data_root=str(self.paths.global_root),
                owner_pid=os.getpid(),
            )
        except Exception:
            self.coordinator.release(request)
            raise
        monitor = ExtensionBuildProgressMonitor(
            pipeline='pdf',
            build_id=build_id,
            extension_paths=self.extension_paths,
            on_progress=on_progress,
        )
        build_context = ExtensionBuildContext(
            pipeline='pdf',
            build_id=build_id,
            task_kind=kind,
            manifest_signature=manifest_signature,
            parser_schema_version=parser_contract['parser_schema_version'],
            source_roots=tuple(str(item.resolve()) for item in source_dirs),
            on_progress=lambda payload: monitor.emit(payload, material_progress=not bool(payload.get('heartbeat_only'))),
            pause_event=pause_event,
            cancel_event=cancel_event,
            incremental=incremental,
            targeted=bool(source_paths),
            resume_requested=requested_resume,
            resume_state=resume_state,
            state_payload=build_payload,
        )
        self._update_pdf_status(
            index_state=ExtensionIndexState.BUILDING,
            build_in_progress=True,
            build_id=build_id,
            vector_ready=False,
            query_ready=False,
            resume_available=False,
            last_error='',
        )
        write_extension_build_state(self.extension_paths, build_payload)
        monitor.start()
        deleted_files = 0
        skipped_files = 0
        recent_issues: list[str] = []
        try:
            if incremental:
                deleted_files, skipped_files, recent_issues = self._scan_once(source_dirs, files, build_context=build_context)
            else:
                skipped_files, recent_issues = self._full_rebuild(source_dirs, files, build_context=build_context)
            stats = self.store.stats()
            vector_ready, query_ready = _vector_contract(self.store, self.vector_index, self._vector_enabled)
            clear_extension_build_state(self.extension_paths)
            self._update_pdf_status(
                index_state=ExtensionIndexState.READY,
                build_in_progress=False,
                build_id='',
                vector_ready=vector_ready,
                query_ready=query_ready,
                resume_available=False,
                last_successful_build_id=build_id,
                last_completed_at=utc_now(),
                indexed_document_count=int(stats.get('files', 0) or 0),
                last_error='',
            )
            return PdfBuildReport(
                indexed_files=int(stats.get('files', 0) or 0),
                indexed_chunks=int(stats.get('chunks', 0) or 0),
                build_id=build_id,
                skipped_files=skipped_files,
                deleted_files=deleted_files,
                vector_ready=vector_ready,
                missing_directories=tuple(str(item) for item in missing_dirs),
                recent_issues=tuple(recent_issues),
                rebuilt=not incremental,
            )
        except BuildCancelledError:
            resumable = bool(not incremental and not bool(source_paths))
            _mark_build_interrupted(
                self.extension_paths,
                build_payload,
                resumable=resumable,
                last_error='extension_build_cancelled',
                phase=str(build_payload.get('phase') or 'parse_files'),
            )
            stats = self.store.stats()
            self._update_pdf_status(
                index_state=ExtensionIndexState.RESUMABLE if resumable else ExtensionIndexState.INTERRUPTED,
                build_in_progress=False,
                build_id=build_id,
                vector_ready=False,
                query_ready=False,
                resume_available=resumable,
                indexed_document_count=int(stats.get('files', 0) or 0),
                last_error='extension_build_cancelled',
            )
            return PdfBuildReport(
                indexed_files=int(stats.get('files', 0) or 0),
                indexed_chunks=int(stats.get('chunks', 0) or 0),
                build_id=build_id,
                skipped_files=int(build_payload.get('skipped_files') or 0),
                deleted_files=int(build_payload.get('deleted_files') or 0),
                cancelled=True,
                resume_available=resumable,
                vector_ready=False,
                missing_directories=tuple(str(item) for item in missing_dirs),
                recent_issues=tuple(recent_issues),
                rebuilt=not incremental,
            )
        except Exception as exc:
            resumable = bool(not incremental and not bool(source_paths))
            _mark_build_interrupted(
                self.extension_paths,
                build_payload,
                resumable=resumable,
                last_error=f'{type(exc).__name__}: {exc}',
                phase=str(build_payload.get('phase') or 'parse_files'),
            )
            stats = self.store.stats()
            self._update_pdf_status(
                index_state=ExtensionIndexState.RESUMABLE if resumable else ExtensionIndexState.ERROR,
                build_in_progress=False,
                build_id=build_id,
                vector_ready=False,
                query_ready=False,
                resume_available=resumable,
                indexed_document_count=int(stats.get('files', 0) or 0),
                last_error=f'{type(exc).__name__}: {exc}',
            )
            raise
        finally:
            monitor.stop()
            release_extension_build_lease(self.extension_paths, build_id=build_id)
            self.coordinator.release(request)
            self._persist_state()

    def _full_rebuild(
        self,
        source_dirs: list[Path],
        files: list[tuple[Path, Path]],
        *,
        build_context: ExtensionBuildContext,
    ) -> tuple[int, list[str]]:
        existing_paths: list[str] = []
        if build_context.targeted:
            existing_paths = self._indexed_paths_under_roots(source_dirs)
            if existing_paths:
                self._delete_paths_from_index(existing_paths)
        elif not build_context.resume_state:
            self.store.reset_all()
            self.vector_index.reset()
        skipped_files = 0
        recent_issues: list[str] = []
        total = len(files)
        indexed_paths: list[str] = []
        completed_files = _resume_completed_files(build_context.resume_state)
        _update_build_state_payload(
            self.extension_paths,
            payload=build_context.state_payload or build_context.resume_state or {
                'state_version': EXTENSION_BUILD_STATE_VERSION,
                'pipeline': build_context.pipeline,
                'build_id': build_context.build_id,
                'manifest_signature': build_context.manifest_signature,
                'parser_schema_version': build_context.parser_schema_version,
                'source_roots': list(build_context.source_roots),
                'enabled_formats': [],
                'completed_files': completed_files,
            },
            phase='scan_sources',
            status='building',
            total=total,
            deleted_files=len(existing_paths) if build_context.targeted else 0,
        )
        _emit_extension_stage(
            build_context.on_progress,
            stage='pdf_build',
            stage_status='scan_sources',
            current=0,
            total=total,
            processed_files=len(completed_files),
            skipped_files=0,
            deleted_files=len(existing_paths) if build_context.targeted else 0,
            overall_percent=0.0,
            close_safe=False,
            build_id=build_context.build_id,
            pipeline=build_context.pipeline,
        )
        for current, (source_root, pdf_path) in enumerate(files, start=1):
            _wait_for_extension_controls(build_context.pause_event, build_context.cancel_event)
            path_key = str(pdf_path.resolve())
            _emit_extension_stage(
                build_context.on_progress,
                stage='pdf_build',
                stage_status='parse_pdf',
                current=current,
                total=total,
                current_path=path_key,
                processed_files=max(current - 1, 0),
                skipped_files=skipped_files,
                error_count=skipped_files,
                deleted_files=len(existing_paths) if build_context.targeted else 0,
                overall_percent=round((current / total) * 86.0, 2) if total else 0.0,
                close_safe=False,
                build_id=build_context.build_id,
                pipeline=build_context.pipeline,
                eta_seconds=_estimate_eta_seconds(started_at=build_context.start_monotonic, current=current, total=total),
            )
            _update_build_state_payload(
                self.extension_paths,
                payload=build_context.state_payload or build_context.resume_state or {
                    'state_version': EXTENSION_BUILD_STATE_VERSION,
                    'pipeline': build_context.pipeline,
                    'build_id': build_context.build_id,
                    'manifest_signature': build_context.manifest_signature,
                    'parser_schema_version': build_context.parser_schema_version,
                    'source_roots': list(build_context.source_roots),
                    'enabled_formats': [],
                    'completed_files': completed_files,
                },
                phase='parse_files',
                status='building',
                current_path=path_key,
                processed_files=max(current - 1, 0),
                skipped_files=skipped_files,
                failed_files=skipped_files,
                deleted_files=len(existing_paths) if build_context.targeted else 0,
                current=current,
                total=total,
                completed_files=completed_files,
            )
            stored_fingerprint = completed_files.get(path_key)
            if stored_fingerprint and fingerprint_matches(pdf_path, stored_fingerprint):
                indexed_paths.append(path_key)
                continue
            indexed, reason = self._replace_one_pdf(source_root, pdf_path)
            if not indexed:
                skipped_files += 1
                if reason:
                    _remember_recent_issue(recent_issues, reason)
                continue
            completed_files[path_key] = file_fingerprint(pdf_path)
            indexed_paths.append(path_key)
            _update_build_state_payload(
                self.extension_paths,
                payload=build_context.state_payload or build_context.resume_state or {
                    'state_version': EXTENSION_BUILD_STATE_VERSION,
                    'pipeline': build_context.pipeline,
                    'build_id': build_context.build_id,
                    'manifest_signature': build_context.manifest_signature,
                    'parser_schema_version': build_context.parser_schema_version,
                    'source_roots': list(build_context.source_roots),
                    'enabled_formats': [],
                    'completed_files': completed_files,
                },
                phase='parse_files',
                status='building',
                current_path=path_key,
                processed_files=current,
                skipped_files=skipped_files,
                failed_files=skipped_files,
                deleted_files=len(existing_paths) if build_context.targeted else 0,
                current=current,
                total=total,
                completed_files=completed_files,
            )
        _wait_for_extension_controls(build_context.pause_event, build_context.cancel_event)
        _emit_extension_stage(
            build_context.on_progress,
            stage='pdf_build',
            stage_status='write_vector',
            current=len(indexed_paths),
            total=total,
            processed_files=len(indexed_paths),
            skipped_files=skipped_files,
            error_count=skipped_files,
            deleted_files=len(existing_paths) if build_context.targeted else 0,
            overall_percent=92.0 if total else 100.0,
            close_safe=False,
            build_id=build_context.build_id,
            pipeline=build_context.pipeline,
        )
        _update_build_state_payload(
            self.extension_paths,
            payload=build_context.state_payload or build_context.resume_state or {
                'state_version': EXTENSION_BUILD_STATE_VERSION,
                'pipeline': build_context.pipeline,
                'build_id': build_context.build_id,
                'manifest_signature': build_context.manifest_signature,
                'parser_schema_version': build_context.parser_schema_version,
                'source_roots': list(build_context.source_roots),
                'enabled_formats': [],
                'completed_files': completed_files,
            },
            phase='write_vector',
            status='building',
            processed_files=len(indexed_paths),
            skipped_files=skipped_files,
            failed_files=skipped_files,
            deleted_files=len(existing_paths) if build_context.targeted else 0,
            current=len(indexed_paths),
            total=total,
            completed_files=completed_files,
        )
        if build_context.targeted:
            if indexed_paths:
                self._upsert_vectors_for_paths(indexed_paths)
        else:
            self._rebuild_vectors(build_context=build_context)
        _emit_extension_stage(
            build_context.on_progress,
            stage='pdf_build',
            stage_status='finalizing',
            current=len(indexed_paths),
            total=total,
            processed_files=len(indexed_paths),
            skipped_files=skipped_files,
            error_count=skipped_files,
            deleted_files=len(existing_paths) if build_context.targeted else 0,
            overall_percent=100.0,
            close_safe=True,
            build_id=build_context.build_id,
            pipeline=build_context.pipeline,
        )
        _update_build_state_payload(
            self.extension_paths,
            payload=build_context.state_payload or build_context.resume_state or {
                'state_version': EXTENSION_BUILD_STATE_VERSION,
                'pipeline': build_context.pipeline,
                'build_id': build_context.build_id,
                'manifest_signature': build_context.manifest_signature,
                'parser_schema_version': build_context.parser_schema_version,
                'source_roots': list(build_context.source_roots),
                'enabled_formats': [],
                'completed_files': completed_files,
            },
            phase='finalizing',
            status='building',
            processed_files=len(indexed_paths),
            skipped_files=skipped_files,
            failed_files=skipped_files,
            deleted_files=len(existing_paths) if build_context.targeted else 0,
            current=len(indexed_paths),
            total=total,
            completed_files=completed_files,
        )
        return skipped_files, recent_issues

    def _scan_once(
        self,
        source_dirs: list[Path],
        files: list[tuple[Path, Path]],
        *,
        build_context: ExtensionBuildContext,
    ) -> tuple[int, int, list[str]]:
        previous_manifest = self.store.fetch_file_manifest()
        current_manifest: dict[str, tuple[float, int]] = {}
        source_by_path: dict[str, tuple[Path, Path]] = {}
        for source_root, pdf_path in files:
            try:
                stat = pdf_path.stat()
            except OSError:
                continue
            path_key = str(pdf_path.resolve())
            current_manifest[path_key] = (float(stat.st_mtime), int(stat.st_size))
            source_by_path[path_key] = (source_root, pdf_path.resolve())

        available_roots = tuple(str(root.resolve()) for root in source_dirs)
        deleted_paths = [
            path for path in previous_manifest
            if _path_belongs_to_roots(path, available_roots) and path not in current_manifest
        ]
        changed_paths = [
            path for path, metadata in current_manifest.items()
            if previous_manifest.get(path) != metadata
        ]
        if deleted_paths:
            self._delete_paths_from_index(deleted_paths)
        skipped_files = 0
        recent_issues: list[str] = []
        total = len(changed_paths)
        _emit_extension_stage(
            build_context.on_progress,
            stage='pdf_scan_once',
            stage_status='scan_sources',
            current=0,
            total=total,
            processed_files=0,
            skipped_files=0,
            deleted_files=len(deleted_paths),
            overall_percent=0.0,
            close_safe=False,
            build_id=build_context.build_id,
            pipeline=build_context.pipeline,
        )
        for current, path in enumerate(changed_paths, start=1):
            _wait_for_extension_controls(build_context.pause_event, build_context.cancel_event)
            source_root, pdf_path = source_by_path[path]
            _emit_extension_stage(
                build_context.on_progress,
                stage='pdf_scan_once',
                stage_status='parse_pdf',
                current=current,
                total=total,
                current_path=str(pdf_path),
                processed_files=max(current - 1, 0),
                skipped_files=skipped_files,
                error_count=skipped_files,
                deleted_files=len(deleted_paths),
                overall_percent=round((current / total) * 86.0, 2) if total else 0.0,
                close_safe=False,
                build_id=build_context.build_id,
                pipeline=build_context.pipeline,
                eta_seconds=_estimate_eta_seconds(started_at=build_context.start_monotonic, current=current, total=total),
            )
            self._delete_paths_from_index([path])
            indexed, reason = self._replace_one_pdf(source_root, pdf_path)
            if not indexed:
                skipped_files += 1
                if reason:
                    _remember_recent_issue(recent_issues, reason)
        if changed_paths:
            _wait_for_extension_controls(build_context.pause_event, build_context.cancel_event)
            _emit_extension_stage(
                build_context.on_progress,
                stage='pdf_scan_once',
                stage_status='write_vector',
                current=len(changed_paths) - skipped_files,
                total=total,
                processed_files=len(changed_paths) - skipped_files,
                skipped_files=skipped_files,
                error_count=skipped_files,
                deleted_files=len(deleted_paths),
                overall_percent=92.0 if total else 100.0,
                close_safe=False,
                build_id=build_context.build_id,
                pipeline=build_context.pipeline,
            )
            self._upsert_vectors_for_paths(changed_paths)
        _emit_extension_stage(
            build_context.on_progress,
            stage='pdf_scan_once',
            stage_status='finalizing',
            current=len(changed_paths) - skipped_files,
            total=total,
            processed_files=len(changed_paths) - skipped_files,
            skipped_files=skipped_files,
            error_count=skipped_files,
            deleted_files=len(deleted_paths),
            overall_percent=100.0,
            close_safe=True,
            build_id=build_context.build_id,
            pipeline=build_context.pipeline,
        )
        return len(deleted_paths), skipped_files, recent_issues

    def _replace_one_pdf(self, source_root: Path, pdf_path: Path) -> tuple[bool, str]:
        try:
            parsed = _parse_pdf_file(source_root, pdf_path)
        except Exception as exc:
            LOGGER.warning('PDF extension skipped broken file: %s (%s: %s)', pdf_path, type(exc).__name__, exc)
            return False, f'{Path(pdf_path).name} · {type(exc).__name__}'
        limit_reason = _parsed_file_limit_reason(parsed)
        if limit_reason:
            LOGGER.warning('PDF extension skipped oversize file: %s (%s)', pdf_path, limit_reason)
            return False, f'{Path(pdf_path).name} · {limit_reason}'
        self.store.replace_file(parsed)
        rendered_payloads = [
            (chunk.chunk_id, chunk.raw_text)
            for chunk in parsed.chunks
            if chunk.raw_text.strip()
        ]
        if rendered_payloads:
            self.store.update_rendered_chunks(rendered_payloads)
        return True, ''

    def _rebuild_vectors(self, *, build_context: ExtensionBuildContext) -> None:
        if not self._vector_enabled:
            return
        total = self.store.count_vector_documents()

        def handle_vector_progress(payload: dict[str, object]) -> None:
            current = int(payload.get('current') or payload.get('written_count') or 0)
            processed = int(payload.get('written_count') or current)
            _emit_extension_stage(
                build_context.on_progress,
                stage='pdf_build',
                stage_status='write_vector',
                current=current,
                total=int(payload.get('total') or total),
                processed_files=processed,
                skipped_files=0,
                error_count=0,
                overall_percent=92.0 if total else 100.0,
                close_safe=False,
                build_id=build_context.build_id,
                pipeline=build_context.pipeline,
                eta_seconds=int(payload.get('eta_seconds') or 0),
            )

        self.vector_index.rebuild(
            self.store.iter_vector_documents(),
            total=total,
            on_progress=handle_vector_progress,
            pause_event=build_context.pause_event,
            cancel_event=build_context.cancel_event,
            reset_index=True,
        )

    def _upsert_vectors_for_paths(self, source_paths: list[str]) -> None:
        if not self._vector_enabled:
            return
        chunk_ids = self.store.get_chunk_ids_for_paths(source_paths)
        if chunk_ids:
            self.vector_index.delete(chunk_ids)
        documents = self.store.fetch_vector_documents(source_paths)
        if documents:
            self.vector_index.upsert(documents)

    def _delete_paths_from_index(self, source_paths: list[str]) -> None:
        clean_paths = [item for item in source_paths if item]
        if not clean_paths:
            return
        chunk_ids = self.store.get_chunk_ids_for_paths(clean_paths)
        if chunk_ids and self._vector_enabled:
            self.vector_index.delete(chunk_ids)
        self.store.delete_files(clean_paths)

    def _refresh_state(self) -> None:
        self._state = self.registry.load(self.paths)

    def _persist_state(self) -> None:
        self.registry.save(self.paths, self._state)

    def _indexed_paths_under_roots(self, roots: list[Path]) -> list[str]:
        manifest = self.store.fetch_file_manifest()
        root_keys = tuple(str(root.resolve()) for root in roots)
        return [path for path in manifest if _path_belongs_to_roots(path, root_keys)]

    def _selected_source_directories(self, source_paths: list[str] | tuple[str, ...] | None = None) -> tuple[list[Path], list[Path]]:
        selected: list[Path] = []
        missing: list[Path] = []
        changed = False
        filter_set = {str(Path(item).resolve()).lower() for item in (source_paths or []) if str(item).strip()}
        for source in self._state.pdf_config.source_directories:
            if not source.selected or source.state == ExtensionDirectoryState.REMOVED_CONFIRMED:
                continue
            if filter_set and str(Path(source.path).expanduser().resolve()).lower() not in filter_set:
                continue
            candidate = Path(source.path).expanduser()
            if candidate.exists() and candidate.is_dir():
                resolved = candidate.resolve()
                selected.append(resolved)
                if source.state != ExtensionDirectoryState.ENABLED or source.last_error:
                    source.state = ExtensionDirectoryState.ENABLED
                    source.last_error = ''
                    changed = True
                continue
            missing.append(candidate)
            if source.state != ExtensionDirectoryState.MISSING_TEMPORARILY or source.last_error != 'source_path_missing':
                source.state = ExtensionDirectoryState.MISSING_TEMPORARILY
                source.last_error = 'source_path_missing'
                changed = True
        if changed:
            self._persist_state()
        return _unique_paths(selected), _unique_paths(missing)

    def _iter_pdf_files(self, source_dirs: list[Path]):
        seen: set[str] = set()
        for source_root in source_dirs:
            for pdf_path in sorted(source_root.rglob('*.pdf')):
                resolved = str(pdf_path.resolve())
                if resolved in seen:
                    continue
                seen.add(resolved)
                yield source_root, pdf_path.resolve()

    def _update_pdf_status(
        self,
        *,
        index_state: ExtensionIndexState | None = None,
        build_in_progress: bool | None = None,
        build_id: str | None = None,
        vector_ready: bool | None = None,
        query_ready: bool | None = None,
        resume_available: bool | None = None,
        last_successful_build_id: str | None = None,
        last_completed_at: str | None = None,
        indexed_document_count: int | None = None,
        last_error: str | None = None,
    ) -> None:
        status = self._state.snapshot.pdf
        if index_state is not None:
            status.index_state = index_state
        if build_in_progress is not None:
            status.build_in_progress = build_in_progress
        if build_id is not None:
            status.build_id = str(build_id or '')
        if vector_ready is not None:
            status.vector_ready = bool(vector_ready)
        if query_ready is not None:
            status.query_ready = bool(query_ready)
        if resume_available is not None:
            status.resume_available = bool(resume_available)
        if last_successful_build_id is not None:
            status.last_successful_build_id = str(last_successful_build_id or '')
        if last_completed_at is not None:
            status.last_completed_at = str(last_completed_at or '')
        if indexed_document_count is not None:
            status.indexed_document_count = max(int(indexed_document_count), 0)
        if last_error is not None:
            status.last_error = last_error

    def _merge_candidate_rows(self, storage_candidates, vector_candidates: dict[str, float]):
        candidate_map = {row['chunk_id']: row for row in storage_candidates}
        missing_vector_ids = [chunk_id for chunk_id in vector_candidates if chunk_id not in candidate_map]
        if missing_vector_ids:
            for row in self.store.fetch_rows_by_chunk_ids(missing_vector_ids):
                candidate_map[row['chunk_id']] = row
        return list(candidate_map.values())

    def _decorate_hit(self, hit: SearchHit) -> SearchHit:
        page_no = _extract_page_no(hit.anchor)
        page_label = f'第 {page_no} 页' if page_no > 0 else (hit.anchor or 'PDF')
        source_name = Path(hit.source_path).name or Path(hit.source_path).stem or 'PDF'
        source_label = f'PDF · {source_name} · {page_label}'
        hit.source_family = 'pdf'
        hit.source_kind = 'pdf'
        hit.page_no = page_no
        hit.source_label = source_label
        hit.title = source_label
        hit.anchor = page_label
        return hit


class TikaExtensionService:
    """Own the isolated Tika parse/build/query lifecycle.

    Why: Tika gives the extension subsystem a single managed parsing gateway for
    many office/web formats, but every parsed document must still stay inside a
    dedicated extension namespace and never touch the Markdown mainline.
    """

    def __init__(
        self,
        config: AppConfig,
        paths: DataPaths,
        *,
        coordinator: ExtensionTaskCoordinator | None = None,
        registry: ExtensionRegistry | None = None,
        runtime_manager: TikaSidecarManager | None = None,
    ) -> None:
        self.config = config
        self.paths = paths
        self.coordinator = coordinator or ExtensionTaskCoordinator()
        self.registry = registry or ExtensionRegistry()
        self.runtime_manager = runtime_manager or TikaSidecarManager()
        self.extension_paths = build_extension_data_paths(paths, 'tika')
        self.store = MetadataStore(self.extension_paths.sqlite_file)
        self._vector_runtime_issue = runtime_dependency_issue(config) if _vector_backend_enabled(config) else None
        self._vector_enabled = _vector_backend_enabled(config) and not self._vector_runtime_issue
        if self._vector_runtime_issue:
            LOGGER.warning('Tika extension is falling back to lexical-only mode because vector runtime is unavailable. %s', self._vector_runtime_issue)
        self.vector_index = create_vector_index(config, self.extension_paths) if self._vector_enabled else NullVectorIndex()
        self._state = self.registry.load(paths)

    def close(self) -> None:
        self.store.close()

    def preflight(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
    ) -> TikaPreflightReport:
        self._refresh_state()
        _ensure_tika_parser_ready()
        source_dirs, missing_dirs = self._selected_tika_source_directories(source_paths)
        enabled_formats = self._enabled_tika_formats()
        files = list(self._iter_tika_files(source_dirs, enabled_formats))
        total_files = 0
        total_bytes = 0
        skipped_files = 0
        recent_issues: list[str] = []
        started_at = time.monotonic()
        _emit_extension_stage(
            on_progress,
            stage='tika_preflight',
            stage_status='prepare_runtime',
            close_safe=True,
            pipeline='tika',
            elapsed_seconds=0.0,
        )
        _emit_extension_stage(
            on_progress,
            stage='tika_preflight',
            stage_status='scan_sources',
            current=0,
            total=len(files),
            processed_files=0,
            skipped_files=0,
            close_safe=True,
            pipeline='tika',
            elapsed_seconds=max(time.monotonic() - started_at, 0.0),
        )
        processed_files = 0
        for _source_root, file_path, format_id in files:
            _emit_extension_stage(
                on_progress,
                stage='tika_preflight',
                stage_status='inspect_tika',
                current=processed_files,
                total=len(files),
                current_path=str(file_path),
                format_id=format_id,
                processed_files=processed_files,
                skipped_files=skipped_files,
                error_count=skipped_files,
                close_safe=True,
                pipeline='tika',
                elapsed_seconds=max(time.monotonic() - started_at, 0.0),
            )
            try:
                total_files += 1
                total_bytes += int(file_path.stat().st_size)
            except Exception as exc:
                skipped_files += 1
                _remember_recent_issue(recent_issues, f'{Path(file_path).name} · unreadable_file')
                LOGGER.warning('Tika preflight skipped unreadable file: %s (%s: %s)', file_path, type(exc).__name__, exc)
            processed_files += 1
        _emit_extension_stage(
            on_progress,
            stage='tika_preflight',
            stage_status='finalizing',
            current=processed_files,
            total=len(files),
            processed_files=processed_files,
            skipped_files=skipped_files,
            error_count=skipped_files,
            overall_percent=100.0,
            close_safe=True,
            pipeline='tika',
            elapsed_seconds=max(time.monotonic() - started_at, 0.0),
        )
        self._persist_state()
        return TikaPreflightReport(
            total_files=total_files,
            total_bytes=total_bytes,
            enabled_formats=tuple(enabled_formats),
            skipped_files=skipped_files,
            missing_directories=tuple(str(item) for item in missing_dirs),
            recent_issues=tuple(recent_issues),
        )

    def full_rebuild(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        markdown_rebuild_active: bool = False,
        markdown_watch_active: bool = False,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        resume: bool = False,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
    ) -> TikaBuildReport:
        return self._run_build(
            kind=ExtensionTaskKind.FULL_REBUILD,
            incremental=False,
            source_paths=source_paths,
            markdown_rebuild_active=markdown_rebuild_active,
            markdown_watch_active=markdown_watch_active,
            on_progress=on_progress,
            resume=resume,
            pause_event=pause_event,
            cancel_event=cancel_event,
        )

    def scan_once(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        markdown_rebuild_active: bool = False,
        markdown_watch_active: bool = False,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
    ) -> TikaBuildReport:
        return self._run_build(
            kind=ExtensionTaskKind.SCAN_ONCE,
            incremental=True,
            source_paths=source_paths,
            markdown_rebuild_active=markdown_rebuild_active,
            markdown_watch_active=markdown_watch_active,
            on_progress=on_progress,
            pause_event=pause_event,
            cancel_event=cancel_event,
        )

    def delete_index(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        markdown_rebuild_active: bool = False,
        markdown_watch_active: bool = False,
    ) -> TikaBuildReport:
        self._refresh_state()
        request = ExtensionTaskRequest(pipeline='tika', kind=ExtensionTaskKind.DELETE_INDEX)
        decision = self.coordinator.can_start(
            request,
            markdown_rebuild_active=markdown_rebuild_active,
            markdown_watch_active=markdown_watch_active,
        )
        if not decision.allowed:
            raise RuntimeError(decision.reason)
        self.coordinator.reserve(request)
        try:
            clear_extension_build_state(self.extension_paths)
            release_extension_build_lease(self.extension_paths)
            filtered_roots, _missing_dirs = self._selected_tika_source_directories(source_paths)
            if filtered_roots:
                target_paths = self._indexed_paths_under_roots(filtered_roots)
                if target_paths:
                    self._delete_paths_from_index(target_paths)
                stats = self.store.stats()
                vector_ready, query_ready = _vector_contract(self.store, self.vector_index, self._vector_enabled)
                self._update_tika_status(
                    index_state=ExtensionIndexState.READY if int(stats.get('files', 0) or 0) > 0 else (ExtensionIndexState.NOT_BUILT if self._state.tika_config.enabled else ExtensionIndexState.DISABLED),
                    build_in_progress=False,
                    build_id='',
                    vector_ready=vector_ready,
                    query_ready=query_ready,
                    resume_available=False,
                    indexed_document_count=int(stats.get('files', 0) or 0),
                    last_error='',
                )
                return TikaBuildReport(
                    indexed_files=int(stats.get('files', 0) or 0),
                    indexed_chunks=int(stats.get('chunks', 0) or 0),
                    enabled_formats=tuple(self._enabled_tika_formats()),
                    deleted_files=len(target_paths),
                    vector_ready=vector_ready,
                    rebuilt=False,
                )
            self.store.reset_all()
            self.vector_index.reset()
            self._update_tika_status(
                index_state=ExtensionIndexState.NOT_BUILT if self._state.tika_config.enabled else ExtensionIndexState.DISABLED,
                build_in_progress=False,
                build_id='',
                vector_ready=False,
                query_ready=False,
                resume_available=False,
                indexed_document_count=0,
                last_error='',
            )
            return TikaBuildReport(indexed_files=0, indexed_chunks=0, enabled_formats=tuple(self._enabled_tika_formats()), rebuilt=False)
        finally:
            self.coordinator.release(request)
            self._persist_state()

    def query_hits(self, query_text: str, *, limit: int, profile: QueryProfile | None = None) -> list[SearchHit]:
        self._refresh_state()
        if not self._state.tika_config.enabled:
            return []
        if self._state.snapshot.tika.index_state != ExtensionIndexState.READY or not bool(self._state.snapshot.tika.query_ready):
            return []
        limit = max(int(limit or 0), 1)
        query_profile = profile or build_query_profile(query_text, limit)
        candidate_limit = max(query_profile.candidate_limit, limit)
        storage_candidates = self.store.search_candidates(query_text, candidate_limit)
        vector_candidates: dict[str, float] = {}
        if query_profile.use_vector and self._vector_enabled:
            vector_limit = max(self.config.vector_candidate_limit, candidate_limit)
            vector_candidates = {item.chunk_id: item.score for item in self.vector_index.search(query_text, vector_limit)}
        candidate_rows = self._merge_candidate_rows(storage_candidates, vector_candidates)
        if candidate_rows:
            hits = rank_candidates(query_text, candidate_rows, vector_candidates, query_profile)
        else:
            hits = rank_candidates(query_text, self.store.fetch_all_rendered_chunks(), vector_candidates, query_profile)
        return [self._decorate_tika_hit(hit) for hit in hits]

    def source_summaries(self, *, source_paths: list[str] | tuple[str, ...] | None = None) -> dict[str, ExtensionSourceIndexSummary]:
        """Return per-source index summaries for the isolated Tika pipeline."""
        self._refresh_state()
        requested = list(source_paths or [item.path for item in self._state.tika_config.source_directories])
        manifest = self.store.fetch_file_manifest()
        summaries: dict[str, ExtensionSourceIndexSummary] = {}
        for source_path in requested:
            normalized = _normalize_source_path_text(source_path)
            if not normalized:
                continue
            summaries[normalized] = _build_source_index_summary(
                self.store,
                manifest,
                normalized,
            )
        return summaries

    def _run_build(
        self,
        *,
        kind: ExtensionTaskKind,
        incremental: bool,
        source_paths: list[str] | tuple[str, ...] | None,
        markdown_rebuild_active: bool,
        markdown_watch_active: bool,
        on_progress: Callable[[dict[str, object]], None] | None,
        resume: bool = False,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
    ) -> TikaBuildReport:
        self._refresh_state()
        parser_contract = _ensure_tika_parser_ready()
        request = ExtensionTaskRequest(pipeline='tika', kind=kind)
        decision = self.coordinator.can_start(
            request,
            markdown_rebuild_active=markdown_rebuild_active,
            markdown_watch_active=markdown_watch_active,
        )
        if not decision.allowed:
            raise RuntimeError(decision.reason)
        source_dirs, missing_dirs = self._selected_tika_source_directories(source_paths)
        enabled_formats = self._enabled_tika_formats()
        if not enabled_formats:
            self._update_tika_status(
                index_state=ExtensionIndexState.NOT_BUILT if self._state.tika_config.enabled else ExtensionIndexState.DISABLED,
                build_in_progress=False,
                build_id='',
                resume_available=False,
                vector_ready=False,
                query_ready=False,
                indexed_document_count=0,
                last_error='tika_no_formats_enabled',
            )
            self._persist_state()
            return TikaBuildReport(indexed_files=0, indexed_chunks=0, enabled_formats=tuple())
        if not incremental and missing_dirs:
            self._update_tika_status(
                index_state=ExtensionIndexState.STALE,
                build_in_progress=False,
                build_id='',
                resume_available=False,
                last_error='tika_sources_missing',
            )
            self._persist_state()
            raise RuntimeError('tika_sources_missing')
        files = list(self._iter_tika_files(source_dirs, enabled_formats))
        manifest_signature = _build_manifest_signature('tika', source_roots=source_dirs, enabled_formats=enabled_formats, files=files)
        requested_resume = bool(resume and not incremental and not bool(source_paths))
        resume_state = read_extension_build_state(self.extension_paths) if requested_resume else None
        if resume_state and not _can_resume_extension_build(
            resume_state,
            pipeline='tika',
            manifest_signature=manifest_signature,
            parser_schema_version=_parser_schema_version('tika'),
            source_roots=tuple(str(item.resolve()) for item in source_dirs),
            enabled_formats=tuple(enabled_formats),
        ):
            clear_extension_build_state(self.extension_paths)
            resume_state = None
        build_id = str((resume_state or {}).get('build_id') or _make_build_id('tika'))
        build_payload: dict[str, object] = {
            'state_version': EXTENSION_BUILD_STATE_VERSION,
            'pipeline': 'tika',
            'build_id': build_id,
            'task_kind': kind.value,
            'incremental': bool(incremental),
            'targeted': bool(source_paths),
            'resume_requested': bool(resume),
            'resume_available': bool(not incremental and not bool(source_paths)),
            'manifest_signature': manifest_signature,
            'parser_schema_version': parser_contract['parser_schema_version'],
            'source_roots': [str(item.resolve()) for item in source_dirs],
            'enabled_formats': list(enabled_formats),
            'runtime_contract': parser_contract,
            'status': 'building',
            'phase': 'prepare_runtime',
            'started_at': str((resume_state or {}).get('started_at') or utc_now()),
            'updated_at': utc_now(),
            'current_path': '',
            'processed_files': int((resume_state or {}).get('processed_files') or 0),
            'skipped_files': int((resume_state or {}).get('skipped_files') or 0),
            'failed_files': int((resume_state or {}).get('failed_files') or 0),
            'deleted_files': 0,
            'current': int((resume_state or {}).get('current') or 0),
            'total': len(files),
            'completed_files': dict((resume_state or {}).get('completed_files') or {}),
        }
        if not source_dirs:
            clear_extension_build_state(self.extension_paths)
            self._update_tika_status(
                index_state=ExtensionIndexState.NOT_BUILT if self._state.tika_config.enabled else ExtensionIndexState.DISABLED,
                build_in_progress=False,
                build_id='',
                vector_ready=False,
                query_ready=False,
                resume_available=False,
                indexed_document_count=0,
                last_error='',
            )
            self._persist_state()
            return TikaBuildReport(
                indexed_files=0,
                indexed_chunks=0,
                enabled_formats=tuple(enabled_formats),
                build_id=build_id,
                missing_directories=tuple(str(item) for item in missing_dirs),
            )

        self.coordinator.reserve(request)
        try:
            acquire_extension_build_lease(
                self.extension_paths,
                pipeline='tika',
                build_id=build_id,
                workspace_id=self.paths.workspace_id,
                data_root=str(self.paths.global_root),
                owner_pid=os.getpid(),
            )
        except Exception:
            self.coordinator.release(request)
            raise
        monitor = ExtensionBuildProgressMonitor(
            pipeline='tika',
            build_id=build_id,
            extension_paths=self.extension_paths,
            on_progress=on_progress,
        )
        build_context = ExtensionBuildContext(
            pipeline='tika',
            build_id=build_id,
            task_kind=kind,
            manifest_signature=manifest_signature,
            parser_schema_version=parser_contract['parser_schema_version'],
            source_roots=tuple(str(item.resolve()) for item in source_dirs),
            enabled_formats=tuple(enabled_formats),
            on_progress=lambda payload: monitor.emit(payload, material_progress=not bool(payload.get('heartbeat_only'))),
            pause_event=pause_event,
            cancel_event=cancel_event,
            incremental=incremental,
            targeted=bool(source_paths),
            resume_requested=requested_resume,
            resume_state=resume_state,
            state_payload=build_payload,
        )
        self._update_tika_status(
            index_state=ExtensionIndexState.BUILDING,
            build_in_progress=True,
            build_id=build_id,
            vector_ready=False,
            query_ready=False,
            resume_available=False,
            last_error='',
        )
        write_extension_build_state(self.extension_paths, build_payload)
        monitor.start()
        deleted_files = 0
        try:
            _emit_extension_stage(
                build_context.on_progress,
                stage='tika_build',
                stage_status='prepare_runtime',
                current=0,
                total=len(files),
                overall_percent=0.0,
                close_safe=False,
                build_id=build_id,
                pipeline='tika',
            )
            runtime = self.runtime_manager.ensure_started(self.paths)
            self._state.snapshot.tika.runtime = runtime
            if not runtime.installed or not runtime.running or not runtime.healthy:
                self._update_tika_status(index_state=ExtensionIndexState.ERROR, build_in_progress=False, build_id=build_id, last_error='tika_runtime_unavailable')
                raise RuntimeError('tika_runtime_unavailable')
            if incremental:
                deleted_files, build_stats = self._scan_once(source_dirs, enabled_formats, files=files, build_context=build_context)
            else:
                build_stats = self._full_rebuild(source_dirs, enabled_formats, files=files, build_context=build_context)
            stats = self.store.stats()
            vector_ready, query_ready = _vector_contract(self.store, self.vector_index, self._vector_enabled)
            clear_extension_build_state(self.extension_paths)
            self._update_tika_status(
                index_state=ExtensionIndexState.READY,
                build_in_progress=False,
                build_id='',
                vector_ready=vector_ready,
                query_ready=query_ready,
                resume_available=False,
                last_successful_build_id=build_id,
                last_completed_at=utc_now(),
                indexed_document_count=int(stats.get('files', 0) or 0),
                last_error='',
            )
            return TikaBuildReport(
                indexed_files=int(stats.get('files', 0) or 0),
                indexed_chunks=int(stats.get('chunks', 0) or 0),
                enabled_formats=tuple(enabled_formats),
                build_id=build_id,
                skipped_files=build_stats.skipped_files,
                expected_skips=build_stats.expected_skips,
                failed_files=build_stats.failed_files,
                deleted_files=deleted_files,
                resume_available=False,
                vector_ready=vector_ready,
                missing_directories=tuple(str(item) for item in missing_dirs),
                recent_issues=tuple(build_stats.recent_issues),
                rebuilt=not incremental,
            )
        except BuildCancelledError:
            resumable = bool(not incremental and not bool(source_paths))
            _mark_build_interrupted(
                self.extension_paths,
                build_payload,
                resumable=resumable,
                last_error='extension_build_cancelled',
                phase=str(build_payload.get('phase') or 'parse_files'),
            )
            stats = self.store.stats()
            self._update_tika_status(
                index_state=ExtensionIndexState.RESUMABLE if resumable else ExtensionIndexState.INTERRUPTED,
                build_in_progress=False,
                build_id=build_id,
                vector_ready=False,
                query_ready=False,
                resume_available=resumable,
                indexed_document_count=int(stats.get('files', 0) or 0),
                last_error='extension_build_cancelled',
            )
            return TikaBuildReport(
                indexed_files=int(stats.get('files', 0) or 0),
                indexed_chunks=int(stats.get('chunks', 0) or 0),
                enabled_formats=tuple(enabled_formats),
                build_id=build_id,
                skipped_files=int(build_payload.get('skipped_files') or 0),
                failed_files=int(build_payload.get('failed_files') or 0),
                deleted_files=int(build_payload.get('deleted_files') or 0),
                cancelled=True,
                resume_available=resumable,
                vector_ready=False,
                missing_directories=tuple(str(item) for item in missing_dirs),
                recent_issues=tuple(str(item) for item in (build_payload.get('recent_issues') or ()) if str(item).strip()),
                rebuilt=not incremental,
            )
        except Exception as exc:
            resumable = bool(not incremental and not bool(source_paths))
            _mark_build_interrupted(
                self.extension_paths,
                build_payload,
                resumable=resumable,
                last_error=f'{type(exc).__name__}: {exc}',
                phase=str(build_payload.get('phase') or 'parse_files'),
            )
            stats = self.store.stats()
            self._update_tika_status(
                index_state=ExtensionIndexState.RESUMABLE if resumable else ExtensionIndexState.ERROR,
                build_in_progress=False,
                build_id=build_id,
                vector_ready=False,
                query_ready=False,
                resume_available=resumable,
                indexed_document_count=int(stats.get('files', 0) or 0),
                last_error=f'{type(exc).__name__}: {exc}',
            )
            raise
        finally:
            monitor.stop()
            release_extension_build_lease(self.extension_paths, build_id=build_id)
            self.coordinator.release(request)
            self._persist_state()

    def _full_rebuild(
        self,
        source_dirs: list[Path],
        enabled_formats: list[str],
        *,
        files: list[tuple[Path, Path, str]],
        build_context: ExtensionBuildContext,
    ) -> TikaBuildStats:
        existing_paths: list[str] = []
        if build_context.targeted:
            existing_paths = self._indexed_paths_under_roots(source_dirs)
            if existing_paths:
                self._delete_paths_from_index(existing_paths)
        elif not build_context.resume_state:
            self.store.reset_all()
            self.vector_index.reset()
        build_stats = TikaBuildStats()
        total = len(files)
        indexed_paths: list[str] = []
        completed_files = _resume_completed_files(build_context.resume_state)
        _update_build_state_payload(
            self.extension_paths,
            payload=build_context.state_payload or build_context.resume_state or {
                'state_version': EXTENSION_BUILD_STATE_VERSION,
                'pipeline': build_context.pipeline,
                'build_id': build_context.build_id,
                'manifest_signature': build_context.manifest_signature,
                'parser_schema_version': build_context.parser_schema_version,
                'source_roots': list(build_context.source_roots),
                'enabled_formats': list(build_context.enabled_formats),
                'completed_files': completed_files,
            },
            phase='scan_sources',
            status='building',
            total=total,
            deleted_files=len(existing_paths) if build_context.targeted else 0,
        )
        _emit_extension_stage(
            build_context.on_progress,
            stage='tika_build',
            stage_status='scan_sources',
            current=0,
            total=total,
            processed_files=len(completed_files),
            skipped_files=0,
            error_count=0,
            deleted_files=len(existing_paths) if build_context.targeted else 0,
            overall_percent=0.0,
            close_safe=False,
            build_id=build_context.build_id,
            pipeline=build_context.pipeline,
        )
        for current, (source_root, file_path, format_id) in enumerate(files, start=1):
            _wait_for_extension_controls(build_context.pause_event, build_context.cancel_event)
            path_key = str(file_path.resolve())
            _emit_extension_stage(
                build_context.on_progress,
                stage='tika_build',
                stage_status='parse_tika',
                current=current,
                total=total,
                current_path=path_key,
                format_id=format_id,
                processed_files=max(current - 1, 0),
                skipped_files=build_stats.skipped_files,
                error_count=build_stats.failed_files,
                deleted_files=len(existing_paths) if build_context.targeted else 0,
                overall_percent=round((current / total) * 86.0, 2) if total else 0.0,
                close_safe=False,
                recent_issue=build_stats.recent_issues[-1] if build_stats.recent_issues else '',
                build_id=build_context.build_id,
                pipeline=build_context.pipeline,
                eta_seconds=_estimate_eta_seconds(started_at=build_context.start_monotonic, current=current, total=total),
            )
            _update_build_state_payload(
                self.extension_paths,
                payload=build_context.state_payload or build_context.resume_state or {
                    'state_version': EXTENSION_BUILD_STATE_VERSION,
                    'pipeline': build_context.pipeline,
                    'build_id': build_context.build_id,
                    'manifest_signature': build_context.manifest_signature,
                    'parser_schema_version': build_context.parser_schema_version,
                    'source_roots': list(build_context.source_roots),
                    'enabled_formats': list(build_context.enabled_formats),
                    'completed_files': completed_files,
                },
                phase='parse_files',
                status='building',
                current_path=path_key,
                processed_files=max(current - 1, 0),
                skipped_files=build_stats.skipped_files,
                failed_files=build_stats.failed_files,
                deleted_files=len(existing_paths) if build_context.targeted else 0,
                current=current,
                total=total,
                completed_files=completed_files,
            )
            stored_fingerprint = completed_files.get(path_key)
            if stored_fingerprint and fingerprint_matches(file_path, stored_fingerprint):
                indexed_paths.append(path_key)
                continue
            outcome = self._replace_one_tika_file(source_root, file_path, format_id)
            if outcome.status != 'indexed':
                self._apply_tika_outcome(build_stats, outcome)
                _emit_extension_stage(
                    build_context.on_progress,
                    stage='tika_build',
                    stage_status='parse_tika',
                    current=current,
                    total=total,
                    current_path=path_key,
                    format_id=format_id,
                    processed_files=current,
                    skipped_files=build_stats.skipped_files,
                    error_count=build_stats.failed_files,
                    deleted_files=len(existing_paths) if build_context.targeted else 0,
                    overall_percent=round((current / total) * 86.0, 2) if total else 0.0,
                    close_safe=False,
                    recent_issue=outcome.reason,
                    build_id=build_context.build_id,
                    pipeline=build_context.pipeline,
                )
                _update_build_state_payload(
                    self.extension_paths,
                    payload=build_context.state_payload or build_context.resume_state or {
                        'state_version': EXTENSION_BUILD_STATE_VERSION,
                        'pipeline': build_context.pipeline,
                        'build_id': build_context.build_id,
                        'manifest_signature': build_context.manifest_signature,
                        'parser_schema_version': build_context.parser_schema_version,
                        'source_roots': list(build_context.source_roots),
                        'enabled_formats': list(build_context.enabled_formats),
                        'completed_files': completed_files,
                    },
                    phase='parse_files',
                    status='building',
                    current_path=path_key,
                    processed_files=current,
                    skipped_files=build_stats.skipped_files,
                    failed_files=build_stats.failed_files,
                    deleted_files=len(existing_paths) if build_context.targeted else 0,
                    current=current,
                    total=total,
                    completed_files=completed_files,
                )
                continue
            completed_files[path_key] = file_fingerprint(file_path)
            indexed_paths.append(path_key)
            _update_build_state_payload(
                self.extension_paths,
                payload=build_context.state_payload or build_context.resume_state or {
                    'state_version': EXTENSION_BUILD_STATE_VERSION,
                    'pipeline': build_context.pipeline,
                    'build_id': build_context.build_id,
                    'manifest_signature': build_context.manifest_signature,
                    'parser_schema_version': build_context.parser_schema_version,
                    'source_roots': list(build_context.source_roots),
                    'enabled_formats': list(build_context.enabled_formats),
                    'completed_files': completed_files,
                },
                phase='parse_files',
                status='building',
                current_path=path_key,
                processed_files=current,
                skipped_files=build_stats.skipped_files,
                failed_files=build_stats.failed_files,
                deleted_files=len(existing_paths) if build_context.targeted else 0,
                current=current,
                total=total,
                completed_files=completed_files,
            )
        _wait_for_extension_controls(build_context.pause_event, build_context.cancel_event)
        _emit_extension_stage(
            build_context.on_progress,
            stage='tika_build',
            stage_status='write_vector',
            current=len(indexed_paths),
            total=total,
            processed_files=len(indexed_paths),
            skipped_files=build_stats.skipped_files,
            error_count=build_stats.failed_files,
            deleted_files=len(existing_paths) if build_context.targeted else 0,
            overall_percent=92.0 if total else 100.0,
            close_safe=False,
            recent_issue=build_stats.recent_issues[-1] if build_stats.recent_issues else '',
            build_id=build_context.build_id,
            pipeline=build_context.pipeline,
        )
        _update_build_state_payload(
            self.extension_paths,
            payload=build_context.state_payload or build_context.resume_state or {
                'state_version': EXTENSION_BUILD_STATE_VERSION,
                'pipeline': build_context.pipeline,
                'build_id': build_context.build_id,
                'manifest_signature': build_context.manifest_signature,
                'parser_schema_version': build_context.parser_schema_version,
                'source_roots': list(build_context.source_roots),
                'enabled_formats': list(build_context.enabled_formats),
                'completed_files': completed_files,
            },
            phase='write_vector',
            status='building',
            processed_files=len(indexed_paths),
            skipped_files=build_stats.skipped_files,
            failed_files=build_stats.failed_files,
            deleted_files=len(existing_paths) if build_context.targeted else 0,
            current=len(indexed_paths),
            total=total,
            completed_files=completed_files,
        )
        if build_context.targeted:
            if indexed_paths:
                self._upsert_vectors_for_paths(indexed_paths)
        else:
            self._rebuild_vectors(build_context=build_context)
        _emit_extension_stage(
            build_context.on_progress,
            stage='tika_build',
            stage_status='finalizing',
            current=len(indexed_paths),
            total=total,
            processed_files=len(indexed_paths),
            skipped_files=build_stats.skipped_files,
            error_count=build_stats.failed_files,
            deleted_files=len(existing_paths) if build_context.targeted else 0,
            overall_percent=100.0,
            close_safe=True,
            recent_issue=build_stats.recent_issues[-1] if build_stats.recent_issues else '',
            build_id=build_context.build_id,
            pipeline=build_context.pipeline,
        )
        return build_stats

    def _scan_once(
        self,
        source_dirs: list[Path],
        enabled_formats: list[str],
        *,
        files: list[tuple[Path, Path, str]],
        build_context: ExtensionBuildContext,
    ) -> tuple[int, TikaBuildStats]:
        previous_manifest = self.store.fetch_file_manifest()
        current_manifest: dict[str, tuple[float, int]] = {}
        source_by_path: dict[str, tuple[Path, Path, str]] = {}
        for source_root, file_path, format_id in files:
            try:
                stat = file_path.stat()
            except OSError:
                continue
            path_key = str(file_path.resolve())
            current_manifest[path_key] = (float(stat.st_mtime), int(stat.st_size))
            source_by_path[path_key] = (source_root, file_path.resolve(), format_id)

        available_roots = tuple(str(root.resolve()) for root in source_dirs)
        deleted_paths = [path for path in previous_manifest if _path_belongs_to_roots(path, available_roots) and path not in current_manifest]
        changed_paths = [path for path, metadata in current_manifest.items() if previous_manifest.get(path) != metadata]
        if deleted_paths:
            self._delete_paths_from_index(deleted_paths)
        build_stats = TikaBuildStats()
        total = len(changed_paths)
        _emit_extension_stage(
            build_context.on_progress,
            stage='tika_scan_once',
            stage_status='scan_sources',
            current=0,
            total=total,
            processed_files=0,
            skipped_files=0,
            error_count=0,
            deleted_files=len(deleted_paths),
            overall_percent=0.0,
            close_safe=False,
            build_id=build_context.build_id,
            pipeline=build_context.pipeline,
        )
        for current, path in enumerate(changed_paths, start=1):
            _wait_for_extension_controls(build_context.pause_event, build_context.cancel_event)
            source_root, file_path, format_id = source_by_path[path]
            _emit_extension_stage(
                build_context.on_progress,
                stage='tika_scan_once',
                stage_status='parse_tika',
                current=current,
                total=total,
                current_path=str(file_path),
                format_id=format_id,
                processed_files=max(current - 1, 0),
                skipped_files=build_stats.skipped_files,
                error_count=build_stats.failed_files,
                deleted_files=len(deleted_paths),
                overall_percent=round((current / total) * 86.0, 2) if total else 0.0,
                close_safe=False,
                recent_issue=build_stats.recent_issues[-1] if build_stats.recent_issues else '',
                build_id=build_context.build_id,
                pipeline=build_context.pipeline,
                eta_seconds=_estimate_eta_seconds(started_at=build_context.start_monotonic, current=current, total=total),
            )
            self._delete_paths_from_index([path])
            outcome = self._replace_one_tika_file(source_root, file_path, format_id)
            if outcome.status != 'indexed':
                self._apply_tika_outcome(build_stats, outcome)
                _emit_extension_stage(
                    build_context.on_progress,
                    stage='tika_scan_once',
                    stage_status='parse_tika',
                    current=current,
                    total=total,
                    current_path=str(file_path),
                    format_id=format_id,
                    processed_files=current,
                    skipped_files=build_stats.skipped_files,
                    error_count=build_stats.failed_files,
                    deleted_files=len(deleted_paths),
                    overall_percent=round((current / total) * 86.0, 2) if total else 0.0,
                    close_safe=False,
                    recent_issue=outcome.reason,
                    build_id=build_context.build_id,
                    pipeline=build_context.pipeline,
                )
        if changed_paths:
            _wait_for_extension_controls(build_context.pause_event, build_context.cancel_event)
            _emit_extension_stage(
                build_context.on_progress,
                stage='tika_scan_once',
                stage_status='write_vector',
                current=len(changed_paths) - build_stats.skipped_files,
                total=total,
                processed_files=len(changed_paths) - build_stats.skipped_files,
                skipped_files=build_stats.skipped_files,
                error_count=build_stats.failed_files,
                deleted_files=len(deleted_paths),
                overall_percent=92.0 if total else 100.0,
                close_safe=False,
                recent_issue=build_stats.recent_issues[-1] if build_stats.recent_issues else '',
                build_id=build_context.build_id,
                pipeline=build_context.pipeline,
            )
            self._upsert_vectors_for_paths(changed_paths)
        _emit_extension_stage(
            build_context.on_progress,
            stage='tika_scan_once',
            stage_status='finalizing',
            current=len(changed_paths) - build_stats.skipped_files,
            total=total,
            processed_files=len(changed_paths) - build_stats.skipped_files,
            skipped_files=build_stats.skipped_files,
            error_count=build_stats.failed_files,
            deleted_files=len(deleted_paths),
            overall_percent=100.0,
            close_safe=True,
            recent_issue=build_stats.recent_issues[-1] if build_stats.recent_issues else '',
            build_id=build_context.build_id,
            pipeline=build_context.pipeline,
        )
        return len(deleted_paths), build_stats

    def _replace_one_tika_file(self, source_root: Path, file_path: Path, format_id: str) -> TikaFileOutcome:
        resolved = file_path.resolve()
        try:
            stat = resolved.stat()
        except OSError as exc:
            LOGGER.warning('Tika extension skipped unreadable file: %s (%s: %s)', resolved, type(exc).__name__, exc)
            return TikaFileOutcome(status='expected_skip', reason=f'无法读取文件 · {resolved.name}', path=str(resolved))
        if int(stat.st_size or 0) <= 0:
            LOGGER.info('Tika extension skipped empty file: %s', resolved)
            return TikaFileOutcome(status='expected_skip', reason=f'空文件 · {resolved.name}', path=str(resolved))
        try:
            runtime = self._state.snapshot.tika.runtime
            parsed_content = parse_file_with_tika(
                resolved,
                port=runtime.port or 9998,
                timeout=EXTENSION_TIKA_PARSE_TIMEOUT_SECONDS,
            )
            parsed = _parse_tika_file(source_root, resolved, parsed_content, format_id=format_id)
        except Exception as exc:
            if isinstance(exc, TikaParseError) and 'timeout' in str(exc).lower():
                try:
                    self.runtime_manager.stop()
                    self._state.snapshot.tika.runtime = self.runtime_manager.ensure_started(self.paths)
                except Exception:
                    pass
            LOGGER.warning('Tika extension failed for file: %s (%s: %s)', resolved, type(exc).__name__, exc)
            return TikaFileOutcome(status='failed', reason=_format_tika_failure_reason(resolved, exc), path=str(resolved))
        if not parsed.chunks:
            LOGGER.info('Tika extension skipped file without extracted text: %s', resolved)
            return TikaFileOutcome(status='expected_skip', reason=f'未提取到正文 · {resolved.name}', path=str(resolved))
        limit_reason = _parsed_file_limit_reason(parsed)
        if limit_reason:
            LOGGER.warning('Tika extension skipped oversize file: %s (%s)', resolved, limit_reason)
            return TikaFileOutcome(status='expected_skip', reason=f'{resolved.name} · {limit_reason}', path=str(resolved))
        self.store.replace_file(parsed)
        rendered_payloads = [(chunk.chunk_id, chunk.raw_text) for chunk in parsed.chunks if chunk.raw_text.strip()]
        if rendered_payloads:
            self.store.update_rendered_chunks(rendered_payloads)
        return TikaFileOutcome(status='indexed', path=str(resolved), chunk_count=len(parsed.chunks))

    def _apply_tika_outcome(self, build_stats: TikaBuildStats, outcome: TikaFileOutcome) -> None:
        if outcome.status == 'indexed':
            return
        build_stats.skipped_files += 1
        if outcome.status == 'failed':
            build_stats.failed_files += 1
        else:
            build_stats.expected_skips += 1
        if outcome.reason:
            _remember_recent_issue(build_stats.recent_issues, outcome.reason)

    def _rebuild_vectors(self, *, build_context: ExtensionBuildContext) -> None:
        if not self._vector_enabled:
            return
        total = self.store.count_vector_documents()

        def handle_vector_progress(payload: dict[str, object]) -> None:
            current = int(payload.get('current') or payload.get('written_count') or 0)
            processed = int(payload.get('written_count') or current)
            _emit_extension_stage(
                build_context.on_progress,
                stage='tika_build',
                stage_status='write_vector',
                current=current,
                total=int(payload.get('total') or total),
                processed_files=processed,
                skipped_files=0,
                error_count=0,
                overall_percent=92.0 if total else 100.0,
                close_safe=False,
                build_id=build_context.build_id,
                pipeline=build_context.pipeline,
                eta_seconds=int(payload.get('eta_seconds') or 0),
            )

        self.vector_index.rebuild(
            self.store.iter_vector_documents(),
            total=total,
            on_progress=handle_vector_progress,
            pause_event=build_context.pause_event,
            cancel_event=build_context.cancel_event,
            reset_index=True,
        )

    def _upsert_vectors_for_paths(self, source_paths: list[str]) -> None:
        if not self._vector_enabled:
            return
        chunk_ids = self.store.get_chunk_ids_for_paths(source_paths)
        if chunk_ids:
            self.vector_index.delete(chunk_ids)
        documents = self.store.fetch_vector_documents(source_paths)
        if documents:
            self.vector_index.upsert(documents)

    def _delete_paths_from_index(self, source_paths: list[str]) -> None:
        clean_paths = [item for item in source_paths if item]
        if not clean_paths:
            return
        chunk_ids = self.store.get_chunk_ids_for_paths(clean_paths)
        if chunk_ids and self._vector_enabled:
            self.vector_index.delete(chunk_ids)
        self.store.delete_files(clean_paths)

    def _refresh_state(self) -> None:
        self._state = self.registry.load(self.paths)
        self._state.snapshot.tika.runtime = self.runtime_manager.status(self.paths)

    def _persist_state(self) -> None:
        self.registry.save(self.paths, self._state)

    def _indexed_paths_under_roots(self, roots: list[Path]) -> list[str]:
        manifest = self.store.fetch_file_manifest()
        root_keys = tuple(str(root.resolve()) for root in roots)
        return [path for path in manifest if _path_belongs_to_roots(path, root_keys)]

    def _selected_tika_source_directories(self, source_paths: list[str] | tuple[str, ...] | None = None) -> tuple[list[Path], list[Path]]:
        selected: list[Path] = []
        missing: list[Path] = []
        changed = False
        filter_set = {str(Path(item).resolve()).lower() for item in (source_paths or []) if str(item).strip()}
        for source in self._state.tika_config.source_directories:
            if not source.selected or source.state == ExtensionDirectoryState.REMOVED_CONFIRMED:
                continue
            if filter_set and str(Path(source.path).expanduser().resolve()).lower() not in filter_set:
                continue
            candidate = Path(source.path).expanduser()
            if candidate.exists() and candidate.is_dir():
                resolved = candidate.resolve()
                selected.append(resolved)
                if source.state != ExtensionDirectoryState.ENABLED or source.last_error:
                    source.state = ExtensionDirectoryState.ENABLED
                    source.last_error = ''
                    changed = True
                continue
            missing.append(candidate)
            if source.state != ExtensionDirectoryState.MISSING_TEMPORARILY or source.last_error != 'source_path_missing':
                source.state = ExtensionDirectoryState.MISSING_TEMPORARILY
                source.last_error = 'source_path_missing'
                changed = True
        if changed:
            self._persist_state()
        return _unique_paths(selected), _unique_paths(missing)

    def _enabled_tika_formats(self) -> list[str]:
        return [item.format_id for item in self._state.tika_config.selected_formats if item.enabled]

    def _iter_tika_files(self, source_dirs: list[Path], enabled_formats: list[str]):
        matcher = _build_tika_suffix_matcher(set(enabled_formats))
        if not matcher:
            return
        seen: set[str] = set()
        for source_root in source_dirs:
            for file_path in sorted(source_root.rglob('*')):
                if not file_path.is_file():
                    continue
                last_suffix = file_path.suffix.lower()
                candidates = matcher.get(last_suffix)
                if not candidates:
                    continue
                name_lower = file_path.name.lower()
                format_id = ''
                for candidate_id, suffix_pattern in candidates:
                    if name_lower.endswith(suffix_pattern):
                        format_id = candidate_id
                        break
                if not format_id:
                    continue
                resolved_path = file_path.resolve()
                resolved = str(resolved_path)
                if resolved in seen:
                    continue
                seen.add(resolved)
                yield source_root, resolved_path, format_id

    def _update_tika_status(
        self,
        *,
        index_state: ExtensionIndexState | None = None,
        build_in_progress: bool | None = None,
        build_id: str | None = None,
        vector_ready: bool | None = None,
        query_ready: bool | None = None,
        resume_available: bool | None = None,
        last_successful_build_id: str | None = None,
        last_completed_at: str | None = None,
        indexed_document_count: int | None = None,
        last_error: str | None = None,
    ) -> None:
        status = self._state.snapshot.tika
        if index_state is not None:
            status.index_state = index_state
        if build_in_progress is not None:
            status.build_in_progress = build_in_progress
        if build_id is not None:
            status.build_id = str(build_id or '')
        if vector_ready is not None:
            status.vector_ready = bool(vector_ready)
        if query_ready is not None:
            status.query_ready = bool(query_ready)
        if resume_available is not None:
            status.resume_available = bool(resume_available)
        if last_successful_build_id is not None:
            status.last_successful_build_id = str(last_successful_build_id or '')
        if last_completed_at is not None:
            status.last_completed_at = str(last_completed_at or '')
        if indexed_document_count is not None:
            status.indexed_document_count = max(int(indexed_document_count), 0)
        if last_error is not None:
            status.last_error = last_error

    def _merge_candidate_rows(self, storage_candidates, vector_candidates: dict[str, float]):
        candidate_map = {row['chunk_id']: row for row in storage_candidates}
        missing_vector_ids = [chunk_id for chunk_id in vector_candidates if chunk_id not in candidate_map]
        if missing_vector_ids:
            for row in self.store.fetch_rows_by_chunk_ids(missing_vector_ids):
                candidate_map[row['chunk_id']] = row
        return list(candidate_map.values())

    def _decorate_tika_hit(self, hit: SearchHit) -> SearchHit:
        source_name = Path(hit.source_path).name or Path(hit.source_path).stem or 'Tika'
        enabled_formats = self._enabled_tika_formats()
        format_id = ''
        if enabled_formats:
            matcher = _build_tika_suffix_matcher(set(enabled_formats))
            candidates = matcher.get(Path(hit.source_path).suffix.lower())
            if candidates:
                name_lower = Path(hit.source_path).name.lower()
                for candidate_id, suffix_pattern in candidates:
                    if name_lower.endswith(suffix_pattern):
                        format_id = candidate_id
                        break
        if not format_id:
            format_id = Path(hit.source_path).suffix.lstrip('.').lower() or 'tika'
        format_label = format_id.upper() if format_id else 'Tika'
        source_label = f'{format_label}(Tika) · {source_name}'
        hit.source_family = 'tika'
        hit.source_kind = format_id or 'tika'
        hit.source_label = source_label
        hit.title = source_label
        return hit


class ExtensionService:
    """Facade for extension entry points.

    Why: the UI and higher layers should only talk to extension-only methods.
    Internally, phase 3/4 delegates real PDF/Tika work to their isolated
    services while keeping all state outside the Markdown mainline.
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        paths: DataPaths | None = None,
        coordinator: ExtensionTaskCoordinator | None = None,
        runtime_manager: TikaSidecarManager | None = None,
    ) -> None:
        self.config = config
        self.paths = paths
        self.coordinator = coordinator or ExtensionTaskCoordinator()
        self.runtime_manager = runtime_manager

    def run_pdf_preflight(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
    ) -> PdfPreflightReport:
        service = self._pdf_service()
        try:
            return service.preflight(source_paths=source_paths, on_progress=on_progress)
        finally:
            service.close()

    def run_pdf_scan_once(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
    ) -> PdfBuildReport:
        service = self._pdf_service()
        try:
            return service.scan_once(
                source_paths=source_paths,
                on_progress=on_progress,
                pause_event=pause_event,
                cancel_event=cancel_event,
            )
        finally:
            service.close()

    def run_pdf_full_rebuild(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        resume: bool = False,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
    ) -> PdfBuildReport:
        service = self._pdf_service()
        try:
            return service.full_rebuild(
                source_paths=source_paths,
                on_progress=on_progress,
                resume=resume,
                pause_event=pause_event,
                cancel_event=cancel_event,
            )
        finally:
            service.close()

    def run_pdf_delete_index(self, *, source_paths: list[str] | tuple[str, ...] | None = None) -> PdfBuildReport:
        service = self._pdf_service()
        try:
            return service.delete_index(source_paths=source_paths)
        finally:
            service.close()

    def run_pdf_source_summaries(self, *, source_paths: list[str] | tuple[str, ...] | None = None) -> dict[str, ExtensionSourceIndexSummary]:
        service = self._pdf_service()
        try:
            return service.source_summaries(source_paths=source_paths)
        finally:
            service.close()

    def run_tika_preflight(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
    ) -> TikaPreflightReport:
        service = self._tika_service()
        try:
            return service.preflight(source_paths=source_paths, on_progress=on_progress)
        finally:
            service.close()

    def run_tika_scan_once(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
    ) -> TikaBuildReport:
        service = self._tika_service()
        try:
            return service.scan_once(
                source_paths=source_paths,
                on_progress=on_progress,
                pause_event=pause_event,
                cancel_event=cancel_event,
            )
        finally:
            service.close()

    def run_tika_full_rebuild(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        resume: bool = False,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
    ) -> TikaBuildReport:
        service = self._tika_service()
        try:
            return service.full_rebuild(
                source_paths=source_paths,
                on_progress=on_progress,
                resume=resume,
                pause_event=pause_event,
                cancel_event=cancel_event,
            )
        finally:
            service.close()

    def run_tika_delete_index(self, *, source_paths: list[str] | tuple[str, ...] | None = None) -> TikaBuildReport:
        service = self._tika_service()
        try:
            return service.delete_index(source_paths=source_paths)
        finally:
            service.close()

    def run_tika_source_summaries(self, *, source_paths: list[str] | tuple[str, ...] | None = None) -> dict[str, ExtensionSourceIndexSummary]:
        service = self._tika_service()
        try:
            return service.source_summaries(source_paths=source_paths)
        finally:
            service.close()

    def _pdf_service(self) -> PdfExtensionService:
        if self.config is None or self.paths is None:
            raise RuntimeError('extension_service_requires_config_and_paths')
        return PdfExtensionService(self.config, self.paths, coordinator=self.coordinator)

    def _tika_service(self) -> TikaExtensionService:
        if self.config is None or self.paths is None:
            raise RuntimeError('extension_service_requires_config_and_paths')
        return TikaExtensionService(self.config, self.paths, coordinator=self.coordinator, runtime_manager=self.runtime_manager)


def _vector_backend_enabled(config: AppConfig) -> bool:
    backend = str(getattr(config, 'vector_backend', '') or '').strip().lower()
    return backend not in {'', 'disabled', 'none', 'off'}


def _path_belongs_to_roots(path: str, roots: tuple[str, ...]) -> bool:
    candidate = Path(path).resolve()
    for root in roots:
        root_path = Path(root).resolve()
        try:
            if candidate == root_path or candidate.is_relative_to(root_path):
                return True
        except Exception:
            continue
    return False


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for item in paths:
        key = str(item.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique.append(item.resolve())
    return unique


def _extract_page_no(anchor: str) -> int:
    text = str(anchor or '').strip()
    for token in text.replace('页', ' ').split():
        if token.isdigit():
            return max(int(token), 0)
    return 0


def _normalize_source_path_text(source_path: str) -> str:
    text = str(source_path or '').strip()
    if not text:
        return ''
    try:
        return str(Path(text).expanduser().resolve())
    except Exception:
        return str(Path(text).expanduser())


def _build_source_index_summary(
    store: MetadataStore,
    manifest: dict[str, tuple[float, int]],
    source_path: str,
) -> ExtensionSourceIndexSummary:
    normalized = _normalize_source_path_text(source_path)
    if not normalized:
        return ExtensionSourceIndexSummary(source_path='')
    relevant_paths = [
        path
        for path in manifest
        if _path_belongs_to_roots(path, (normalized,))
    ]
    if not relevant_paths:
        return ExtensionSourceIndexSummary(source_path=normalized)
    last_indexed_mtime = max(float(manifest[path][0]) for path in relevant_paths)
    return ExtensionSourceIndexSummary(
        source_path=normalized,
        indexed_files=len(relevant_paths),
        indexed_chunks=store.count_render_rows(relevant_paths),
        vector_documents=store.count_vector_documents(relevant_paths),
        last_indexed_mtime=last_indexed_mtime,
        has_indexed_data=True,
    )


def _supported_extension_runtime_contract() -> dict[str, str]:
    return {
        'pypdf': EXTENSION_SUPPORTED_PYPDF_RANGE,
        'tika_server': EXTENSION_SUPPORTED_TIKA_SERVER_VERSION,
        'java': EXTENSION_SUPPORTED_JAVA_RANGE,
    }


def _parser_schema_version(pipeline: str) -> str:
    return EXTENSION_PDF_PARSER_SCHEMA_VERSION if str(pipeline).strip().lower() == 'pdf' else EXTENSION_TIKA_PARSER_SCHEMA_VERSION


def _make_build_id(pipeline: str) -> str:
    return f'{pipeline}-{uuid.uuid4().hex[:12]}'


def _estimate_eta_seconds(*, started_at: float, current: int, total: int) -> int:
    if total <= 0 or current <= 0:
        return 0
    elapsed = max(time.monotonic() - started_at, 0.0)
    rate = elapsed / max(current, 1)
    remaining = max(total - current, 0)
    return max(int(round(rate * remaining)), 0)


def _build_manifest_signature(
    pipeline: str,
    *,
    source_roots: list[Path],
    enabled_formats: list[str] | tuple[str, ...] = (),
    files: list[tuple[Path, Path]] | list[tuple[Path, Path, str]] | None = None,
) -> str:
    lines = [
        f'pipeline={pipeline}',
        f'parser_schema={_parser_schema_version(pipeline)}',
        f'sources={";".join(sorted(str(item.resolve()) for item in source_roots))}',
        f'formats={";".join(sorted(str(item).strip().lower() for item in enabled_formats if str(item).strip()))}',
    ]
    for item in files or []:
        file_path = item[1]
        try:
            stat = file_path.stat()
        except OSError:
            continue
        lines.append(f'{file_path.resolve()}|{float(stat.st_mtime or 0.0)}|{int(stat.st_size or 0)}')
    return str(abs(hash('\n'.join(lines))))


def _resume_completed_files(state: dict[str, object] | None) -> dict[str, dict[str, object]]:
    raw = state.get('completed_files') if isinstance(state, dict) else {}
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, dict[str, object]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        normalized[str(key)] = dict(value)
    return normalized


def _can_resume_extension_build(
    state: dict[str, object] | None,
    *,
    pipeline: str,
    manifest_signature: str,
    parser_schema_version: str,
    source_roots: tuple[str, ...],
    enabled_formats: tuple[str, ...],
) -> bool:
    if not isinstance(state, dict):
        return False
    if int(state.get('state_version') or 0) != EXTENSION_BUILD_STATE_VERSION:
        return False
    if str(state.get('pipeline') or '').strip().lower() != str(pipeline or '').strip().lower():
        return False
    if str(state.get('manifest_signature') or '') != str(manifest_signature or ''):
        return False
    if str(state.get('parser_schema_version') or '') != str(parser_schema_version or ''):
        return False
    if tuple(str(item) for item in (state.get('source_roots') or ())) != tuple(str(item) for item in source_roots):
        return False
    if tuple(str(item) for item in (state.get('enabled_formats') or ())) != tuple(str(item) for item in enabled_formats):
        return False
    status = str(state.get('status') or '').strip().lower()
    return status in {'interrupted', 'resumable', 'building', 'cancelling', 'paused'}


def _wait_for_extension_controls(
    pause_event: threading.Event | None,
    cancel_event: threading.Event | None,
) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise BuildCancelledError()
    if pause_event is None or not pause_event.is_set():
        return
    while pause_event.is_set():
        if cancel_event is not None and cancel_event.is_set():
            raise BuildCancelledError()
        time.sleep(0.1)


def _update_build_state_payload(
    extension_paths: DataPaths,
    *,
    payload: dict[str, object],
    phase: str | None = None,
    status: str | None = None,
    current_path: str | None = None,
    processed_files: int | None = None,
    skipped_files: int | None = None,
    failed_files: int | None = None,
    deleted_files: int | None = None,
    current: int | None = None,
    total: int | None = None,
    completed_files: dict[str, dict[str, object]] | None = None,
) -> None:
    if phase is not None:
        payload['phase'] = str(phase or '').strip()
    if status is not None:
        payload['status'] = str(status or '').strip()
    if current_path is not None:
        payload['current_path'] = str(current_path or '').strip()
    if processed_files is not None:
        payload['processed_files'] = max(int(processed_files or 0), 0)
    if skipped_files is not None:
        payload['skipped_files'] = max(int(skipped_files or 0), 0)
    if failed_files is not None:
        payload['failed_files'] = max(int(failed_files or 0), 0)
    if deleted_files is not None:
        payload['deleted_files'] = max(int(deleted_files or 0), 0)
    if current is not None:
        payload['current'] = max(int(current or 0), 0)
    if total is not None:
        payload['total'] = max(int(total or 0), 0)
    if completed_files is not None:
        payload['completed_files'] = dict(completed_files)
    write_extension_build_state(extension_paths, payload)


def _mark_build_interrupted(
    extension_paths: DataPaths,
    payload: dict[str, object],
    *,
    resumable: bool,
    last_error: str,
    phase: str,
) -> None:
    payload['last_error'] = str(last_error or '').strip()
    payload['resume_available'] = bool(resumable)
    _update_build_state_payload(
        extension_paths,
        payload=payload,
        phase=phase,
        status='resumable' if resumable else 'interrupted',
    )


def _resolve_distribution_version(distribution_name: str, *, module_name: str | None = None) -> str:
    metadata_error: Exception | None = None
    try:
        version = importlib_metadata.version(distribution_name)
    except Exception as exc:
        metadata_error = exc
    else:
        return str(version or '')
    if module_name:
        module = import_module(module_name)
        module_version = getattr(module, '__version__', '') or ''
        if module_version:
            return str(module_version)
    if metadata_error is not None:
        LOGGER.warning(
            'extension_distribution_version_unavailable: %s (%s: %s)',
            distribution_name,
            type(metadata_error).__name__,
            metadata_error,
        )
    return ''


def _ensure_pdf_parser_ready() -> dict[str, str]:
    try:
        import_module('omniclip_rag.extensions.parsers.pdf')
        version = _resolve_distribution_version('pypdf', module_name='pypdf')
    except Exception as exc:
        raise RuntimeError(f'pdf_parser_unavailable: {type(exc).__name__}: {exc}') from exc
    return {
        'parser_schema_version': EXTENSION_PDF_PARSER_SCHEMA_VERSION,
        'pypdf_version': str(version or ''),
        **_supported_extension_runtime_contract(),
    }


def _ensure_tika_parser_ready() -> dict[str, str]:
    try:
        import_module('omniclip_rag.extensions.parsers.tika')
    except Exception as exc:
        raise RuntimeError(f'tika_parser_unavailable: {type(exc).__name__}: {exc}') from exc
    return {
        'parser_schema_version': EXTENSION_TIKA_PARSER_SCHEMA_VERSION,
        **_supported_extension_runtime_contract(),
    }


def _parsed_file_limit_reason(parsed) -> str:
    chunk_count = len(getattr(parsed, 'chunks', ()) or ())
    total_chars = sum(len(str(getattr(chunk, 'raw_text', '') or '')) for chunk in getattr(parsed, 'chunks', ()) or ())
    if chunk_count > EXTENSION_MAX_PARSED_CHUNKS:
        return f'片段数量过大（{chunk_count}）'
    if total_chars > EXTENSION_MAX_PARSED_TEXT_CHARS:
        return f'正文过大（{total_chars} 字符）'
    return ''


def _vector_contract(store: MetadataStore, vector_index, vector_enabled: bool) -> tuple[bool, bool]:
    stats = store.stats()
    indexed_files = int(stats.get('files', 0) or 0)
    if not vector_enabled:
        return False, indexed_files >= 0
    expected_documents = store.count_vector_documents()
    if expected_documents <= 0:
        return True, indexed_files >= 0
    status_fn = getattr(vector_index, 'status', None)
    status_payload = status_fn() if callable(status_fn) else {}
    vector_ready = bool(status_payload.get('table_ready'))
    return vector_ready, indexed_files >= 0


def _emit_extension_stage(
    on_progress: Callable[[dict[str, object]], None] | None,
    *,
    stage: str,
    stage_status: str,
    current: int = 0,
    total: int = 0,
    current_path: str = '',
    format_id: str = '',
    processed_files: int = 0,
    skipped_files: int = 0,
    error_count: int | None = None,
    deleted_files: int = 0,
    overall_percent: float | None = None,
    close_safe: bool = False,
    recent_issue: str = '',
    build_id: str = '',
    pipeline: str = '',
    eta_seconds: int | None = None,
    elapsed_seconds: float | None = None,
    heartbeat_only: bool = False,
    watchdog_stalled: bool = False,
    watchdog_report_path: str = '',
) -> None:
    percent = overall_percent
    if percent is None:
        percent = round((current / total) * 100.0, 2) if total else 0.0
    payload: dict[str, object] = {
        'stage': stage,
        'stage_status': stage_status,
        'current': max(int(current or 0), 0),
        'total': max(int(total or 0), 0),
        'current_path': current_path,
        'processed_files': max(int(processed_files or 0), 0),
        'skipped_files': max(int(skipped_files or 0), 0),
        'error_count': max(int(error_count if error_count is not None else skipped_files or 0), 0),
        'deleted_files': max(int(deleted_files or 0), 0),
        'overall_percent': max(float(percent or 0.0), 0.0),
        'close_safe': bool(close_safe),
    }
    if build_id:
        payload['build_id'] = str(build_id)
    if pipeline:
        payload['pipeline'] = str(pipeline)
    if eta_seconds is not None:
        payload['eta_seconds'] = max(int(eta_seconds or 0), 0)
    if elapsed_seconds is not None:
        payload['elapsed_seconds'] = max(float(elapsed_seconds or 0.0), 0.0)
    if heartbeat_only:
        payload['heartbeat_only'] = True
    if watchdog_stalled:
        payload['watchdog_stalled'] = True
    if watchdog_report_path:
        payload['watchdog_report_path'] = str(watchdog_report_path)
    if format_id:
        payload['format_id'] = format_id
    if current_path and 'current_file' not in payload:
        payload['current_file'] = Path(current_path).name
    if recent_issue:
        payload['recent_issue'] = str(recent_issue)
    _emit_progress(on_progress, payload)


def _emit_progress(on_progress: Callable[[dict[str, object]], None] | None, payload: dict[str, object]) -> None:
    if on_progress is None:
        return
    on_progress(payload)


def _remember_recent_issue(issues: list[str], message: str, *, limit: int = 3) -> None:
    normalized = str(message or '').strip()
    if not normalized:
        return
    if normalized in issues:
        return
    issues.append(normalized)
    if len(issues) > limit:
        del issues[:-limit]


def _format_tika_failure_reason(file_path: Path, exc: Exception) -> str:
    name = file_path.name
    if isinstance(exc, TikaParseError):
        if exc.status_code is not None:
            return f'HTTP {exc.status_code} · {name}'
        return f'Tika 解析失败 · {name}'
    return f'{type(exc).__name__} · {name}'
