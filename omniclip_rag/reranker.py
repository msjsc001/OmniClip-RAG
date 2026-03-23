from __future__ import annotations

import gc
import time
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .canary_backend import CANARY_RERANKER_MODEL_ID, is_canary_reranker_model, rerank_score_tensor
from .config import AppConfig, DataPaths
from .errors import RuntimeDependencyError
from .models import RerankOutcome, SearchHit
from .vector_index import (
    _clear_cuda_cache,
    _configure_huggingface_environment,
    _normalize_model_dir_name,
    _runtime_import_environment,
    _torch_cuda_peak_memory,
    _torch_cuda_reset_peak_memory,
    _torch_cuda_synchronize,
    build_repo_download_guidance_context,
    detect_acceleration,
    download_hf_repo_snapshot,
    hf_repo_cache_dir,
    resolve_vector_device,
)


class Reranker(Protocol):
    def warmup(
        self,
        *,
        allow_download: bool = False,
        download_source: str = 'official',
        download_log: Callable[[str], None] | None = None,
        warmup_after_download: bool = True,
    ) -> dict[str, object]: ...

    def rerank(self, query_text: str, hits: list[SearchHit], candidate_limit: int) -> tuple[list[SearchHit], RerankOutcome]: ...


class NullReranker:
    def __init__(self, config: AppConfig, paths: DataPaths) -> None:
        self.config = config
        self.paths = paths

    def warmup(
        self,
        *,
        allow_download: bool = False,
        download_source: str = 'official',
        download_log: Callable[[str], None] | None = None,
        warmup_after_download: bool = True,
    ) -> dict[str, object]:
        del allow_download, download_source, download_log, warmup_after_download
        return {
            'backend': 'disabled',
            'model': self.config.reranker_model,
            'model_ready': False,
            'requested_device': self.config.vector_device,
            'resolved_device': resolve_vector_device(self.config.vector_device),
        }

    def rerank(self, query_text: str, hits: list[SearchHit], candidate_limit: int) -> tuple[list[SearchHit], RerankOutcome]:
        return hits, RerankOutcome(enabled=False, applied=False, skipped_reason='disabled')


_LIVE_RERANKERS: weakref.WeakSet[CrossEncoderReranker] = weakref.WeakSet()


