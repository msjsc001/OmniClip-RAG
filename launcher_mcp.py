from __future__ import annotations

from launcher_support import (
    _apply_pending_runtime_updates,
    _bootstrap_local_packages,
    _collect_bundle_dll_dirs,
    _runtime_bootstrap_paths,
)


__all__ = [
    '_apply_pending_runtime_updates',
    '_bootstrap_local_packages',
    '_collect_bundle_dll_dirs',
    '_runtime_bootstrap_paths',
]


_bootstrap_local_packages()

from omniclip_rag.app_entry.mcp import main


if __name__ == '__main__':
    raise SystemExit(main())
