from __future__ import annotations

from pathlib import Path

from ..config import DataPaths


def build_extension_data_paths(paths: DataPaths, pipeline: str) -> DataPaths:
    """Create a fully isolated DataPaths view for one extension pipeline."""

    pipeline = str(pipeline or '').strip().lower() or 'extension'
    root = paths.root / 'extensions' / pipeline
    state_dir = root / 'state'
    logs_dir = root / 'logs'
    exports_dir = root / 'exports'
    for directory in (root, state_dir, logs_dir, exports_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return DataPaths(
        global_root=paths.global_root,
        shared_root=paths.shared_root,
        workspaces_dir=paths.workspaces_dir,
        workspace_id=paths.workspace_id,
        root=root,
        state_dir=state_dir,
        logs_dir=logs_dir,
        cache_dir=paths.cache_dir,
        exports_dir=exports_dir,
        config_file=paths.config_file,
        sqlite_file=state_dir / f'{pipeline}.sqlite3',
    )


__all__ = ['build_extension_data_paths']
