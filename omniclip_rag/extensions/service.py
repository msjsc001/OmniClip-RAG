from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..config import AppConfig, DataPaths
from ..models import SearchHit
from ..retrieval_policy import QueryProfile, build_query_profile, rank_candidates
from ..storage import MetadataStore
from ..vector_index import NullVectorIndex, create_vector_index, runtime_dependency_issue
from .models import ExtensionDirectoryState, ExtensionIndexState, TikaFormatSupportTier
from .parsers.pdf import extract_pdf_pages, parse_pdf_file
from .parsers.tika import detect_tika_format, enabled_tika_suffixes, parse_tika_file
from .paths import build_extension_data_paths
from .registry import ExtensionRegistry, ExtensionRegistryState
from .runtimes import TikaSidecarManager, parse_file_with_tika

LOGGER = logging.getLogger(__name__)


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
    skipped_files: int = 0
    deleted_files: int = 0
    missing_directories: tuple[str, ...] = ()
    rebuilt: bool = False


@dataclass(slots=True)
class TikaPreflightReport:
    """Preflight summary for the isolated Tika pipeline."""

    total_files: int
    total_bytes: int
    enabled_formats: tuple[str, ...]
    skipped_files: int = 0
    missing_directories: tuple[str, ...] = ()


@dataclass(slots=True)
class TikaBuildReport:
    """Result snapshot after a Tika build-like operation."""

    indexed_files: int
    indexed_chunks: int
    enabled_formats: tuple[str, ...]
    skipped_files: int = 0
    deleted_files: int = 0
    missing_directories: tuple[str, ...] = ()
    rebuilt: bool = False


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

    def preflight(self, *, source_paths: list[str] | tuple[str, ...] | None = None) -> PdfPreflightReport:
        """Estimate the isolated PDF build scope without mutating any index data."""
        self._refresh_state()
        source_dirs, missing_dirs = self._selected_source_directories(source_paths)
        total_files = 0
        total_pages = 0
        total_bytes = 0
        skipped_files = 0
        for _source_root, pdf_path in self._iter_pdf_files(source_dirs):
            try:
                total_files += 1
                total_bytes += int(pdf_path.stat().st_size)
                total_pages += len(extract_pdf_pages(pdf_path))
            except Exception as exc:
                skipped_files += 1
                LOGGER.warning('PDF preflight skipped unreadable file: %s (%s: %s)', pdf_path, type(exc).__name__, exc)
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
        )

    def scan_once(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        markdown_rebuild_active: bool = False,
        markdown_watch_active: bool = False,
        on_progress: Callable[[dict[str, object]], None] | None = None,
    ) -> PdfBuildReport:
        """Apply one-shot incremental PDF changes without enabling watch mode."""
        return self._run_build(
            kind=ExtensionTaskKind.SCAN_ONCE,
            incremental=True,
            source_paths=source_paths,
            markdown_rebuild_active=markdown_rebuild_active,
            markdown_watch_active=markdown_watch_active,
            on_progress=on_progress,
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
            filtered_roots, _missing_dirs = self._selected_source_directories(source_paths)
            if filtered_roots:
                target_paths = self._indexed_paths_under_roots(filtered_roots)
                if target_paths:
                    self._delete_paths_from_index(target_paths)
                stats = self.store.stats()
                self._update_pdf_status(
                    index_state=ExtensionIndexState.READY if int(stats.get('files', 0) or 0) > 0 else (ExtensionIndexState.NOT_BUILT if self._state.pdf_config.enabled else ExtensionIndexState.DISABLED),
                    build_in_progress=False,
                    indexed_document_count=int(stats.get('files', 0) or 0),
                    last_error='',
                )
                return PdfBuildReport(indexed_files=int(stats.get('files', 0) or 0), indexed_chunks=int(stats.get('chunks', 0) or 0), deleted_files=len(target_paths), rebuilt=False)
            self.store.reset_all()
            self.vector_index.reset()
            self._update_pdf_status(
                index_state=ExtensionIndexState.NOT_BUILT if self._state.pdf_config.enabled else ExtensionIndexState.DISABLED,
                build_in_progress=False,
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
        if self._state.snapshot.pdf.index_state != ExtensionIndexState.READY:
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

    def _run_build(
        self,
        *,
        kind: ExtensionTaskKind,
        incremental: bool,
        source_paths: list[str] | tuple[str, ...] | None,
        markdown_rebuild_active: bool,
        markdown_watch_active: bool,
        on_progress: Callable[[dict[str, object]], None] | None,
    ) -> PdfBuildReport:
        self._refresh_state()
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
                last_error='pdf_sources_missing',
            )
            self._persist_state()
            raise RuntimeError('pdf_sources_missing')

        self.coordinator.reserve(request)
        self._update_pdf_status(index_state=ExtensionIndexState.BUILDING, build_in_progress=True, last_error='')
        deleted_files = 0
        skipped_files = 0
        try:
            if not source_dirs:
                self._update_pdf_status(
                    index_state=ExtensionIndexState.NOT_BUILT if self._state.pdf_config.enabled else ExtensionIndexState.DISABLED,
                    build_in_progress=False,
                    indexed_document_count=0,
                    last_error='',
                )
                return PdfBuildReport(indexed_files=0, indexed_chunks=0, missing_directories=tuple(str(item) for item in missing_dirs))

            if incremental:
                deleted_files, skipped_files = self._scan_once(source_dirs, on_progress=on_progress)
            else:
                skipped_files = self._full_rebuild(source_dirs, on_progress=on_progress, targeted=bool(source_paths))
            stats = self.store.stats()
            self._update_pdf_status(
                index_state=ExtensionIndexState.READY,
                build_in_progress=False,
                indexed_document_count=int(stats.get('files', 0) or 0),
                last_error='',
            )
            return PdfBuildReport(
                indexed_files=int(stats.get('files', 0) or 0),
                indexed_chunks=int(stats.get('chunks', 0) or 0),
                skipped_files=skipped_files,
                deleted_files=deleted_files,
                missing_directories=tuple(str(item) for item in missing_dirs),
                rebuilt=not incremental,
            )
        finally:
            self.coordinator.release(request)
            self._persist_state()

    def _full_rebuild(
        self,
        source_dirs: list[Path],
        *,
        on_progress: Callable[[dict[str, object]], None] | None,
        targeted: bool = False,
    ) -> int:
        if targeted:
            existing_paths = self._indexed_paths_under_roots(source_dirs)
            if existing_paths:
                self._delete_paths_from_index(existing_paths)
        else:
            self.store.reset_all()
            self.vector_index.reset()
        skipped_files = 0
        files = list(self._iter_pdf_files(source_dirs))
        total = len(files)
        indexed_paths: list[str] = []
        for current, (source_root, pdf_path) in enumerate(files, start=1):
            _emit_progress(
                on_progress,
                {
                    'stage': 'pdf_build',
                    'current': current,
                    'total': total,
                    'current_path': str(pdf_path),
                    'stage_status': 'parse_pdf',
                    'overall_percent': round((current / total) * 100.0, 2) if total else 0.0,
                },
            )
            if not self._replace_one_pdf(source_root, pdf_path):
                skipped_files += 1
                continue
            indexed_paths.append(str(pdf_path.resolve()))
        if targeted:
            if indexed_paths:
                self._upsert_vectors_for_paths(indexed_paths)
        else:
            self._rebuild_vectors()
        return skipped_files

    def _scan_once(
        self,
        source_dirs: list[Path],
        *,
        on_progress: Callable[[dict[str, object]], None] | None,
    ) -> tuple[int, int]:
        previous_manifest = self.store.fetch_file_manifest()
        current_manifest: dict[str, tuple[float, int]] = {}
        source_by_path: dict[str, tuple[Path, Path]] = {}
        for source_root, pdf_path in self._iter_pdf_files(source_dirs):
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
        total = len(changed_paths)
        for current, path in enumerate(changed_paths, start=1):
            source_root, pdf_path = source_by_path[path]
            _emit_progress(
                on_progress,
                {
                    'stage': 'pdf_scan_once',
                    'current': current,
                    'total': total,
                    'current_path': str(pdf_path),
                    'stage_status': 'parse_pdf',
                    'overall_percent': round((current / total) * 100.0, 2) if total else 0.0,
                },
            )
            self._delete_paths_from_index([path])
            if not self._replace_one_pdf(source_root, pdf_path):
                skipped_files += 1
        if changed_paths:
            self._upsert_vectors_for_paths(changed_paths)
        return len(deleted_paths), skipped_files

    def _replace_one_pdf(self, source_root: Path, pdf_path: Path) -> bool:
        try:
            parsed = parse_pdf_file(source_root, pdf_path)
        except Exception as exc:
            LOGGER.warning('PDF extension skipped broken file: %s (%s: %s)', pdf_path, type(exc).__name__, exc)
            return False
        self.store.replace_file(parsed)
        rendered_payloads = [
            (chunk.chunk_id, chunk.raw_text)
            for chunk in parsed.chunks
            if chunk.raw_text.strip()
        ]
        if rendered_payloads:
            self.store.update_rendered_chunks(rendered_payloads)
        return True

    def _rebuild_vectors(self) -> None:
        if not self._vector_enabled:
            return
        total = self.store.count_vector_documents()
        self.vector_index.rebuild(self.store.iter_vector_documents(), total=total)

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
        indexed_document_count: int | None = None,
        last_error: str | None = None,
    ) -> None:
        status = self._state.snapshot.pdf
        if index_state is not None:
            status.index_state = index_state
        if build_in_progress is not None:
            status.build_in_progress = build_in_progress
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

    def preflight(self, *, source_paths: list[str] | tuple[str, ...] | None = None) -> TikaPreflightReport:
        self._refresh_state()
        source_dirs, missing_dirs = self._selected_tika_source_directories(source_paths)
        enabled_formats = self._enabled_tika_formats()
        total_files = 0
        total_bytes = 0
        skipped_files = 0
        for _source_root, file_path, _format_id in self._iter_tika_files(source_dirs, enabled_formats):
            try:
                total_files += 1
                total_bytes += int(file_path.stat().st_size)
            except Exception as exc:
                skipped_files += 1
                LOGGER.warning('Tika preflight skipped unreadable file: %s (%s: %s)', file_path, type(exc).__name__, exc)
        self._persist_state()
        return TikaPreflightReport(
            total_files=total_files,
            total_bytes=total_bytes,
            enabled_formats=tuple(enabled_formats),
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
    ) -> TikaBuildReport:
        return self._run_build(
            kind=ExtensionTaskKind.FULL_REBUILD,
            incremental=False,
            source_paths=source_paths,
            markdown_rebuild_active=markdown_rebuild_active,
            markdown_watch_active=markdown_watch_active,
            on_progress=on_progress,
        )

    def scan_once(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        markdown_rebuild_active: bool = False,
        markdown_watch_active: bool = False,
        on_progress: Callable[[dict[str, object]], None] | None = None,
    ) -> TikaBuildReport:
        return self._run_build(
            kind=ExtensionTaskKind.SCAN_ONCE,
            incremental=True,
            source_paths=source_paths,
            markdown_rebuild_active=markdown_rebuild_active,
            markdown_watch_active=markdown_watch_active,
            on_progress=on_progress,
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
            filtered_roots, _missing_dirs = self._selected_tika_source_directories(source_paths)
            if filtered_roots:
                target_paths = self._indexed_paths_under_roots(filtered_roots)
                if target_paths:
                    self._delete_paths_from_index(target_paths)
                stats = self.store.stats()
                self._update_tika_status(
                    index_state=ExtensionIndexState.READY if int(stats.get('files', 0) or 0) > 0 else (ExtensionIndexState.NOT_BUILT if self._state.tika_config.enabled else ExtensionIndexState.DISABLED),
                    build_in_progress=False,
                    indexed_document_count=int(stats.get('files', 0) or 0),
                    last_error='',
                )
                return TikaBuildReport(indexed_files=int(stats.get('files', 0) or 0), indexed_chunks=int(stats.get('chunks', 0) or 0), enabled_formats=tuple(self._enabled_tika_formats()), deleted_files=len(target_paths), rebuilt=False)
            self.store.reset_all()
            self.vector_index.reset()
            self._update_tika_status(
                index_state=ExtensionIndexState.NOT_BUILT if self._state.tika_config.enabled else ExtensionIndexState.DISABLED,
                build_in_progress=False,
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
        if self._state.snapshot.tika.index_state != ExtensionIndexState.READY:
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

    def _run_build(
        self,
        *,
        kind: ExtensionTaskKind,
        incremental: bool,
        source_paths: list[str] | tuple[str, ...] | None,
        markdown_rebuild_active: bool,
        markdown_watch_active: bool,
        on_progress: Callable[[dict[str, object]], None] | None,
    ) -> TikaBuildReport:
        self._refresh_state()
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
                indexed_document_count=0,
                last_error='tika_no_formats_enabled',
            )
            self._persist_state()
            return TikaBuildReport(indexed_files=0, indexed_chunks=0, enabled_formats=tuple())
        if not incremental and missing_dirs:
            self._update_tika_status(
                index_state=ExtensionIndexState.STALE,
                build_in_progress=False,
                last_error='tika_sources_missing',
            )
            self._persist_state()
            raise RuntimeError('tika_sources_missing')

        self.coordinator.reserve(request)
        self._update_tika_status(index_state=ExtensionIndexState.BUILDING, build_in_progress=True, last_error='')
        deleted_files = 0
        skipped_files = 0
        try:
            runtime = self.runtime_manager.ensure_started(self.paths)
            self._state.snapshot.tika.runtime = runtime
            if not runtime.installed or not runtime.running or not runtime.healthy:
                self._update_tika_status(index_state=ExtensionIndexState.ERROR, build_in_progress=False, last_error='tika_runtime_unavailable')
                raise RuntimeError('tika_runtime_unavailable')

            if not source_dirs:
                self._update_tika_status(
                    index_state=ExtensionIndexState.NOT_BUILT if self._state.tika_config.enabled else ExtensionIndexState.DISABLED,
                    build_in_progress=False,
                    indexed_document_count=0,
                    last_error='',
                )
                return TikaBuildReport(indexed_files=0, indexed_chunks=0, enabled_formats=tuple(enabled_formats), missing_directories=tuple(str(item) for item in missing_dirs))

            if incremental:
                deleted_files, skipped_files = self._scan_once(source_dirs, enabled_formats, on_progress=on_progress)
            else:
                skipped_files = self._full_rebuild(source_dirs, enabled_formats, on_progress=on_progress, targeted=bool(source_paths))
            stats = self.store.stats()
            self._update_tika_status(
                index_state=ExtensionIndexState.READY,
                build_in_progress=False,
                indexed_document_count=int(stats.get('files', 0) or 0),
                last_error='',
            )
            return TikaBuildReport(
                indexed_files=int(stats.get('files', 0) or 0),
                indexed_chunks=int(stats.get('chunks', 0) or 0),
                enabled_formats=tuple(enabled_formats),
                skipped_files=skipped_files,
                deleted_files=deleted_files,
                missing_directories=tuple(str(item) for item in missing_dirs),
                rebuilt=not incremental,
            )
        finally:
            self.coordinator.release(request)
            self._persist_state()

    def _full_rebuild(self, source_dirs: list[Path], enabled_formats: list[str], *, on_progress: Callable[[dict[str, object]], None] | None, targeted: bool = False) -> int:
        if targeted:
            existing_paths = self._indexed_paths_under_roots(source_dirs)
            if existing_paths:
                self._delete_paths_from_index(existing_paths)
        else:
            self.store.reset_all()
            self.vector_index.reset()
        skipped_files = 0
        files = list(self._iter_tika_files(source_dirs, enabled_formats))
        total = len(files)
        indexed_paths: list[str] = []
        for current, (source_root, file_path, format_id) in enumerate(files, start=1):
            _emit_progress(
                on_progress,
                {
                    'stage': 'tika_build',
                    'current': current,
                    'total': total,
                    'current_path': str(file_path),
                    'format_id': format_id,
                    'stage_status': 'parse_tika',
                    'overall_percent': round((current / total) * 100.0, 2) if total else 0.0,
                },
            )
            if not self._replace_one_tika_file(source_root, file_path, format_id):
                skipped_files += 1
                continue
            indexed_paths.append(str(file_path.resolve()))
        if targeted:
            if indexed_paths:
                self._upsert_vectors_for_paths(indexed_paths)
        else:
            self._rebuild_vectors()
        return skipped_files

    def _scan_once(self, source_dirs: list[Path], enabled_formats: list[str], *, on_progress: Callable[[dict[str, object]], None] | None) -> tuple[int, int]:
        previous_manifest = self.store.fetch_file_manifest()
        current_manifest: dict[str, tuple[float, int]] = {}
        source_by_path: dict[str, tuple[Path, Path, str]] = {}
        for source_root, file_path, format_id in self._iter_tika_files(source_dirs, enabled_formats):
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
        skipped_files = 0
        total = len(changed_paths)
        for current, path in enumerate(changed_paths, start=1):
            source_root, file_path, format_id = source_by_path[path]
            _emit_progress(
                on_progress,
                {
                    'stage': 'tika_scan_once',
                    'current': current,
                    'total': total,
                    'current_path': str(file_path),
                    'format_id': format_id,
                    'stage_status': 'parse_tika',
                    'overall_percent': round((current / total) * 100.0, 2) if total else 0.0,
                },
            )
            self._delete_paths_from_index([path])
            if not self._replace_one_tika_file(source_root, file_path, format_id):
                skipped_files += 1
        if changed_paths:
            self._upsert_vectors_for_paths(changed_paths)
        return len(deleted_paths), skipped_files

    def _replace_one_tika_file(self, source_root: Path, file_path: Path, format_id: str) -> bool:
        try:
            runtime = self._state.snapshot.tika.runtime
            xhtml = parse_file_with_tika(file_path, port=runtime.port or 9998)
            parsed = parse_tika_file(source_root, file_path, xhtml, format_id=format_id)
        except Exception as exc:
            LOGGER.warning('Tika extension skipped poisoned file: %s (%s: %s)', file_path, type(exc).__name__, exc)
            return False
        self.store.replace_file(parsed)
        rendered_payloads = [(chunk.chunk_id, chunk.raw_text) for chunk in parsed.chunks if chunk.raw_text.strip()]
        if rendered_payloads:
            self.store.update_rendered_chunks(rendered_payloads)
        return True

    def _rebuild_vectors(self) -> None:
        if not self._vector_enabled:
            return
        total = self.store.count_vector_documents()
        self.vector_index.rebuild(self.store.iter_vector_documents(), total=total)

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
        return [item.format_id for item in self._state.tika_config.selected_formats if item.enabled and item.tier != TikaFormatSupportTier.POOR]

    def _iter_tika_files(self, source_dirs: list[Path], enabled_formats: list[str]):
        suffixes = enabled_tika_suffixes(enabled_formats)
        if not suffixes:
            return
        seen: set[str] = set()
        suffix_set = {item.lower() for item in suffixes}
        for source_root in source_dirs:
            for file_path in sorted(source_root.rglob('*')):
                if not file_path.is_file() or file_path.suffix.lower() not in suffix_set:
                    continue
                resolved_path = file_path.resolve()
                resolved = str(resolved_path)
                if resolved in seen:
                    continue
                format_id = detect_tika_format(resolved_path)
                if format_id not in enabled_formats:
                    continue
                seen.add(resolved)
                yield source_root, resolved_path, format_id

    def _update_tika_status(
        self,
        *,
        index_state: ExtensionIndexState | None = None,
        build_in_progress: bool | None = None,
        indexed_document_count: int | None = None,
        last_error: str | None = None,
    ) -> None:
        status = self._state.snapshot.tika
        if index_state is not None:
            status.index_state = index_state
        if build_in_progress is not None:
            status.build_in_progress = build_in_progress
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
        format_id = detect_tika_format(Path(hit.source_path)) or Path(hit.source_path).suffix.lstrip('.').lower() or 'tika'
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

    def run_pdf_preflight(self, *, source_paths: list[str] | tuple[str, ...] | None = None) -> PdfPreflightReport:
        service = self._pdf_service()
        try:
            return service.preflight(source_paths=source_paths)
        finally:
            service.close()

    def run_pdf_scan_once(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
    ) -> PdfBuildReport:
        service = self._pdf_service()
        try:
            return service.scan_once(source_paths=source_paths, on_progress=on_progress)
        finally:
            service.close()

    def run_pdf_full_rebuild(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
    ) -> PdfBuildReport:
        service = self._pdf_service()
        try:
            return service.full_rebuild(source_paths=source_paths, on_progress=on_progress)
        finally:
            service.close()

    def run_pdf_delete_index(self, *, source_paths: list[str] | tuple[str, ...] | None = None) -> PdfBuildReport:
        service = self._pdf_service()
        try:
            return service.delete_index(source_paths=source_paths)
        finally:
            service.close()

    def run_tika_preflight(self, *, source_paths: list[str] | tuple[str, ...] | None = None) -> TikaPreflightReport:
        service = self._tika_service()
        try:
            return service.preflight(source_paths=source_paths)
        finally:
            service.close()

    def run_tika_scan_once(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
    ) -> TikaBuildReport:
        service = self._tika_service()
        try:
            return service.scan_once(source_paths=source_paths, on_progress=on_progress)
        finally:
            service.close()

    def run_tika_full_rebuild(
        self,
        *,
        source_paths: list[str] | tuple[str, ...] | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
    ) -> TikaBuildReport:
        service = self._tika_service()
        try:
            return service.full_rebuild(source_paths=source_paths, on_progress=on_progress)
        finally:
            service.close()

    def run_tika_delete_index(self, *, source_paths: list[str] | tuple[str, ...] | None = None) -> TikaBuildReport:
        service = self._tika_service()
        try:
            return service.delete_index(source_paths=source_paths)
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


def _emit_progress(on_progress: Callable[[dict[str, object]], None] | None, payload: dict[str, object]) -> None:
    if on_progress is None:
        return
    on_progress(payload)
