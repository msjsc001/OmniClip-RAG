from __future__ import annotations

import logging
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from PySide6 import QtCore

from ..config import ensure_data_paths, normalize_vault_path
from ..errors import BuildCancelledError, RuntimeDependencyError
from ..models import QueryInsights, QueryResult
from ..service import WATCHDOG_AVAILABLE, OmniClipService


LOGGER = logging.getLogger(__name__)


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


class QueryWorker(QtCore.QObject):
    progress = QtCore.Signal(object)
    succeeded = QtCore.Signal(object)
    failed = QtCore.Signal(str, str)
    finished = QtCore.Signal()

    def __init__(self, *, config, paths, query_text: str, copy_result: bool, score_threshold: float, allowed_families: tuple[str, ...]) -> None:
        super().__init__()
        self._config = config
        self._paths = paths
        self._query_text = query_text
        self._copy_result = copy_result
        self._score_threshold = score_threshold
        self._allowed_families = tuple(allowed_families)
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
                allowed_families=self._allowed_families,
                on_progress=lambda payload: _safe_emit(self.progress, dict(payload)),
            )
            snapshot = service.status_snapshot()
            _safe_emit(
                self.succeeded,
                QueryTaskResult(
                    query_text=self._query_text,
                    copied=self._copy_result,
                    score_threshold=self._score_threshold,
                    allowed_families=self._allowed_families,
                    result=result,
                    status_snapshot=snapshot,
                )
            )
        except RuntimeDependencyError as exc:
            LOGGER.exception('Query worker failed because the vector runtime is not ready.')
            _safe_emit(self.failed, str(exc).strip(), traceback.format_exc())
        except Exception as exc:
            LOGGER.exception('Query worker crashed unexpectedly.')
            _safe_emit(self.failed, str(exc).strip() or exc.__class__.__name__, traceback.format_exc())
        finally:
            service.close()
            _safe_emit(self.finished)


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


