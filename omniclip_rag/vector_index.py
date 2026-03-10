from __future__ import annotations

import os
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
        resolved_device = resolve_vector_device(self.config.vector_device)
        controller = BuildPerformanceController(self.config, resolved_device)
        iterator = iter(documents)
        table = None
        pending_rows: list[dict[str, object]] = []

        _wait_for_controls(pause_event, cancel_event)
        _emit_progress(on_progress, {"stage": "vectorizing", "current": 0, "total": total, "stage_status": "loading_model", **controller.snapshot().to_progress_payload()})
        self._load_embedder()

        def flush_rows(force: bool = False) -> float:
            nonlocal table, pending_rows
            if not pending_rows:
                return 0.0
            write_target = max(controller.current_write_batch_size, 1)
            if not force and len(pending_rows) < write_target:
                return 0.0
            flushed = 0
            started = time.perf_counter()
            while pending_rows and (force or flushed < write_target):
                batch_rows = pending_rows[:write_target]
                pending_rows = pending_rows[len(batch_rows):]
                if not batch_rows:
                    break
                if table is None:
                    self._ensure_table(len(batch_rows[0]["vector"]))
                    table = self._table()
                table.add(batch_rows)
                flushed += len(batch_rows)
                if not force:
                    break
            return max((time.perf_counter() - started) * 1000.0, 0.0)

        while True:
            _wait_for_controls(pause_event, cancel_event)
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
                    rows = self._embed_documents(batch, batch_size=min(len(batch), controller.current_encode_batch_size))
                    encode_elapsed_ms = max((time.perf_counter() - started) * 1000.0, 0.0)
                    break
                except RuntimeError as exc:
                    if _is_oom_error(exc) and controller.current_encode_batch_size > controller.min_encode_batch_size:
                        _clear_cuda_cache()
                        tuning = controller.note_oom()
                        _emit_progress(on_progress, {"stage": "vectorizing", "current": processed, "total": total, **tuning.to_progress_payload()})
                        continue
                    raise
            _wait_for_controls(pause_event, cancel_event)
            if not rows:
                continue
            pending_rows.extend(rows)
            write_elapsed_ms = flush_rows(force=False)
            processed += len(rows)
            tuning = controller.observe(encode_elapsed_ms=encode_elapsed_ms, write_elapsed_ms=write_elapsed_ms)
            _emit_progress(on_progress, {"stage": "vectorizing", "current": processed, "total": total, **tuning.to_progress_payload()})

        write_elapsed_ms = flush_rows(force=True)
        if write_elapsed_ms > 0:
            tuning = controller.observe(encode_elapsed_ms=0.0, write_elapsed_ms=write_elapsed_ms)
            _emit_progress(on_progress, {"stage": "vectorizing", "current": processed, "total": total, **tuning.to_progress_payload()})

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
        return [{**document, "vector": [float(value) for value in vector]} for document, vector in zip(documents, vectors, strict=True)]

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

        from huggingface_hub import snapshot_download
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeDependencyError(_runtime_dependency_message(self.config.vector_runtime, self.config.vector_device)) from exc

        if not is_local_model_ready(self.config, self.paths):
            snapshot_download(
                repo_id=self.config.vector_model,
                local_dir=str(local_model_dir),
                local_files_only=self.config.vector_local_files_only,
            )

        if not is_local_model_ready(self.config, self.paths):
            raise RuntimeError(
                "本地模型目录存在，但内容不完整。请先重新运行 bootstrap-model，"
                "或清理 cache/models 后重新预热。"
            )

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
        return LanceDbVectorIndex(config, paths, embedder_factory=embedder_factory)
    raise NotImplementedError(f"当前向量后端尚未接入：{config.vector_backend}")


def get_local_model_dir(config: AppConfig, paths: DataPaths) -> Path:
    return paths.cache_dir / "models" / _normalize_model_dir_name(config.vector_model)


def is_local_model_ready(config: AppConfig, paths: DataPaths) -> bool:
    return _is_model_dir_ready(get_local_model_dir(config, paths), config.vector_runtime)


def detect_acceleration() -> dict[str, object]:
    global _ACCELERATION_CACHE
    if _ACCELERATION_CACHE is not None:
        return dict(_ACCELERATION_CACHE)

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
    options = detect_acceleration().get("device_options") or ["auto", "cpu"]
    return [str(item) for item in options]


def resolve_vector_device(device_name: str | None) -> str:
    requested = (device_name or "cpu").strip().lower() or "cpu"
    acceleration = detect_acceleration()
    if requested in {"auto", "gpu"}:
        return "cuda" if acceleration.get("cuda_available") else "cpu"
    if requested == "cuda" and not acceleration.get("cuda_available"):
        return "cpu"
    return requested


