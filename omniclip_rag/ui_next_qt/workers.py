from __future__ import annotations

import logging
import json
import os
import subprocess
import tempfile
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6 import QtCore

from ..errors import BuildCancelledError, RuntimeDependencyError
from ..query_subprocess import (
    config_to_payload,
    query_result_from_payload,
    query_worker_command,
    query_worker_creationflags,
    query_worker_environment,
    write_json_atomic,
)
from ..service import WATCHDOG_AVAILABLE, OmniClipService
from ..startup_prewarm import (
    STARTUP_PREWARM_TIMEOUT_SECONDS,
    idle_priority_creationflags,
    runtime_probe_command,
    runtime_probe_environment,
)
from ..watch_subprocess import (
    watch_reindex_worker_command,
    watch_reindex_worker_environment,
)


LOGGER = logging.getLogger(__name__)


class _FairWatchReindexGate:
    """Serialize semantic workers without letting one vault reacquire forever."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._queue: deque[object] = deque()
        self._active = False

    def acquire(self, stop_event: threading.Event) -> bool:
        token = object()
        with self._condition:
            self._queue.append(token)
            while True:
                if stop_event.is_set():
                    try:
                        self._queue.remove(token)
                    except ValueError:
                        pass
                    self._condition.notify_all()
                    return False
                if not self._active and self._queue and self._queue[0] is token:
                    self._queue.popleft()
                    self._active = True
                    return True
                self._condition.wait(timeout=0.1)

    def release(self) -> None:
        with self._condition:
            if not self._active:
                return
            self._active = False
            self._condition.notify_all()


_WATCH_REINDEX_PROCESS_GATE = _FairWatchReindexGate()


def _safe_emit(signal, *args) -> None:
    try:
        signal.emit(*args)
    except RuntimeError:
        LOGGER.debug('Worker signal was dropped because the QObject was already deleted.', exc_info=True)


@dataclass(slots=True)
class QueryTaskResult:
    query_text: str
    copied: bool
    result: object
    score_threshold: float = 0.0
    allowed_families: tuple[str, ...] = field(default_factory=tuple)
    status_snapshot: dict[str, object] = field(default_factory=dict)


# Why: QThread+moveToThread 模式下，同步阻塞式 run() 会完全占满 QThread，
#      导致 QThread 事件循环永远不会启动，thread.quit() 无效，deleteLater 永远不执行，
#      最终触发 "QThread: Destroyed while thread is still running" 和进程死锁。
#      回归老版 gui.py 验证过无数次的 threading.Thread(daemon=True) 模式，
#      保留 Qt Signal 做跨线程 UI 更新（Signal.emit() 本身线程安全）。


class _IsolatedQueryWorker(QtCore.QObject):
    progress = QtCore.Signal(object)
    succeeded = QtCore.Signal(object)
    failed = QtCore.Signal(str, str)
    finished = QtCore.Signal()

    def __init__(
        self,
        *,
        config,
        query_text: str,
        copy_result: bool,
        score_threshold: float,
        allowed_families: tuple[str, ...],
    ) -> None:
        super().__init__()
        self._config = config
        self._query_text = query_text
        self._copy_result = copy_result
        self._score_threshold = score_threshold
        self._allowed_families = tuple(allowed_families)
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._cancel_event = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel_event.set()
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass

    @staticmethod
    def _reap_child_process(process: subprocess.Popen, *, terminate: bool) -> None:
        if terminate and process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                LOGGER.warning('Query child process could not be reaped after termination.')
        except OSError:
            pass

    def _request_payload(
        self,
        *,
        query_mode: str = 'hybrid',
        vector_device: str = '',
    ) -> dict[str, object]:
        config_payload = config_to_payload(self._config)
        if str(vector_device or '').strip():
            config_payload['vector_device'] = str(vector_device).strip()
        return {
            'config': config_payload,
            'query_text': self._query_text,
            'copy_result': self._copy_result,
            'score_threshold': self._score_threshold,
            'allowed_families': list(self._allowed_families),
            'query_mode': query_mode,
        }

    def _run_child(self, request: dict[str, object]) -> tuple[dict[str, object] | None, str]:
        with tempfile.TemporaryDirectory(prefix='omniclip-query-') as temp_dir:
            temp_root = Path(temp_dir)
            request_path = temp_root / 'request.json'
            output_path = temp_root / 'result.json'
            progress_path = temp_root / 'progress.json'
            write_json_atomic(request_path, request)
            command = query_worker_command(
                request_path=request_path,
                output_path=output_path,
                progress_path=progress_path,
            )
            kwargs: dict[str, object] = {
                'env': query_worker_environment(),
                'stdin': subprocess.DEVNULL,
                'stdout': subprocess.DEVNULL,
                'stderr': subprocess.DEVNULL,
            }
            creationflags = query_worker_creationflags()
            if creationflags:
                kwargs['creationflags'] = creationflags
            process: subprocess.Popen | None = None
            try:
                process = subprocess.Popen(command, **kwargs)
                self._process = process
                last_progress_text = ''
                while process.poll() is None:
                    if self._cancel_event.is_set():
                        self.cancel()
                        return None, '查询已取消'
                    if progress_path.exists():
                        try:
                            progress_text = progress_path.read_text(encoding='utf-8')
                            if progress_text and progress_text != last_progress_text:
                                last_progress_text = progress_text
                                _safe_emit(self.progress, dict(json.loads(progress_text)))
                        except (OSError, json.JSONDecodeError):
                            pass
                    time.sleep(0.1)
                return_code = int(process.wait() or 0)
                if output_path.exists():
                    try:
                        return dict(json.loads(output_path.read_text(encoding='utf-8'))), ''
                    except (OSError, json.JSONDecodeError) as exc:
                        return None, f'查询子进程返回了损坏的结果：{exc}'
                return None, f'查询子进程异常退出（代码 {return_code}）'
            finally:
                if process is not None:
                    self._reap_child_process(
                        process,
                        terminate=process.poll() is None,
                    )
                if self._process is process:
                    self._process = None

    def _emit_success(self, payload: dict[str, object], *, recovery_mode: str = '') -> None:
        result = query_result_from_payload(dict(payload.get('result') or {}))
        if recovery_mode:
            warnings = tuple(getattr(result.insights, 'runtime_warnings', ()) or ())
            warning_code = (
                'semantic_query_process_cpu_recovered'
                if recovery_mode == 'cpu-semantic'
                else 'semantic_query_process_recovered'
            )
            trace_line = (
                'GPU 语义查询子进程异常退出，本次已由新的独立进程使用 CPU 语义检索完成。'
                if recovery_mode == 'cpu-semantic'
                else '语义查询子进程异常退出，本次已自动使用独立字面检索完成。'
            )
            result.insights.runtime_warnings = tuple(dict.fromkeys([
                *warnings,
                warning_code,
            ]))
            result.insights.trace_lines = tuple(dict.fromkeys([
                *tuple(getattr(result.insights, 'trace_lines', ()) or ()),
                trace_line,
            ]))
        _safe_emit(
            self.succeeded,
            QueryTaskResult(
                query_text=str(payload.get('query_text') or self._query_text),
                copied=bool(payload.get('copied')),
                score_threshold=float(payload.get('score_threshold') or self._score_threshold),
                allowed_families=tuple(payload.get('allowed_families') or self._allowed_families),
                result=result,
                status_snapshot=dict(payload.get('status_snapshot') or {}),
            ),
        )

    def _run(self) -> None:
        try:
            payload, crash_message = self._run_child(self._request_payload())
            if self._cancel_event.is_set():
                return
            if payload is not None and str(payload.get('status') or '').lower() == 'ok':
                self._emit_success(payload)
                return
            if payload is not None:
                message = str(payload.get('error_message') or '').strip() or '查询子进程执行失败。'
                detail = str(payload.get('traceback') or '').strip()
                _safe_emit(self.failed, message, detail)
                return

            LOGGER.error('%s; retrying semantic retrieval on CPU without reranking.', crash_message)
            _safe_emit(self.progress, {
                'stage_status': 'semantic_cpu_fallback',
                'overall_percent': 10.0,
            })
            semantic_payload, semantic_crash = self._run_child(
                self._request_payload(
                    query_mode='hybrid_no_rerank',
                    vector_device='cpu',
                )
            )
            if self._cancel_event.is_set():
                return
            if (
                semantic_payload is not None
                and str(semantic_payload.get('status') or '').lower() == 'ok'
            ):
                self._emit_success(semantic_payload, recovery_mode='cpu-semantic')
                return

            LOGGER.error(
                '%s; CPU semantic retry also failed (%s); retrying lexical-only.',
                crash_message,
                semantic_crash,
            )
            _safe_emit(self.progress, {
                'stage_status': 'lexical_fallback',
                'overall_percent': 10.0,
            })
            fallback_payload, fallback_crash = self._run_child(
                self._request_payload(query_mode='lexical-only')
            )
            if self._cancel_event.is_set():
                return
            if (
                fallback_payload is not None
                and str(fallback_payload.get('status') or '').lower() == 'ok'
            ):
                self._emit_success(fallback_payload, recovery_mode='lexical')
                return
            if fallback_payload is not None:
                fallback_message = str(fallback_payload.get('error_message') or '').strip()
                fallback_detail = str(fallback_payload.get('traceback') or '').strip()
            else:
                fallback_message = fallback_crash
                fallback_detail = ''
            message = (
                f'{crash_message}。CPU 语义重试也未完成（{semantic_crash}）；'
                '主程序仍在运行；自动字面检索也未能完成：'
                f'{fallback_message or "未知错误"}'
            )
            _safe_emit(self.failed, message, fallback_detail)
        except Exception as exc:
            LOGGER.exception('Isolated query coordinator crashed unexpectedly.')
            _safe_emit(self.failed, str(exc).strip() or exc.__class__.__name__, traceback.format_exc())
        finally:
            self._process = None
            _safe_emit(self.finished)


class QueryWorker(_IsolatedQueryWorker):
    def __init__(self, *, config, paths, query_text: str, copy_result: bool, score_threshold: float, allowed_families: tuple[str, ...]) -> None:
        del paths
        super().__init__(
            config=config,
            query_text=query_text,
            copy_result=copy_result,
            score_threshold=score_threshold,
            allowed_families=allowed_families,
        )


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
            _safe_emit(self.succeeded, self._fn())
        except Exception as exc:
            LOGGER.exception('Background function worker crashed unexpectedly.')
            _safe_emit(self.failed, str(exc).strip() or exc.__class__.__name__, traceback.format_exc())
        finally:
            _safe_emit(self.finished)


class RuntimeProbeWorker(QtCore.QObject):
    succeeded = QtCore.Signal(object)
    failed = QtCore.Signal(str, str)
    cancelled = QtCore.Signal()
    finished = QtCore.Signal()

    def __init__(
        self,
        *,
        probe_kind: str,
        data_root: str,
        timeout_seconds: int = STARTUP_PREWARM_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__()
        self._probe_kind = str(probe_kind)
        self._data_root = str(data_root or '')
        self._timeout_seconds = max(int(timeout_seconds), 1)
        self._cancel_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel_event.set()
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass

    def _run(self) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix='omniclip-runtime-probe-') as temp_dir:
                output_path = Path(temp_dir) / 'result.json'
                command = runtime_probe_command(
                    probe_kind=self._probe_kind,
                    output_path=output_path,
                    data_root=self._data_root,
                )
                kwargs: dict[str, object] = {
                    'env': runtime_probe_environment(data_root=self._data_root),
                    'stdin': subprocess.DEVNULL,
                    'stdout': subprocess.DEVNULL,
                    'stderr': subprocess.DEVNULL,
                }
                creationflags = idle_priority_creationflags()
                if creationflags:
                    kwargs['creationflags'] = creationflags
                self._process = subprocess.Popen(command, **kwargs)
                deadline = time.monotonic() + self._timeout_seconds
                while self._process.poll() is None:
                    if self._cancel_event.wait(0.1):
                        self.cancel()
                        _safe_emit(self.cancelled)
                        return
                    if time.monotonic() >= deadline:
                        self.cancel()
                        raise TimeoutError(f'Runtime probe exceeded {self._timeout_seconds} seconds')
                if self._cancel_event.is_set():
                    _safe_emit(self.cancelled)
                    return
                if not output_path.exists():
                    raise RuntimeError(f'Runtime probe exited with code {self._process.returncode} without a result')
                result = json.loads(output_path.read_text(encoding='utf-8'))
                if str(result.get('status') or '').lower() != 'ok':
                    message = str(result.get('error_message') or '').strip() or 'Runtime probe failed'
                    detail = str(result.get('traceback') or '').strip()
                    raise RuntimeError(f'{message}\n{detail}'.strip())
                _safe_emit(self.succeeded, dict(result.get('payload') or {}))
        except Exception as exc:
            LOGGER.exception('Runtime probe subprocess failed.')
            _safe_emit(self.failed, str(exc).strip() or exc.__class__.__name__, traceback.format_exc())
        finally:
            self._process = None
            _safe_emit(self.finished)


class ProgressFunctionWorker(QtCore.QObject):
    progress = QtCore.Signal(object)
    succeeded = QtCore.Signal(object)
    failed = QtCore.Signal(str, str)
    finished = QtCore.Signal()

    def __init__(self, *, fn: Callable[[Callable[[dict[str, object]], None]], object]) -> None:
        super().__init__()
        self._fn = fn
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            result = self._fn(lambda payload: _safe_emit(self.progress, dict(payload)))
            _safe_emit(self.succeeded, result)
        except Exception as exc:
            LOGGER.exception('Background progress function worker crashed unexpectedly.')
            _safe_emit(self.failed, str(exc).strip() or exc.__class__.__name__, traceback.format_exc())
        finally:
            _safe_emit(self.finished)


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
            _safe_emit(self.succeeded, payload)
        except BuildCancelledError:
            _safe_emit(self.cancelled, service.status_snapshot())
        except RuntimeDependencyError as exc:
            LOGGER.exception('Service task worker failed because the vector runtime is not ready.')
            _safe_emit(self.runtimeError, str(exc).strip() or exc.__class__.__name__)
        except Exception as exc:
            LOGGER.exception('Service task worker crashed unexpectedly.')
            _safe_emit(self.failed, str(exc).strip() or exc.__class__.__name__, traceback.format_exc())
        finally:
            service.close()
            _safe_emit(self.finished)

    def _emit_progress(self, payload: dict[str, object]) -> None:
        _safe_emit(self.progress, dict(payload))


class ServiceFunctionWorker(QtCore.QObject):
    log = QtCore.Signal(str)
    succeeded = QtCore.Signal(object)
    runtimeError = QtCore.Signal(str)
    failed = QtCore.Signal(str, str)
    finished = QtCore.Signal()

    def __init__(
        self,
        *,
        config,
        paths,
        runner: Callable[[OmniClipService, Callable[[str], None]], object],
    ) -> None:
        super().__init__()
        self._config = config
        self._paths = paths
        self._runner = runner
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        service = OmniClipService(self._config, self._paths)
        try:
            payload = self._runner(service, self._emit_log)
            _safe_emit(self.succeeded, payload)
        except RuntimeDependencyError as exc:
            LOGGER.exception('Service function worker failed because the vector runtime is not ready.')
            _safe_emit(self.runtimeError, str(exc).strip() or exc.__class__.__name__)
        except Exception as exc:
            LOGGER.exception('Service function worker crashed unexpectedly.')
            _safe_emit(self.failed, str(exc).strip() or exc.__class__.__name__, traceback.format_exc())
        finally:
            service.close()
            _safe_emit(self.finished)


class MultiVaultQueryWorker(_IsolatedQueryWorker):
    def __init__(
        self,
        *,
        config,
        query_text: str,
        copy_result: bool,
        score_threshold: float,
        allowed_families: tuple[str, ...],
    ) -> None:
        super().__init__(
            config=config,
            query_text=query_text,
            copy_result=copy_result,
            score_threshold=score_threshold,
            allowed_families=allowed_families,
        )


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
        self._process: subprocess.Popen | None = None

    def stop(self) -> None:
        self._stop_event.set()
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        vault_name = Path(str(getattr(self._config, 'vault_path', '') or '')).name or 'vault'
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f'omniclip-watch-{vault_name}',
        )
        self._thread.start()

    def is_running(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())

    def wait(self, timeout: float | None = None) -> bool:
        thread = self._thread
        if thread is None or thread is threading.current_thread():
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    @staticmethod
    def _reap_process(process: subprocess.Popen, *, terminate: bool) -> None:
        if terminate and process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                LOGGER.warning('Live-watch reindex child could not be reaped.')
        except OSError:
            pass

    def _run_reindex_child(
        self,
        changed: list[str],
        deleted: list[str],
        _stop_event: threading.Event,
    ) -> dict[str, object]:
        acquired = _WATCH_REINDEX_PROCESS_GATE.acquire(self._stop_event)
        if not acquired:
            raise BuildCancelledError('cancelled')

        try:
            with tempfile.TemporaryDirectory(prefix='omniclip-watch-') as temp_dir:
                temp_root = Path(temp_dir)
                request_path = temp_root / 'request.json'
                output_path = temp_root / 'result.json'
                write_json_atomic(
                    request_path,
                    {
                        'config': config_to_payload(self._config),
                        'changed': list(changed),
                        'deleted': list(deleted),
                    },
                )
                kwargs: dict[str, object] = {
                    'env': watch_reindex_worker_environment(),
                    'stdin': subprocess.DEVNULL,
                    'stdout': subprocess.DEVNULL,
                    'stderr': subprocess.DEVNULL,
                }
                creationflags = query_worker_creationflags()
                if creationflags:
                    kwargs['creationflags'] = creationflags
                process: subprocess.Popen | None = None
                try:
                    process = subprocess.Popen(
                        watch_reindex_worker_command(
                            request_path=request_path,
                            output_path=output_path,
                        ),
                        **kwargs,
                    )
                    self._process = process
                    while process.poll() is None:
                        if self._stop_event.wait(0.1):
                            raise BuildCancelledError('cancelled')
                    return_code = int(process.wait() or 0)
                    if self._stop_event.is_set():
                        raise BuildCancelledError('cancelled')
                    if not output_path.exists():
                        raise RuntimeError(f'热监听增量子进程异常退出（代码 {return_code}）')
                    payload = dict(json.loads(output_path.read_text(encoding='utf-8')))
                    if str(payload.get('status') or '').lower() != 'ok':
                        message = str(payload.get('error_message') or '').strip()
                        detail = str(payload.get('traceback') or '').strip()
                        raise RuntimeError(message or detail or '热监听增量子进程执行失败。')
                    return payload
                finally:
                    if process is not None:
                        self._reap_process(
                            process,
                            terminate=process.poll() is None,
                        )
                    if self._process is process:
                        self._process = None
        finally:
            _WATCH_REINDEX_PROCESS_GATE.release()

    def _run(self) -> None:
        service = OmniClipService(self._config, self._paths)
        raw_mode = 'polling' if self._force_polling or not WATCHDOG_AVAILABLE else 'watchdog'
        try:
            service.watch_until_stopped(
                self._stop_event,
                interval=self._interval,
                force_polling=self._force_polling,
                on_update=lambda payload: _safe_emit(self.updated, dict(payload)),
                reindex_runner=self._run_reindex_child,
            )
        except Exception as exc:
            LOGGER.exception('Watch worker crashed unexpectedly.')
            _safe_emit(self.failed, str(exc).strip() or exc.__class__.__name__, traceback.format_exc())
        finally:
            service.close()
            _safe_emit(self.stopped, raw_mode)
            _safe_emit(self.finished)
