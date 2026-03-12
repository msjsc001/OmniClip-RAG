import os
import sys
from pathlib import Path


_handles = []
base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)).resolve()
app_dir = Path(getattr(sys, "executable", base_dir)).resolve().parent if getattr(sys, "frozen", False) else base_dir
runtime_dir = app_dir / "runtime"

for candidate in (runtime_dir, base_dir / ".packages", base_dir / ".vendor"):
    if candidate.exists():
        candidate_path = str(candidate)
        if candidate_path not in sys.path:
            sys.path.insert(0, candidate_path)

search_dirs = [
    runtime_dir,
    runtime_dir / "pyarrow",
    runtime_dir / "pyarrow.libs",
    runtime_dir / "numpy.libs",
    runtime_dir / "scipy.libs",
    runtime_dir / "torch" / "lib",
    runtime_dir / "onnxruntime",
    runtime_dir / "onnxruntime" / "capi",
    runtime_dir / "PySide6",
    runtime_dir / "PySide6" / "plugins",
    runtime_dir / "shiboken6",
    runtime_dir / "tokenizers",
    runtime_dir / "safetensors",
    base_dir,
    base_dir / "PySide6",
    base_dir / "PySide6" / "plugins",
    base_dir / "shiboken6",
    base_dir / ".packages",
    base_dir / ".packages" / "PySide6",
    base_dir / ".packages" / "shiboken6",
    base_dir / ".packages" / "pyarrow",
    base_dir / ".packages" / "pyarrow.libs",
    base_dir / ".packages" / "numpy.libs",
    base_dir / ".packages" / "scipy.libs",
    base_dir / ".packages" / "torch" / "lib",
    base_dir / "pyarrow",
    base_dir / "pyarrow.libs",
    base_dir / "numpy.libs",
    base_dir / "scipy.libs",
    base_dir / "torch" / "lib",
    base_dir / "onnxruntime" / "capi",
    base_dir / "tokenizers",
    base_dir / "safetensors",
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
