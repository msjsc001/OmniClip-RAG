from __future__ import annotations

import gc
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
from collections.abc import Iterable
from pathlib import Path
from typing import Callable, Protocol

from .build_control import BuildPerformanceController
from .config import AppConfig, DataPaths
from .errors import BuildCancelledError, RuntimeDependencyError
from .process_utils import run_hidden


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

LOGGER = logging.getLogger(__name__)


class NullVectorIndex:
    def rebuild(
        self,
        documents: Iterable[dict[str, str]],
        *,
        total: int | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
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
        import lancedb

        self.config = config
        self.paths = paths
        self._embedder_factory = embedder_factory or self._default_embedder_factory
        self._embedder: Embedder | None = None
        self._db_dir = paths.state_dir / "lancedb"
        self._db_dir.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(self._db_dir))
        self._table_name = "chunks"
        self._vector_dimension: int | None = None

    def rebuild(
        self,
        documents: Iterable[dict[str, str]],
        *,
        total: int | None = None,
        on_progress: Callable[[dict[str, object]], None] | None = None,
        pause_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
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

        processed = 0
        encoded = 0
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

        def progress_display_current() -> int:
            return min(total, processed) if total and total > 0 else processed

        def pipeline_progress_current() -> int:
            return min(total, max(processed, encoded)) if total and total > 0 else max(processed, encoded)

        def current_progress_ratio() -> float:
            return (pipeline_progress_current() / total) if total and total > 0 else 0.0

        last_vector_heartbeat_at = 0.0

        def emit_vector_progress(snapshot, *, stage_status: str | None = None) -> None:
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
            _emit_progress(on_progress, payload)

        def maybe_emit_vector_heartbeat(snapshot, *, stage_status: str, min_interval_seconds: float = _VECTOR_PROGRESS_HEARTBEAT_SECONDS) -> None:
            nonlocal last_vector_heartbeat_at
            now = time.time()
            if now - last_vector_heartbeat_at < max(float(min_interval_seconds), 0.0):
                return
            last_vector_heartbeat_at = now
            emit_vector_progress(snapshot, stage_status=stage_status)

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

        _wait_for_controls(pause_event, cancel_event)
        emit_vector_progress(controller.snapshot(), stage_status='loading_model')
        self._load_embedder()
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
                batch: list[dict[str, str]] = []
                for _ in range(desired_batch):
                    try:
                        batch.append(next(iterator))
                    except StopIteration:
                        break
                if not batch:
                    break

                encode_elapsed_ms = 0.0
                while True:
                    started = time.perf_counter()
                    try:
                        vectors = self._encode(
                            [item['rendered_text'] for item in batch],
                            batch_size=min(len(batch), controller.current_encode_batch_size),
                        )
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
        vector = self._encode([query_text])[0]
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
            **acceleration,
        }

    def reset(self) -> None:
        if self._table_exists():
            self._db.drop_table(self._table_name)
        self._vector_dimension = None
        table_dir = self._db_dir / f"{self._table_name}.lance"
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
        self._db.create_table(self._table_name, schema=schema, mode="overwrite")

    def _table_exists(self) -> bool:
        tables = self._db.list_tables()
        if hasattr(tables, "tables"):
            return self._table_name in tables.tables
        return self._table_name in tables

    def _table(self):
        return self._db.open_table(self._table_name)

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
        return embedder.encode(
            texts,
            batch_size=batch_size or self.config.vector_batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
        )

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
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeDependencyError(_runtime_dependency_message(self.config.vector_runtime, self.config.vector_device)) from exc

        runtime_name = (self.config.vector_runtime or "torch").lower()
        resolved_device = resolve_vector_device(self.config.vector_device)
        cache_key = (str(local_model_dir), runtime_name, resolved_device)
        cached = _EMBEDDER_CACHE.get(cache_key)
        if cached is not None:
            return cached

        embedder = SentenceTransformer(
            str(local_model_dir),
            device=resolved_device,
            cache_folder=str(runtime_cache_dir),
            backend=self.config.vector_runtime,
            local_files_only=True,
        )
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
        except (ImportError, ModuleNotFoundError, OSError) as exc:
            return MissingRuntimeVectorIndex(config, paths, backend=backend, reason=exc)
    raise NotImplementedError(f"当前向量后端尚未接入：{config.vector_backend}")


