from __future__ import annotations

import gc
import json
import hashlib
import importlib
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from collections.abc import Iterable
from pathlib import Path
from typing import Callable, Protocol

from .build_control import BuildPerformanceController
from .canary_backend import CANARY_VECTOR_MODEL_ID, encode_batch_tensors, is_canary_vector_model
from .config import AppConfig, DataPaths, build_data_paths
from .data_root_bootstrap import resolve_active_data_root
from .errors import BuildCancelledError, RuntimeDependencyError
from .process_utils import run_hidden
from .runtime_layout import (
    list_pending_runtime_updates,
    load_runtime_component_registry,
    normalize_runtime_component_id,
    runtime_component_live_roots,
    runtime_component_registry_path,
)


class VectorCandidate(Protocol):
    chunk_id: str
    score: float


class Embedder(Protocol):
    def encode(self, texts: list[str], *, batch_size: int = 16, show_progress_bar: bool = False, normalize_embeddings: bool = True): ...


class VectorIndex(Protocol):
    def rebuild(
        self,
        documents: Iterable[dict[str, str]],
        *,
        total: int | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
        progress_offset: int = 0,
        reset_index: bool = True,
    ) -> None: ...

    def upsert(self, documents: list[dict[str, str]]) -> None: ...

    def delete(self, chunk_ids: list[str]) -> None: ...

    def search(self, query_text: str, limit: int) -> list["_VectorCandidate"]: ...

    def warmup(self) -> dict[str, object]: ...

    def reset(self) -> None: ...


class _VectorCandidate:
    def __init__(self, chunk_id: str, score: float) -> None:
        self.chunk_id = chunk_id
        self.score = score


_EMBEDDER_CACHE: dict[tuple[str, str, str], Embedder] = {}
_ACCELERATION_CACHE: dict[str, object] | None = None
_ACCELERATION_LOCK = threading.Lock()
_WRITE_BATCH_ROW_CAPS = {
    'cpu': {'quiet': 64, 'balanced': 96, 'peak': 128},
    'cuda': {'quiet': 128, 'balanced': 256, 'peak': 384},
}
_WRITE_BATCH_MEMORY_BUDGET_BYTES = {
    'cpu': 8 * 1024 * 1024,
    'cuda': 24 * 1024 * 1024,
}
_MIN_SAFE_WRITE_BATCH_ROWS = 32
_MIN_RETRY_WRITE_BATCH_ROWS = 8
_VECTOR_PROGRESS_HEARTBEAT_SECONDS = 0.75
_VECTOR_PRESSURE_SLEEP_SECONDS = 0.35
_VECTOR_TEXT_CHAR_LIMITS = {
    'cpu': 8000,
    'cuda': 8000,
}
_VECTOR_BATCH_CHAR_BUDGETS = {
    'cpu': {'quiet': 16000, 'balanced': 20000, 'peak': 24000},
    'cuda': {'quiet': 20000, 'balanced': 26000, 'peak': 32000},
}
_VECTOR_SLOW_BATCH_WARNING_SECONDS = 12.0
_VECTOR_STALL_STACK_DUMP_SECONDS = 45.0
_RUNTIME_STDLIB_SUPPORT_MODULES = (
    'asyncio.base_events',
    'asyncio.base_futures',
    'asyncio.events',
    'concurrent.futures',
    'concurrent.futures.process',
    'http.cookies',
    'multiprocessing',
    'multiprocessing.connection',
    'multiprocessing.context',
    'multiprocessing.process',
    'multiprocessing.reduction',
    'multiprocessing.util',
    'pdb',
    'timeit',
)
_RUNTIME_STDLIB_SUPPORT_READY: set[str] = set()
_RUNTIME_STDLIB_SUPPORT_LOCK = threading.Lock()
_RUNTIME_STDLIB_SUPPORT_LOGGED = False
_RUNTIME_CAPABILITY_STATE_FILENAME = '_runtime_capabilities.json'
_RUNTIME_CAPABILITY_STATE_VERSION = 2

LOGGER = logging.getLogger(__name__)


class _TorchCanaryEmbedder:
    def __init__(self, *, device: str) -> None:
        self.device = str(device or 'cpu')

    def encode(
        self,
        texts: list[str],
        *,
        batch_size: int = 16,
        show_progress_bar: bool = False,
        normalize_embeddings: bool = True,
    ):
        del batch_size, show_progress_bar
        with _runtime_import_environment(component_id='semantic-core'):
            import torch
        return encode_batch_tensors(
            torch,
            texts,
            device=self.device,
            normalize=normalize_embeddings,
        )


class NullVectorIndex:
    def rebuild(
        self,
        documents: Iterable[dict[str, str]],
        *,
        total: int | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
        progress_offset: int = 0,
        reset_index: bool = True,
    ) -> None:
        return None

    def upsert(self, documents: list[dict[str, str]]) -> None:
        return None

    def delete(self, chunk_ids: list[str]) -> None:
        return None

    def search(self, query_text: str, limit: int) -> list[_VectorCandidate]:
        return []

    def warmup(self) -> dict[str, object]:
        acceleration = detect_acceleration()
        return {
            "backend": "disabled",
            "model": None,
            "dimension": 0,
            "model_ready": False,
            "requested_device": "cpu",
            "resolved_device": resolve_vector_device("cpu"),
            **acceleration,
        }

    def status(self) -> dict[str, object]:
        return {
            'backend': 'disabled',
            'table_ready': False,
            'runtime_ready': False,
        }

    def reset(self) -> None:
        return None


class MissingRuntimeVectorIndex:
    def __init__(self, config: AppConfig, paths: DataPaths, *, backend: str, reason: BaseException | None = None) -> None:
        self.config = config
        self.paths = paths
        self.backend = backend
        self.reason = reason
        detail = ''
        if reason is not None:
            detail = f'\n\n底层缺失：{type(reason).__name__}: {reason}'
        self._message = runtime_dependency_message(config.vector_runtime, config.vector_device) + detail

    def _raise_runtime(self) -> None:
        raise RuntimeDependencyError(self._message)

    def rebuild(
        self,
        documents: Iterable[dict[str, str]],
        *,
        total: int | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
        progress_offset: int = 0,
        reset_index: bool = True,
    ) -> None:
        self._raise_runtime()

    def upsert(self, documents: list[dict[str, str]]) -> None:
        self._raise_runtime()

    def delete(self, chunk_ids: list[str]) -> None:
        self._raise_runtime()

    def search(self, query_text: str, limit: int) -> list[_VectorCandidate]:
        return []

    def warmup(self) -> dict[str, object]:
        self._raise_runtime()

    def status(self) -> dict[str, object]:
        return {
            'backend': self.backend,
            'table_ready': False,
            'runtime_ready': False,
            'reason': str(self.reason or ''),
        }

    def reset(self) -> None:
        return None


