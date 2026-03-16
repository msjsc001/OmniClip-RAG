from __future__ import annotations

"""Parser namespace for isolated extension pipelines.

Why: the Markdown mainline must not eagerly import optional extension parsers.
Importing concrete adapters here makes a plain Markdown query depend on PDF/Tika
parser health at module import time, which is exactly the coupling this
subsystem was meant to avoid. Concrete parsers should be imported directly by
their owning extension service only when that pipeline is actually used.
"""

__all__: list[str] = []
