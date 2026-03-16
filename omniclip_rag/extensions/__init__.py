from __future__ import annotations

"""Isolated extension-format subsystem namespace.

Why: the Markdown mainline must be able to import its service layer without
pulling in optional extension parsers or runtimes. Keep this package root
minimal and let callers import concrete submodules lazily.
"""

__all__: list[str] = []