class LanceDbVectorIndex:
    def __init__(
        self,
        config: AppConfig,
        paths: DataPaths,
        *,
        embedder_factory: Callable[[], Embedder] | None = None,
    ) -> None:
        self.config = config
        self.paths = paths
        self._embedder_factory = embedder_factory or self._default_embedder_factory
        self._embedder: Embedder | None = None
        self._db_dir = paths.state_dir / "lancedb"
        self._db_dir.mkdir(parents=True, exist_ok=True)
        self._db = None
        self._table_name = "chunks"
        self._vector_dimension: int | None = None
        self._last_execution_report: dict[str, object] = {}

    def rebuild(
        self,
        documents: Iterable[dict[str, str]],
        *,
        total: int | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
        progress_offset: int = 0,
        reset_index: bool = True,
    ) -> None:
        if reset_index:
            self.reset()
        if total is None:
            try:
                total = len(documents)  # type: ignore[arg-type]
            except TypeError:
                total = 0
        if total <= 0:
            iterator = iter(documents)
            try:
                first_document = next(iterator)
            except StopIteration:
                return
            documents = [first_document, *iterator]
            total = len(documents)

        processed = max(int(progress_offset or 0), 0)
        encoded = max(int(progress_offset or 0), 0)
        encode_elapsed_total_ms = 0.0
        prepare_elapsed_total_ms = 0.0
        write_elapsed_total_ms = 0.0
        write_flush_count = 0
        staged_rows_hint = 0
        resolved_device = resolve_vector_device(self.config.vector_device)
        controller = BuildPerformanceController(self.config, resolved_device)
        iterator = iter(documents)
        if resolved_device == 'cuda':
            write_queue_capacity = {'quiet': 2, 'balanced': 4, 'peak': 6}.get(controller.profile, 4)
        else:
            write_queue_capacity = {'quiet': 1, 'balanced': 2, 'peak': 3}.get(controller.profile, 2)
        write_queue: queue.Queue = queue.Queue(maxsize=max(int(write_queue_capacity), 1))
        completion_queue: queue.Queue = queue.Queue()
        writer_stop = threading.Event()
        writer_thread: threading.Thread | None = None
        progress_emit_lock = threading.Lock()
        pending_prepared_document: tuple[dict[str, str], str, dict[str, int | bool]] | None = None

        def progress_display_current() -> int:
            return min(total, processed) if total and total > 0 else processed

        def pipeline_progress_current() -> int:
            return min(total, max(processed, encoded)) if total and total > 0 else max(processed, encoded)

        def current_progress_ratio() -> float:
            return (pipeline_progress_current() / total) if total and total > 0 else 0.0

        last_vector_heartbeat_at = 0.0

        def emit_vector_progress(snapshot, *, stage_status: str | None = None, extra: dict[str, object] | None = None) -> None:
            with progress_emit_lock:
                payload: dict[str, object] = {
                    'stage': 'vectorizing',
                    'current': progress_display_current(),
                    'total': total,
                    'encoded_count': encoded,
                    'written_count': processed,
                    'write_queue_depth': write_queue.qsize(),
                    'write_queue_capacity': write_queue_capacity,
                    'staged_write_rows': staged_rows_hint,
                    'encode_elapsed_total_ms': round(encode_elapsed_total_ms, 3),
                    'prepare_elapsed_total_ms': round(prepare_elapsed_total_ms, 3),
                    'write_elapsed_total_ms': round(write_elapsed_total_ms, 3),
                    'write_flush_count': write_flush_count,
                }
                if stage_status:
                    payload['stage_status'] = stage_status
                elif encoded > processed:
                    payload['stage_status'] = 'writing' if encoded >= total else 'encoding'
                payload.update(snapshot.to_progress_payload())
                if extra:
                    payload.update(extra)
                _emit_progress(on_progress, payload)

        def maybe_emit_vector_heartbeat(snapshot, *, stage_status: str, min_interval_seconds: float = _VECTOR_PROGRESS_HEARTBEAT_SECONDS, extra: dict[str, object] | None = None) -> None:
            nonlocal last_vector_heartbeat_at
            now = time.time()
            if now - last_vector_heartbeat_at < max(float(min_interval_seconds), 0.0):
                return
            last_vector_heartbeat_at = now
            emit_vector_progress(snapshot, stage_status=stage_status, extra=extra)

        def guard_resource_pressure() -> bool:
            sample = controller.monitor.sample(force=False)
            memory = sample.memory_percent if sample.memory_percent is not None else 0.0
            gpu_memory = sample.gpu_memory_percent if sample.gpu_memory_percent is not None else 0.0
            queue_fill = max(0.0, min(float(write_queue.qsize()) / max(float(write_queue_capacity), 1.0), 1.0))
            backlog_rows = max(encoded - processed, staged_rows_hint, 0)
            pipeline_active = backlog_rows >= max(controller.min_write_batch_size, controller.current_encode_batch_size) or write_queue.qsize() >= write_queue_capacity
            memory_guard = min(controller.targets['memory_soft'] + (6.0 if controller.profile == 'peak' else 4.0), 98.0)
            gpu_memory_guard = min(controller.targets['gpu_memory_soft'] + 3.0, 98.0)
            if pipeline_active and (memory >= memory_guard or gpu_memory >= gpu_memory_guard):
                snapshot = controller.note_pressure(reason='memory_guard', action='hold', shrink_ratio=0.65, cooldown_seconds=1.4, sample=sample)
                maybe_emit_vector_heartbeat(snapshot, stage_status='recovering', min_interval_seconds=0.4)
                _release_vector_memory(clear_cuda=resolved_device == 'cuda')
                time.sleep(_VECTOR_PRESSURE_SLEEP_SECONDS)
                return True
            if queue_fill >= 0.95:
                snapshot = controller.note_pressure(reason='write_backpressure', action='hold', shrink_ratio=0.9, cooldown_seconds=0.6, sample=sample)
                maybe_emit_vector_heartbeat(snapshot, stage_status='backpressure', min_interval_seconds=0.4)
                time.sleep(min(_VECTOR_PRESSURE_SLEEP_SECONDS, 0.25))
                return True
            return False

        def writer() -> None:
            table = None
            staged_documents: list[dict[str, str]] = []
            staged_vectors: list[object] = []
            vector_dimension_hint = int(self._vector_dimension or 0)

            def flush(force: bool = False) -> None:
                nonlocal table, staged_documents, staged_vectors, vector_dimension_hint
                while staged_documents:
                    target_size = self._safe_write_batch_limit(
                        controller.current_write_batch_size,
                        resolved_device=resolved_device,
                        profile=controller.profile,
                        vector_dimension=vector_dimension_hint,
                    )
                    if not force and len(staged_documents) < target_size:
                        break
                    target = min(max(target_size, 1), len(staged_documents))
                    if target <= 0:
                        break
                    retry_target = target
                    while retry_target > 0:
                        batch_documents = staged_documents[:retry_target]
                        batch_vectors = staged_vectors[:retry_target]
                        if not batch_documents:
                            retry_target = 0
                            break
                        if vector_dimension_hint <= 0:
                            vector_dimension_hint = self._infer_vector_dimension(batch_vectors)
                        rows = None
                        try:
                            prepare_started = time.perf_counter()
                            rows = self._materialize_rows(batch_documents, batch_vectors)
                            prepare_elapsed_ms = max((time.perf_counter() - prepare_started) * 1000.0, 0.0)
                            if rows and vector_dimension_hint <= 0:
                                vector_dimension_hint = len(rows[0]['vector'])
                            if table is None and rows:
                                self._ensure_table(len(rows[0]['vector']))
                                table = self._table()
                            write_started = time.perf_counter()
                            if rows:
                                table.add(rows)
                            write_elapsed_ms = max((time.perf_counter() - write_started) * 1000.0, 0.0)
                        except Exception as exc:
                            if not _is_memory_pressure_exception(exc) or retry_target <= _MIN_RETRY_WRITE_BATCH_ROWS:
                                raise
                            next_retry_target = max(_MIN_RETRY_WRITE_BATCH_ROWS, retry_target // 2)
                            if next_retry_target >= retry_target and retry_target > 1:
                                next_retry_target = retry_target - 1
                            LOGGER.warning('Vector writer hit memory pressure at batch=%s; retrying with batch=%s.', retry_target, next_retry_target)
                            try:
                                self.delete([str(item.get('chunk_id', '')) for item in batch_documents if str(item.get('chunk_id', ''))])
                            except Exception:
                                pass
                            _release_vector_memory(clear_cuda=resolved_device == 'cuda')
                            retry_target = next_retry_target
                            continue
                        del staged_documents[:retry_target]
                        del staged_vectors[:retry_target]
                        completion_queue.put(
                            (
                                'write',
                                {
                                    'written': len(rows or []),
                                    'prepare_elapsed_ms': prepare_elapsed_ms,
                                    'write_elapsed_ms': write_elapsed_ms,
                                    'queue_depth': write_queue.qsize(),
                                    'staged_rows': len(staged_documents),
                                },
                            )
                        )
                        if rows is not None:
                            del rows
                        break
                    if retry_target <= 0:
                        break

            try:
                while True:
                    _wait_for_controls(pause_event, cancel_event)
                    try:
                        item = write_queue.get(timeout=0.15)
                    except queue.Empty:
                        if writer_stop.is_set():
                            flush(force=True)
                            break
                        continue
                    force = item is None
                    if force:
                        writer_stop.set()
                    else:
                        batch_documents, batch_vectors = item
                        vector_batch = list(batch_vectors)
                        if vector_dimension_hint <= 0:
                            vector_dimension_hint = self._infer_vector_dimension(vector_batch)
                        staged_documents.extend(batch_documents)
                        staged_vectors.extend(vector_batch)
                    flush(force=force)
                    if force:
                        break
            except BuildCancelledError:
                completion_queue.put(('cancelled', None))
            except Exception as exc:
                completion_queue.put(('error', exc))

        def drain_write_completions(*, wait_for_one: bool = False) -> tuple[float, float]:
            nonlocal processed, prepare_elapsed_total_ms, write_elapsed_total_ms, write_flush_count, staged_rows_hint
            drained_prepare_ms = 0.0
            drained_write_ms = 0.0
            waited = False
            while True:
                try:
                    if wait_for_one and not waited:
                        kind, payload = completion_queue.get(timeout=0.25)
                        waited = True
                    else:
                        kind, payload = completion_queue.get_nowait()
                except queue.Empty:
                    break
                if kind == 'write':
                    report = payload
                    written = int(report.get('written', 0) or 0)
                    prepare_elapsed_ms = float(report.get('prepare_elapsed_ms', 0.0) or 0.0)
                    write_elapsed_ms = float(report.get('write_elapsed_ms', 0.0) or 0.0)
                    processed += written
                    drained_prepare_ms += prepare_elapsed_ms
                    drained_write_ms += write_elapsed_ms
                    prepare_elapsed_total_ms += prepare_elapsed_ms
                    write_elapsed_total_ms += write_elapsed_ms
                    write_flush_count += 1
                    staged_rows_hint = int(report.get('staged_rows', 0) or 0)
                elif kind == 'cancelled':
                    raise BuildCancelledError('cancelled')
                elif kind == 'error':
                    if isinstance(payload, Exception):
                        raise payload
                    raise RuntimeError(str(payload))
            return drained_prepare_ms, drained_write_ms

        def observe_write_feedback(drained_prepare_ms: float, drained_write_ms: float) -> None:
            if drained_prepare_ms <= 0.0 and drained_write_ms <= 0.0:
                return
            tuning = controller.observe(
                encode_elapsed_ms=0.0,
                prepare_elapsed_ms=drained_prepare_ms,
                write_elapsed_ms=drained_write_ms,
                write_queue_depth=write_queue.qsize(),
                write_queue_capacity=write_queue_capacity,
                progress_ratio=current_progress_ratio(),
            )
            emit_vector_progress(tuning)

        def dispatch_encoded_batch(batch_documents: list[dict[str, str]], batch_vectors) -> None:
            while True:
                _wait_for_controls(pause_event, cancel_event)
                drained_prepare_ms, drained_write_ms = drain_write_completions()
                observe_write_feedback(drained_prepare_ms, drained_write_ms)
                try:
                    write_queue.put((batch_documents, batch_vectors), timeout=0.15)
                    return
                except queue.Full:
                    snapshot = controller.note_pressure(reason='write_backpressure', action='hold', shrink_ratio=0.9, cooldown_seconds=0.6, force_sample=False)
                    maybe_emit_vector_heartbeat(snapshot, stage_status='backpressure', min_interval_seconds=0.4)
                    drained_prepare_ms, drained_write_ms = drain_write_completions(wait_for_one=True)
                    observe_write_feedback(drained_prepare_ms, drained_write_ms)
                    time.sleep(min(_VECTOR_PRESSURE_SLEEP_SECONDS, 0.25))
                    continue

        def collect_vector_batch(desired_batch: int, *, item_char_limit: int, batch_char_budget: int) -> tuple[list[dict[str, str]], list[str], dict[str, int]]:
            nonlocal pending_prepared_document
            batch: list[dict[str, str]] = []
            batch_texts: list[str] = []
            total_vector_chars = 0
            max_source_chars = 0
            max_vector_chars = 0
            truncated_count = 0

            while len(batch) < max(int(desired_batch), 1):
                if pending_prepared_document is not None:
                    document, vector_text, meta = pending_prepared_document
                    pending_prepared_document = None
                else:
                    try:
                        document = next(iterator)
                    except StopIteration:
                        break
                    vector_text, meta = _prepare_vector_text(document.get('rendered_text', ''), max_chars=item_char_limit)
                candidate_total_chars = total_vector_chars + int(meta['vector_chars'])
                if batch and candidate_total_chars > batch_char_budget:
                    pending_prepared_document = (document, vector_text, meta)
                    break
                batch.append(document)
                batch_texts.append(vector_text)
                total_vector_chars = candidate_total_chars
                max_source_chars = max(max_source_chars, int(meta['source_chars']))
                max_vector_chars = max(max_vector_chars, int(meta['vector_chars']))
                truncated_count += int(bool(meta['truncated']))

            return batch, batch_texts, {
                'documents': len(batch),
                'total_vector_chars': total_vector_chars,
                'max_source_chars': max_source_chars,
                'max_vector_chars': max_vector_chars,
                'truncated_count': truncated_count,
            }

        def encode_batch_with_watchdog(batch_texts: list[str], batch_stats: dict[str, int]):
            encode_started_at = time.perf_counter()
            encode_done = threading.Event()

            def heartbeat() -> None:
                slow_logged = False
                stack_dumped = False
                poll_seconds = max(min(_VECTOR_PROGRESS_HEARTBEAT_SECONDS, 0.5), 0.1)
                while not encode_done.wait(timeout=poll_seconds):
                    elapsed = time.perf_counter() - encode_started_at
                    if elapsed >= _VECTOR_SLOW_BATCH_WARNING_SECONDS and not slow_logged:
                        LOGGER.warning(
                            'Vector encode batch is still running after %.1fs (docs=%s vector_chars=%s max_source_chars=%s truncated=%s encoded=%s written=%s queue=%s/%s).',
                            elapsed,
                            batch_stats['documents'],
                            batch_stats['total_vector_chars'],
                            batch_stats['max_source_chars'],
                            batch_stats['truncated_count'],
                            encoded,
                            processed,
                            write_queue.qsize(),
                            write_queue_capacity,
                        )
                        slow_logged = True
                    if elapsed >= _VECTOR_STALL_STACK_DUMP_SECONDS and not stack_dumped:
                        _log_thread_stacks(
                            'Vector encode batch appears stalled',
                            docs=batch_stats['documents'],
                            vector_chars=batch_stats['total_vector_chars'],
                            max_source_chars=batch_stats['max_source_chars'],
                            encoded=encoded,
                            written=processed,
                            queue_depth=write_queue.qsize(),
                            queue_capacity=write_queue_capacity,
                        )
                        stack_dumped = True
                    snapshot = controller.snapshot(controller.monitor.sample(force=False), action='hold', reason='encode_active')
                    maybe_emit_vector_heartbeat(
                        snapshot,
                        stage_status='encoding',
                        min_interval_seconds=max(_VECTOR_PROGRESS_HEARTBEAT_SECONDS, 0.25),
                        extra={
                            'encoding_batch_docs': batch_stats['documents'],
                            'encoding_batch_chars': batch_stats['total_vector_chars'],
                            'encoding_batch_max_chars': batch_stats['max_source_chars'],
                            'encoding_batch_truncated': batch_stats['truncated_count'],
                            'encoding_elapsed_seconds': round(elapsed, 2),
                        },
                    )

            watchdog_thread = threading.Thread(target=heartbeat, daemon=True)
            watchdog_thread.start()
            try:
                return self._encode(
                    batch_texts,
                    batch_size=min(len(batch_texts), controller.current_encode_batch_size),
                )
            finally:
                encode_done.set()
                watchdog_thread.join(timeout=0.1)

        _wait_for_controls(pause_event, cancel_event)
        emit_vector_progress(controller.snapshot(), stage_status='loading_model')
        embedder = self._load_embedder()
        vector_text_char_limit = _infer_vector_text_char_limit(embedder, resolved_device=resolved_device)
        vector_batch_char_budget = _infer_vector_batch_char_budget(
            vector_text_char_limit,
            resolved_device=resolved_device,
            profile=controller.profile,
        )
        LOGGER.info(
            'Vector encode safeguards enabled: text_char_limit=%s batch_char_budget=%s device=%s profile=%s.',
            vector_text_char_limit,
            vector_batch_char_budget,
            resolved_device,
            controller.profile,
        )
        writer_thread = threading.Thread(target=writer, daemon=True)
        writer_thread.start()

        try:
            while True:
                _wait_for_controls(pause_event, cancel_event)
                drained_prepare_ms, drained_write_ms = drain_write_completions()
                observe_write_feedback(drained_prepare_ms, drained_write_ms)
                if guard_resource_pressure():
                    continue

                desired_batch = max(controller.current_encode_batch_size, 1)
                batch, batch_texts, batch_stats = collect_vector_batch(
                    desired_batch,
                    item_char_limit=vector_text_char_limit,
                    batch_char_budget=vector_batch_char_budget,
                )
                if not batch:
                    break
                if batch_stats['truncated_count'] > 0:
                    LOGGER.warning(
                        'Vector batch includes %s oversized chunks; embedding text was trimmed to <=%s chars (max source chars=%s, docs=%s, vector chars=%s).',
                        batch_stats['truncated_count'],
                        vector_text_char_limit,
                        batch_stats['max_source_chars'],
                        batch_stats['documents'],
                        batch_stats['total_vector_chars'],
                    )

                encode_elapsed_ms = 0.0
                while True:
                    started = time.perf_counter()
                    try:
                        vectors = encode_batch_with_watchdog(batch_texts, batch_stats)
                        encode_elapsed_ms = max((time.perf_counter() - started) * 1000.0, 0.0)
                        break
                    except RuntimeError as exc:
                        if _is_oom_error(exc) and controller.current_encode_batch_size > controller.min_encode_batch_size:
                            _release_vector_memory(clear_cuda=True)
                            tuning = controller.note_oom()
                            emit_vector_progress(tuning, stage_status='recovering')
                            time.sleep(_VECTOR_PRESSURE_SLEEP_SECONDS)
                            continue
                        raise

                _wait_for_controls(pause_event, cancel_event)
                encoded += len(batch)
                encode_elapsed_total_ms += encode_elapsed_ms
                dispatch_encoded_batch(batch, vectors)
                del batch_texts
                del vectors
                drained_prepare_ms, drained_write_ms = drain_write_completions()
                tuning = controller.observe(
                    encode_elapsed_ms=encode_elapsed_ms,
                    prepare_elapsed_ms=drained_prepare_ms,
                    write_elapsed_ms=drained_write_ms,
                    write_queue_depth=write_queue.qsize(),
                    write_queue_capacity=write_queue_capacity,
                    progress_ratio=current_progress_ratio(),
                )
                emit_vector_progress(tuning)

            _release_vector_memory(clear_cuda=resolved_device == 'cuda')
            while True:
                _wait_for_controls(pause_event, cancel_event)
                try:
                    write_queue.put(None, timeout=0.15)
                    break
                except queue.Full:
                    snapshot = controller.note_pressure(reason='write_backpressure', action='hold', shrink_ratio=0.9, cooldown_seconds=0.6, force_sample=False)
                    maybe_emit_vector_heartbeat(snapshot, stage_status='backpressure', min_interval_seconds=0.4)
                    drained_prepare_ms, drained_write_ms = drain_write_completions(wait_for_one=True)
                    observe_write_feedback(drained_prepare_ms, drained_write_ms)
                    time.sleep(min(_VECTOR_PRESSURE_SLEEP_SECONDS, 0.25))
            while (writer_thread is not None and writer_thread.is_alive()) or processed < encoded:
                _wait_for_controls(pause_event, cancel_event)
                drained_prepare_ms, drained_write_ms = drain_write_completions(wait_for_one=True)
                observe_write_feedback(drained_prepare_ms, drained_write_ms)
                if drained_prepare_ms <= 0.0 and drained_write_ms <= 0.0:
                    heartbeat_reason = 'cooldown_recovery' if controller.in_cooldown() else 'write_backpressure'
                    heartbeat_snapshot = controller.snapshot(controller.monitor.sample(force=False), action='hold', reason=heartbeat_reason)
                    heartbeat_status = 'recovering' if heartbeat_reason == 'cooldown_recovery' else 'flushing'
                    maybe_emit_vector_heartbeat(heartbeat_snapshot, stage_status=heartbeat_status)
            remaining_prepare_ms, remaining_write_ms = drain_write_completions()
            observe_write_feedback(remaining_prepare_ms, remaining_write_ms)
        finally:
            writer_stop.set()
            if writer_thread is not None and writer_thread.is_alive():
                try:
                    write_queue.put_nowait(None)
                except queue.Full:
                    pass
                writer_thread.join(timeout=1.0)
            drain_write_completions()
            _release_vector_memory(clear_cuda=resolved_device == 'cuda')

    def upsert(self, documents: list[dict[str, str]]) -> None:
        if not documents:
            return
        rows = self._embed_documents(documents)
        self._ensure_table(len(rows[0]["vector"]))
        self.delete([row["chunk_id"] for row in rows])
        self._table().add(rows)

    def delete(self, chunk_ids: list[str]) -> None:
        if not chunk_ids or not self._table_exists():
            return
        table = self._table()
        batch_size = max(int(self.config.vector_batch_size or 16) * 16, 256)
        for start in range(0, len(chunk_ids), batch_size):
            batch = [value for value in chunk_ids[start : start + batch_size] if value]
            if not batch:
                continue
            quoted = ", ".join(f"'{self._escape(value)}'" for value in batch)
            table.delete(f"chunk_id IN ({quoted})")

    def search(self, query_text: str, limit: int) -> list[_VectorCandidate]:
        if not query_text.strip() or not self._table_exists():
            return []
        raw_vector = self._encode([query_text])[0]
        vector = self._coerce_vector(raw_vector)
        rows = self._table().search(vector).limit(limit).to_list()
        return [
            _VectorCandidate(chunk_id=row["chunk_id"], score=_distance_to_score(row.get("_distance", 1.0)))
            for row in rows
        ]

    def warmup(self) -> dict[str, object]:
        vector = self._encode(["模型预热"])[0]
        acceleration = detect_acceleration()
        requested_device = (self.config.vector_device or "cpu").lower()
        resolved_device = resolve_vector_device(self.config.vector_device)
        return {
            "backend": "lancedb",
            "model": self.config.vector_model,
            "dimension": len(vector),
            "local_model_dir": str(get_local_model_dir(self.config, self.paths)),
            "model_ready": is_local_model_ready(self.config, self.paths),
            "requested_device": requested_device,
            "resolved_device": resolved_device,
            'execution_report': dict(self._last_execution_report),
            **acceleration,
        }

    def status(self) -> dict[str, object]:
        runtime_state = inspect_runtime_environment()
        if self._vector_dimension is None and self._table_exists():
            try:
                schema = self._table().schema
                self._vector_dimension = schema.field("vector").type.list_size
            except Exception:
                pass
        return {
            'backend': 'lancedb',
            'table_ready': self._table_exists(),
            'runtime_ready': bool(runtime_state.get('runtime_complete')),
            'db_dir': str(self._db_dir),
            'table_name': self._table_name,
            'vector_dimension': int(self._vector_dimension or 0),
            'execution_report': dict(self._last_execution_report),
            'runtime_root': str(runtime_state.get('runtime_dir') or ''),
        }

    def last_execution_report(self) -> dict[str, object]:
        return dict(self._last_execution_report)

    def reset(self) -> None:
        if self._db is not None and self._table_exists():
            self._db_connection().drop_table(self._table_name)
        self._vector_dimension = None
        table_dir = self._table_storage_path()
        if table_dir.exists():
            shutil.rmtree(table_dir, ignore_errors=True)

    @staticmethod
    def _infer_vector_dimension(vectors) -> int:
        try:
            if len(vectors) <= 0:
                return 0
            first = vectors[0]
        except Exception:
            return 0
        try:
            return max(int(len(first)), 0)
        except Exception:
            try:
                return max(int(len(LanceDbVectorIndex._coerce_vector(first))), 0)
            except Exception:
                return 0

    @staticmethod
    def _safe_write_batch_limit(desired_rows: int, *, resolved_device: str, profile: str, vector_dimension: int) -> int:
        device_key = 'cuda' if (resolved_device or '').strip().lower() == 'cuda' else 'cpu'
        hard_cap = int(_WRITE_BATCH_ROW_CAPS[device_key].get(str(profile or 'balanced').strip().lower() or 'balanced', _WRITE_BATCH_ROW_CAPS[device_key]['balanced']))
        safe_desired = max(int(desired_rows or 0), _MIN_SAFE_WRITE_BATCH_ROWS)
        if vector_dimension <= 0:
            return min(safe_desired, hard_cap)
        raw_bytes_per_row = max(int(vector_dimension), 1) * 4
        budget_rows = max(_MIN_SAFE_WRITE_BATCH_ROWS, int(_WRITE_BATCH_MEMORY_BUDGET_BYTES[device_key] / max(raw_bytes_per_row, 1)))
        return max(_MIN_SAFE_WRITE_BATCH_ROWS, min(safe_desired, hard_cap, budget_rows))

    def _ensure_table(self, dimension: int) -> None:
        if self._table_exists():
            if self._vector_dimension is None:
                schema = self._table().schema
                self._vector_dimension = schema.field("vector").type.list_size
            return

        with _runtime_import_environment(component_id='vector-store'):
            import pyarrow as pa

        self._vector_dimension = dimension
        schema = pa.schema(
            [
                pa.field("chunk_id", pa.string()),
                pa.field("source_path", pa.string()),
                pa.field("title", pa.string()),
                pa.field("anchor", pa.string()),
                pa.field("rendered_text", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dimension)),
            ]
        )
        self._db_connection().create_table(self._table_name, schema=schema, mode="overwrite")

    def _table_exists(self) -> bool:
        if self._db is not None:
            tables = self._db_connection().list_tables()
            if hasattr(tables, "tables"):
                return self._table_name in tables.tables
            return self._table_name in tables
        return self._table_storage_path().exists()

    def _table(self):
        return self._db_connection().open_table(self._table_name)

    def _table_storage_path(self) -> Path:
        return self._db_dir / f"{self._table_name}.lance"

    def _db_connection(self):
        if self._db is None:
            with _runtime_import_environment(component_id='vector-store'):
                import lancedb
            self._db = lancedb.connect(str(self._db_dir))
        return self._db

    def _embed_documents(self, documents: list[dict[str, str]], *, batch_size: int | None = None) -> list[dict[str, object]]:
        texts = [item["rendered_text"] for item in documents]
        vectors = self._encode(texts, batch_size=batch_size)
        return self._materialize_rows(documents, vectors)

    def _materialize_rows(self, documents: list[dict[str, str]], vectors) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for document, vector in zip(documents, vectors, strict=True):
            rows.append({**document, "vector": self._coerce_vector(vector)})
        return rows

    @staticmethod
    def _coerce_vector(vector) -> list[float]:
        if hasattr(vector, 'tolist'):
            values = vector.tolist()
        else:
            values = list(vector)
        return [float(value) for value in values]

    def _encode(self, texts: list[str], *, batch_size: int | None = None):
        embedder = self._load_embedder()
        requested_device = (self.config.vector_device or 'auto').strip().lower() or 'auto'
        resolved_device = resolve_vector_device(self.config.vector_device)
        report: dict[str, object] = {
            'requested_device': requested_device,
            'resolved_device': resolved_device,
            'model_device': str(getattr(embedder, 'device', '') or ''),
            'actual_device': str(getattr(embedder, 'device', '') or resolved_device),
            'cuda_peak_mem_before': 0,
            'cuda_peak_mem_after': 0,
            'cuda_peak_mem_delta': 0,
            'elapsed_ms': 0,
            'execution_error_class': '',
            'execution_error_message': '',
        }
        started_at = time.perf_counter()
        if resolved_device == 'cuda':
            try:
                with _runtime_import_environment(component_id='semantic-core'):
                    import torch
                    device_name = str(getattr(embedder, 'device', '') or 'cuda:0')
                    _torch_cuda_reset_peak_memory(torch, device_name)
                    report['cuda_peak_mem_before'] = _torch_cuda_peak_memory(torch, device_name)
                    vectors = embedder.encode(
                        texts,
                        batch_size=batch_size or self.config.vector_batch_size,
                        show_progress_bar=False,
                        normalize_embeddings=True,
                    )
                    _torch_cuda_synchronize(torch, device_name)
                    report['cuda_peak_mem_after'] = _torch_cuda_peak_memory(torch, device_name)
                    report['cuda_peak_mem_delta'] = max(int(report['cuda_peak_mem_after']) - int(report['cuda_peak_mem_before']), 0)
                    report['actual_device'] = str(getattr(vectors, 'device', '') or report['model_device'] or device_name)
            except Exception as exc:
                report['execution_error_class'] = exc.__class__.__name__
                report['execution_error_message'] = str(exc).strip() or exc.__class__.__name__
                self._last_execution_report = report
                raise
        else:
            vectors = embedder.encode(
                texts,
                batch_size=batch_size or self.config.vector_batch_size,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
        report['elapsed_ms'] = max(int((time.perf_counter() - started_at) * 1000), 0)
        self._last_execution_report = report
        return vectors

    def _load_embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = self._embedder_factory()
        return self._embedder

    def _default_embedder_factory(self) -> Embedder:
        model_root = self.paths.cache_dir / "models"
        local_model_dir = get_local_model_dir(self.config, self.paths)
        runtime_cache_dir = model_root / "_runtime"
        hf_home_dir = model_root / "_hf_home"
        model_root.mkdir(parents=True, exist_ok=True)
        runtime_cache_dir.mkdir(parents=True, exist_ok=True)
        hf_home_dir.mkdir(parents=True, exist_ok=True)
        _configure_huggingface_environment(hf_home_dir)

        prepare_local_model_snapshot(self.config, self.paths, allow_download=True)
        runtime_name = (self.config.vector_runtime or "torch").lower()
        resolved_device = resolve_vector_device(self.config.vector_device)
        cache_key = (str(local_model_dir), runtime_name, resolved_device)
        cached = _EMBEDDER_CACHE.get(cache_key)
        if cached is not None:
            return cached
        if is_canary_vector_model(self.config.vector_model):
            embedder = _TorchCanaryEmbedder(device=resolved_device)
            _EMBEDDER_CACHE[cache_key] = embedder
            return embedder

        try:
            # Why: the semantic runtime context must stay active for the whole
            # model-construction phase, not only for importing the Python module.
            # `SentenceTransformer(...)` loads local weights and extra
            # transformers/torch pieces; building it outside the runtime context
            # makes the packaged app report "semantic ready" while the actual
            # query path still falls back to lexical-only.
            with _runtime_import_environment(component_id='semantic-core'):
                from sentence_transformers import SentenceTransformer

                embedder = SentenceTransformer(
                    str(local_model_dir),
                    device=resolved_device,
                    cache_folder=str(runtime_cache_dir),
                    backend=self.config.vector_runtime,
                    local_files_only=True,
                )
        except ImportError as exc:
            raise RuntimeDependencyError(_runtime_dependency_message(self.config.vector_runtime, self.config.vector_device)) from exc
        _EMBEDDER_CACHE[cache_key] = embedder
        return embedder

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("'", "''")


# Why: 模型目录一旦完整，就必须彻底走本地，避免首轮建库因为 SSL / 代理波动反复访问远端。
def create_vector_index(
    config: AppConfig,
    paths: DataPaths,
    *,
    embedder_factory: Callable[[], Embedder] | None = None,
) -> VectorIndex:
    backend = (config.vector_backend or "disabled").strip().lower()
    if backend in {"", "disabled", "none", "off"}:
        return NullVectorIndex()
    if backend in {"lancedb", "lance", "lance-db"}:
        try:
            return LanceDbVectorIndex(config, paths, embedder_factory=embedder_factory)
        except (ImportError, ModuleNotFoundError, OSError, AttributeError, TypeError) as exc:
            return MissingRuntimeVectorIndex(config, paths, backend=backend, reason=exc)
    raise NotImplementedError(f"当前向量后端尚未接入：{config.vector_backend}")


def get_local_model_dir(config: AppConfig, paths: DataPaths) -> Path:
    return paths.cache_dir / "models" / _normalize_model_dir_name(config.vector_model)


def model_download_guidance_context(config: AppConfig, paths: DataPaths) -> dict[str, str]:
    model_name = str(config.vector_model or "BAAI/bge-m3").strip() or "BAAI/bge-m3"
    model_root = paths.cache_dir / "models"
    model_dir = get_local_model_dir(config, paths)
    hf_home_dir = model_root / "_hf_home"
    model_root.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)
    hf_home_dir.mkdir(parents=True, exist_ok=True)
    return {
        "model": model_name,
        "model_dir": str(model_dir),
        "hf_home_dir": str(hf_home_dir),
        "official_url": f"https://huggingface.co/{model_name}",
        "mirror_url": f"https://hf-mirror.com/{model_name}",
        "install_cli_command": 'PowerShell -ExecutionPolicy Bypass -NoProfile -Command "irm https://hf.co/cli/install.ps1 | iex"',
        "official_download_command": _build_model_download_command(model_name, model_dir, hf_home_dir, use_mirror=False),
        "mirror_download_command": _build_model_download_command(model_name, model_dir, hf_home_dir, use_mirror=True),
    }