def get_local_model_dir(config: AppConfig, paths: DataPaths) -> Path:
    return paths.cache_dir / "models" / _normalize_model_dir_name(config.vector_model)


def is_local_model_ready(config: AppConfig, paths: DataPaths) -> bool:
    return _is_model_dir_ready(get_local_model_dir(config, paths), config.vector_runtime)


def prepare_local_model_snapshot(
    config: AppConfig,
    paths: DataPaths,
    *,
    allow_download: bool = True,
) -> dict[str, object]:
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


def _runtime_dir_path() -> Path:
    return _application_root_dir() / 'runtime'


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


def _runtime_marker_exists(runtime_dir: Path, marker: str) -> bool:
    candidate = runtime_dir / marker
    if candidate.exists():
        return True
    return any(runtime_dir.glob(f'{marker}-*.dist-info'))


def inspect_runtime_environment() -> dict[str, object]:
    runtime_dir = _runtime_dir_path()
    runtime_dir_exists = runtime_dir.exists() and runtime_dir.is_dir()
    runtime_dir_has_content = False
    if runtime_dir_exists:
        try:
            runtime_dir_has_content = any(runtime_dir.iterdir())
        except OSError:
            runtime_dir_has_content = False
    missing_items = [
        display_name
        for marker, display_name in _RUNTIME_REQUIRED_MARKERS.items()
        if not _runtime_marker_exists(runtime_dir, marker)
    ] if runtime_dir_exists else list(_RUNTIME_REQUIRED_MARKERS.values())
    return {
        'runtime_dir': runtime_dir,
        'runtime_exists': runtime_dir_exists,
        'runtime_has_content': runtime_dir_has_content,
        'runtime_complete': runtime_dir_exists and runtime_dir_has_content and not missing_items,
        'runtime_missing_items': missing_items,
    }


