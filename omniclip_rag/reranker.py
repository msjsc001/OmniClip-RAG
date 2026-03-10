from __future__ import annotations

import gc
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .config import AppConfig, DataPaths
from .errors import RuntimeDependencyError
from .models import RerankOutcome, SearchHit
from .vector_index import _configure_huggingface_environment, _normalize_model_dir_name, detect_acceleration, resolve_vector_device


class Reranker(Protocol):
    def warmup(self, *, allow_download: bool = False) -> dict[str, object]: ...

    def rerank(self, query_text: str, hits: list[SearchHit], candidate_limit: int) -> tuple[list[SearchHit], RerankOutcome]: ...


class NullReranker:
    def __init__(self, config: AppConfig, paths: DataPaths) -> None:
        self.config = config
        self.paths = paths

    def warmup(self, *, allow_download: bool = False) -> dict[str, object]:
        return {
            'backend': 'disabled',
            'model': self.config.reranker_model,
            'model_ready': False,
            'requested_device': self.config.vector_device,
            'resolved_device': resolve_vector_device(self.config.vector_device),
        }

    def rerank(self, query_text: str, hits: list[SearchHit], candidate_limit: int) -> tuple[list[SearchHit], RerankOutcome]:
        return hits, RerankOutcome(enabled=False, applied=False, skipped_reason='disabled')


class CrossEncoderReranker:
    def __init__(self, config: AppConfig, paths: DataPaths, *, loader: Callable[[Path, str], object] | None = None) -> None:
        self.config = config
        self.paths = paths
        self._loader = loader or self._default_loader
        self._models: dict[str, object] = {}

    def warmup(self, *, allow_download: bool = False) -> dict[str, object]:
        local_dir = get_local_reranker_dir(self.config, self.paths)
        if allow_download and not is_local_reranker_ready(self.config, self.paths):
            self._download_model(local_dir)
        model_ready = is_local_reranker_ready(self.config, self.paths)
        requested_device = (self.config.vector_device or 'auto').strip().lower() or 'auto'
        resolved_device = resolve_vector_device(requested_device)
        if model_ready:
            self._load_model(resolved_device)
        return {
            'backend': 'cross-encoder',
            'model': self.config.reranker_model,
            'model_ready': model_ready,
            'requested_device': requested_device,
            'resolved_device': resolved_device,
            **detect_acceleration(),
        }

    def rerank(self, query_text: str, hits: list[SearchHit], candidate_limit: int) -> tuple[list[SearchHit], RerankOutcome]:
        limit = max(min(int(candidate_limit or 0), len(hits)), 0)
        requested_device = (self.config.vector_device or 'auto').strip().lower() or 'auto'
        resolved_device = resolve_vector_device(requested_device)
        if limit <= 1:
            return hits, RerankOutcome(
                enabled=True,
                applied=False,
                model=self.config.reranker_model,
                requested_device=requested_device,
                resolved_device=resolved_device,
                candidate_count=limit,
                reranked_count=limit,
                skipped_reason='insufficient_candidates',
            )
        if not is_local_reranker_ready(self.config, self.paths):
            return hits, RerankOutcome(
                enabled=True,
                applied=False,
                model=self.config.reranker_model,
                requested_device=requested_device,
                resolved_device=resolved_device,
                candidate_count=limit,
                reranked_count=0,
                skipped_reason='model_missing',
            )

        started_at = time.perf_counter()
        prefix = list(hits[:limit])
        suffix = list(hits[limit:])
        pairs = [(query_text, _compact_hit_text(hit, self.config.reranker_max_chars)) for hit in prefix]
        used_device = resolved_device
        batch_size = self._initial_batch_size(resolved_device)
        degraded_to_cpu = False
        oom_recovered = False
        scores: list[float] | None = None

        while True:
            try:
                scores = self._predict(pairs, used_device, batch_size)
                break
            except Exception as exc:
                if _is_oom_error(exc) and used_device == 'cuda':
                    oom_recovered = True
                    _clear_cuda_cache()
                    if batch_size > 1:
                        batch_size = max(batch_size // 2, 1)
                        continue
                    if requested_device != 'cpu':
                        used_device = 'cpu'
                        degraded_to_cpu = True
                        batch_size = self._initial_batch_size('cpu')
                        continue
                return hits, RerankOutcome(
                    enabled=True,
                    applied=False,
                    model=self.config.reranker_model,
                    requested_device=requested_device,
                    resolved_device=used_device,
                    candidate_count=limit,
                    reranked_count=0,
                    batch_size=batch_size,
                    elapsed_ms=max(int((time.perf_counter() - started_at) * 1000), 0),
                    degraded_to_cpu=degraded_to_cpu,
                    oom_recovered=oom_recovered,
                    skipped_reason=type(exc).__name__,
                )

        normalized_scores = _normalize_rerank_scores(scores or [])
        rescored: list[SearchHit] = []
        for hit, rerank_score in zip(prefix, normalized_scores, strict=True):
            combined = hit.score * 0.35 + rerank_score * 0.65
            rescored.append(
                SearchHit(
                    score=max(0.0, min(combined, 100.0)),
                    title=hit.title,
                    anchor=hit.anchor,
                    source_path=hit.source_path,
                    rendered_text=hit.rendered_text,
                    chunk_id=hit.chunk_id,
                    display_text=hit.display_text,
                    preview_text=hit.preview_text,
                    reason=hit.reason,
                )
            )
        rescored.sort(key=lambda item: item.score, reverse=True)
        outcome = RerankOutcome(
            enabled=True,
            applied=True,
            model=self.config.reranker_model,
            requested_device=requested_device,
            resolved_device=used_device,
            candidate_count=limit,
            reranked_count=len(rescored),
            batch_size=batch_size,
            elapsed_ms=max(int((time.perf_counter() - started_at) * 1000), 0),
            degraded_to_cpu=degraded_to_cpu,
            oom_recovered=oom_recovered,
        )
        return rescored + suffix, outcome

    def _predict(self, pairs: list[tuple[str, str]], device: str, batch_size: int) -> list[float]:
        model = self._load_model(device)
        raw_scores = model.predict(pairs, batch_size=batch_size, show_progress_bar=False)
        return [float(value) for value in raw_scores]

    def _load_model(self, device: str):
        cached = self._models.get(device)
        if cached is not None:
            return cached
        local_dir = get_local_reranker_dir(self.config, self.paths)
        model = self._loader(local_dir, device)
        self._models[device] = model
        return model

    def _download_model(self, local_dir: Path) -> None:
        local_dir.parent.mkdir(parents=True, exist_ok=True)
        _configure_huggingface_environment(self.paths.cache_dir / 'models' / '_hf_home')
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id=self.config.reranker_model,
            local_dir=str(local_dir),
            local_files_only=False,
        )

    def _default_loader(self, local_dir: Path, device: str):
        _configure_huggingface_environment(self.paths.cache_dir / 'models' / '_hf_home')
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise RuntimeDependencyError('当前还缺少 sentence-transformers 运行时，无法启用 reranker。') from exc
        return CrossEncoder(str(local_dir), device=device, automodel_args={'local_files_only': True})

    def _initial_batch_size(self, device: str) -> int:
        if device == 'cuda':
            return max(int(self.config.reranker_batch_size_cuda or 8), 1)
        return max(int(self.config.reranker_batch_size_cpu or 4), 1)