def is_local_model_ready(config: AppConfig, paths: DataPaths) -> bool:
    if is_canary_vector_model(config.vector_model):
        return True
    return _is_model_dir_ready(get_local_model_dir(config, paths), config.vector_runtime)


def prepare_local_model_snapshot(
    config: AppConfig,
    paths: DataPaths,
    *,
    allow_download: bool = True,
) -> dict[str, object]:
    if is_canary_vector_model(config.vector_model):
        return {
            "backend": "builtin-canary",
            "model": config.vector_model,
            "local_model_dir": "",
            "model_ready": True,
            "requested_device": (config.vector_device or "auto").strip().lower() or "auto",
            "resolved_device": resolve_vector_device(config.vector_device),
        }
    model_root = paths.cache_dir / "models"
    local_model_dir = get_local_model_dir(config, paths)
    hf_home_dir = model_root / "_hf_home"
    model_root.mkdir(parents=True, exist_ok=True)
    local_model_dir.parent.mkdir(parents=True, exist_ok=True)
    hf_home_dir.mkdir(parents=True, exist_ok=True)
    _configure_huggingface_environment(hf_home_dir)

    if allow_download and not is_local_model_ready(config, paths):
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise RuntimeDependencyError("当前还缺少 huggingface-hub 运行时，暂时不能下载模型缓存。") from exc
        snapshot_download(
            repo_id=config.vector_model,
            local_dir=str(local_model_dir),
            local_files_only=config.vector_local_files_only,
        )

    model_ready = is_local_model_ready(config, paths)
    if not model_ready:
        raise RuntimeError(
            "本地模型目录存在，但内容不完整。请先重新运行下载模型，"
            "或清理 cache/models 后重新下载。"
        )

    return {
        "backend": "model-cache",
        "model": config.vector_model,
        "local_model_dir": str(local_model_dir),
        "model_ready": True,
        "requested_device": (config.vector_device or "auto").strip().lower() or "auto",
        "resolved_device": resolve_vector_device(config.vector_device),
    }


_RUNTIME_REQUIRED_MARKERS = {
    '_runtime_bootstrap.json': '_runtime_bootstrap.json',
    'torch': 'torch',
    'sentence_transformers': 'sentence-transformers',
    'transformers': 'transformers',
    'huggingface_hub': 'huggingface-hub',
    'safetensors': 'safetensors',
    'lancedb': 'lancedb',
    'onnxruntime': 'onnxruntime',
    'pyarrow': 'pyarrow',
    'numpy': 'numpy',
    'pandas': 'pandas',
    'scipy': 'scipy',
}
_CUDA_SETUP_GUIDE_URL = 'https://pytorch.org/get-started/locally/'
_RUNTIME_PACKAGE_SOURCE_URLS = {
    'official': 'https://pypi.org/simple',
    'mirror': 'https://pypi.tuna.tsinghua.edu.cn/simple',
}
_RUNTIME_COMPONENTS = {
    'semantic-core': {
        'markers': (
            '_runtime_bootstrap.json',
            'torch',
            'numpy',
            'scipy',
            'sentence_transformers',
            'transformers',
            'huggingface_hub',
            'safetensors',
        ),
        'cleanup_patterns': (
            'torch', 'torch-*dist-info',
            'functorch', 'functorch-*dist-info',
            'torchgen', 'torchgen-*dist-info',
            'numpy', 'numpy-*dist-info', 'numpy.libs',
            'scipy', 'scipy-*dist-info', 'scipy.libs',
            'sentence_transformers', 'sentence_transformers-*dist-info',
            'transformers', 'transformers-*dist-info',
            'huggingface_hub', 'huggingface_hub-*dist-info',
            'safetensors', 'safetensors-*dist-info',
        ),
        'disk_usage': {
            'cpu': '约 1.1 GB - 1.9 GB',
            'cuda': '约 3.2 GB - 4.3 GB',
        },
        'download_usage': {
            'cpu': '约 0.9 GB - 1.6 GB',
            'cuda': '约 2.5 GB - 4.4 GB',
        },
    },
    'compute-core': {
        'markers': (
            '_runtime_bootstrap.json',
            'torch',
            'numpy',
            'scipy',
        ),
        'cleanup_patterns': (
            'torch', 'torch-*dist-info',
            'functorch', 'functorch-*dist-info',
            'torchgen', 'torchgen-*dist-info',
            'numpy', 'numpy-*dist-info', 'numpy.libs',
            'scipy', 'scipy-*dist-info', 'scipy.libs',
        ),
        'disk_usage': {
            'cpu': '约 0.9 GB - 1.4 GB',
            'cuda': '约 3.0 GB - 3.8 GB',
        },
        'download_usage': {
            'cpu': '约 0.7 GB - 1.2 GB',
            'cuda': '约 2.3 GB - 4.0 GB',
        },
    },
    'model-stack': {
        'markers': (
            'sentence_transformers',
            'transformers',
            'huggingface_hub',
            'safetensors',
        ),
        'cleanup_patterns': (
            'sentence_transformers', 'sentence_transformers-*dist-info',
            'transformers', 'transformers-*dist-info',
            'huggingface_hub', 'huggingface_hub-*dist-info',
            'safetensors', 'safetensors-*dist-info',
        ),
        'disk_usage': {
            'cpu': '约 0.2 GB - 0.5 GB',
            'cuda': '约 0.2 GB - 0.5 GB',
        },
        'download_usage': {
            'cpu': '约 0.2 GB - 0.4 GB',
            'cuda': '约 0.2 GB - 0.4 GB',
        },
    },
    'vector-store': {
        'markers': (
            'lancedb',
            'onnxruntime',
            'pyarrow',
            'pandas',
        ),
        'cleanup_patterns': (
            'lancedb', 'lancedb-*dist-info',
            'onnxruntime', 'onnxruntime-*dist-info',
            'pyarrow', 'pyarrow-*dist-info', 'pyarrow.libs',
            'pandas', 'pandas-*dist-info',
        ),
        'disk_usage': {
            'cpu': '约 0.4 GB - 0.9 GB',
            'cuda': '约 0.5 GB - 1.0 GB',
        },
        'download_usage': {
            'cpu': '约 0.3 GB - 0.8 GB',
            'cuda': '约 0.4 GB - 0.9 GB',
        },
    },
}
_RUNTIME_VISIBLE_COMPONENT_IDS = (
    'semantic-core',
    'vector-store',
)
_RUNTIME_COMPONENT_MODULES = {
    'semantic-core': ('torch', 'numpy', 'scipy', 'sentence_transformers', 'transformers', 'huggingface_hub', 'safetensors'),
    'compute-core': ('torch', 'numpy', 'scipy'),
    'model-stack': ('sentence_transformers', 'transformers', 'huggingface_hub', 'safetensors'),
    'vector-store': ('lancedb', 'onnxruntime', 'pyarrow', 'pandas'),
}
_RUNTIME_IMPORT_RESET_PREFIXES = (
    'torch',
    'sentence_transformers',
    'transformers',
    'huggingface_hub',
    'safetensors',
    'numpy',
    'scipy',
    'lancedb',
    'onnxruntime',
    'pyarrow',
    'pandas',
    'sklearn',
    'asyncio',
)
_RUNTIME_REQUIRED_STRUCTURE = {
    'torch': ('__init__.py',),
    'numpy': ('__init__.py', '_core'),
    'scipy': ('__init__.py', 'linalg'),
    'sentence_transformers': ('__init__.py',),
    'transformers': ('__init__.py', 'utils'),
    'huggingface_hub': ('__init__.py', 'hf_api.py'),
    'safetensors': ('__init__.py',),
    'lancedb': ('__init__.py',),
    'onnxruntime': ('__init__.py',),
    'pyarrow': ('__init__.py',),
    'pandas': ('__init__.py',),
}