def detect_acceleration(*, force_refresh: bool = False) -> dict[str, object]:
    global _ACCELERATION_CACHE
    if not force_refresh and _ACCELERATION_CACHE is not None:
        return dict(_ACCELERATION_CACHE)

    with _ACCELERATION_LOCK:
        if not force_refresh and _ACCELERATION_CACHE is not None:
            return dict(_ACCELERATION_CACHE)

        runtime_state = inspect_runtime_environment()
        payload: dict[str, object] = {
            "torch_available": False,
            "torch_version": "",
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
        }

        gpu_names = _detect_nvidia_gpus()
        if gpu_names:
            payload["gpu_present"] = True
            payload["gpu_name"] = gpu_names[0]

        nvcc_version = _detect_nvcc_version()
        if nvcc_version:
            payload["nvcc_available"] = True
            payload["nvcc_version"] = nvcc_version

        try:
            import torch
        except Exception as exc:
            payload["torch_error"] = f"{type(exc).__name__}: {exc}"
            torch = None

        if torch is not None:
            payload["torch_available"] = True
            payload["torch_version"] = getattr(torch, "__version__", "")
            payload["runtime_status"] = "cpu"
            try:
                cuda_available = bool(torch.cuda.is_available())
            except Exception:
                cuda_available = False
            payload["cuda_available"] = cuda_available
            if cuda_available:
                try:
                    device_count = int(torch.cuda.device_count())
                except Exception:
                    device_count = 0
                payload["cuda_device_count"] = device_count
                if device_count > 0:
                    try:
                        payload["cuda_name"] = str(torch.cuda.get_device_name(0))
                    except Exception:
                        payload["cuda_name"] = ""
                payload["device_options"] = ["auto", "cpu", "cuda"]
                payload["recommended_device"] = "cuda"
                payload["runtime_status"] = "cuda"
            elif gpu_names:
                payload["recommended_device"] = "cpu"

        try:
            import sentence_transformers  # noqa: F401
        except Exception as exc:
            payload["sentence_transformers_error"] = f"{type(exc).__name__}: {exc}"
        else:
            payload["sentence_transformers_available"] = True

        _ACCELERATION_CACHE = dict(payload)
        return dict(payload)

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
) -> dict[str, object]:
    acceleration = detect_acceleration(force_refresh=force_refresh)
    runtime_state = inspect_runtime_environment()
    requested = (device_name or 'auto').strip().lower() or 'auto'
    runtime_name = (runtime_name or 'torch').strip().lower() or 'torch'
    wants_gpu = requested in {'auto', 'gpu', 'cuda'} and bool(acceleration.get('gpu_present'))
    recommended_profile = 'cuda' if wants_gpu else 'cpu'
    app_dir = _application_root_dir()
    runtime_dir = Path(runtime_state['runtime_dir'])
    install_script = _install_runtime_script_path()
    relative_script = _install_runtime_script_relative()
    app_dir_literal = _powershell_literal(str(app_dir))
    command = (
        "PowerShell -ExecutionPolicy Bypass -NoProfile -Command "
        f"\"Set-Location -LiteralPath '{app_dir_literal}'; & '{relative_script}' -Profile {recommended_profile}\""
    )
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
    if runtime_state['runtime_complete']:
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
        '- 现在要么还没检测到可直接使用的 CUDA 条件，要么当前程序目录下的 runtime 还没安装完整。',
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
        f'- 命令（可复制）：{command}',
        f'- 会安装到：{runtime_dir}',
        '- 安装后会发生什么：会补齐 PyTorch、LanceDB、sentence-transformers、pyarrow、onnxruntime 等本地运行时；重启程序后就能正常执行本地语义建库、向量查询和 GPU 加速。',
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
        'install_script': install_script,
        'install_command': command,
        'cuda_guide_url': _CUDA_SETUP_GUIDE_URL,
        'gpu_present': bool(acceleration.get('gpu_present')),
        'gpu_name': gpu_name,
        'nvcc_available': bool(acceleration.get('nvcc_available')),
        'nvcc_version': nvcc_version,
        'cuda_available': bool(acceleration.get('cuda_available')),
        'torch_available': bool(acceleration.get('torch_available')),
        'torch_version': str(acceleration.get('torch_version') or ''),
        'torch_error': str(acceleration.get('torch_error') or ''),
        'sentence_transformers_available': bool(acceleration.get('sentence_transformers_available')),
        'sentence_transformers_error': str(acceleration.get('sentence_transformers_error') or ''),
        'runtime_exists': bool(runtime_state['runtime_exists']),
        'runtime_complete': bool(runtime_state['runtime_complete']),
        'runtime_missing_items': missing_items,
        'cuda_step_status': cuda_step_status,
        'runtime_step_status': runtime_step_status,
        'resolved_device': resolve_vector_device(requested),
        'disk_usage': disk_usage,
        'download_usage': download_usage,
        'current_status_lines': current_status_lines,
        'extra_detail': detail,
        'plain_text': '\n'.join(plain_lines),
    }


def runtime_dependency_issue(config: AppConfig) -> str | None:
    backend = (config.vector_backend or 'disabled').strip().lower()
    if backend in {'', 'disabled', 'none', 'off'}:
        return None
    runtime_name = (config.vector_runtime or 'torch').strip().lower() or 'torch'
    failures: list[str] = []
    required_imports = [*_RUNTIME_REQUIRED_IMPORTS, *_RUNTIME_REQUIRED_IMPORTS_BY_RUNTIME.get(runtime_name, ())]
    for module_name, display_name in required_imports:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            failures.append(f'- {display_name}: {type(exc).__name__}: {exc}')
    if not failures:
        return None
    detail = '底层缺失：\n' + '\n'.join(failures)
    context = runtime_guidance_context(runtime_name, config.vector_device, force_refresh=True, extra_detail=detail)
    return str(context.get('plain_text') or '').strip() or detail


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


def _clear_cuda_cache() -> None:
    try:
        import torch
    except Exception:
        return
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        return


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