def create_reranker(config: AppConfig, paths: DataPaths, *, loader: Callable[[Path, str], object] | None = None) -> Reranker:
    if not getattr(config, 'reranker_enabled', False):
        return NullReranker(config, paths)
    return CrossEncoderReranker(config, paths, loader=loader)


def get_local_reranker_dir(config: AppConfig, paths: DataPaths) -> Path:
    return paths.cache_dir / 'models' / _normalize_model_dir_name(config.reranker_model)


def is_local_reranker_ready(config: AppConfig, paths: DataPaths) -> bool:
    path = get_local_reranker_dir(config, paths)
    if not path.exists() or not (path / 'config.json').exists():
        return False
    weight_files = (
        path / 'pytorch_model.bin',
        path / 'model.safetensors',
        path / 'pytorch_model.bin.index.json',
        path / 'model.safetensors.index.json',
    )
    return any(candidate.exists() for candidate in weight_files)


def _compact_hit_text(hit: SearchHit, limit: int) -> str:
    prefix = f"标题：{hit.title}\n路径：{hit.anchor}\n内容：{hit.display_text or hit.rendered_text}".strip()
    if len(prefix) <= max(limit, 0):
        return prefix
    return prefix[: max(limit - 1, 0)].rstrip() + '…'


def _normalize_rerank_scores(scores: list[float]) -> list[float]:
    if not scores:
        return []
    minimum = min(scores)
    maximum = max(scores)
    if abs(maximum - minimum) < 1e-9:
        return [60.0 for _ in scores]
    return [20.0 + ((value - minimum) / (maximum - minimum)) * 80.0 for value in scores]


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
    gc.collect()