def _detect_nvcc_version() -> str:
    try:
        result = subprocess.run(
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


def _runtime_dependency_message(runtime_name: str | None, device_name: str | None) -> str:
    acceleration = detect_acceleration()
    gpu_name = str(acceleration.get("gpu_name") or acceleration.get("cuda_name") or "").strip()
    nvcc_version = str(acceleration.get("nvcc_version") or "").strip()
    requested = (device_name or "auto").strip().lower() or "auto"
    runtime_name = (runtime_name or "torch").strip().lower() or "torch"
    wants_gpu = requested in {"auto", "gpu", "cuda"} and acceleration.get("gpu_present")
    recommended_profile = "cuda" if wants_gpu else "cpu"

    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
        install_script = app_dir / "InstallRuntime.ps1"
        setup_doc = app_dir / "RUNTIME_SETUP.md"
        relative_script = install_script.name if install_script.exists() else "InstallRuntime.ps1"
    else:
        app_dir = Path(__file__).resolve().parents[1]
        install_script = app_dir / "scripts" / "install_runtime.ps1"
        setup_doc = app_dir / "RUNTIME_SETUP.md"
        relative_script = ".\\scripts\\install_runtime.ps1"

    direct_command = f'PowerShell -ExecutionPolicy Bypass -File "{install_script}" -Profile {recommended_profile}'
    in_place_command = f'PowerShell -ExecutionPolicy Bypass -File "{relative_script}" -Profile {recommended_profile}'
    cpu_command = f'PowerShell -ExecutionPolicy Bypass -File "{install_script}" -Profile cpu'
    cuda_command = f'PowerShell -ExecutionPolicy Bypass -File "{install_script}" -Profile cuda'

    if recommended_profile == "cuda":
        disk_usage = "约 4.3 GB - 4.6 GB"
        download_usage = "约 3 GB - 5 GB"
    else:
        disk_usage = "约 1.3 GB - 2.0 GB"
        download_usage = "约 1 GB - 2 GB"

    state_lines: list[str] = []
    if acceleration.get("gpu_present"):
        state_lines.append(f"- 显卡：{gpu_name or 'NVIDIA GPU'}")
        if nvcc_version:
            state_lines.append(f"- 系统 CUDA：{nvcc_version}")
    else:
        state_lines.append("- 显卡：未检测到 NVIDIA GPU")

    if acceleration.get("torch_available"):
        state_lines.append(f"- 程序内 PyTorch：已安装（{acceleration.get('torch_version') or 'unknown'}）")
    else:
        state_lines.append("- 程序内 PyTorch：未安装")

    if acceleration.get("sentence_transformers_available"):
        state_lines.append("- 程序内 sentence-transformers：已安装")
    else:
        state_lines.append("- 程序内 sentence-transformers：未安装")

    state_lines.append(f"- 当前设备选择：{requested}")
    state_lines.append(f"- 当前实际设备：{resolve_vector_device(requested)}")
    if acceleration.get("torch_error"):
        state_lines.append(f"- PyTorch 导入失败：{acceleration.get('torch_error')}")
    if acceleration.get("sentence_transformers_error"):
        state_lines.append(f"- sentence-transformers 导入失败：{acceleration.get('sentence_transformers_error')}")

    setup_hint = f"说明文档：\n{setup_doc}" if setup_doc.exists() else "说明文档：RUNTIME_SETUP.md"

    lines = [
        "当前还不能开始本地语义建库或向量查询。",
        "",
        "原因",
        f"- 这个轻量发布包没有内置 {runtime_name} / sentence-transformers 这类大型运行时。",
        "- 现在缺少的不是模型目录，而是把文本编码成向量的本地运行时。",
        "",
        "当前状态",
        *state_lines,
        "",
        "怎么安装",
        "如果你已经在程序目录：",
        in_place_command,
        "",
        "如果你现在不在程序目录，直接复制完整路径命令：",
        direct_command,
        "",
        "安装后会发生什么",
        f"- 会在下列目录创建 runtime 文件夹：{app_dir}",
        "- 会安装 PyTorch、sentence-transformers 和相关依赖。",
        "- 安装完成后，重启程序，再执行全量建库、模型预热或向量查询即可。",
        "",
        "大约需要多少空间",
        f"- 最终落盘：{disk_usage}",
        f"- 网络下载：{download_usage}",
        "",
        "如果你暂时只想走 CPU",
        f"- 不需要把向量后端改成 disabled，直接安装 CPU 运行时即可：{cpu_command}",
    ]
    if acceleration.get("gpu_present") and recommended_profile != "cuda":
        lines.extend([
            "- 如果你以后想启用显卡，再改用这条命令：",
            cuda_command,
            "",
        ])
    lines.extend([
        setup_hint,
        "如果你现在完全不想安装运行时，才把“向量后端”改成 disabled，这会临时关闭向量检索。",
    ])
    return "\n".join(lines)

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
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
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

