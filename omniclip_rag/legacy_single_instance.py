from __future__ import annotations

import os
from pathlib import Path

from .config import ensure_data_paths

if os.name == 'nt':
    import msvcrt
else:
    import fcntl


class ProcessSingletonLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def try_acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open('a+b')
        try:
            handle.seek(0)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
                handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()


def legacy_ui_lock() -> ProcessSingletonLock:
    return ProcessSingletonLock(ensure_data_paths().shared_root / 'legacy-ui.lock')


def is_legacy_ui_running() -> bool:
    lock = legacy_ui_lock()
    acquired = lock.try_acquire()
    if acquired:
        lock.release()
    return not acquired
