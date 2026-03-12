from __future__ import annotations

from ..gui import OmniClipDesktopApp
from ..legacy_single_instance import legacy_ui_lock


__all__ = ['OmniClipDesktopApp', 'main']


def main() -> int:
    lock = legacy_ui_lock()
    if not lock.try_acquire():
        return 0
    try:
        return OmniClipDesktopApp().run()
    finally:
        lock.release()
