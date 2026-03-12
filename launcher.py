from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_DLL_HANDLES: list[object] = []


def _runtime_bootstrap_paths(runtime_dir: Path) -> tuple[list[Path], list[Path]]:
    marker = runtime_dir / '_runtime_bootstrap.json'
    if not marker.exists():
        return [], []
    try:
        payload = json.loads(marker.read_text(encoding='utf-8'))
    except Exception:
        return [], []

    sys_paths: list[Path] = []
    dll_paths: list[Path] = []
    for key in ('stdlib', 'platstdlib'):
        value = str(payload.get(key) or '').strip()
        if not value:
            continue
        candidate = Path(value)
        if candidate.exists() and candidate not in sys_paths:
            sys_paths.append(candidate)
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


def _bootstrap_local_packages() -> None:
    bundle_root = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    payload_root = Path(getattr(sys, '_MEIPASS', bundle_root)).resolve()
    runtime_dir = bundle_root / 'runtime'
    extra_sys_paths, extra_dll_paths = _runtime_bootstrap_paths(runtime_dir)

    package_dirs = [
        bundle_root,
        payload_root,
        runtime_dir,
        *extra_sys_paths,
        payload_root / '.packages',
        payload_root / '.vendor',
        bundle_root / '.packages',
        bundle_root / '.vendor',
    ]
    for candidate in package_dirs:
        if not candidate.exists():
            continue
        candidate_path = str(candidate)
        if candidate_path not in sys.path:
            sys.path.insert(0, candidate_path)

    dll_dirs = [
        runtime_dir,
        runtime_dir / 'bin',
        runtime_dir / 'pyarrow.libs',
        runtime_dir / 'numpy.libs',
        runtime_dir / 'scipy.libs',
        runtime_dir / 'torch' / 'lib',
        payload_root,
        payload_root / 'PySide6',
        payload_root / 'shiboken6',
        payload_root / '.packages',
        payload_root / '.packages' / 'PySide6',
        payload_root / '.packages' / 'shiboken6',
        payload_root / '.packages' / 'pyarrow.libs',
        payload_root / '.packages' / 'numpy.libs',
        payload_root / '.packages' / 'scipy.libs',
        payload_root / '.packages' / 'torch' / 'lib',
        *extra_dll_paths,
        bundle_root / '.packages',
        bundle_root / '.packages' / 'PySide6',
        bundle_root / '.packages' / 'shiboken6',
        bundle_root / '.packages' / 'pyarrow.libs',
        bundle_root / '.packages' / 'numpy.libs',
        bundle_root / '.packages' / 'scipy.libs',
        bundle_root / '.packages' / 'torch' / 'lib',
    ]
    _register_dll_directories(dll_dirs)


_bootstrap_local_packages()

from omniclip_rag.app_entry.desktop import main


if __name__ == '__main__':
    raise SystemExit(main())
