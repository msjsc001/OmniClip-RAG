from __future__ import annotations

import sys
from pathlib import Path

import launcher_support as _support


__all__ = [
    '_apply_pending_runtime_updates',
    '_bootstrap_local_packages',
    '_collect_bundle_dll_dirs',
    '_register_dll_directories',
    '_runtime_bootstrap_paths',
]


def _runtime_bootstrap_paths(runtime_dir: Path):
    return _support._runtime_bootstrap_paths(runtime_dir)


def _register_dll_directories(paths):
    return _support._register_dll_directories(paths)


def _collect_bundle_dll_dirs(*, bundle_root: Path, payload_root: Path, runtime_dir: Path | None = None, extra_dll_paths=None):
    return _support._collect_bundle_dll_dirs(
        bundle_root=bundle_root,
        payload_root=payload_root,
        runtime_dir=runtime_dir,
        extra_dll_paths=extra_dll_paths,
    )


def _apply_pending_runtime_updates(runtime_dir: Path):
    return _support._apply_pending_runtime_updates(runtime_dir)


def _bootstrap_local_packages() -> None:
    bundle_root = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    payload_root = Path(getattr(sys, '_MEIPASS', bundle_root)).resolve()
    runtime_dir = _support._preferred_runtime_dir()
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
