from __future__ import annotations

import logging
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass

from PySide6 import QtCore

from ..errors import BuildCancelledError, RuntimeDependencyError
from ..service import WATCHDOG_AVAILABLE, OmniClipService


LOGGER = logging.getLogger(__name__)

@dataclass(slots=True)
class QueryTaskResult:
    query_text: str
    copied: bool
    score_threshold: float
    result: object
    status_snapshot: dict[str, object]


# Why: QThread+moveToThread 模式下，同步阻塞式 run() 会完全占满 QThread，
#      导致 QThread 事件循环永远不会启动，thread.quit() 无效，deleteLater 永远不执行，
#      最终触发 "QThread: Destroyed while thread is still running" 和进程死锁。
#      回归老版 gui.py 验证过无数次的 threading.Thread(daemon=True) 模式，
#      保留 Qt Signal 做跨线程 UI 更新（Signal.emit() 本身线程安全）。


class QueryWorker(QtCore.QObject):
    progress = QtCore.Signal(object)
    succeeded = QtCore.Signal(object)
    failed = QtCore.Signal(str, str)
    finished = QtCore.Signal()

    def __init__(self, *, config, paths, query_text: str, copy_result: bool, score_threshold: float) -> None:
        super().__init__()
        self._config = config
        self._paths = paths
        self._query_text = query_text
        self._copy_result = copy_result
        self._score_threshold = score_threshold
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        service = OmniClipService(self._config, self._paths)
        try:
            result = service.query(
                self._query_text,
                copy_result=self._copy_result,
                score_threshold=self._score_threshold,
                on_progress=lambda payload: self.progress.emit(dict(payload)),
            )
            snapshot = service.status_snapshot()
            self.succeeded.emit(
                QueryTaskResult(
                    query_text=self._query_text,
                    copied=self._copy_result,
                    score_threshold=self._score_threshold,
                    result=result,
                    status_snapshot=snapshot,
                )
            )
        except RuntimeDependencyError as exc:
            LOGGER.exception('Query worker failed because the vector runtime is not ready.')
            self.failed.emit(str(exc).strip(), traceback.format_exc())
        except Exception as exc:
            LOGGER.exception('Query worker crashed unexpectedly.')
            self.failed.emit(str(exc).strip() or exc.__class__.__name__, traceback.format_exc())
        finally:
            service.close()
            self.finished.emit()


class FunctionWorker(QtCore.QObject):
    succeeded = QtCore.Signal(object)
    failed = QtCore.Signal(str, str)
    finished = QtCore.Signal()

    def __init__(self, *, fn: Callable[[], object]) -> None:
        super().__init__()
        self._fn = fn
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self.succeeded.emit(self._fn())
        except Exception as exc:
            LOGGER.exception('Background function worker crashed unexpectedly.')
            self.failed.emit(str(exc).strip() or exc.__class__.__name__, traceback.format_exc())
        finally:
            self.finished.emit()


class ServiceTaskWorker(QtCore.QObject):
    progress = QtCore.Signal(object)
    succeeded = QtCore.Signal(object)
    cancelled = QtCore.Signal(object)
    runtimeError = QtCore.Signal(str)
    failed = QtCore.Signal(str, str)
    finished = QtCore.Signal()

    def __init__(
        self,
        *,
        config,
        paths,
        runner: Callable[[OmniClipService, Callable[[dict[str, object]], None], threading.Event | None, threading.Event | None], object],
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._paths = paths
        self._runner = runner
        self._pause_event = pause_event
        self._cancel_event = cancel_event
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        service = OmniClipService(self._config, self._paths)
        try:
            payload = self._runner(service, self._emit_progress, self._pause_event, self._cancel_event)
            self.succeeded.emit(payload)
        except BuildCancelledError:
            self.cancelled.emit(service.status_snapshot())
        except RuntimeDependencyError as exc:
            LOGGER.exception('Service task worker failed because the vector runtime is not ready.')
            self.runtimeError.emit(str(exc).strip() or exc.__class__.__name__)
        except Exception as exc:
            LOGGER.exception('Service task worker crashed unexpectedly.')
            self.failed.emit(str(exc).strip() or exc.__class__.__name__, traceback.format_exc())
        finally:
            service.close()
            self.finished.emit()

    def _emit_progress(self, payload: dict[str, object]) -> None:
        self.progress.emit(dict(payload))


class WatchWorker(QtCore.QObject):
    updated = QtCore.Signal(object)
    failed = QtCore.Signal(str, str)
    stopped = QtCore.Signal(str)
    finished = QtCore.Signal()

    def __init__(self, *, config, paths, interval: float, force_polling: bool) -> None:
        super().__init__()
        self._config = config
        self._paths = paths
        self._interval = interval
        self._force_polling = force_polling
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def stop(self) -> None:
        self._stop_event.set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        service = OmniClipService(self._config, self._paths)
        raw_mode = 'polling' if self._force_polling or not WATCHDOG_AVAILABLE else 'watchdog'
        try:
            service.watch_until_stopped(
                self._stop_event,
                interval=self._interval,
                force_polling=self._force_polling,
                on_update=lambda payload: self.updated.emit(dict(payload)),
            )
        except Exception as exc:
            LOGGER.exception('Watch worker crashed unexpectedly.')
            self.failed.emit(str(exc).strip() or exc.__class__.__name__, traceback.format_exc())
        finally:
            service.close()
            self.stopped.emit(raw_mode)
            self.finished.emit()

