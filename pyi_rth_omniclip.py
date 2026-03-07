import os
import sys
from pathlib import Path


_handles = []
base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
search_dirs = [
    base_dir,
    base_dir / "pyarrow",
    base_dir / "pyarrow.libs",
    base_dir / "numpy.libs",
    base_dir / "scipy.libs",
    base_dir / "torch" / "lib",
    base_dir / ".packages",
    base_dir / ".packages" / "pyarrow",
    base_dir / ".packages" / "pyarrow.libs",
    base_dir / ".packages" / "numpy.libs",
    base_dir / ".packages" / "scipy.libs",
    base_dir / ".packages" / "torch" / "lib",
]
existing = [str(path) for path in search_dirs if path.exists()]
if existing:
    current = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join(existing + ([current] if current else []))
    if hasattr(os, "add_dll_directory"):
        for path in existing:
            try:
                _handles.append(os.add_dll_directory(path))
            except OSError:
                continue
