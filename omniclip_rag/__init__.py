from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_vendor_packages() -> None:
    root = Path(__file__).resolve().parents[1]
    for directory_name in (".packages", ".vendor"):
        candidate = root / directory_name
        if candidate.exists():
            candidate_path = str(candidate)
            if candidate_path not in sys.path:
                sys.path.insert(0, candidate_path)


_bootstrap_vendor_packages()

__all__ = ["__version__"]

__version__ = "0.1.4"