class CrossEncoderReranker:
    def __init__(self, config: AppConfig, paths: DataPaths, *, loader: Callable[[Path, str], object] | None = None) -> None:
        self.config = config
        self.paths = paths
        self._loader = loader or self._default_loader
        self._models: dict[str, object] = {}
        _LIVE_RERANKERS.add(self)

    def warmup(
        self,
        *,
        allow_download: bool = False,
        download_source: str = 'official',
        download_log: Callable[[str], None] | None = None,
        warmup_after_download: bool = True,
    ) -> dict[str, object]:
        local_dir = get_local_reranker_dir(self.config, self.paths)
        if allow_download and not is_local_reranker_ready(self.config, self.paths):
            self._download_model(local_dir, download_source=download_source, download_log=download_log)
        model_ready = is_local_reranker_ready(self.config, self.paths)
        requested_device = (self.config.vector_device or 'auto').strip().lower() or 'auto'
        resolved_device = resolve_vector_device(requested_device)
        if model_ready and warmup_after_download:
            if download_log is not None:
                download_log(f'开始预热重排模型：{local_dir}')
            self._load_model(resolved_device)
            if download_log is not None:
                download_log(f'重排模型预热完成：{local_dir}')
        elif model_ready and download_log is not None:
            download_log(f'重排模型目录校验通过：{local_dir}')
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
                scores, model_device, cuda_before, cuda_after, cuda_delta = self._predict(pairs, used_device, batch_size)
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
                    fallback_reason='reranker_execution_failed',
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
            model_device=model_device,
            actual_device=model_device or used_device,
            candidate_count=limit,
            reranked_count=len(rescored),
            batch_size=batch_size,
            elapsed_ms=max(int((time.perf_counter() - started_at) * 1000), 0),
            degraded_to_cpu=degraded_to_cpu,
            oom_recovered=oom_recovered,
            fallback_reason='cuda_oom_to_cpu' if degraded_to_cpu else '',
            cuda_peak_mem_before=cuda_before,
            cuda_peak_mem_after=cuda_after,
            cuda_peak_mem_delta=cuda_delta,
        )
        return rescored + suffix, outcome

    def _predict(self, pairs: list[tuple[str, str]], device: str, batch_size: int) -> tuple[list[float], str, int, int, int]:
        model = self._load_model(device)
        model_device = _resolve_model_device(model, device)
        cuda_before = 0
        cuda_after = 0
        cuda_delta = 0
        if str(model_device or device).startswith('cuda'):
            try:
                with _runtime_import_environment(component_id='semantic-core'):
                    import torch
                _torch_cuda_reset_peak_memory(torch, str(model_device or device))
                cuda_before = _torch_cuda_peak_memory(torch, str(model_device or device))
                raw_scores = model.predict(pairs, batch_size=batch_size, show_progress_bar=False)
                _torch_cuda_synchronize(torch, str(model_device or device))
                cuda_after = _torch_cuda_peak_memory(torch, str(model_device or device))
                cuda_delta = max(int(cuda_after) - int(cuda_before), 0)
            except Exception:
                raw_scores = model.predict(pairs, batch_size=batch_size, show_progress_bar=False)
        else:
            raw_scores = model.predict(pairs, batch_size=batch_size, show_progress_bar=False)
        return [float(value) for value in raw_scores], str(model_device or device), cuda_before, cuda_after, cuda_delta

    def _load_model(self, device: str):
        cached = self._models.get(device)
        if cached is not None:
            return cached
        local_dir = get_local_reranker_dir(self.config, self.paths)
        model = self._loader(local_dir, device)
        self._models[device] = model
        return model

    def _release_models(self) -> None:
        for model in list(self._models.values()):
            try:
                inner_model = getattr(model, 'model', None)
                if callable(getattr(inner_model, 'cpu', None)):
                    inner_model.cpu()
                elif callable(getattr(model, 'cpu', None)):
                    model.cpu()
            except Exception:
                continue
        self._models.clear()

    def _download_model(
        self,
        local_dir: Path,
        *,
        download_source: str = 'official',
        download_log: Callable[[str], None] | None = None,
    ) -> None:
        download_hf_repo_snapshot(
            repo_id=self.config.reranker_model,
            local_dir=local_dir,
            hf_home_dir=self.paths.cache_dir / 'models' / '_hf_home',
            local_files_only=False,
            download_source=download_source,
            download_log=download_log,
            missing_dependency_message='当前还缺少 huggingface-hub 运行时，暂时不能下载重排模型缓存。',
        )

    def _default_loader(self, local_dir: Path, device: str):
        _configure_huggingface_environment(self.paths.cache_dir / 'models' / '_hf_home')
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise RuntimeDependencyError('当前还缺少 sentence-transformers 运行时，无法启用 reranker。') from exc
        return CrossEncoder(str(local_dir), device=device, local_files_only=True)

    def _initial_batch_size(self, device: str) -> int:
        if device == 'cuda':
            return max(int(self.config.reranker_batch_size_cuda or 8), 1)
        return max(int(self.config.reranker_batch_size_cpu or 4), 1)