def runtime_component_catalog() -> tuple[dict[str, object], ...]:
    catalog = []
    for component_id in _RUNTIME_VISIBLE_COMPONENT_IDS:
        payload = _RUNTIME_COMPONENTS[component_id]
        catalog.append({
            'component_id': component_id,
            'markers': tuple(payload.get('markers', ())),
            'cleanup_patterns': tuple(payload.get('cleanup_patterns', ())),
            'disk_usage': dict(payload.get('disk_usage', {})),
            'download_usage': dict(payload.get('download_usage', {})),
        })
    return tuple(catalog)


def runtime_component_status(component_id: str) -> dict[str, object]:
    component = _RUNTIME_COMPONENTS.get(component_id)
    if component is None:
        raise KeyError(f'Unknown runtime component: {component_id}')
    layout = inspect_runtime_environment()
    runtime_dir = Path(layout['runtime_dir'])
    registry = load_runtime_component_registry(runtime_dir)
    normalized_component = normalize_runtime_component_id(component_id)
    component_profile = ''
    registry_payload = registry.get(normalized_component)
    if isinstance(registry_payload, dict):
        component_profile = str(registry_payload.get('profile') or '').strip().lower()
    total_count = len(tuple(component.get('markers', ())))
    missing_items = _runtime_component_missing_items(runtime_dir, component_id) if layout['runtime_exists'] else [_RUNTIME_REQUIRED_MARKERS.get(marker, marker) for marker in component.get('markers', ())]
    installed = max(total_count - len(missing_items), 0)
    live_ready = bool(layout['runtime_exists']) and not missing_items
    status = 'ready' if live_ready else ('missing' if installed <= 0 else 'incomplete')
    pending_aliases = _runtime_component_pending_aliases(component_id)
    pending_match = bool(set(layout.get('runtime_pending_components') or []).intersection(pending_aliases | {'all'}))
    if pending_match and not live_ready:
        status = 'pending'
    return {
        'component_id': component_id,
        'status': status,
        'ready': live_ready,
        'missing_items': missing_items,
        'installed_count': installed,
        'total_count': total_count,
        'cleanup_patterns': tuple(component.get('cleanup_patterns', ())),
        'profile': component_profile,
    }


def build_runtime_install_command(profile: str, *, source: str = 'official', component: str = 'all') -> str:
    normalized_profile = (profile or 'cpu').strip().lower() or 'cpu'
    normalized_source = (source or 'official').strip().lower() or 'official'
    normalized_component = (component or 'all').strip().lower() or 'all'
    if normalized_source not in _RUNTIME_PACKAGE_SOURCE_URLS:
        normalized_source = 'official'
    if normalized_component != 'all' and normalized_component not in _RUNTIME_COMPONENTS:
        normalized_component = 'all'
    app_dir_literal = _powershell_literal(_application_root_dir())
    script_literal = _powershell_literal(_install_runtime_script_relative())
    command = (
        "PowerShell -ExecutionPolicy Bypass -NoProfile -Command "
        f'\"Set-Location -LiteralPath {app_dir_literal}; & {script_literal} -Profile {normalized_profile} -Source {normalized_source} -WaitForProcessName OmniClipRAG'
    )
    if normalized_component != 'all':
        command += f" -Component {normalized_component}"
    command += '\"'
    return command


def runtime_install_sources() -> dict[str, str]:
    return dict(_RUNTIME_PACKAGE_SOURCE_URLS)


def runtime_component_usage(component_id: str, profile: str) -> dict[str, str]:
    component = _RUNTIME_COMPONENTS.get(component_id)
    if component is None:
        raise KeyError(f'Unknown runtime component: {component_id}')
    normalized_profile = (profile or 'cpu').strip().lower() or 'cpu'
    return {
        'disk_usage': str(component.get('disk_usage', {}).get(normalized_profile) or component.get('disk_usage', {}).get('cpu') or ''),
        'download_usage': str(component.get('download_usage', {}).get(normalized_profile) or component.get('download_usage', {}).get('cpu') or ''),
    }


def _runtime_capability_state_path(runtime_dir: Path) -> Path:
    return Path(runtime_dir) / _RUNTIME_CAPABILITY_STATE_FILENAME


def _load_runtime_capability_state(runtime_dir: Path) -> dict[str, object]:
    state_path = _runtime_capability_state_path(runtime_dir)
    default_payload = {
        'version': _RUNTIME_CAPABILITY_STATE_VERSION,
        'gpu_probe': {},
        'gpu_query_probe': {},
    }
    if not state_path.exists():
        return dict(default_payload)
    try:
        payload = json.loads(state_path.read_text(encoding='utf-8'))
    except Exception:
        return dict(default_payload)
    if not isinstance(payload, dict):
        return dict(default_payload)
    payload.setdefault('version', _RUNTIME_CAPABILITY_STATE_VERSION)
    legacy_probe = payload.pop('gpu_execution_probe', None)
    if not isinstance(payload.get('gpu_probe'), dict):
        payload['gpu_probe'] = dict(legacy_probe or {}) if isinstance(legacy_probe, dict) else {}
    elif isinstance(legacy_probe, dict) and not payload['gpu_probe']:
        payload['gpu_probe'] = dict(legacy_probe)
    if not isinstance(payload.get('gpu_query_probe'), dict):
        payload['gpu_query_probe'] = {}
    return payload


