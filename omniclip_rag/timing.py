from __future__ import annotations

import json
from pathlib import Path

from .config import AppConfig
from .vector_index import resolve_vector_device

HISTORY_LIMIT = 8


def build_history_file(state_dir: Path) -> Path:
    return state_dir / 'build_history.json'


def load_build_history(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return []
    if isinstance(payload, dict):
        entries = payload.get('entries', [])
    elif isinstance(payload, list):
        entries = payload
    else:
        entries = []
    return [item for item in entries if isinstance(item, dict)]


def append_build_history(path: Path, entry: dict[str, object]) -> None:
    entries = load_build_history(path)
    entries.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {'entries': entries[-HISTORY_LIMIT:]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def find_matching_history(path: Path, config: AppConfig) -> dict[str, object] | None:
    resolved_device = resolve_vector_device(config.vector_device)
    for entry in reversed(load_build_history(path)):
        if str(entry.get('vector_backend') or '') != str(config.vector_backend or ''):
            continue
        if str(entry.get('vector_model') or '') != str(config.vector_model or ''):
            continue
        if str(entry.get('vector_runtime') or '') != str(config.vector_runtime or ''):
            continue
        if str(entry.get('resolved_device') or '') != resolved_device:
            continue
        return entry
    return None


def build_timing_profile(
    config: AppConfig,
    *,
    history_entry: dict[str, object] | None,
    vector_enabled: bool,
    model_ready: bool,
) -> dict[str, float | bool | str]:
    resolved_device = resolve_vector_device(config.vector_device)
    lowered_model = (config.vector_model or '').lower()
    runtime = (config.vector_runtime or 'torch').lower()

    parse_per_file = 0.12
    render_per_chunk = 0.005
    vector_per_chunk = 0.0
    vector_load_seconds = 0.0

    if vector_enabled:
        if 'bge-m3' in lowered_model:
            if resolved_device == 'cuda':
                vector_per_chunk = 0.018
                vector_load_seconds = 8.0 if model_ready else 18.0
            elif runtime == 'onnx':
                vector_per_chunk = 0.16
                vector_load_seconds = 10.0 if model_ready else 22.0
            else:
                vector_per_chunk = 0.34
                vector_load_seconds = 16.0 if model_ready else 30.0
        else:
            if resolved_device == 'cuda':
                vector_per_chunk = 0.012
                vector_load_seconds = 6.0 if model_ready else 14.0
            else:
                vector_per_chunk = 0.20
                vector_load_seconds = 12.0 if model_ready else 24.0

    history_based = False
    if history_entry:
        files = max(int(history_entry.get('files', 0) or 0), 1)
        chunks = max(int(history_entry.get('chunks', 0) or 0), 1)
        parse_per_file = _clamp_rate(float(history_entry.get('indexing_seconds', 0.0) or 0.0) / files, parse_per_file)
        render_per_chunk = _clamp_rate(float(history_entry.get('rendering_seconds', 0.0) or 0.0) / chunks, render_per_chunk)
        if vector_enabled:
            vector_per_chunk = _clamp_rate(float(history_entry.get('vectorizing_seconds', 0.0) or 0.0) / chunks, vector_per_chunk)
            vector_load_seconds = max(float(history_entry.get('vector_load_seconds', 0.0) or 0.0), vector_load_seconds * 0.5)
        history_based = True

    return {
        'resolved_device': resolved_device,
        'parse_per_file': parse_per_file,
        'render_per_chunk': render_per_chunk,
        'vector_per_chunk': vector_per_chunk,
        'vector_load_seconds': vector_load_seconds if vector_enabled else 0.0,
        'history_based': history_based,
    }


def estimate_total_build_seconds(
    config: AppConfig,
    file_count: int,
    chunk_count: int,
    *,
    vector_enabled: bool,
    model_ready: bool,
    history_entry: dict[str, object] | None,
) -> tuple[int, bool]:
    profile = build_timing_profile(
        config,
        history_entry=history_entry,
        vector_enabled=vector_enabled,
        model_ready=model_ready,
    )
    total_seconds = (
        max(file_count, 0) * float(profile['parse_per_file'])
        + max(chunk_count, 0) * float(profile['render_per_chunk'])
        + max(chunk_count, 0) * float(profile['vector_per_chunk'])
        + float(profile['vector_load_seconds'])
    )
    return max(6, int(round(total_seconds))), bool(profile['history_based'])


def estimate_remaining_build_seconds(
    config: AppConfig,
    *,
    stage: str,
    current: int,
    total: int,
    elapsed_total: float,
    stage_elapsed: float,
    parsed_chunks: int = 0,
    estimated_total_chunks: int = 0,
    history_entry: dict[str, object] | None,
    vector_enabled: bool,
    model_ready: bool,
) -> tuple[int, float]:
    profile = build_timing_profile(
        config,
        history_entry=history_entry,
        vector_enabled=vector_enabled,
        model_ready=model_ready,
    )
    parse_rate = float(profile['parse_per_file'])
    render_rate = float(profile['render_per_chunk'])
    vector_rate = float(profile['vector_per_chunk'])
    vector_load = float(profile['vector_load_seconds']) if vector_enabled else 0.0
    elapsed_total = max(float(elapsed_total), 0.1)
    stage_elapsed = max(float(stage_elapsed), 0.0)
    current = max(int(current), 0)
    total = max(int(total), 0)
    estimated_total_chunks = max(int(estimated_total_chunks or 0), int(parsed_chunks or 0), int(total if stage in {'rendering', 'vectorizing'} else 0))

    if stage == 'indexing':
        observed_rate = (stage_elapsed / current) if current > 0 else 0.0
        parse_rate = _blend_rate(parse_rate, observed_rate)
        remaining = max(total - current, 0) * parse_rate
        remaining += estimated_total_chunks * render_rate
        remaining += estimated_total_chunks * vector_rate
        if vector_enabled:
            remaining += vector_load
    elif stage == 'rendering':
        observed_rate = (stage_elapsed / current) if current > 0 else 0.0
        render_rate = _blend_rate(render_rate, observed_rate)
        remaining = max(total - current, 0) * render_rate
        remaining += max(total, estimated_total_chunks) * vector_rate
        if vector_enabled:
            remaining += vector_load
    elif stage == 'vectorizing':
        observed_rate = (stage_elapsed / current) if current > 0 else 0.0
        vector_rate = _blend_rate(vector_rate, observed_rate)
        remaining = max(total - current, 0) * vector_rate
        if vector_enabled and current <= 0:
            remaining += vector_load
    else:
        remaining = 0.0

    remaining = max(0.0, remaining)
    total_estimate = elapsed_total + remaining
    percent = 100.0 if total_estimate <= 0 else min((elapsed_total / total_estimate) * 100.0, 100.0)
    return int(round(remaining)), percent


def _blend_rate(default_rate: float, observed_rate: float) -> float:
    if observed_rate <= 0:
        return default_rate
    lower = max(default_rate * 0.25, 0.001)
    upper = max(default_rate * 4.0, lower)
    observed = min(max(observed_rate, lower), upper)
    return default_rate * 0.35 + observed * 0.65


def _clamp_rate(value: float, fallback: float) -> float:
    if value <= 0:
        return fallback
    lower = max(fallback * 0.2, 0.001)
    upper = max(fallback * 6.0, lower)
    return min(max(value, lower), upper)