class MultiVaultQueryWorker(QtCore.QObject):
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

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _selected_vaults(self) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        raw_values = list(getattr(self._config, 'md_selected_vault_paths', ()) or ())
        if not raw_values:
            raw_values = [getattr(self._config, 'vault_path', '')]
        for raw_value in raw_values:
            normalized = normalize_vault_path(raw_value)
            lowered = normalized.lower()
            if not normalized or lowered in seen:
                continue
            seen.add(lowered)
            ordered.append(normalized)
        return ordered

    def _emit_outer_progress(
        self,
        *,
        index: int,
        total: int,
        vault_path: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        payload = dict(payload or {})
        inner_percent = float(payload.get('overall_percent', 0.0) or 0.0)
        outer_percent = ((index - 1) + (max(0.0, min(inner_percent, 100.0)) / 100.0)) / max(total, 1) * 100.0
        payload['overall_percent'] = outer_percent
        payload['vault_path'] = vault_path
        payload.setdefault('current_path', vault_path)
        payload.setdefault('stage_status', 'query_vault')
        _safe_emit(self.progress, payload)

    def _decorate_hits(self, hits: list[object], *, vault_path: str, multi_vault: bool) -> list[object]:
        if not multi_vault:
            return list(hits)
        vault_name = Path(vault_path).name or vault_path
        decorated: list[object] = []
        for hit in hits:
            source_label = str(getattr(hit, 'source_label', '') or '').strip()
            if source_label:
                source_label = f'{vault_name} · {source_label}'
            else:
                source_label = vault_name
            decorated.append(replace(hit, source_label=source_label))
        return decorated

    def _run(self) -> None:
        selected_vaults = self._selected_vaults()
        if not selected_vaults:
            _safe_emit(self.failed, 'No markdown vault is enabled for querying.', '')
            _safe_emit(self.finished)
            return
        total = len(selected_vaults)
        merged_hits: list[object] = []
        runtime_warnings: list[str] = []
        trace_lines: list[str] = []
        snapshots: dict[str, dict[str, object]] = {}
        multi_vault = total > 1
        try:
            for index, vault_path in enumerate(selected_vaults, start=1):
                config = replace(
                    self._config,
                    vault_path=vault_path,
                    md_selected_vault_paths=list(selected_vaults),
                )
                paths = ensure_data_paths(getattr(config, 'data_root', None), vault_path)
                service = OmniClipService(config, paths)
                try:
                    snapshot = service.status_snapshot()
                    snapshots[vault_path] = dict(snapshot)
                    available_families = {str(item).strip().lower() for item in snapshot.get('query_available_families', []) if str(item).strip()}
                    requested = {item.strip().lower() for item in self._allowed_families if item.strip()}
                    if not (available_families & requested):
                        self._emit_outer_progress(
                            index=index,
                            total=total,
                            vault_path=vault_path,
                            payload={
                                'stage_status': 'skip_unavailable',
                                'overall_percent': 100.0,
                                'current_path': vault_path,
                            },
                        )
                        trace_lines.append(f'已跳过未就绪来源目录：{vault_path}')
                        continue
                    self._emit_outer_progress(
                        index=index,
                        total=total,
                        vault_path=vault_path,
                        payload={
                            'stage_status': 'query_vault',
                            'overall_percent': 0.0,
                            'current_path': vault_path,
                        },
                    )
                    result = service.query(
                        self._query_text,
                        copy_result=self._copy_result,
                        score_threshold=self._score_threshold,
                        allowed_families=self._allowed_families,
                        on_progress=lambda payload, i=index, t=total, v=vault_path: self._emit_outer_progress(
                            index=i,
                            total=t,
                            vault_path=v,
                            payload=dict(payload),
                        ),
                    )
                    merged_hits.extend(self._decorate_hits(list(getattr(result, 'hits', []) or ()), vault_path=vault_path, multi_vault=multi_vault))
                    insights = getattr(result, 'insights', None)
                    if insights is not None:
                        runtime_warnings.extend(tuple(getattr(insights, 'runtime_warnings', ()) or ()))
                        trace_lines.extend(tuple(getattr(insights, 'trace_lines', ()) or ()))
                finally:
                    service.close()
            merged_hits.sort(key=lambda hit: float(getattr(hit, 'score', 0.0) or 0.0), reverse=True)
            final_limit = max(int(getattr(self._config, 'query_limit', 15) or 15), 1)
            final_hits = merged_hits[:final_limit]
            dedup_warnings = tuple(dict.fromkeys(str(item).strip() for item in runtime_warnings if str(item).strip()))
            dedup_trace_lines = tuple(dict.fromkeys(str(item).strip() for item in trace_lines if str(item).strip()))
            result = QueryResult(
                hits=final_hits,
                context_text='',
                insights=QueryInsights(
                    selected_hits=len(final_hits),
                    runtime_warnings=dedup_warnings,
                    trace_lines=dedup_trace_lines,
                    query_plan={
                        'mode': 'multi_vault_fanout',
                        'vaults': list(selected_vaults),
                    },
                ),
            )
            _safe_emit(
                self.succeeded,
                QueryTaskResult(
                    query_text=self._query_text,
                    copied=self._copy_result,
                    score_threshold=self._score_threshold,
                    allowed_families=self._allowed_families,
                    result=result,
                    status_snapshot={
                        'mode': 'multi_vault_fanout',
                        'vaults': list(selected_vaults),
                        'vault_snapshots': snapshots,
                    },
                ),
            )
        except RuntimeDependencyError as exc:
            LOGGER.exception('Multi-vault query worker failed because the vector runtime is not ready.')
            _safe_emit(self.failed, str(exc).strip(), traceback.format_exc())
        except Exception as exc:
            LOGGER.exception('Multi-vault query worker crashed unexpectedly.')
            _safe_emit(self.failed, str(exc).strip() or exc.__class__.__name__, traceback.format_exc())
        finally:
            _safe_emit(self.finished)

    def _emit_log(self, message: str) -> None:
        text_value = str(message or '').strip()
        if text_value:
            _safe_emit(self.log, text_value)


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
                on_update=lambda payload: _safe_emit(self.updated, dict(payload)),
            )
        except Exception as exc:
            LOGGER.exception('Watch worker crashed unexpectedly.')
            _safe_emit(self.failed, str(exc).strip() or exc.__class__.__name__, traceback.format_exc())
        finally:
            service.close()
            _safe_emit(self.stopped, raw_mode)
            _safe_emit(self.finished)

