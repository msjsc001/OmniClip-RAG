from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from omniclip_rag.runtime_layout import ensure_runtime_layout


_DLL_HANDLES: list[object] = []


def _runtime_bootstrap_paths(runtime_dir: Path) -> tuple[list[Path], list[Path]]:
    marker = runtime_dir / '_runtime_bootstrap.json'
    if not marker.exists():
        return [], []
    try:
        payload = json.loads(marker.read_text(encoding='utf-8'))
    except Exception:
        return [], []

    # Why: the frozen app must keep using its own bundled stdlib. Pulling
    # stdlib/platstdlib from an external Python installation poisons imports
    # (for example asyncio/base_events) and makes runtime health checks lie.
    sys_paths: list[Path] = []
    dll_paths: list[Path] = []
    dll_value = str(payload.get('dll_dir') or '').strip()
    if dll_value:
        candidate = Path(dll_value)
        if candidate.exists():
            dll_paths.append(candidate)
    return sys_paths, dll_paths


def _register_dll_directories(paths: list[Path]) -> None:
    existing = [str(path) for path in paths if path.exists()]
    if not existing:
        return
    current = os.environ.get('PATH', '')
    os.environ['PATH'] = os.pathsep.join(existing + ([current] if current else []))
    if hasattr(os, 'add_dll_directory'):
        for item in existing:
            try:
                _DLL_HANDLES.append(os.add_dll_directory(item))
            except OSError:
                continue


def _collect_bundle_dll_dirs(
    *,
    bundle_root: Path,
    payload_root: Path,
    runtime_dir: Path | None = None,
    extra_dll_paths: list[Path] | None = None,
) -> list[Path]:
    """Return DLL search directories needed by the lean packaged build.

    Why: PyInstaller stages vendored Qt/Shiboken DLLs under ``_internal/.vendor``
    while the importable extension modules live under ``_internal/PySide6`` and
    ``_internal/shiboken6``. Runtime packages stay isolated and are mounted only
    when semantic features actually need them.
    """

    extra_dll_paths = list(extra_dll_paths or [])
    return [
        payload_root,
        payload_root / 'PySide6',
        payload_root / 'PySide6' / 'plugins',
        payload_root / 'shiboken6',
        payload_root / '.vendor',
        payload_root / '.vendor' / 'PySide6',
        payload_root / '.vendor' / 'PySide6' / 'plugins',
        payload_root / '.vendor' / 'shiboken6',
        payload_root / '.packages',
        payload_root / '.packages' / 'PySide6',
        payload_root / '.packages' / 'PySide6' / 'plugins',
        payload_root / '.packages' / 'shiboken6',
        payload_root / '.packages' / 'pyarrow.libs',
        payload_root / '.packages' / 'numpy.libs',
        payload_root / '.packages' / 'scipy.libs',
        payload_root / '.packages' / 'torch' / 'lib',
        *extra_dll_paths,
        bundle_root / '.vendor',
        bundle_root / '.vendor' / 'PySide6',
        bundle_root / '.vendor' / 'PySide6' / 'plugins',
        bundle_root / '.vendor' / 'shiboken6',
        bundle_root / '.packages',
        bundle_root / '.packages' / 'PySide6',
        bundle_root / '.packages' / 'PySide6' / 'plugins',
        bundle_root / '.packages' / 'shiboken6',
        bundle_root / '.packages' / 'pyarrow.libs',
        bundle_root / '.packages' / 'numpy.libs',
        bundle_root / '.packages' / 'scipy.libs',
        bundle_root / '.packages' / 'torch' / 'lib',
    ]




def _apply_pending_runtime_updates(runtime_dir: Path) -> list[str]:
    return ensure_runtime_layout(runtime_dir)

def _bootstrap_local_packages() -> None:
    bundle_root = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    payload_root = Path(getattr(sys, '_MEIPASS', bundle_root)).resolve()
    runtime_dir = bundle_root / 'runtime'
    extra_sys_paths, extra_dll_paths = _runtime_bootstrap_paths(runtime_dir)

    package_dirs = [
        bundle_root,
        payload_root,
        *extra_sys_paths,
        payload_root / '.packages',
        payload_root / '.vendor',
        bundle_root / '.packages',
        bundle_root / '.vendor',
    ]
    prepend_paths = bool(getattr(sys, 'frozen', False))
    for candidate in package_dirs:
        if not candidate.exists():
            continue
        candidate_path = str(candidate)
        if candidate_path not in sys.path:
            if prepend_paths:
                sys.path.insert(0, candidate_path)
            else:
                sys.path.append(candidate_path)

    dll_dirs = _collect_bundle_dll_dirs(
        bundle_root=bundle_root,
        payload_root=payload_root,
        extra_dll_paths=extra_dll_paths,
    )
    _register_dll_directories(dll_dirs)


_bootstrap_local_packages()

from omniclip_rag.app_entry.desktop import main


if __name__ == '__main__':
    raise SystemExit(main())
