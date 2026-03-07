from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .config import AppConfig, DataPaths


@dataclass(slots=True)
class VectorCandidate:
    chunk_id: str
    score: float


class Embedder(Protocol):
    def encode(self, texts: list[str], *, batch_size: int = 16, show_progress_bar: bool = False, normalize_embeddings: bool = True): ...


class VectorIndex(Protocol):
    def rebuild(self, documents: list[dict[str, str]]) -> None: ...

    def upsert(self, documents: list[dict[str, str]]) -> None: ...

    def delete(self, chunk_ids: list[str]) -> None: ...

    def search(self, query_text: str, limit: int) -> list[VectorCandidate]: ...

    def warmup(self) -> dict[str, object]: ...

    def reset(self) -> None: ...


class NullVectorIndex:
    def rebuild(self, documents: list[dict[str, str]]) -> None:
        return None

    def upsert(self, documents: list[dict[str, str]]) -> None:
        return None

    def delete(self, chunk_ids: list[str]) -> None:
        return None

    def search(self, query_text: str, limit: int) -> list[VectorCandidate]:
        return []

    def warmup(self) -> dict[str, object]:
        return {"backend": "disabled", "model": None, "dimension": 0}

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

    def rebuild(self, documents: list[dict[str, str]]) -> None:
        self.reset()
        if not documents:
            return
        rows = self._embed_documents(documents)
        self._ensure_table(len(rows[0]["vector"]))
        self._table().add(rows)

    def upsert(self, documents: list[dict[str, str]]) -> None:
        if not documents:
            return
        rows = self._embed_documents(documents)
        self._ensure_table(len(rows[0]["vector"]))
        chunk_ids = [row["chunk_id"] for row in rows]
        self.delete(chunk_ids)
        self._table().add(rows)

    def delete(self, chunk_ids: list[str]) -> None:
        if not chunk_ids or not self._table_exists():
            return
        quoted = ", ".join(f"'{self._escape(value)}'" for value in chunk_ids)
        self._table().delete(f"chunk_id IN ({quoted})")

    def search(self, query_text: str, limit: int) -> list[VectorCandidate]:
        if not query_text.strip() or not self._table_exists():
            return []
        vector = self._encode([query_text])[0]
        results = self._table().search(vector).limit(limit).to_list()
        return [
            VectorCandidate(
                chunk_id=row["chunk_id"],
                score=_distance_to_score(row.get("_distance", 1.0)),
            )
            for row in results
        ]

    def warmup(self) -> dict[str, object]:
        vector = self._encode(["模型预热"])[0]
        return {
            "backend": "lancedb",
            "model": self.config.vector_model,
            "dimension": len(vector),
            "local_model_dir": str(get_local_model_dir(self.config, self.paths)),
            "model_ready": is_local_model_ready(self.config, self.paths),
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
                vector_field = schema.field("vector")
                self._vector_dimension = vector_field.type.list_size
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

    def _embed_documents(self, documents: list[dict[str, str]]) -> list[dict[str, object]]:
        texts = [item["rendered_text"] for item in documents]
        vectors = self._encode(texts)
        rows: list[dict[str, object]] = []
        for document, vector in zip(documents, vectors, strict=True):
            rows.append({**document, "vector": [float(value) for value in vector]})
        return rows

    def _encode(self, texts: list[str]):
        embedder = self._load_embedder()
        return embedder.encode(
            texts,
            batch_size=self.config.vector_batch_size,
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
        from sentence_transformers import SentenceTransformer

        if not is_local_model_ready(self.config, self.paths) or not self.config.vector_local_files_only:
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

        return SentenceTransformer(
            str(local_model_dir),
            device=self.config.vector_device,
            cache_folder=str(runtime_cache_dir),
            backend=self.config.vector_runtime,
            local_files_only=True,
        )

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("'", "''")


# Why: 先固化接口边界，后面调模型或 reranker 只动这层，不污染 parser / storage / service。
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