class CanaryTorchReranker:
    def __init__(self, config: AppConfig, paths: DataPaths) -> None:
        self.config = config
        self.paths = paths

    def warmup(
        self,
        *,
        allow_download: bool = False,
        download_source: str = 'official',
        download_log: Callable[[str], None] | None = None,
        warmup_after_download: bool = True,
    ) -> dict[str, object]:
        del allow_download, download_source, download_log, warmup_after_download
        requested_device = (self.config.vector_device or 'auto').strip().lower() or 'auto'
        resolved_device = resolve_vector_device(requested_device)
        return {
            'backend': 'canary-cross-encoder',
            'model': self.config.reranker_model,
            'model_ready': True,
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
        try:
            with _runtime_import_environment(component_id='semantic-core'):
                import torch
            device_name = 'cuda:0' if resolved_device == 'cuda' else 'cpu'
            cuda_before = 0
            if device_name.startswith('cuda'):
                _torch_cuda_reset_peak_memory(torch, device_name)
                cuda_before = _torch_cuda_peak_memory(torch, device_name)
            scores = [
                float(rerank_score_tensor(torch, query_text, _compact_hit_text(hit, self.config.reranker_max_chars), device=device_name).item())
                for hit in hits[:limit]
            ]
            if device_name.startswith('cuda'):
                _torch_cuda_synchronize(torch, device_name)
                cuda_after = _torch_cuda_peak_memory(torch, device_name)
                cuda_delta = max(int(cuda_after) - int(cuda_before), 0)
            else:
                cuda_after = 0
                cuda_delta = 0
        except Exception as exc:
            return hits, RerankOutcome(
                enabled=True,
                applied=False,
                model=self.config.reranker_model,
                requested_device=requested_device,
                resolved_device=resolved_device,
                candidate_count=limit,
                reranked_count=0,
                skipped_reason=exc.__class__.__name__,
                fallback_reason='reranker_execution_failed',
            )
        normalized_scores = _normalize_rerank_scores(scores)
        rescored: list[SearchHit] = []
        for hit, rerank_score in zip(hits[:limit], normalized_scores, strict=True):
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
                    source_family=hit.source_family,
                    source_kind=hit.source_kind,
                    source_label=hit.source_label,
                    page_no=hit.page_no,
                )
            )
        rescored.sort(key=lambda item: item.score, reverse=True)
        return rescored + list(hits[limit:]), RerankOutcome(
            enabled=True,
            applied=True,
            model=self.config.reranker_model,
            requested_device=requested_device,
            resolved_device=resolved_device,
            model_device=device_name,
            actual_device=device_name,
            candidate_count=limit,
            reranked_count=len(rescored),
            batch_size=max(int(self.config.reranker_batch_size_cuda if device_name.startswith('cuda') else self.config.reranker_batch_size_cpu), 1),
            elapsed_ms=0,
            degraded_to_cpu=False,
            oom_recovered=False,
            fallback_reason='',
            cuda_peak_mem_before=cuda_before,
            cuda_peak_mem_after=cuda_after,
            cuda_peak_mem_delta=cuda_delta,
        )


def create_reranker(config: AppConfig, paths: DataPaths, *, loader: Callable[[Path, str], object] | None = None) -> Reranker:
    if not getattr(config, 'reranker_enabled', False):
        return NullReranker(config, paths)
    if is_canary_reranker_model(config.reranker_model):
        return CanaryTorchReranker(config, paths)
    return CrossEncoderReranker(config, paths, loader=loader)


def release_process_reranker_resources(*, clear_cuda: bool = True) -> None:
    for reranker in list(_LIVE_RERANKERS):
        try:
            reranker._release_models()
        except Exception:
            continue
    try:
        gc.collect()
    except Exception:
        pass
    if clear_cuda:
        _clear_cuda_cache()


def get_local_reranker_dir(config: AppConfig, paths: DataPaths) -> Path:
    return paths.cache_dir / 'models' / _normalize_model_dir_name(config.reranker_model)


def reranker_download_guidance_context(config: AppConfig, paths: DataPaths) -> dict[str, str]:
    model_root = paths.cache_dir / 'models'
    model_dir = get_local_reranker_dir(config, paths)
    hf_home_dir = model_root / '_hf_home'
    model_root.mkdir(parents=True, exist_ok=True)
    return build_repo_download_guidance_context(config.reranker_model, model_dir, hf_home_dir)


def is_local_reranker_ready(config: AppConfig, paths: DataPaths) -> bool:
    if is_canary_reranker_model(config.reranker_model):
        return True
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


def get_local_reranker_repo_cache_dir(config: AppConfig, paths: DataPaths) -> Path:
    return hf_repo_cache_dir(paths.cache_dir / 'models' / '_hf_home', config.reranker_model)


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


def _resolve_model_device(model: object, fallback_device: str) -> str:
    direct = getattr(model, 'device', None)
    if direct:
        return str(direct)
    nested_model = getattr(model, 'model', None)
    nested_device = getattr(nested_model, 'device', None)
    if nested_device:
        return str(nested_device)
    target_device = getattr(model, '_target_device', None)
    if target_device:
        return str(target_device)
    return str(fallback_device or 'cpu')


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
