from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..config import AppConfig, DataPaths
from .models import ExtensionDirectoryState, ExtensionIndexState
from .parsers.tika import enabled_tika_suffixes
from .registry import ExtensionRegistry
from .service import ExtensionTaskCoordinator, ExtensionTaskKind, ExtensionTaskRequest, PdfExtensionService, TikaExtensionService

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _WatchLoopState:
    stop_event: threading.Event
    thread: threading.Thread | None = None
    last_manifest: dict[str, tuple[float, int]] | None = None


class ExtensionWatchService:
    """Independent watch service for PDF/Tika extension pipelines.

    Why: extension watches must never reuse the Markdown mainline watch state.
    This poll-based service keeps source manifests, pending-change counters, and
    collision handling in the extension namespace so heavy rebuilds can be
    coordinated instead of racing the main indexer.
    """

    def __init__(
        self,
        config: AppConfig,
        paths: DataPaths,
        *,
        coordinator: ExtensionTaskCoordinator | None = None,
        registry: ExtensionRegistry | None = None,
        markdown_rebuild_active=None,
        markdown_watch_active=None,
        pdf_service_factory=None,
        tika_service_factory=None,
        poll_interval: float = 1.0,
    ) -> None:
        self.config = config
        self.paths = paths
        self.coordinator = coordinator or ExtensionTaskCoordinator()
        self.registry = registry or ExtensionRegistry()
        self.markdown_rebuild_active = markdown_rebuild_active or (lambda: False)
        self.markdown_watch_active = markdown_watch_active or (lambda: False)
        self.pdf_service_factory = pdf_service_factory or (lambda: PdfExtensionService(config, paths, coordinator=self.coordinator, registry=self.registry))
        self.tika_service_factory = tika_service_factory or (lambda: TikaExtensionService(config, paths, coordinator=self.coordinator, registry=self.registry))
        self.poll_interval = max(float(poll_interval or 1.0), 0.1)
        self._loops: dict[str, _WatchLoopState] = {
            'pdf': _WatchLoopState(stop_event=threading.Event()),
            'tika': _WatchLoopState(stop_event=threading.Event()),
        }

    def start_pdf_watch(self) -> None:
        self._start_pipeline_watch('pdf')

    def stop_pdf_watch(self) -> None:
        self._stop_pipeline_watch('pdf')

    def start_tika_watch(self) -> None:
        self._start_pipeline_watch('tika')

    def stop_tika_watch(self) -> None:
        self._stop_pipeline_watch('tika')

    def shutdown(self) -> None:
        self.stop_pdf_watch()
        self.stop_tika_watch()

    def _start_pipeline_watch(self, pipeline: str) -> None:
        loop = self._loops[pipeline]
        if loop.thread is not None and loop.thread.is_alive():
            return
        loop.stop_event.clear()
        try:
            loop.last_manifest = self._build_manifest(pipeline)
            last_error = ''
        except Exception as exc:
            loop.last_manifest = {}
            last_error = str(exc)
            LOGGER.warning('Failed to prepare initial extension manifest for %s watch: %s', pipeline, exc)
        self._update_watch_state(pipeline, running=True, pending_changes=0, last_error=last_error)
        loop.thread = threading.Thread(target=self._watch_loop, args=(pipeline,), daemon=True, name=f'extension-watch-{pipeline}')
        loop.thread.start()

    def _stop_pipeline_watch(self, pipeline: str) -> None:
        loop = self._loops[pipeline]
        loop.stop_event.set()
        thread = loop.thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        loop.thread = None
        loop.last_manifest = None
        self._update_watch_state(pipeline, running=False, last_error='')

    def _watch_loop(self, pipeline: str) -> None:
        loop = self._loops[pipeline]
        while not loop.stop_event.is_set():
            try:
                current_manifest = self._build_manifest(pipeline)
                if loop.last_manifest is None:
                    loop.last_manifest = current_manifest
                elif current_manifest != loop.last_manifest:
                    self._update_watch_state(pipeline, last_event_at=_utc_now_iso())
                    request = ExtensionTaskRequest(pipeline=pipeline, kind=ExtensionTaskKind.SCAN_ONCE)
                    decision = self.coordinator.can_start(
                        request,
                        markdown_rebuild_active=bool(self.markdown_rebuild_active()),
                        markdown_watch_active=bool(self.markdown_watch_active()),
                    )
                    if not decision.allowed:
                        self._bump_pending(pipeline)
                    else:
                        self._run_scan_once(pipeline)
                    loop.last_manifest = current_manifest
            except Exception as exc:
                LOGGER.warning('Extension watch loop error on %s: %s', pipeline, exc)
                self._update_watch_state(pipeline, last_error=str(exc))
            loop.stop_event.wait(self.poll_interval)

    def _run_scan_once(self, pipeline: str) -> None:
        if pipeline == 'pdf':
            service = self.pdf_service_factory()
        else:
            service = self.tika_service_factory()
        try:
            service.scan_once(
                markdown_rebuild_active=bool(self.markdown_rebuild_active()),
                markdown_watch_active=bool(self.markdown_watch_active()),
            )
            self._update_watch_state(pipeline, pending_changes=0, last_scan_at=_utc_now_iso(), last_error='')
        finally:
            close = getattr(service, 'close', None)
            if callable(close):
                close()

    def _build_manifest(self, pipeline: str) -> dict[str, tuple[float, int]]:
        state = self.registry.load(self.paths)
        if pipeline == 'pdf':
            sources = [item for item in state.pdf_config.source_directories if item.selected and item.state != ExtensionDirectoryState.REMOVED_CONFIRMED]
            suffixes = ('.pdf',)
        else:
            sources = [item for item in state.tika_config.source_directories if item.selected and item.state != ExtensionDirectoryState.REMOVED_CONFIRMED]
            enabled_formats = [item.format_id for item in state.tika_config.selected_formats if item.enabled]
            suffixes = enabled_tika_suffixes(enabled_formats)
        manifest: dict[str, tuple[float, int]] = {}
        suffix_set = {item.lower() for item in suffixes}
        if not suffix_set:
            return manifest
        for source in sources:
            root = Path(source.path).expanduser()
            if not root.exists() or not root.is_dir():
                continue
            for path in root.rglob('*'):
                if not path.is_file() or path.suffix.lower() not in suffix_set:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                manifest[str(path.resolve())] = (float(stat.st_mtime), int(stat.st_size))
        return manifest

    def _bump_pending(self, pipeline: str) -> None:
        state = self.registry.load(self.paths)
        watch_state = state.snapshot.pdf.watch_state if pipeline == 'pdf' else state.snapshot.tika.watch_state
        watch_state.pending_changes += 1
        self._sync_watch_flags(state, pipeline)
        self.registry.save(self.paths, state)

    def _update_watch_state(
        self,
        pipeline: str,
        *,
        running: bool | None = None,
        last_event_at: str | None = None,
        last_scan_at: str | None = None,
        last_error: str | None = None,
        pending_changes: int | None = None,
    ) -> None:
        state = self.registry.load(self.paths)
        status = state.snapshot.pdf if pipeline == 'pdf' else state.snapshot.tika
        if running is not None:
            status.watch_running = running
            status.watch_state.running = running
        if last_event_at is not None:
            status.watch_state.last_event_at = last_event_at
        if last_scan_at is not None:
            status.watch_state.last_scan_at = last_scan_at
        if last_error is not None:
            status.watch_state.last_error = last_error
        if pending_changes is not None:
            status.watch_state.pending_changes = max(int(pending_changes), 0)
        if pipeline == 'pdf':
            state.pdf_config.watch_enabled = status.watch_state.running
        else:
            state.tika_config.watch_enabled = status.watch_state.running
        self._sync_watch_flags(state, pipeline)
        self.registry.save(self.paths, state)

    def _sync_watch_flags(self, state, pipeline: str) -> None:
        if pipeline == 'pdf':
            state.snapshot.pdf.watch_running = state.snapshot.pdf.watch_state.running
        else:
            state.snapshot.tika.watch_running = state.snapshot.tika.watch_state.running


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')
