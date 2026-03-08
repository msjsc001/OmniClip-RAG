from __future__ import annotations


class BuildCancelledError(RuntimeError):
    """Raised when the user cancels a long-running full rebuild."""


class RuntimeDependencyError(RuntimeError):
    """Raised when the packaged app is missing an optional heavy AI runtime."""

