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
        if value:
            candidate = Path(value)
            if candidate.exists() and candidate not in sys_paths:
                sys_paths.append(candidate)
    dll_value = str(payload.get('dll_dir') or '').strip()
    if dll_value:
        dll_candidate = Path(dll_value)
        if dll_candidate.exists():
            dll_paths.append(dll_candidate)
    return sys_paths, dll_paths


def _bootstrap_vendor_packages() -> None:
    root = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    runtime_dir = root / 'runtime'
    extra_sys_paths, extra_dll_paths = _runtime_bootstrap_paths(runtime_dir)
    package_dirs = [runtime_dir, *extra_sys_paths, root / '.packages', root / '.vendor']
    for candidate in package_dirs:
        if candidate.exists():
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
        *extra_dll_paths,
        root / '.packages',
        root / '.packages' / 'pyarrow.libs',
        root / '.packages' / 'numpy.libs',
        root / '.packages' / 'scipy.libs',
        root / '.packages' / 'torch' / 'lib',
    ]
    existing = [str(path) for path in dll_dirs if path.exists()]
    if not existing:
        return

    current = os.environ.get('PATH', '')
    os.environ['PATH'] = os.pathsep.join(existing + ([current] if current else []))
    if hasattr(os, 'add_dll_directory'):
        for path in existing:
            try:
                _DLL_HANDLES.append(os.add_dll_directory(path))
            except OSError:
                continue


_bootstrap_vendor_packages()

__all__ = ['__version__']

__version__ = '0.1.6'