def _write_runtime_capability_state(runtime_dir: Path, payload: dict[str, object]) -> None:
    runtime_dir = Path(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    state_payload = _load_runtime_capability_state(runtime_dir)
    state_payload.update(payload)
    state_payload['version'] = _RUNTIME_CAPABILITY_STATE_VERSION
    _runtime_capability_state_path(runtime_dir).write_text(
        json.dumps(state_payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding='utf-8',
    )


def _utc_now_iso() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _trace_digest(payload: object, prefix: str) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return f"{prefix}:{hashlib.sha1(serialized.encode('utf-8')).hexdigest()[:12]}"


def _safe_realpath(value: str | Path | None) -> str:
    if value is None:
        return ''
    try:
        return str(Path(value).resolve(strict=False))
    except Exception:
        return str(value)


def runtime_trace_metadata() -> dict[str, object]:
    runtime_state = inspect_runtime_environment()
    runtime_dir = Path(runtime_state.get('runtime_dir') or '')
    registry_path = runtime_component_registry_path(runtime_dir)
    registry_payload = load_runtime_component_registry(runtime_dir) if runtime_dir.exists() else {}
    pending_updates = list_pending_runtime_updates(runtime_dir) if runtime_dir.exists() else []
    manifest_payload: object = {}
    if registry_path.exists():
        try:
            manifest_payload = json.loads(registry_path.read_text(encoding='utf-8'))
        except Exception:
            manifest_payload = {'registry_path': str(registry_path), 'invalid': True}
    elif registry_payload:
        manifest_payload = registry_payload
    else:
        manifest_payload = {'layout': 'legacy-or-empty'}
    runtime_manifest_version = _trace_digest(manifest_payload, 'runtime-manifest')
    live_runtime_id = _trace_digest(
        {
            'runtime_dir': _safe_realpath(runtime_dir),
            'registry': registry_payload,
        },
        'runtime-live',
    )
    pending_runtime_id = _trace_digest(pending_updates, 'runtime-pending') if pending_updates else ''
    runtime_instance_id = _trace_digest(
        {
            'runtime_manifest_version': runtime_manifest_version,
            'live_runtime_id': live_runtime_id,
        },
        'runtime-instance',
    )
    return {
        'runtime_root': _safe_realpath(runtime_dir),
        'runtime_preferred_root': _safe_realpath(runtime_state.get('preferred_runtime_dir') or ''),
        'runtime_manifest_version': runtime_manifest_version,
        'live_runtime_id': live_runtime_id,
        'pending_runtime_id': pending_runtime_id,
        'runtime_instance_id': runtime_instance_id,
    }

_RUNTIME_REQUIRED_IMPORTS = (
    ('torch', 'torch'),
    ('sentence_transformers', 'sentence-transformers'),
    ('transformers', 'transformers'),
    ('huggingface_hub', 'huggingface-hub'),
    ('safetensors', 'safetensors'),
    ('lancedb', 'lancedb'),
    ('pyarrow', 'pyarrow'),
    ('numpy', 'numpy'),
    ('pandas', 'pandas'),
    ('scipy', 'scipy'),
)
_RUNTIME_REQUIRED_IMPORTS_BY_RUNTIME = {
    'onnx': (('onnxruntime', 'onnxruntime'),),
}


def _application_root_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _preferred_runtime_dir_path() -> Path:
    override = str(os.environ.get('OMNICLIP_RUNTIME_ROOT') or '').strip()
    if override:
        return Path(override).expanduser().resolve()
    resolved = resolve_active_data_root()
    return build_data_paths(resolved.path).shared_root / 'runtime'


def _runtime_candidate_sort_key(path: Path) -> tuple[int, tuple[int, ...], float]:
    name = path.name.strip()
    version_label = path.parent.name.strip() if name.lower() == 'runtime' else name
    version_match = re.search(r'v(\d+(?:\.\d+)*)', version_label, re.IGNORECASE)
    modified_at = path.stat().st_mtime if path.exists() else 0.0
    if version_match:
        parts = tuple(int(part) for part in version_match.group(1).split('.') if part.isdigit())
        return (3, parts, modified_at)
    return (1 if name.lower() == 'runtime' else 0, (), modified_at)


def _legacy_runtime_candidate_dirs() -> list[Path]:
    app_dir = _application_root_dir()
    candidates: list[Path] = []
    current_local = app_dir / 'runtime'
    if current_local.exists():
        candidates.append(current_local)
    parent_runtime = app_dir.parent / 'runtime'
    if parent_runtime.exists():
        candidates.append(parent_runtime)
    try:
        sibling_apps = [item for item in app_dir.parent.iterdir() if item.is_dir()]
    except OSError:
        sibling_apps = []
    sibling_runtime_dirs: list[Path] = []
    for sibling_app in sibling_apps:
        if sibling_app == app_dir:
            continue
        sibling_name = sibling_app.name.strip()
        if not re.match(r'^OmniClipRAG-v\d+(?:\.\d+)*$', sibling_name, re.IGNORECASE):
            continue
        sibling_runtime = sibling_app / 'runtime'
        if sibling_runtime.exists():
            sibling_runtime_dirs.append(sibling_runtime)
    sibling_runtime_dirs.sort(key=_runtime_candidate_sort_key, reverse=True)
    candidates.extend(sibling_runtime_dirs)
    seen: set[Path] = set()
    ordered: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(candidate)
    return ordered


def _install_runtime_script_path() -> Path:
    app_dir = _application_root_dir()
    if getattr(sys, 'frozen', False):
        return app_dir / 'InstallRuntime.ps1'
    return app_dir / 'scripts' / 'install_runtime.ps1'


def _install_runtime_script_relative() -> str:
    if getattr(sys, 'frozen', False):
        return '.\\InstallRuntime.ps1'
    return '.\\scripts\\install_runtime.ps1'


def _powershell_literal(value: str) -> str:
    return str(value).replace("'", "''")


def _runtime_component_dependency_ids(component_id: str) -> tuple[str, ...]:
    normalized = normalize_runtime_component_id(component_id)
    if normalized in {'semantic-core', 'vector-store'}:
        # Why: query-time vector execution depends on semantic-core's numpy/scipy
        # stack even when lancedb/pyarrow still live at the flat vector-store root.
        # Keeping semantic-core first guarantees the packaged app sees one canonical
        # numerical stack instead of mixing legacy flat runtime leftovers.
        return ('semantic-core', 'vector-store')
    return (normalized,)


def _runtime_component_pending_aliases(component_id: str) -> set[str]:
    aliases: set[str] = set()
    for candidate_id in _runtime_component_dependency_ids(component_id):
        aliases.add(candidate_id)
        if candidate_id == 'semantic-core':
            aliases.update({'compute-core', 'model-stack'})
    return aliases


def _runtime_search_roots(runtime_dir: Path, *, include_pending: bool = False, component_id: str | None = None) -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()
    normalized_component = normalize_runtime_component_id(component_id) if component_id else ''

    def register(candidate: Path | None, *, prepend: bool = False) -> None:
        if candidate is None or not candidate.exists() or not candidate.is_dir():
            return
        resolved = candidate.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        if prepend:
            roots.insert(0, candidate)
        else:
            roots.append(candidate)

    component_ids = _runtime_component_dependency_ids(normalized_component) if normalized_component else ('semantic-core', 'vector-store', 'all')
    live_roots: list[Path] = []
    for candidate_id in component_ids:
        component_roots = list(runtime_component_live_roots(runtime_dir, candidate_id))
        if component_roots:
            live_roots.extend(component_roots)
        elif runtime_dir.exists() and runtime_dir.is_dir():
            # Why: a partially componentized runtime may still keep some legacy
            # payloads at the flat runtime root. Semantic imports must be able to
            # see their transitive storage dependencies until every component has
            # been migrated to isolated live roots.
            live_roots.append(runtime_dir)
    if not live_roots and runtime_dir.exists() and runtime_dir.is_dir():
        live_roots.append(runtime_dir)
    for root in live_roots:
        register(root)

    if include_pending:
        aliases = _runtime_component_pending_aliases(normalized_component) if normalized_component else None
        for payload in list_pending_runtime_updates(runtime_dir):
            payload_component = normalize_runtime_component_id(str(payload.get('component') or ''))
            if aliases is not None and payload_component not in aliases and payload_component != 'all':
                continue
            payload_dir = Path(str(payload.get('payload_dir') or '')).resolve()
            register(payload_dir, prepend=True)
    return roots


def _runtime_bootstrap_metadata(runtime_root: Path) -> dict[str, str]:
    marker = runtime_root / '_runtime_bootstrap.json'
    if not marker.exists():
        return {}
    try:
        payload = json.loads(marker.read_text(encoding='utf-8'))
    except Exception:
        return {}
    normalized: dict[str, str] = {}
    for key in ('python_exe', 'stdlib', 'platstdlib', 'dll_dir'):
        value = str(payload.get(key) or '').strip()
        if value:
            normalized[key] = value
    return normalized


def _runtime_bootstrap_dll_dir(runtime_root: Path) -> Path | None:
    payload = _runtime_bootstrap_metadata(runtime_root)
    dll_value = payload.get('dll_dir', '')
    if not dll_value:
        return None
    candidate = Path(dll_value)
    if not candidate.exists():
        return None
    try:
        candidate.resolve().relative_to(Path(runtime_root).resolve())
    except Exception:
        # Why: runtime payloads installed by an external Python must not leak
        # that interpreter's DLL directory back into the packaged app.
        return None
    return candidate


def _runtime_bootstrap_sys_paths(runtime_root: Path) -> list[Path]:
    # Why: the frozen app must keep using its own interpreter stdlib.
    # Re-injecting stdlib/platstdlib from the Python used to install runtime
    # corrupts imports (for example asyncio/_multiprocessing/base_events) and
    # makes healthy runtime payloads look broken.
    return []


def _runtime_component_validation_manifest(runtime_root: Path) -> dict[str, object]:
    marker = runtime_root / '_runtime_validation.json'
    if not marker.exists():
        return {}
    try:
        payload = json.loads(marker.read_text(encoding='utf-8'))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _runtime_probe_python_command(runtime_root: Path) -> list[str]:
    payload = _runtime_bootstrap_metadata(runtime_root)
    python_exe = str(payload.get('python_exe') or '').strip()
    if python_exe and Path(python_exe).exists():
        return [python_exe]
    for candidate in (['py', '-3.13'], ['python']):
        try:
            result = run_hidden(candidate + ['--version'], capture_output=True, text=True, timeout=5)
        except Exception:
            continue
        if result.returncode == 0:
            return candidate
    return []


def _probe_runtime_semantic_core(runtime_dir: Path) -> dict[str, object] | None:
    roots = _runtime_search_roots(runtime_dir, include_pending=False, component_id='semantic-core')
    if not roots:
        return None
    runtime_root = Path(roots[0]).resolve()
    command = _runtime_probe_python_command(runtime_root)
    if not command:
        return None
    script = (
        "import importlib, json, os, sys\n"
        "from pathlib import Path\n"
        "result = {'torch_available': False, 'torch_version': '', 'torch_cuda_build': '', 'torch_error': '', 'sentence_transformers_available': False, 'sentence_transformers_error': '', 'cuda_available': False, 'cuda_device_count': 0, 'cuda_name': ''}\n"
        "existing = []\n"
        "roots = [Path(raw_root).resolve() for raw_root in sys.argv[1:]]\n"
        "for root in reversed(roots):\n"
        "    paths = [root, root / 'bin', root / 'pyarrow.libs', root / 'numpy.libs', root / 'scipy.libs', root / 'torch' / 'lib']\n"
        "    for candidate in paths:\n"
        "        if candidate.exists():\n"
        "            sys.path.insert(0, str(candidate))\n"
        "            existing.append(str(candidate))\n"
        "            if hasattr(os, 'add_dll_directory'):\n"
        "                try: os.add_dll_directory(str(candidate))\n"
        "                except OSError: pass\n"
        "if existing:\n"
        "    os.environ['PATH'] = os.pathsep.join(existing + [os.environ.get('PATH', '')])\n"
        "try:\n"
        "    torch = importlib.import_module('torch')\n"
        "    result['torch_available'] = True\n"
        "    result['torch_version'] = str(getattr(torch, '__version__', '') or '')\n"
        "    result['torch_cuda_build'] = str(getattr(getattr(torch, 'version', None), 'cuda', '') or '')\n"
        "    try:\n"
        "        result['cuda_available'] = bool(torch.cuda.is_available())\n"
        "    except Exception:\n"
        "        result['cuda_available'] = False\n"
        "    if result['cuda_available']:\n"
        "        try: result['cuda_device_count'] = int(torch.cuda.device_count())\n"
        "        except Exception: result['cuda_device_count'] = 0\n"
        "        if result['cuda_device_count'] > 0:\n"
        "            try: result['cuda_name'] = str(torch.cuda.get_device_name(0))\n"
        "            except Exception: result['cuda_name'] = ''\n"
        "except Exception as exc:\n"
        "    result['torch_error'] = f'{type(exc).__name__}: {exc}'\n"
        "if result['torch_available']:\n"
        "    try:\n"
        "        importlib.import_module('sentence_transformers')\n"
        "        result['sentence_transformers_available'] = True\n"
        "    except Exception as exc:\n"
        "        result['sentence_transformers_error'] = f'{type(exc).__name__}: {exc}'\n"
        "print(json.dumps(result, ensure_ascii=True))\n"
    )
    try:
        completed = run_hidden(command + ['-I', '-S', '-c', script, *[str(Path(root).resolve()) for root in roots]], capture_output=True, text=True, timeout=180)
    except Exception as exc:
        return {'probe_error': f'{type(exc).__name__}: {exc}'}
    stdout = str(completed.stdout or '').strip()
    stderr = str(completed.stderr or '').strip()
    if completed.returncode != 0:
        return {'probe_error': stderr or stdout or f'probe exited with {completed.returncode}'}
    try:
        payload = json.loads(stdout or '{}')
    except Exception as exc:
        return {'probe_error': f'JSONDecodeError: {exc}: {stdout[:240]}'}
    if not isinstance(payload, dict):
        return {'probe_error': 'probe returned non-dict payload'}
    return payload


def _probe_runtime_semantic_core_inprocess(runtime_dir: Path) -> dict[str, object] | None:
    roots = _runtime_search_roots(runtime_dir, include_pending=False, component_id='semantic-core')
    if not roots:
        return None
    payload: dict[str, object] = {
        'torch_available': False,
        'torch_version': '',
        'torch_cuda_build': '',
        'torch_error': '',
        'sentence_transformers_available': False,
        'sentence_transformers_error': '',
        'cuda_available': False,
        'cuda_device_count': 0,
        'cuda_name': '',
    }
    # Why: the in-process probe runs inside the same frozen process as the real
    # query path. Purging runtime modules here forces numpy/torch/scipy style
    # C-extensions to reload later in the same process, which can explode with
    # "cannot load module more than once per process" and makes a healthy CPU
    # semantic stack look broken only at query time. The probe must therefore be
    # observational, not destructive.
    with _runtime_import_environment(component_id='semantic-core'):
        torch = None
        try:
            import torch
            if not _module_resolves_inside_roots(torch, roots):
                raise ImportError('torch resolved outside semantic runtime roots')
        except Exception as exc:
            payload['torch_error'] = f'{type(exc).__name__}: {exc}'
        else:
            payload['torch_available'] = True
            payload['torch_version'] = str(getattr(torch, '__version__', '') or '')
            payload['torch_cuda_build'] = str(getattr(getattr(torch, 'version', None), 'cuda', '') or '')
            try:
                payload['cuda_available'] = bool(torch.cuda.is_available())
            except Exception:
                payload['cuda_available'] = False
            if payload['cuda_available']:
                try:
                    payload['cuda_device_count'] = int(torch.cuda.device_count())
                except Exception:
                    payload['cuda_device_count'] = 0
                if payload['cuda_device_count'] > 0:
                    try:
                        payload['cuda_name'] = str(torch.cuda.get_device_name(0))
                    except Exception:
                        payload['cuda_name'] = ''

        if payload['torch_available']:
            try:
                import sentence_transformers
                if not _module_resolves_inside_roots(sentence_transformers, roots):
                    raise ImportError('sentence-transformers resolved outside semantic runtime roots')
            except Exception as exc:
                payload['sentence_transformers_error'] = f'{type(exc).__name__}: {exc}'
            else:
                payload['sentence_transformers_available'] = True
    return payload


def _purge_runtime_import_state() -> None:
    importlib.invalidate_caches()
    for module_name in list(sys.modules):
        if any(module_name == prefix or module_name.startswith(prefix + '.') for prefix in _RUNTIME_IMPORT_RESET_PREFIXES):
            sys.modules.pop(module_name, None)


def _collect_module_origins(module: object) -> list[Path]:
    origins: list[Path] = []
    module_file = getattr(module, '__file__', None)
    if module_file:
        origins.append(Path(str(module_file)).resolve())
    module_path = getattr(module, '__path__', None)
    if module_path:
        try:
            iterator = iter(module_path)
        except TypeError:
            iterator = ()
        for entry in iterator:
            try:
                origins.append(Path(str(entry)).resolve())
            except Exception:
                continue
    unique: list[Path] = []
    seen: set[Path] = set()
    for item in origins:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def _module_resolves_inside_roots(module: object, roots: Iterable[Path]) -> bool:
    resolved_roots = [Path(root).resolve() for root in roots]
    origins = _collect_module_origins(module)
    if not origins or not resolved_roots:
        return False
    for origin in origins:
        if not any(_path_under_runtime(origin, root) for root in resolved_roots):
            return False
    return True


def _module_resolves_inside_runtime(module: object, runtime_dir: Path) -> bool:
    return _module_resolves_inside_roots(module, [Path(runtime_dir).resolve()])


def _purge_runtime_import_state_for_roots(active_roots: Iterable[Path]) -> None:
    resolved_roots = [Path(root).resolve() for root in active_roots if Path(root).exists()]
    if not resolved_roots:
        return
    importlib.invalidate_caches()
    for module_name, module in list(sys.modules.items()):
        if not any(module_name == prefix or module_name.startswith(prefix + '.') for prefix in _RUNTIME_IMPORT_RESET_PREFIXES):
            continue
        if module is None or not _module_resolves_inside_roots(module, resolved_roots):
            sys.modules.pop(module_name, None)


def _ensure_runtime_stdlib_support_loaded() -> None:
    global _RUNTIME_STDLIB_SUPPORT_LOGGED
    loaded_now: list[str] = []
    with _RUNTIME_STDLIB_SUPPORT_LOCK:
        for module_name in _RUNTIME_STDLIB_SUPPORT_MODULES:
            if module_name in _RUNTIME_STDLIB_SUPPORT_READY:
                continue
            try:
                importlib.import_module(module_name)
            except Exception as exc:
                LOGGER.error('Runtime stdlib support preload failed for %s: %s: %s', module_name, type(exc).__name__, exc)
                raise
            _RUNTIME_STDLIB_SUPPORT_READY.add(module_name)
            loaded_now.append(module_name)
        if loaded_now and not _RUNTIME_STDLIB_SUPPORT_LOGGED:
            LOGGER.info('Runtime stdlib support preloaded: %s', ', '.join(loaded_now))
            _RUNTIME_STDLIB_SUPPORT_LOGGED = True


def _path_under_runtime(candidate: Path, runtime_root: Path) -> bool:
    try:
        candidate.resolve().relative_to(runtime_root)
        return True
    except Exception:
        return False


@contextmanager
def _runtime_import_environment(*, component_id: str | None = None) -> Iterable[None]:
    runtime_dir = _runtime_dir_path()
    if not runtime_dir.exists() or not runtime_dir.is_dir():
        yield
        return

    # Why: pending payloads are only a staged update. The current session should
    # keep using the live runtime tree until startup applies the staged update on
    # the next launch.
    search_roots = _runtime_search_roots(runtime_dir, include_pending=False, component_id=component_id)
    # Why: the packaged app must keep using one stable semantic runtime inside a
    # single process. Re-purging C-extension modules like numpy/torch on every
    # query causes "cannot load module more than once per process" failures.
    inserted_sys_paths: list[str] = []
    dll_paths: list[str] = []
    dll_handles: list[object] = []
    original_path = os.environ.get('PATH', '')
    original_sys_path = list(sys.path)

    runtime_root_resolved = runtime_dir.resolve()
    sys.path[:] = [
        item for item in sys.path
        if not (
            str(item).strip()
            and Path(str(item)).exists()
            and _path_under_runtime(Path(str(item)), runtime_root_resolved)
        )
    ]

    def register_sys_path(candidate: Path | None) -> None:
        if candidate is None or not candidate.exists():
            return
        candidate_str = str(candidate)
        if candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)
            inserted_sys_paths.append(candidate_str)

    def register_dll_path(candidate: Path | None) -> None:
        if candidate is None or not candidate.exists():
            return
        candidate_str = str(candidate)
        if candidate_str not in dll_paths:
            dll_paths.append(candidate_str)

    for root in reversed(search_roots):
        register_sys_path(root)
        for bootstrap_sys_path in reversed(_runtime_bootstrap_sys_paths(root)):
            register_sys_path(bootstrap_sys_path)

    for root in search_roots:
        bootstrap_dll_dir = _runtime_bootstrap_dll_dir(root)
        for candidate in (
            root / 'bin',
            root / 'pyarrow.libs',
            root / 'numpy.libs',
            root / 'scipy.libs',
            root / 'torch' / 'lib',
        ):
            register_dll_path(candidate)
        register_dll_path(bootstrap_dll_dir)

    if dll_paths:
        os.environ['PATH'] = os.pathsep.join(dll_paths + ([original_path] if original_path else []))
        if hasattr(os, 'add_dll_directory'):
            for item in dll_paths:
                try:
                    dll_handles.append(os.add_dll_directory(item))
                except OSError:
                    continue
    _ensure_runtime_stdlib_support_loaded()

    try:
        yield
    finally:
        os.environ['PATH'] = original_path
        for handle in dll_handles:
            try:
                handle.close()
            except Exception:
                pass
        sys.path[:] = original_sys_path


def _runtime_marker_structure_ready(root: Path, marker: str) -> bool:
    if marker == '_runtime_bootstrap.json':
        return (root / marker).exists()
    candidate = root / marker
    if not candidate.exists() or not candidate.is_dir():
        return False
    for required_entry in _RUNTIME_REQUIRED_STRUCTURE.get(marker, ()):
        if not (candidate / required_entry).exists():
            return False
    return True


def _runtime_marker_exists(runtime_dir: Path, marker: str, *, include_pending: bool = False, component_id: str | None = None) -> bool:
    for root in _runtime_search_roots(runtime_dir, include_pending=include_pending, component_id=component_id):
        if _runtime_marker_structure_ready(root, marker):
            return True
    return False


def _inspect_runtime_layout_state_for(runtime_dir: Path) -> dict[str, object]:
    runtime_dir = Path(runtime_dir)
    try:
        runtime_dir_exists = runtime_dir.exists() and runtime_dir.is_dir()
    except (OSError, PermissionError):
        runtime_dir_exists = False
    runtime_dir_has_content = False
    if runtime_dir_exists:
        try:
            runtime_dir_has_content = any(runtime_dir.iterdir())
        except OSError:
            runtime_dir_has_content = False
    pending_updates = list_pending_runtime_updates(runtime_dir) if runtime_dir_exists else []
    pending_components = []
    for payload in pending_updates:
        component_name = normalize_runtime_component_id(str(payload.get('component') or '').strip())
        if component_name and component_name not in pending_components:
            pending_components.append(component_name)
    return {
        'runtime_dir': runtime_dir,
        'runtime_exists': runtime_dir_exists,
        'runtime_has_content': runtime_dir_has_content,
        'runtime_pending': bool(pending_updates),
        'runtime_pending_components': pending_components,
    }


def _runtime_component_missing_items(runtime_dir: Path, component_id: str) -> list[str]:
    component = _RUNTIME_COMPONENTS.get(component_id)
    if component is None:
        raise KeyError(f'Unknown runtime component: {component_id}')
    normalized_component = normalize_runtime_component_id(component_id)
    return [
        _RUNTIME_REQUIRED_MARKERS.get(marker, marker)
        for marker in component.get('markers', ())
        if not _runtime_marker_exists(runtime_dir, marker, include_pending=False, component_id=normalized_component)
    ]


def inspect_runtime_environment(runtime_dir: Path | None = None) -> dict[str, object]:
    runtime_dir = Path(runtime_dir or _runtime_dir_path())
    layout = _inspect_runtime_layout_state_for(runtime_dir)
    semantic_missing = _runtime_component_missing_items(runtime_dir, 'semantic-core') if layout['runtime_exists'] else list(_RUNTIME_COMPONENT_MODULES['semantic-core'])
    vector_missing = _runtime_component_missing_items(runtime_dir, 'vector-store') if layout['runtime_exists'] else list(_RUNTIME_COMPONENT_MODULES['vector-store'])
    missing_items = list(dict.fromkeys([*semantic_missing, *vector_missing]))
    return {
        **layout,
        'runtime_complete': bool(layout['runtime_exists']) and bool(layout['runtime_has_content']) and not missing_items,
        'runtime_missing_items': missing_items,
        'active_runtime_dir': runtime_dir,
        'preferred_runtime_dir': _preferred_runtime_dir_path(),
    }


def _discover_active_runtime_dir() -> Path:
    return _preferred_runtime_dir_path()


def _runtime_dir_path() -> Path:
    return _discover_active_runtime_dir()


def detect_acceleration(*, force_refresh: bool = False, safe_mode: bool = False) -> dict[str, object]:
    global _ACCELERATION_CACHE
    if not safe_mode and not force_refresh and _ACCELERATION_CACHE is not None:
        return dict(_ACCELERATION_CACHE)

    with _ACCELERATION_LOCK:
        if not safe_mode and not force_refresh and _ACCELERATION_CACHE is not None:
            return dict(_ACCELERATION_CACHE)

        runtime_dir = _runtime_dir_path()
        runtime_state = inspect_runtime_environment()
        runtime_meta = runtime_trace_metadata()
        semantic_missing = _runtime_component_missing_items(runtime_dir, 'semantic-core') if runtime_state['runtime_exists'] else list(_RUNTIME_COMPONENT_MODULES['semantic-core'])
        payload: dict[str, object] = {
            "torch_available": False,
            "torch_version": "",
            "torch_cuda_build": "",
            "torch_error": "",
            "sentence_transformers_available": False,
            "sentence_transformers_error": "",
            "cuda_available": False,
            "cuda_device_count": 0,
            "cuda_name": "",
            "gpu_present": False,
            "gpu_name": "",
            "nvcc_available": False,
            "nvcc_version": "",
            "device_options": ["auto", "cpu"],
            "recommended_device": "cpu",
            "runtime_status": "missing",
            'runtime_exists': runtime_state['runtime_exists'],
            'runtime_complete': runtime_state['runtime_complete'],
            'runtime_missing_items': list(runtime_state['runtime_missing_items']),
            'safe_mode': bool(safe_mode),
            **runtime_meta,
            'gpu_probe_state': 'not-run',
            'gpu_probe_verified': False,
            'gpu_probe_reason': '',
            'gpu_probe_error_class': '',
            'gpu_probe_error_message': '',
            'gpu_probe_actual_device': '',
            'gpu_probe_elapsed_ms': 0,
            'gpu_probe_verified_at': '',
            'gpu_probe_runtime_instance_id': '',
            'gpu_execution_state': 'not-run',
            'gpu_execution_verified': False,
            'gpu_execution_reason': '',
            'gpu_execution_error_class': '',
            'gpu_execution_error_message': '',
            'gpu_execution_actual_device': '',
            'gpu_execution_reranker_actual_device': '',
            'gpu_execution_elapsed_ms': 0,
            'gpu_execution_verified_at': '',
            'gpu_execution_runtime_instance_id': '',
        }

        gpu_names = _detect_nvidia_gpus()
        if gpu_names:
            payload["gpu_present"] = True
            payload["gpu_name"] = gpu_names[0]

        nvcc_version = _detect_nvcc_version()
        if nvcc_version:
            payload["nvcc_available"] = True
            payload["nvcc_version"] = nvcc_version

        if safe_mode:
            if payload['gpu_present']:
                payload['device_options'] = ["auto", "cpu", "cuda"]
            payload['torch_error'] = 'safe startup deferred torch probe'
            _merge_cached_gpu_probe_state(payload, runtime_dir, runtime_meta)
            _merge_cached_gpu_execution_state(payload, runtime_dir, runtime_meta)
            return dict(payload)

        if semantic_missing:
            detail = ', '.join(semantic_missing[:6])
            payload['torch_error'] = f'semantic runtime files are incomplete: {detail}'
            payload['sentence_transformers_error'] = f'semantic runtime files are incomplete: {detail}'
            _merge_cached_gpu_probe_state(payload, runtime_dir, runtime_meta)
            _merge_cached_gpu_execution_state(payload, runtime_dir, runtime_meta)
            _ACCELERATION_CACHE = dict(payload)
            return dict(payload)

        probe_payload = _probe_runtime_semantic_core_inprocess(runtime_dir) or {}
        if not probe_payload.get('torch_available') and not probe_payload.get('sentence_transformers_available'):
            external_probe = _probe_runtime_semantic_core(runtime_dir)
            if external_probe is not None and not external_probe.get('probe_error'):
                probe_payload = external_probe
            elif external_probe is not None and external_probe.get('probe_error') and not probe_payload.get('torch_error'):
                probe_payload['torch_error'] = str(external_probe.get('probe_error') or '')
                probe_payload['sentence_transformers_error'] = str(external_probe.get('probe_error') or '')

        payload['torch_available'] = bool(probe_payload.get('torch_available'))
        payload['torch_version'] = str(probe_payload.get('torch_version') or '')
        payload['torch_cuda_build'] = str(probe_payload.get('torch_cuda_build') or '')
        payload['torch_error'] = str(probe_payload.get('torch_error') or '')
        payload['sentence_transformers_available'] = bool(probe_payload.get('sentence_transformers_available'))
        payload['sentence_transformers_error'] = str(probe_payload.get('sentence_transformers_error') or '')
        payload['cuda_available'] = bool(probe_payload.get('cuda_available'))
        payload['cuda_device_count'] = int(probe_payload.get('cuda_device_count') or 0)
        payload['cuda_name'] = str(probe_payload.get('cuda_name') or '')
        if payload['torch_available']:
            payload['runtime_status'] = 'cuda' if payload['cuda_available'] else 'cpu'
        if payload['cuda_available']:
            payload['device_options'] = ['auto', 'cpu', 'cuda']
            payload['recommended_device'] = 'cuda'
        elif gpu_names:
            payload['recommended_device'] = 'cpu'

        _merge_cached_gpu_probe_state(payload, runtime_dir, runtime_meta)
        _merge_cached_gpu_execution_state(payload, runtime_dir, runtime_meta)
        _ACCELERATION_CACHE = dict(payload)
        return dict(payload)


def _merge_cached_gpu_probe_state(payload: dict[str, object], runtime_dir: Path, runtime_meta: dict[str, object]) -> None:
    state = _load_runtime_capability_state(runtime_dir)
    probe = dict(state.get('gpu_probe') or {})
    current_instance_id = str(runtime_meta.get('runtime_instance_id') or '')
    probe_instance_id = str(probe.get('runtime_instance_id') or '')
    if not probe:
        payload['gpu_probe_state'] = 'not-run' if payload.get('gpu_present') else 'not-needed'
        payload['gpu_probe_reason'] = '' if payload.get('gpu_present') else 'no_gpu_present'
        return
    if probe_instance_id and current_instance_id and probe_instance_id != current_instance_id:
        payload['gpu_probe_state'] = 'stale'
        payload['gpu_probe_reason'] = 'runtime_instance_changed'
        payload['gpu_probe_runtime_instance_id'] = probe_instance_id
        payload['gpu_probe_verified_at'] = str(probe.get('completed_at') or '')
        return
    success = bool(probe.get('success'))
    state_value = str(probe.get('state') or '').strip().lower() or ('verified' if success else 'failed')
    payload['gpu_probe_state'] = state_value
    payload['gpu_probe_verified'] = success and state_value == 'verified'
    payload['gpu_probe_reason'] = str(probe.get('reason') or '')
    payload['gpu_probe_error_class'] = str(probe.get('execution_error_class') or '')
    payload['gpu_probe_error_message'] = str(probe.get('execution_error_message') or '')
    payload['gpu_probe_actual_device'] = str(probe.get('actual_device') or '')
    payload['gpu_probe_elapsed_ms'] = int(probe.get('elapsed_ms') or 0)
    payload['gpu_probe_verified_at'] = str(probe.get('completed_at') or '')
    payload['gpu_probe_runtime_instance_id'] = probe_instance_id


def _merge_cached_gpu_execution_state(payload: dict[str, object], runtime_dir: Path, runtime_meta: dict[str, object]) -> None:
    state = _load_runtime_capability_state(runtime_dir)
    probe = dict(state.get('gpu_query_probe') or {})
    current_instance_id = str(runtime_meta.get('runtime_instance_id') or '')
    probe_instance_id = str(probe.get('runtime_instance_id') or '')
    if not probe:
        payload['gpu_execution_state'] = 'not-run' if payload.get('gpu_present') else 'not-needed'
        payload['gpu_execution_reason'] = '' if payload.get('gpu_present') else 'no_gpu_present'
        return
    if probe_instance_id and current_instance_id and probe_instance_id != current_instance_id:
        payload['gpu_execution_state'] = 'stale'
        payload['gpu_execution_reason'] = 'runtime_instance_changed'
        payload['gpu_execution_runtime_instance_id'] = probe_instance_id
        payload['gpu_execution_verified_at'] = str(probe.get('completed_at') or '')
        return
    success = bool(probe.get('success'))
    state_value = str(probe.get('state') or '').strip().lower() or ('verified' if success else 'failed')
    payload['gpu_execution_state'] = state_value
    payload['gpu_execution_verified'] = success and state_value == 'verified'
    payload['gpu_execution_reason'] = str(probe.get('reason') or '')
    payload['gpu_execution_error_class'] = str(probe.get('execution_error_class') or '')
    payload['gpu_execution_error_message'] = str(probe.get('execution_error_message') or '')
    payload['gpu_execution_actual_device'] = str(probe.get('actual_device') or '')
    payload['gpu_execution_reranker_actual_device'] = str(probe.get('reranker_actual_device') or '')
    payload['gpu_execution_elapsed_ms'] = int(probe.get('elapsed_ms') or 0)
    payload['gpu_execution_verified_at'] = str(probe.get('completed_at') or '')
    payload['gpu_execution_runtime_instance_id'] = probe_instance_id


def _torch_cuda_peak_memory(torch_module, device: str) -> int:
    try:
        cuda_module = getattr(torch_module, 'cuda', None)
        if cuda_module is None or not hasattr(cuda_module, 'max_memory_allocated'):
            return 0
        return int(cuda_module.max_memory_allocated(device))
    except Exception:
        return 0


def _torch_cuda_reset_peak_memory(torch_module, device: str) -> None:
    try:
        cuda_module = getattr(torch_module, 'cuda', None)
        if cuda_module is not None and hasattr(cuda_module, 'reset_peak_memory_stats'):
            cuda_module.reset_peak_memory_stats(device)
    except Exception:
        return


def _torch_cuda_synchronize(torch_module, device: str) -> None:
    try:
        cuda_module = getattr(torch_module, 'cuda', None)
        if cuda_module is not None and hasattr(cuda_module, 'synchronize'):
            cuda_module.synchronize(device)
    except TypeError:
        cuda_module = getattr(torch_module, 'cuda', None)
        if cuda_module is not None and hasattr(cuda_module, 'synchronize'):
            cuda_module.synchronize()


def _torch_make_probe_tensor(torch_module, *, device: str):
    if hasattr(torch_module, 'ones'):
        return torch_module.ones((4, 4), device=device)
    if hasattr(torch_module, 'zeros'):
        return torch_module.zeros((4, 4), device=device)
    if hasattr(torch_module, 'tensor'):
        return torch_module.tensor([[1.0, 1.0, 1.0, 1.0]] * 4, device=device)
    raise RuntimeError('torch tensor factory is unavailable')


def probe_runtime_gpu_execution(*, force_refresh: bool = False) -> dict[str, object]:
    runtime_dir = _runtime_dir_path()
    runtime_meta = runtime_trace_metadata()
    if not force_refresh:
        cached_payload = {
            'gpu_present': bool(_detect_nvidia_gpus()),
            'gpu_probe_state': 'not-run',
        }
        _merge_cached_gpu_probe_state(cached_payload, runtime_dir, runtime_meta)
        if str(cached_payload.get('gpu_probe_state') or '').strip().lower() in {'verified', 'failed', 'stale', 'not-needed'}:
            return {
                'success': bool(cached_payload.get('gpu_probe_verified')),
                'state': str(cached_payload.get('gpu_probe_state') or 'not-run'),
                'reason': str(cached_payload.get('gpu_probe_reason') or ''),
                'execution_error_class': str(cached_payload.get('gpu_probe_error_class') or ''),
                'execution_error_message': str(cached_payload.get('gpu_probe_error_message') or ''),
                'actual_device': str(cached_payload.get('gpu_probe_actual_device') or ''),
                'elapsed_ms': int(cached_payload.get('gpu_probe_elapsed_ms') or 0),
                'completed_at': str(cached_payload.get('gpu_probe_verified_at') or ''),
                'runtime_instance_id': str(cached_payload.get('gpu_probe_runtime_instance_id') or ''),
            }

    started_at = time.perf_counter()
    started_at_iso = _utc_now_iso()
    payload: dict[str, object] = {
        'probe_kind': 'gpu-smoke',
        'started_at': started_at_iso,
        'completed_at': '',
        'runtime_instance_id': str(runtime_meta.get('runtime_instance_id') or ''),
        'live_runtime_id': str(runtime_meta.get('live_runtime_id') or ''),
        'success': False,
        'state': 'not-run',
        'reason': '',
        'requested_device': 'cuda',
        'resolved_device': 'cuda',
        'actual_device': '',
        'execution_error_class': '',
        'execution_error_message': '',
        'torch_import_ok': False,
        'torch_version': '',
        'torch_cuda_build': '',
        'cuda_is_available': False,
        'cuda_device_count': 0,
        'cuda_name': '',
        'cuda_peak_mem_before': 0,
        'cuda_peak_mem_after': 0,
        'cuda_peak_mem_delta': 0,
        'elapsed_ms': 0,
    }

    gpu_names = _detect_nvidia_gpus()
    if not gpu_names:
        payload['state'] = 'not-needed'
        payload['reason'] = 'no_gpu_present'
        payload['completed_at'] = _utc_now_iso()
        _write_runtime_capability_state(runtime_dir, {'gpu_probe': payload})
        return payload

    try:
        with _runtime_import_environment(component_id='semantic-core'):
            import torch

            payload['torch_import_ok'] = True
            payload['torch_version'] = str(getattr(torch, '__version__', '') or '')
            torch_version_info = getattr(torch, 'version', None)
            payload['torch_cuda_build'] = str(getattr(torch_version_info, 'cuda', '') or '')
            payload['cuda_is_available'] = bool(getattr(torch, 'cuda').is_available())
            payload['cuda_device_count'] = int(getattr(torch, 'cuda').device_count()) if payload['cuda_is_available'] else 0
            payload['cuda_name'] = str(getattr(torch, 'cuda').get_device_name(0)) if payload['cuda_device_count'] > 0 else ''
            if not payload['cuda_is_available'] or payload['cuda_device_count'] <= 0:
                payload['state'] = 'failed'
                payload['reason'] = 'cuda_unavailable'
            else:
                device_name = 'cuda:0'
                payload['resolved_device'] = device_name
                _torch_cuda_reset_peak_memory(torch, device_name)
                payload['cuda_peak_mem_before'] = _torch_cuda_peak_memory(torch, device_name)
                left = _torch_make_probe_tensor(torch, device=device_name)
                right = _torch_make_probe_tensor(torch, device=device_name)
                if hasattr(torch, 'matmul'):
                    output = torch.matmul(left, right)
                elif hasattr(torch, 'mm'):
                    output = torch.mm(left, right)
                else:
                    output = left
                _torch_cuda_synchronize(torch, device_name)
                payload['cuda_peak_mem_after'] = _torch_cuda_peak_memory(torch, device_name)
                payload['cuda_peak_mem_delta'] = max(int(payload['cuda_peak_mem_after']) - int(payload['cuda_peak_mem_before']), 0)
                payload['actual_device'] = str(getattr(output, 'device', getattr(left, 'device', device_name)) or device_name)
                payload['success'] = payload['actual_device'].startswith('cuda')
                payload['state'] = 'verified' if payload['success'] else 'failed'
                payload['reason'] = '' if payload['success'] else 'actual_device_not_cuda'
                del output
                del right
                del left
                try:
                    if hasattr(torch, 'cuda') and hasattr(torch.cuda, 'empty_cache'):
                        torch.cuda.empty_cache()
                except Exception:
                    pass
    except Exception as exc:
        payload['state'] = 'failed'
        payload['reason'] = 'gpu_smoke_failed'
        payload['execution_error_class'] = exc.__class__.__name__
        payload['execution_error_message'] = str(exc).strip() or exc.__class__.__name__

    payload['elapsed_ms'] = max(int((time.perf_counter() - started_at) * 1000), 0)
    payload['completed_at'] = _utc_now_iso()
    _write_runtime_capability_state(runtime_dir, {'gpu_probe': payload})
    global _ACCELERATION_CACHE
    if _ACCELERATION_CACHE is not None:
        _ACCELERATION_CACHE = None
    return payload


def probe_runtime_gpu_query_execution(*, force_refresh: bool = False) -> dict[str, object]:
    runtime_dir = _runtime_dir_path()
    runtime_meta = runtime_trace_metadata()
    if not force_refresh:
        cached_payload = {
            'gpu_present': bool(_detect_nvidia_gpus()),
            'gpu_execution_state': 'not-run',
        }
        _merge_cached_gpu_execution_state(cached_payload, runtime_dir, runtime_meta)
        if str(cached_payload.get('gpu_execution_state') or '').strip().lower() in {'verified', 'failed', 'stale', 'not-needed'}:
            return {
                'success': bool(cached_payload.get('gpu_execution_verified')),
                'state': str(cached_payload.get('gpu_execution_state') or 'not-run'),
                'reason': str(cached_payload.get('gpu_execution_reason') or ''),
                'execution_error_class': str(cached_payload.get('gpu_execution_error_class') or ''),
                'execution_error_message': str(cached_payload.get('gpu_execution_error_message') or ''),
                'actual_device': str(cached_payload.get('gpu_execution_actual_device') or ''),
                'reranker_actual_device': str(cached_payload.get('gpu_execution_reranker_actual_device') or ''),
                'elapsed_ms': int(cached_payload.get('gpu_execution_elapsed_ms') or 0),
                'completed_at': str(cached_payload.get('gpu_execution_verified_at') or ''),
                'runtime_instance_id': str(cached_payload.get('gpu_execution_runtime_instance_id') or ''),
            }

    from .runtime_canary import run_gpu_query_canary

    payload = dict(run_gpu_query_canary())
    payload.setdefault('probe_kind', 'gpu-query-canary')
    payload.setdefault('runtime_instance_id', str(runtime_meta.get('runtime_instance_id') or ''))
    payload.setdefault('live_runtime_id', str(runtime_meta.get('live_runtime_id') or ''))
    payload.setdefault('completed_at', _utc_now_iso())
    _write_runtime_capability_state(runtime_dir, {'gpu_query_probe': payload})
    global _ACCELERATION_CACHE
    if _ACCELERATION_CACHE is not None:
        _ACCELERATION_CACHE = None
    return payload


def runtime_management_snapshot(*, force_refresh: bool = False, verify_gpu: bool = False) -> dict[str, object]:
    """Return a Runtime-page snapshot without forcing heavy probes unless asked.

    Why: the Runtime page separates live-component refresh from expensive GPU
    execution verification. A normal refresh should stay light/medium-weight and
    reuse cached verification; explicit verification is the only path that runs
    smoke + query canaries.
    """

    payload = detect_acceleration(force_refresh=force_refresh)
    runtime_dir = _runtime_dir_path()
    runtime_meta = runtime_trace_metadata()
    _merge_cached_gpu_probe_state(payload, runtime_dir, runtime_meta)
    _merge_cached_gpu_execution_state(payload, runtime_dir, runtime_meta)
    if not verify_gpu:
        return payload
    if not bool(payload.get('gpu_present')):
        payload['gpu_probe_state'] = 'not-needed'
        payload['gpu_probe_verified'] = False
        payload['gpu_probe_reason'] = 'no_gpu_present'
        payload['gpu_execution_state'] = 'not-needed'
        payload['gpu_execution_verified'] = False
        payload['gpu_execution_reason'] = 'no_gpu_present'
        return payload
    if not bool(payload.get('torch_available')) or not bool(payload.get('sentence_transformers_available')):
        payload['gpu_probe_state'] = 'failed'
        payload['gpu_probe_verified'] = False
        payload['gpu_probe_reason'] = 'semantic_runtime_unavailable'
        payload['gpu_execution_state'] = 'blocked'
        payload['gpu_execution_verified'] = False
        payload['gpu_execution_reason'] = 'gpu_probe_failed'
        return payload
    if not bool(payload.get('cuda_available')):
        payload['gpu_probe_state'] = 'failed'
        payload['gpu_probe_verified'] = False
        payload['gpu_probe_reason'] = 'cuda_unavailable'
        payload['gpu_execution_state'] = 'blocked'
        payload['gpu_execution_verified'] = False
        payload['gpu_execution_reason'] = 'gpu_probe_failed'
        return payload
    probe = probe_runtime_gpu_execution(force_refresh=force_refresh)
    payload['gpu_probe_state'] = str(probe.get('state') or 'not-run')
    payload['gpu_probe_verified'] = bool(probe.get('success'))
    payload['gpu_probe_reason'] = str(probe.get('reason') or '')
    payload['gpu_probe_error_class'] = str(probe.get('execution_error_class') or '')
    payload['gpu_probe_error_message'] = str(probe.get('execution_error_message') or '')
    payload['gpu_probe_actual_device'] = str(probe.get('actual_device') or '')
    payload['gpu_probe_elapsed_ms'] = int(probe.get('elapsed_ms') or 0)
    payload['gpu_probe_verified_at'] = str(probe.get('completed_at') or '')
    payload['gpu_probe_runtime_instance_id'] = str(probe.get('runtime_instance_id') or '')
    payload['torch_cuda_build'] = str(probe.get('torch_cuda_build') or payload.get('torch_cuda_build') or '')
    if not bool(probe.get('success')):
        payload['gpu_execution_state'] = 'blocked'
        payload['gpu_execution_verified'] = False
        payload['gpu_execution_reason'] = 'gpu_probe_failed'
        payload['gpu_execution_error_class'] = str(probe.get('execution_error_class') or '')
        payload['gpu_execution_error_message'] = str(probe.get('execution_error_message') or '')
        return payload
    query_probe = probe_runtime_gpu_query_execution(force_refresh=force_refresh)
    payload['gpu_execution_state'] = str(query_probe.get('state') or 'not-run')
    payload['gpu_execution_verified'] = bool(query_probe.get('success'))
    payload['gpu_execution_reason'] = str(query_probe.get('reason') or '')
    payload['gpu_execution_error_class'] = str(query_probe.get('execution_error_class') or '')
    payload['gpu_execution_error_message'] = str(query_probe.get('execution_error_message') or '')
    payload['gpu_execution_actual_device'] = str(query_probe.get('actual_device') or '')
    payload['gpu_execution_reranker_actual_device'] = str(query_probe.get('reranker_actual_device') or '')
    payload['gpu_execution_elapsed_ms'] = int(query_probe.get('elapsed_ms') or 0)
    payload['gpu_execution_verified_at'] = str(query_probe.get('completed_at') or '')
    payload['gpu_execution_runtime_instance_id'] = str(query_probe.get('runtime_instance_id') or '')
    return payload


def refresh_runtime_capability_snapshot(*, force_refresh: bool = False) -> dict[str, object]:
    return runtime_management_snapshot(force_refresh=force_refresh, verify_gpu=True)


def get_device_options() -> list[str]:
    acceleration = detect_acceleration()
    options = [str(item) for item in (acceleration.get("device_options") or ["auto", "cpu"])]
    if acceleration.get('gpu_present') and 'cuda' not in options:
        options.append('cuda')
    return options


def resolve_vector_device(device_name: str | None) -> str:
    requested = (device_name or "cpu").strip().lower() or "cpu"
    acceleration = detect_acceleration()
    if requested in {"auto", "gpu"}:
        if acceleration.get("cuda_available"):
            return "cuda"
        if requested == "gpu":
            refreshed = detect_acceleration(force_refresh=True)
            return "cuda" if refreshed.get("cuda_available") else "cpu"
        return "cpu"
    if requested == "cuda" and not acceleration.get("cuda_available"):
        refreshed = detect_acceleration(force_refresh=True)
        return "cuda" if refreshed.get("cuda_available") else "cpu"
    return requested

def _detect_nvcc_version() -> str:
    try:
        result = run_hidden(
            ["nvcc", "-V"],
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return ""
    output = (result.stdout or result.stderr or "").strip()
    match = re.search(r"release\s+([0-9]+(?:\.[0-9]+)?)", output, re.IGNORECASE)
    if match:
        return match.group(1)
    return output.splitlines()[-1].strip() if output else ""


def runtime_guidance_context(
    runtime_name: str | None,
    device_name: str | None,
    *,
    force_refresh: bool = False,
    extra_detail: str = '',
    acceleration_payload: dict[str, object] | None = None,
    runtime_state: dict[str, object] | None = None,
) -> dict[str, object]:
    acceleration = dict(acceleration_payload or detect_acceleration(force_refresh=force_refresh))
    runtime_state = dict(runtime_state or inspect_runtime_environment())
    requested = (device_name or 'auto').strip().lower() or 'auto'
    runtime_name = (runtime_name or 'torch').strip().lower() or 'torch'
    wants_gpu = requested in {'auto', 'gpu', 'cuda'} and bool(acceleration.get('gpu_present'))
    recommended_profile = 'cuda' if wants_gpu else 'cpu'
    app_dir = _application_root_dir()
    runtime_dir = Path(runtime_state['runtime_dir'])
    install_target_dir = Path(runtime_state.get('preferred_runtime_dir') or runtime_dir)
    install_script = _install_runtime_script_path()
    source_urls = runtime_install_sources()
    official_command = build_runtime_install_command(recommended_profile, source='official', component='all')
    mirror_command = build_runtime_install_command(recommended_profile, source='mirror', component='all')
    command = official_command
    gpu_name = str(acceleration.get('gpu_name') or acceleration.get('cuda_name') or '').strip()
    nvcc_version = str(acceleration.get('nvcc_version') or '').strip()
    if recommended_profile == 'cuda':
        disk_usage = '约 4.3 GB - 4.6 GB'
        download_usage = '约 3 GB - 5 GB'
    else:
        disk_usage = '约 1.3 GB - 2.0 GB'
        download_usage = '约 1 GB - 2 GB'

    if acceleration.get('cuda_available'):
        cuda_step_status = '已检测到可用 CUDA 环境'
    elif acceleration.get('gpu_present') and nvcc_version:
        cuda_step_status = f'已检测到系统 CUDA 工具链（{nvcc_version}）'
    elif acceleration.get('gpu_present'):
        cuda_step_status = '已检测到 NVIDIA 显卡，但还没检测到可用 CUDA 条件'
    else:
        cuda_step_status = '未检测到 NVIDIA 显卡或可用 CUDA 条件'

    missing_items = list(runtime_state['runtime_missing_items'])
    if runtime_state.get('runtime_pending_components'):
        runtime_step_status = '已下载待应用更新，关闭程序后会自动生效'
    elif runtime_state['runtime_complete']:
        runtime_step_status = 'runtime 已完整'
    elif runtime_state['runtime_exists'] and runtime_state['runtime_has_content']:
        runtime_step_status = 'runtime 已存在，但内容还不完整'
    elif runtime_state['runtime_exists']:
        runtime_step_status = 'runtime 文件夹已创建，但还是空的'
    else:
        runtime_step_status = '还没有检测到 runtime 文件夹'

    current_status_lines = [
        f"- 显卡：{gpu_name or '未检测到 NVIDIA GPU'}" if acceleration.get('gpu_present') else '- 显卡：未检测到 NVIDIA GPU',
        f'- 系统 CUDA：{nvcc_version}' if nvcc_version else '- 系统 CUDA：未检测到',
        f"- 程序内 PyTorch：已加载（{acceleration.get('torch_version') or 'unknown'}）" if acceleration.get('torch_available') else '- 程序内 PyTorch：未加载',
        '- 程序内 sentence-transformers：已加载' if acceleration.get('sentence_transformers_available') else '- 程序内 sentence-transformers：未加载',
        f'- 当前设备选择：{requested}',
        f'- 当前实际设备：{resolve_vector_device(requested)}',
        f"- runtime 文件夹：{'已检测到' if runtime_state['runtime_exists'] else '未检测到'}",
        f"- runtime 完整性：{'完整' if runtime_state['runtime_complete'] else '不完整'}",
    ]
    if missing_items:
        current_status_lines.append(f"- runtime 缺少项：{', '.join(missing_items)}")
    pending_components = list(runtime_state.get('runtime_pending_components') or [])
    if pending_components:
        current_status_lines.append(f"- 待应用更新：{', '.join(pending_components)}（关闭程序后自动生效）")
    if acceleration.get('torch_error'):
        current_status_lines.append(f"- PyTorch 导入失败：{acceleration.get('torch_error')}")
    if acceleration.get('sentence_transformers_error'):
        current_status_lines.append(f"- sentence-transformers 导入失败：{acceleration.get('sentence_transformers_error')}")

    detail = str(extra_detail or '').strip()
    problem_line = '- 你当前选择的是 CUDA(N卡GPU)，但显卡加速这部分还没准备好。' if requested == 'cuda' else '- 当前本地语义运行环境还没准备好，所以现在还不能执行本地语义建库或向量查询。'
    cuda_step_title = '第一步：安装或确认 CUDA 环境' if requested == 'cuda' or recommended_profile == 'cuda' else '第一步：如果你以后想启用 CUDA(N卡GPU)，先安装或确认 CUDA 环境'
    plain_lines = [
        '当前还不能开始本地语义建库或向量查询。',
        '',
        '怎么了',
        problem_line,
        '- 现在要么还没检测到可直接使用的 CUDA 条件，要么当前可用的 runtime 还没安装完整。',
        '',
        '为什么',
        f'- 这个轻量发布包没有内置 {runtime_name}、PyTorch、LanceDB、sentence-transformers、pyarrow 这类大型运行时。',
        '- 主程序可以先打开，但要做本地语义建库或向量查询，还需要把外置运行时补齐；如果你想走 GPU，还要另外满足 CUDA 条件。',
        '',
        '怎么做',
        f'{cuda_step_title}（状态：{cuda_step_status}）',
        f'- 官方链接（可复制）：{_CUDA_SETUP_GUIDE_URL}',
        '- 做完后会发生什么：程序就能识别到系统里的 NVIDIA / CUDA 条件；如果第二步还没做，本地向量功能仍然不能直接运行。',
        '',
        f'第二步：在 Windows 终端里安装 runtime（状态：{runtime_step_status}）',
        f"- 官方源（可复制）：{source_urls['official']}",
        f'- 官方命令（可复制）：{official_command}',
        f"- 镜像源（可复制）：{source_urls['mirror']}",
        f'- 镜像命令（可复制）：{mirror_command}',
        f'- 会安装到：{install_target_dir}',
        '- 安装后会发生什么：会先把 PyTorch、LanceDB、sentence-transformers、pyarrow、onnxruntime 等本地运行时下载到待应用目录；关闭一次程序后后台会自动切换到新组件，下一次打开时就能正常执行本地语义建库、向量查询和 GPU 加速。',
        f'- 预计落盘：{disk_usage}；预计下载：{download_usage}',
        '',
        '当前状态',
        *current_status_lines,
        '',
        '如果只使用CPU',
        '- 也可以继续选择 lancedb，只是不走 CUDA 加速。',
        '- 如果后面仍要使用本地语义建库或向量查询，还是先完成上面的 runtime 安装步骤。',
    ]
    if detail:
        plain_lines.extend(['', '补充信息', detail])
    return {
        'runtime_name': runtime_name,
        'requested_device': requested,
        'recommended_profile': recommended_profile,
        'app_dir': app_dir,
        'runtime_dir': runtime_dir,
        'active_runtime_dir': runtime_dir,
        'preferred_runtime_dir': install_target_dir,
        'install_target_dir': install_target_dir,
        'install_script': install_script,
        'install_command': command,
        'official_install_command': official_command,
        'mirror_install_command': mirror_command,
        'official_runtime_source_url': source_urls['official'],
        'mirror_runtime_source_url': source_urls['mirror'],
        'cuda_guide_url': _CUDA_SETUP_GUIDE_URL,
        'gpu_present': bool(acceleration.get('gpu_present')),
        'gpu_name': gpu_name,
        'nvcc_available': bool(acceleration.get('nvcc_available')),
        'nvcc_version': nvcc_version,
        'cuda_available': bool(acceleration.get('cuda_available')),
        'torch_available': bool(acceleration.get('torch_available')),
        'torch_version': str(acceleration.get('torch_version') or ''),
        'torch_cuda_build': str(acceleration.get('torch_cuda_build') or ''),
        'torch_error': str(acceleration.get('torch_error') or ''),
        'sentence_transformers_available': bool(acceleration.get('sentence_transformers_available')),
        'sentence_transformers_error': str(acceleration.get('sentence_transformers_error') or ''),
        # GPU probe/execution states are derived from the runtime capability cache. They are
        # needed by the Runtime page to avoid "GPU is ready but UI still red" false negatives.
        'gpu_probe_state': str(acceleration.get('gpu_probe_state') or 'not-run'),
        'gpu_probe_verified': bool(acceleration.get('gpu_probe_verified')),
        'gpu_probe_reason': str(acceleration.get('gpu_probe_reason') or ''),
        'gpu_probe_error_class': str(acceleration.get('gpu_probe_error_class') or ''),
        'gpu_probe_error_message': str(acceleration.get('gpu_probe_error_message') or ''),
        'gpu_probe_actual_device': str(acceleration.get('gpu_probe_actual_device') or ''),
        'gpu_probe_elapsed_ms': int(acceleration.get('gpu_probe_elapsed_ms') or 0),
        'gpu_probe_verified_at': str(acceleration.get('gpu_probe_verified_at') or ''),
        'gpu_probe_runtime_instance_id': str(acceleration.get('gpu_probe_runtime_instance_id') or ''),
        'gpu_execution_state': str(acceleration.get('gpu_execution_state') or 'not-run'),
        'gpu_execution_verified': bool(acceleration.get('gpu_execution_verified')),
        'gpu_execution_reason': str(acceleration.get('gpu_execution_reason') or ''),
        'gpu_execution_error_class': str(acceleration.get('gpu_execution_error_class') or ''),
        'gpu_execution_error_message': str(acceleration.get('gpu_execution_error_message') or ''),
        'gpu_execution_actual_device': str(acceleration.get('gpu_execution_actual_device') or ''),
        'gpu_execution_reranker_actual_device': str(acceleration.get('gpu_execution_reranker_actual_device') or ''),
        'gpu_execution_elapsed_ms': int(acceleration.get('gpu_execution_elapsed_ms') or 0),
        'gpu_execution_verified_at': str(acceleration.get('gpu_execution_verified_at') or ''),
        'gpu_execution_runtime_instance_id': str(acceleration.get('gpu_execution_runtime_instance_id') or ''),
        'runtime_exists': bool(runtime_state['runtime_exists']),
        'runtime_complete': bool(runtime_state['runtime_complete']),
        'runtime_missing_items': missing_items,
        'runtime_pending': bool(runtime_state.get('runtime_pending')),
        'runtime_pending_components': list(runtime_state.get('runtime_pending_components') or []),
        'cuda_step_status': cuda_step_status,
        'runtime_step_status': runtime_step_status,
        'resolved_device': resolve_vector_device(requested),
        'disk_usage': disk_usage,
        'download_usage': download_usage,
        'current_status_lines': current_status_lines,
        'extra_detail': detail,
        'plain_text': '\n'.join(plain_lines),
    }


def runtime_dependency_issue(config: AppConfig, *, force_refresh: bool = False) -> str | None:
    backend = (config.vector_backend or 'disabled').strip().lower()
    if backend in {'', 'disabled', 'none', 'off'}:
        return None
    runtime_name = (config.vector_runtime or 'torch').strip().lower() or 'torch'
    runtime_state = inspect_runtime_environment()
    acceleration = detect_acceleration(force_refresh=force_refresh)
    failures: list[str] = []
    semantic_state = runtime_component_status('semantic-core')
    vector_state = runtime_component_status('vector-store')
    for item in list(semantic_state.get('missing_items') or []):
        failures.append(f'- 本地语义核心: {item}')
    for item in list(vector_state.get('missing_items') or []):
        failures.append(f'- 索引与存储支撑: {item}')
    if runtime_name == 'torch' and not bool(acceleration.get('torch_available')):
        failures.append(f"- torch: {acceleration.get('torch_error') or 'unavailable'}")
    if not bool(acceleration.get('sentence_transformers_available')):
        failures.append(f"- sentence-transformers: {acceleration.get('sentence_transformers_error') or 'unavailable'}")
    if runtime_name == 'onnx' and not bool(vector_state.get('ready')):
        failures.append('- onnxruntime: 索引与存储支撑尚未完整')
    failures = list(dict.fromkeys([str(item).strip() for item in failures if str(item).strip()]))
    if not failures and runtime_state.get('runtime_complete'):
        return None
    detail = '底层缺失：\n' + '\n'.join(failures) if failures else ''
    context = runtime_guidance_context(runtime_name, config.vector_device, force_refresh=True, extra_detail=detail, acceleration_payload=acceleration, runtime_state=runtime_state)
    return str(context.get('plain_text') or '').strip() or detail or None


def _runtime_dependency_message(runtime_name: str | None, device_name: str | None) -> str:
    return str(runtime_guidance_context(runtime_name, device_name, force_refresh=True).get('plain_text') or '')


def runtime_dependency_message(runtime_name: str | None, device_name: str | None) -> str:
    return _runtime_dependency_message(runtime_name, device_name)

def _configure_huggingface_environment(hf_home_dir: Path) -> None:
    hub_dir = hf_home_dir / "hub"
    assets_dir = hf_home_dir / "assets"
    xet_dir = hf_home_dir / "xet"
    for directory in (hub_dir, assets_dir, xet_dir):
        directory.mkdir(parents=True, exist_ok=True)

    os.environ.pop("TRANSFORMERS_CACHE", None)
    os.environ["HF_HOME"] = str(hf_home_dir)
    os.environ["HF_HUB_CACHE"] = str(hub_dir)
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(hub_dir)
    os.environ["HUGGINGFACE_ASSETS_CACHE"] = str(assets_dir)
    os.environ["HF_XET_CACHE"] = str(xet_dir)
    os.environ["SENTENCE_TRANSFORMERS_HOME"] = str(hub_dir)
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    try:
        from huggingface_hub import constants as hf_constants
    except ImportError:
        return

    hf_constants.HF_HOME = str(hf_home_dir)
    hf_constants.hf_cache_home = str(hf_home_dir)
    hf_constants.HF_HUB_CACHE = str(hub_dir)
    hf_constants.HUGGINGFACE_HUB_CACHE = str(hub_dir)
    hf_constants.HUGGINGFACE_ASSETS_CACHE = str(assets_dir)
    hf_constants.HF_XET_CACHE = str(xet_dir)
    hf_constants.HF_HUB_DISABLE_XET = True


def _is_oom_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return 'out of memory' in message or 'cuda out of memory' in message


def _is_memory_pressure_exception(exc: Exception) -> bool:
    if isinstance(exc, MemoryError):
        return True
    if _is_oom_error(exc):
        return True
    message = str(exc).lower()
    return any(token in message for token in (
        'bad_alloc',
        'bad alloc',
        'cannot allocate',
        'not enough memory',
        'memoryerror',
        'memory pressure',
        'allocator',
    ))


def _infer_vector_text_char_limit(embedder: object, *, resolved_device: str) -> int:
    device_key = 'cuda' if (resolved_device or '').strip().lower() == 'cuda' else 'cpu'
    default_limit = int(_VECTOR_TEXT_CHAR_LIMITS[device_key])
    try:
        model_limit = int(getattr(embedder, 'max_seq_length', 0) or 0)
    except Exception:
        model_limit = 0
    if model_limit <= 0:
        return default_limit
    return max(2048, min(default_limit, model_limit * 4))


def _infer_vector_batch_char_budget(item_char_limit: int, *, resolved_device: str, profile: str) -> int:
    device_key = 'cuda' if (resolved_device or '').strip().lower() == 'cuda' else 'cpu'
    normalized_profile = str(profile or 'balanced').strip().lower() or 'balanced'
    default_budget = int(_VECTOR_BATCH_CHAR_BUDGETS[device_key].get(normalized_profile, _VECTOR_BATCH_CHAR_BUDGETS[device_key]['balanced']))
    return max(int(item_char_limit), default_budget)


def _prepare_vector_text(text: str, *, max_chars: int) -> tuple[str, dict[str, int | bool]]:
    normalized = str(text or '')
    source_chars = len(normalized)
    safe_limit = max(int(max_chars or 0), 256)
    if source_chars <= safe_limit:
        return normalized, {
            'source_chars': source_chars,
            'vector_chars': source_chars,
            'truncated': False,
        }
    head_chars = max(int(safe_limit * 0.72), 192)
    tail_chars = max(safe_limit - head_chars - 5, 64)
    if head_chars + tail_chars + 5 > safe_limit:
        tail_chars = max(safe_limit - head_chars - 5, 0)
    if tail_chars > 0:
        clipped = f"{normalized[:head_chars].rstrip()}\n...\n{normalized[-tail_chars:].lstrip()}"
    else:
        clipped = normalized[:safe_limit]
    return clipped, {
        'source_chars': source_chars,
        'vector_chars': len(clipped),
        'truncated': True,
    }


def _log_thread_stacks(label: str, **context: object) -> None:
    context_line = ', '.join(f'{key}={value}' for key, value in context.items() if value is not None)
    frames = sys._current_frames()
    threads = {thread.ident: thread.name for thread in threading.enumerate() if thread.ident is not None}
    sections: list[str] = []
    for thread_id, frame in frames.items():
        name = threads.get(thread_id, 'unknown')
        stack = ''.join(traceback.format_stack(frame)[-8:])
        sections.append(f'--- Thread {name} ({thread_id}) ---\n{stack}')
    LOGGER.warning('%s%s\n%s', label, f' [{context_line}]' if context_line else '', '\n'.join(sections))


def _clear_cuda_cache() -> None:
    try:
        import torch
    except Exception:
        return
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            ipc_collect = getattr(torch.cuda, 'ipc_collect', None)
            if callable(ipc_collect):
                ipc_collect()
    except Exception:
        return


def release_process_vector_resources(*, clear_cuda: bool = True, reset_acceleration: bool = True) -> None:
    global _ACCELERATION_CACHE
    _EMBEDDER_CACHE.clear()
    if reset_acceleration:
        _ACCELERATION_CACHE = None
    _release_vector_memory(clear_cuda=clear_cuda)


def _release_vector_memory(*, clear_cuda: bool = False) -> None:
    try:
        gc.collect()
    except Exception:
        pass
    if clear_cuda:
        _clear_cuda_cache()


def _distance_to_score(distance: float) -> float:
    return 1.0 / (1.0 + max(float(distance), 0.0))


def _normalize_model_dir_name(model_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "__", (model_name or "").strip())
    return normalized or "model"


def _powershell_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _build_model_download_command(repo_id: str, model_dir: Path, hf_home_dir: Path, *, use_mirror: bool) -> str:
    command_parts = [
        f"$target = {_powershell_literal(model_dir)}",
        f"$hfHome = {_powershell_literal(hf_home_dir)}",
        "New-Item -ItemType Directory -Force -Path $target, $hfHome | Out-Null",
        "$env:HF_HOME = $hfHome",
    ]
    if use_mirror:
        command_parts.append("$env:HF_ENDPOINT = 'https://hf-mirror.com'")
    command_parts.append(f"hf download {_powershell_literal(repo_id)} --local-dir $target")
    command = "; ".join(command_parts)
    return f'PowerShell -ExecutionPolicy Bypass -NoProfile -Command "{command}"'


def _is_model_dir_ready(path: Path, runtime: str) -> bool:
    if not path.exists():
        return False
    if not (path / "modules.json").exists() or not (path / "config.json").exists():
        return False
    runtime = (runtime or "torch").lower()
    if runtime == "onnx":
        return (path / "onnx" / "model.onnx").exists()
    weight_files = (
        path / "pytorch_model.bin",
        path / "model.safetensors",
        path / "pytorch_model.bin.index.json",
        path / "model.safetensors.index.json",
    )
    return any(candidate.exists() for candidate in weight_files)


def _detect_nvidia_gpus() -> list[str]:
    import os
    if os.name == 'nt':
        # On Windows + Python 3.13 + PySide6, subprocess.run can cause fatal
        # 0x8001010d COM Access Violations when called from background threads.
        # We bypass subprocess entirely and use NVML via ctypes if available.
        try:
            import ctypes
            nvml = ctypes.WinDLL(r"C:\Windows\System32\nvml.dll")
            nvml.nvmlInit_v2.restype = ctypes.c_int
            if nvml.nvmlInit_v2() != 0:
                return []
            
            count = ctypes.c_uint()
            nvml.nvmlDeviceGetCount_v2(ctypes.byref(count))
            
            names = []
            for i in range(count.value):
                handle = ctypes.c_void_p()
                nvml.nvmlDeviceGetHandleByIndex_v2(i, ctypes.byref(handle))
                
                name_buf = ctypes.create_string_buffer(256)
                nvml.nvmlDeviceGetName(handle, name_buf, 256)
                names.append(name_buf.value.decode('utf-8', errors='replace'))
                
            nvml.nvmlShutdown()
            return names
        except Exception:
            # Fallback to safe but empty if NVML fails (better than crash)
            return []
    
    # Non-Windows safe path
    try:
        result = run_hidden(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
        )
    except Exception:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]



def _emit_progress(on_progress: Callable[[dict[str, object]], None] | None, payload: dict[str, object]) -> None:
    if on_progress is None:
        return
    on_progress(payload)


def _wait_for_controls(pause_event: threading.Event | None, cancel_event: threading.Event | None) -> None:
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise BuildCancelledError("cancelled")
        if pause_event is None or not pause_event.is_set():
            return
        time.sleep(0.12)

















