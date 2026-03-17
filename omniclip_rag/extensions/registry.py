from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config import DataPaths
from .models import (
    ExtensionDirectoryState,
    ExtensionIndexState,
    ExtensionSourceDirectory,
    ExtensionSubsystemSnapshot,
    ExtensionWatchState,
    PdfExtensionConfig,
    PdfExtensionStatus,
    TikaExtensionConfig,
    TikaExtensionStatus,
    TikaFormatSelection,
    TikaFormatSupportTier,
    TikaRuntimeStatus,
)
from .tika_catalog import build_tika_format_catalog, merge_tika_format_selections


REGISTRY_FILE_NAME = 'extensions_registry.json'


@dataclass(slots=True)
class ExtensionRegistryState:
    """Persisted extension-only config/state snapshot."""

    pdf_config: PdfExtensionConfig = field(default_factory=PdfExtensionConfig)
    tika_config: TikaExtensionConfig = field(default_factory=TikaExtensionConfig)
    snapshot: ExtensionSubsystemSnapshot = field(default_factory=ExtensionSubsystemSnapshot)


class ExtensionRegistry:
    """Coordinates extension-only config/state aggregation.

    The registry owns extension snapshots separately from the Markdown
    workspace config so extension work can be added without polluting the main
    config schema.
    """

    def __init__(self) -> None:
        self._state = ExtensionRegistryState()

    def file_path(self, paths: DataPaths) -> Path:
        """Return the isolated registry file path for the current workspace."""
        return paths.state_dir / REGISTRY_FILE_NAME

    def load(self, paths: DataPaths) -> ExtensionRegistryState:
        """Load extension config/state from isolated storage."""
        path = self.file_path(paths)
        state = ExtensionRegistryState()
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                payload = {}
            state = self._deserialize(payload)
        catalog = build_tika_format_catalog(paths)
        state.tika_config.selected_formats = merge_tika_format_selections(state.tika_config.selected_formats, catalog)
        self._state = state
        return state

    def save(self, paths: DataPaths, state: ExtensionRegistryState) -> None:
        """Persist extension config/state to isolated storage."""
        path = self.file_path(paths)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._serialize(state), ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        self._state = state

    def summarize(self) -> ExtensionSubsystemSnapshot:
        """Return the latest status summary for UI chips and diagnostics."""
        return self._state.snapshot

    def _serialize(self, state: ExtensionRegistryState) -> dict[str, object]:
        return {
            'pdf_config': {
                'enabled': state.pdf_config.enabled,
                'include_in_query': state.pdf_config.include_in_query,
                'watch_enabled': state.pdf_config.watch_enabled,
                'source_directories': [self._serialize_source(item) for item in state.pdf_config.source_directories],
            },
            'tika_config': {
                'enabled': state.tika_config.enabled,
                'include_in_query': state.tika_config.include_in_query,
                'watch_enabled': state.tika_config.watch_enabled,
                'source_directories': [self._serialize_source(item) for item in state.tika_config.source_directories],
                'selected_formats': [self._serialize_tika_format(item) for item in state.tika_config.selected_formats],
            },
            'snapshot': {
                'pdf': self._serialize_pdf_status(state.snapshot.pdf),
                'tika': self._serialize_tika_status(state.snapshot.tika),
            },
        }

    def _deserialize(self, payload: dict[str, object]) -> ExtensionRegistryState:
        pdf_payload = payload.get('pdf_config') if isinstance(payload.get('pdf_config'), dict) else {}
        tika_payload = payload.get('tika_config') if isinstance(payload.get('tika_config'), dict) else {}
        snapshot_payload = payload.get('snapshot') if isinstance(payload.get('snapshot'), dict) else {}
        return ExtensionRegistryState(
            pdf_config=PdfExtensionConfig(
                enabled=bool(pdf_payload.get('enabled', False)),
                include_in_query=bool(pdf_payload.get('include_in_query', False)),
                watch_enabled=bool(pdf_payload.get('watch_enabled', False)),
                source_directories=self._parse_sources(pdf_payload.get('source_directories')),
            ),
            tika_config=TikaExtensionConfig(
                enabled=bool(tika_payload.get('enabled', False)),
                include_in_query=bool(tika_payload.get('include_in_query', False)),
                watch_enabled=bool(tika_payload.get('watch_enabled', False)),
                source_directories=self._parse_sources(tika_payload.get('source_directories')),
                selected_formats=self._parse_tika_formats(tika_payload.get('selected_formats')),
            ),
            snapshot=ExtensionSubsystemSnapshot(
                pdf=self._parse_pdf_status(snapshot_payload.get('pdf')),
                tika=self._parse_tika_status(snapshot_payload.get('tika')),
            ),
        )

    def _serialize_source(self, source: ExtensionSourceDirectory) -> dict[str, object]:
        return {
            'path': source.path,
            'state': source.state.value,
            'selected': source.selected,
            'source_label': source.source_label,
            'last_error': source.last_error,
            'managed_by_workspace': source.managed_by_workspace,
        }

    def _serialize_tika_format(self, item: TikaFormatSelection) -> dict[str, object]:
        return {
            'format_id': item.format_id,
            'display_name': item.display_name,
            'tier': item.tier.value,
            'enabled': item.enabled,
            'visible': item.visible,
        }

    def _serialize_watch_state(self, state: ExtensionWatchState) -> dict[str, object]:
        return {
            'running': state.running,
            'last_event_at': state.last_event_at,
            'last_scan_at': state.last_scan_at,
            'last_error': state.last_error,
            'pending_changes': state.pending_changes,
        }

    def _serialize_pdf_status(self, status: PdfExtensionStatus) -> dict[str, object]:
        return {
            'index_state': status.index_state.value,
            'build_in_progress': status.build_in_progress,
            'watch_running': status.watch_running,
            'watch_state': self._serialize_watch_state(status.watch_state),
            'last_error': status.last_error,
            'indexed_document_count': status.indexed_document_count,
        }

    def _serialize_tika_status(self, status: TikaExtensionStatus) -> dict[str, object]:
        return {
            'index_state': status.index_state.value,
            'build_in_progress': status.build_in_progress,
            'watch_running': status.watch_running,
            'watch_state': self._serialize_watch_state(status.watch_state),
            'last_error': status.last_error,
            'indexed_document_count': status.indexed_document_count,
            'runtime': {
                'installed': status.runtime.installed,
                'installing': status.runtime.installing,
                'java_available': status.runtime.java_available,
                'jar_available': status.runtime.jar_available,
                'starting': status.runtime.starting,
                'running': status.runtime.running,
                'healthy': status.runtime.healthy,
                'version': status.runtime.version,
                'install_root': status.runtime.install_root,
                'java_path': status.runtime.java_path,
                'jar_path': status.runtime.jar_path,
                'pid': status.runtime.pid,
                'port': status.runtime.port,
                'last_error': status.runtime.last_error,
            },
        }

    def _parse_sources(self, payload: object) -> list[ExtensionSourceDirectory]:
        result: list[ExtensionSourceDirectory] = []
        if not isinstance(payload, list):
            return result
        for item in payload:
            if not isinstance(item, dict):
                continue
            path = str(item.get('path') or '').strip()
            if not path:
                continue
            result.append(
                ExtensionSourceDirectory(
                    path=path,
                    state=self._enum_value(ExtensionDirectoryState, item.get('state'), ExtensionDirectoryState.DISABLED),
                    selected=bool(item.get('selected', False)),
                    source_label=str(item.get('source_label') or ''),
                    last_error=str(item.get('last_error') or ''),
                    managed_by_workspace=bool(item.get('managed_by_workspace', False)),
                )
            )
        return result

    def _parse_tika_formats(self, payload: object) -> list[TikaFormatSelection]:
        result: list[TikaFormatSelection] = []
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                format_id = str(item.get('format_id') or '').strip().lower()
                if not format_id:
                    continue
                result.append(
                    TikaFormatSelection(
                        format_id=format_id,
                        display_name=str(item.get('display_name') or format_id),
                        tier=self._enum_value(TikaFormatSupportTier, item.get('tier'), TikaFormatSupportTier.UNKNOWN),
                        enabled=bool(item.get('enabled', False)),
                        visible=bool(item.get('visible', True)),
                    )
                )
        return result

    def _parse_watch_state(self, payload: object) -> ExtensionWatchState:
        if not isinstance(payload, dict):
            return ExtensionWatchState()
        return ExtensionWatchState(
            running=bool(payload.get('running', False)),
            last_event_at=str(payload.get('last_event_at') or ''),
            last_scan_at=str(payload.get('last_scan_at') or ''),
            last_error=str(payload.get('last_error') or ''),
            pending_changes=int(payload.get('pending_changes', 0) or 0),
        )

    def _parse_pdf_status(self, payload: object) -> PdfExtensionStatus:
        if not isinstance(payload, dict):
            return PdfExtensionStatus()
        watch_state = self._parse_watch_state(payload.get('watch_state'))
        return PdfExtensionStatus(
            index_state=self._enum_value(ExtensionIndexState, payload.get('index_state'), ExtensionIndexState.DISABLED),
            build_in_progress=bool(payload.get('build_in_progress', False)),
            watch_running=bool(payload.get('watch_running', watch_state.running)),
            watch_state=watch_state,
            last_error=str(payload.get('last_error') or ''),
            indexed_document_count=int(payload.get('indexed_document_count', 0) or 0),
        )

    def _parse_tika_status(self, payload: object) -> TikaExtensionStatus:
        if not isinstance(payload, dict):
            return TikaExtensionStatus()
        runtime_payload = payload.get('runtime') if isinstance(payload.get('runtime'), dict) else {}
        watch_state = self._parse_watch_state(payload.get('watch_state'))
        return TikaExtensionStatus(
            index_state=self._enum_value(ExtensionIndexState, payload.get('index_state'), ExtensionIndexState.DISABLED),
            build_in_progress=bool(payload.get('build_in_progress', False)),
            watch_running=bool(payload.get('watch_running', watch_state.running)),
            watch_state=watch_state,
            last_error=str(payload.get('last_error') or ''),
            indexed_document_count=int(payload.get('indexed_document_count', 0) or 0),
            runtime=TikaRuntimeStatus(
                installed=bool(runtime_payload.get('installed', False)),
                installing=bool(runtime_payload.get('installing', False)),
                java_available=bool(runtime_payload.get('java_available', False)),
                jar_available=bool(runtime_payload.get('jar_available', False)),
                starting=bool(runtime_payload.get('starting', False)),
                running=bool(runtime_payload.get('running', False)),
                healthy=bool(runtime_payload.get('healthy', False)),
                version=str(runtime_payload.get('version') or ''),
                install_root=str(runtime_payload.get('install_root') or ''),
                java_path=str(runtime_payload.get('java_path') or ''),
                jar_path=str(runtime_payload.get('jar_path') or ''),
                pid=int(runtime_payload.get('pid', 0) or 0),
                port=int(runtime_payload.get('port', 9998) or 9998),
                last_error=str(runtime_payload.get('last_error') or ''),
            ),
        )

    def _enum_value(self, enum_cls, raw_value: object, default):
        try:
            return enum_cls(str(raw_value or default.value))
        except ValueError:
            return default
