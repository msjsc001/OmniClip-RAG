from __future__ import annotations

import json
import os
import shutil
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .config import AppConfig, DataPaths
from .errors import BuildCancelledError
from .models import SpaceEstimate
from .parser import parse_markdown_file
from .ui_i18n import normalize_language
from .timing import build_history_file, estimate_total_build_seconds, find_matching_history
from .vector_index import is_local_model_ready


GIB = 1024 ** 3
MIB = 1024 ** 2


_PRECHECK_TEXTS = {
    "zh-CN": {
        "parsed": "已对 {readable} / {scanned} 个 Markdown 文件做解析级预估，不是只按文件体积拍脑袋。",
        "target_disk": "目标写入盘：{target}",
        "density": "Logseq 文件占比约 {ratio:.0%}，引用密度 {density:.2f}。",
        "vector": "向量后端：{backend}，模型：{model}。",
        "build_time": "预计首轮全量建库时间约 {build_time}。",
        "build_time_with_download": "预计首轮全量建库时间约 {build_time}；如果本地还没有模型，再额外预留约 {download_time} 用于首次下载。",
        "build_time_history": "本次建库时间优先参考了当前笔记库最近一次真实建库记录，比纯公式预估更接近实际。",
        "bge_m3": "bge-m3 在 Windows 本地首轮落盘按保守值估算，建议至少预留 8-10 GB 空闲。",
        "space_blocked": "可用空间低于预估需求，默认不建议直接开始建库。",
        "space_tight": "空间刚够，但余量偏紧，建议先清理磁盘再跑首轮建库。",
        "space_ok": "空间余量充足，可以开始首轮建库。",
        "local_only_empty": "当前启用了 vector_local_files_only，但本地模型缓存为空，首轮向量建库会直接失败。",
        "local_only_incomplete": "当前启用了 vector_local_files_only，但本地模型缓存不完整，建议先重新运行 bootstrap-model。",
        "skipped": "已跳过 {count} 个不可读 Markdown 文件，不会阻断流程。示例：{example}",
        "all_skipped": "扫描到的 Markdown 文件全部不可读，当前目录不适合作为笔记库根目录。建议改用真正的笔记目录。",
    },
    "en": {
        "parsed": "Estimated by fully parsing {readable} / {scanned} Markdown files instead of guessing only from raw file size.",
        "target_disk": "Target disk for writes: {target}",
        "density": "Logseq ratio is about {ratio:.0%}, with reference density {density:.2f}.",
        "vector": "Vector backend: {backend}, model: {model}.",
        "build_time": "Estimated first full build time: about {build_time}.",
        "build_time_with_download": "Estimated first full build time: about {build_time}; if the model is not cached yet, keep about {download_time} extra for the first download.",
        "build_time_history": "This build-time estimate is based primarily on the most recent real build of the current vault, so it should be closer to reality than a fixed formula.",
        "bge_m3": "For bge-m3 on Windows, the first local download is estimated conservatively. Keep at least 8-10 GB free when possible.",
        "space_blocked": "Available space is below the estimated requirement, so starting immediately is not recommended.",
        "space_tight": "Space is only barely enough. Cleaning disk space before the first full index build is recommended.",
        "space_ok": "Disk headroom looks healthy and the first full build can proceed.",
        "local_only_empty": "vector_local_files_only is enabled, but the local model cache is empty, so the first vector build would fail immediately.",
        "local_only_incomplete": "vector_local_files_only is enabled, but the local model cache is incomplete. Run bootstrap-model again first.",
        "skipped": "Skipped {count} unreadable Markdown files without aborting the workflow. Example: {example}",
        "all_skipped": "All discovered Markdown files were unreadable. The current folder is not a suitable vault root. Choose the real note directory instead.",
    },
}


def estimate_storage_for_vault(
    config: AppConfig,
    paths: DataPaths,
    files: list[Path] | None = None,
    *,
    on_progress: Callable[[dict[str, object]], None] | None = None,
    pause_event: threading.Event | None = None,
    cancel_event: threading.Event | None = None,
) -> SpaceEstimate:
    files = files or _scan_vault(config, on_progress=on_progress, pause_event=pause_event, cancel_event=cancel_event)
    language = normalize_language(config.ui_language)
    labels = _PRECHECK_TEXTS.get(language, _PRECHECK_TEXTS["en"])

    vault_total_bytes = 0
    parsed_chunk_count = 0
    ref_count = 0
    logseq_file_count = 0
    markdown_file_count = 0
    raw_chunk_bytes = 0
    anchor_bytes = 0
    title_bytes = 0
    property_bytes = 0
    skipped_files: list[str] = []

    total_files = len(files)
    for index, path in enumerate(files, start=1):
        _wait_for_preflight_controls(pause_event, cancel_event)
        relative_path = _safe_relative_path(config.vault_dir, path)
        try:
            parsed = parse_markdown_file(config.vault_dir, path)
        except OSError:
            skipped_files.append(relative_path)
        else:
            vault_total_bytes += parsed.size
            parsed_chunk_count += len(parsed.chunks)
            ref_count += sum(len(chunk.refs) for chunk in parsed.chunks)
            if parsed.kind == "logseq":
                logseq_file_count += 1
            else:
                markdown_file_count += 1
            raw_chunk_bytes += sum(_encoded_len(chunk.raw_text) for chunk in parsed.chunks)
            anchor_bytes += sum(_encoded_len(chunk.anchor) for chunk in parsed.chunks)
            title_bytes += sum(_encoded_len(chunk.title) for chunk in parsed.chunks)
            property_bytes += _encoded_len(json.dumps(parsed.page_properties, ensure_ascii=False))
            property_bytes += sum(_encoded_len(json.dumps(chunk.properties, ensure_ascii=False)) for chunk in parsed.chunks)
        if on_progress is not None and (total_files <= 64 or index == total_files or index % 8 == 0):
            on_progress({
                'stage': 'preflight',
                'current': index,
                'total': total_files,
                'current_path': relative_path,
                'overall_percent': (index / max(total_files, 1)) * 92.0,
            })

    scanned_file_count = len(files)
    file_count = logseq_file_count + markdown_file_count
    ref_density = ref_count / max(parsed_chunk_count, 1)
    logseq_ratio = logseq_file_count / max(file_count, 1)
    rendered_multiplier = 1.12 + min(ref_density * 0.55, 0.95) + (0.10 if logseq_ratio >= 0.4 else 0.0)
    estimated_rendered_bytes = int(raw_chunk_bytes * rendered_multiplier + anchor_bytes + title_bytes + property_bytes * 0.8)

    estimated_sqlite_bytes = (
        raw_chunk_bytes
        + estimated_rendered_bytes
        + anchor_bytes
        + title_bytes
        + property_bytes
        + parsed_chunk_count * 768
        + ref_count * 144
        + file_count * 384
    )
    estimated_fts_bytes = int(max(estimated_rendered_bytes * 1.08, vault_total_bytes * 0.65) + parsed_chunk_count * 256)

    vector_backend = (config.vector_backend or "disabled").strip().lower()
    vector_enabled = vector_backend not in {"", "disabled", "none", "off"}
    vector_dimension = _estimate_vector_dimension(config.vector_model)
    estimated_vector_bytes = 0
    estimated_model_bytes = 0
    if vector_enabled:
        estimated_vector_bytes = int(parsed_chunk_count * (vector_dimension * 4 + 1536) + estimated_rendered_bytes * 0.22)
        estimated_model_bytes = estimate_model_cache_bytes(config.vector_model, config.vector_runtime)

    model_ready = is_local_model_ready(config, paths)
    history_entry = find_matching_history(build_history_file(paths.state_dir), config)
    estimated_build_seconds, build_time_history = estimate_total_build_seconds(
        config,
        file_count,
        parsed_chunk_count,
        vector_enabled=vector_enabled,
        model_ready=model_ready,
        history_entry=history_entry,
    )
    estimated_download_seconds = _estimate_download_duration_seconds(config.vector_model, config.vector_runtime) if vector_enabled and not model_ready else 0

    if on_progress is not None:
        on_progress({
            'stage': 'preflight_finalize',
            'current': 0,
            'total': 0,
            'current_path': str(paths.state_dir),
            'overall_percent': 95.0,
        })
    current_state_bytes = _directory_size(
        paths.state_dir,
        pause_event=pause_event,
        cancel_event=cancel_event,
        on_progress=on_progress,
        stage='preflight_finalize',
        overall_percent=95.0,
    )
    model_cache_dir = paths.cache_dir / "models"
    if on_progress is not None:
        on_progress({
            'stage': 'preflight_finalize',
            'current': 0,
            'total': 0,
            'current_path': str(model_cache_dir),
            'overall_percent': 98.0,
        })
    current_model_cache_bytes = _directory_size(
        model_cache_dir,
        pause_event=pause_event,
        cancel_event=cancel_event,
        on_progress=on_progress,
        stage='preflight_finalize',
        overall_percent=98.0,
    )

    additional_index_bytes = max(estimated_sqlite_bytes + estimated_fts_bytes + estimated_vector_bytes - current_state_bytes, 0)
    additional_model_bytes = max(estimated_model_bytes - current_model_cache_bytes, 0)
    estimated_peak_temp_bytes = max(
        int(estimated_rendered_bytes * 0.18 + estimated_vector_bytes * 0.12),
        int(1536 * MIB) if vector_enabled else int(256 * MIB),
    )
    safety_margin_bytes = max(int(vault_total_bytes * 0.25), int(estimated_sqlite_bytes * 0.12), int(1 * GIB))

    available_free_bytes = shutil.disk_usage(paths.global_root).free
    required_free_bytes = additional_index_bytes + additional_model_bytes + estimated_peak_temp_bytes + safety_margin_bytes

    notes = [
        labels["parsed"].format(readable=file_count, scanned=scanned_file_count),
        labels["target_disk"].format(target=paths.global_root.drive or paths.global_root),
        labels["density"].format(ratio=logseq_ratio, density=ref_density),
        labels["vector"].format(backend=config.vector_backend, model=config.vector_model),
    ]
    if skipped_files:
        notes.append(labels["skipped"].format(count=len(skipped_files), example=skipped_files[0]))
    if vector_enabled and "bge-m3" in (config.vector_model or "").lower():
        notes.append(labels["bge_m3"])
    if build_time_history:
        notes.append(labels["build_time_history"])
    if estimated_download_seconds > 0:
        notes.append(labels["build_time_with_download"].format(build_time=_format_duration(estimated_build_seconds), download_time=_format_duration(estimated_download_seconds)))
    else:
        notes.append(labels["build_time"].format(build_time=_format_duration(estimated_build_seconds)))

    can_proceed = available_free_bytes >= required_free_bytes
    risk_level = "ok"
    if file_count == 0 and scanned_file_count > 0:
        can_proceed = False
        risk_level = "blocked"
        notes.append(labels["all_skipped"])
    elif not can_proceed:
        risk_level = "insufficient"
        notes.append(labels["space_blocked"])
    elif available_free_bytes < int(required_free_bytes * 1.2):
        risk_level = "tight"
        notes.append(labels["space_tight"])
    else:
        notes.append(labels["space_ok"])

    if vector_enabled and config.vector_local_files_only and not model_ready:
        can_proceed = False
        risk_level = "blocked"
        if current_model_cache_bytes == 0:
            notes.append(labels["local_only_empty"])
        else:
            notes.append(labels["local_only_incomplete"])

    return SpaceEstimate(
        run_at=datetime.now(timezone.utc).isoformat(),
        vault_file_count=file_count,
        vault_total_bytes=vault_total_bytes,
        parsed_chunk_count=parsed_chunk_count,
        ref_count=ref_count,
        logseq_file_count=logseq_file_count,
        markdown_file_count=markdown_file_count,
        estimated_sqlite_bytes=estimated_sqlite_bytes,
        estimated_fts_bytes=estimated_fts_bytes,
        estimated_vector_bytes=estimated_vector_bytes,
        estimated_model_bytes=estimated_model_bytes,
        estimated_peak_temp_bytes=estimated_peak_temp_bytes,
        safety_margin_bytes=safety_margin_bytes,
        current_state_bytes=current_state_bytes,
        current_model_cache_bytes=current_model_cache_bytes,
        required_free_bytes=required_free_bytes,
        available_free_bytes=available_free_bytes,
        vector_backend=config.vector_backend,
        vector_model=config.vector_model,
        can_proceed=can_proceed,
        risk_level=risk_level,
        estimated_build_seconds=estimated_build_seconds,
        estimated_download_seconds=estimated_download_seconds,
        notes=notes,
    )


def _wait_for_preflight_controls(pause_event: threading.Event | None, cancel_event: threading.Event | None) -> None:
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise BuildCancelledError('cancelled')
        if pause_event is None or not pause_event.is_set():
            return
        threading.Event().wait(0.1)


def _scan_vault(
    config: AppConfig,
    *,
    on_progress: Callable[[dict[str, object]], None] | None = None,
    pause_event: threading.Event | None = None,
    cancel_event: threading.Event | None = None,
) -> list[Path]:
    if not config.vault_path:
        return []
    ignore = set(config.ignore_dirs)
    files: list[Path] = []
    for root, dirnames, filenames in os.walk(config.vault_dir, topdown=True):
        _wait_for_preflight_controls(pause_event, cancel_event)
        dirnames[:] = [name for name in dirnames if name not in ignore]
        current_root = Path(root)
        for filename in filenames:
            _wait_for_preflight_controls(pause_event, cancel_event)
            if not filename.lower().endswith('.md'):
                continue
            files.append((current_root / filename).resolve())
            if on_progress is not None and (len(files) <= 8 or len(files) % 32 == 0):
                on_progress({
                    'stage': 'preflight_scan',
                    'current': len(files),
                    'total': 0,
                    'current_path': _safe_relative_path(config.vault_dir, current_root / filename),
                    'overall_percent': 0.0,
                })
    return sorted(files)


def _estimate_vector_dimension(model_name: str) -> int:
    lowered = (model_name or "").lower()
    if "bge-m3" in lowered:
        return 1024
    if "small" in lowered:
        return 384
    if "base" in lowered:
        return 768
    if "large" in lowered:
        return 1024
    return 1024


def estimate_model_cache_bytes(model_name: str, runtime: str) -> int:
    lowered = (model_name or "").lower()
    runtime = (runtime or "torch").lower()
    if "bge-m3" in lowered:
        return int(4.6 * GIB) if runtime == "torch" else int(3.4 * GIB)
    if "small" in lowered:
        return int(0.8 * GIB)
    if "base" in lowered:
        return int(1.4 * GIB)
    return int(2.0 * GIB)


def _directory_size(
    path: Path,
    *,
    pause_event: threading.Event | None = None,
    cancel_event: threading.Event | None = None,
    on_progress: Callable[[dict[str, object]], None] | None = None,
    stage: str = 'preflight_finalize',
    overall_percent: float = 0.0,
) -> int:
    if not path.exists():
        return 0
    total = 0
    counted_files = 0
    for root, _dirnames, filenames in os.walk(path, topdown=True):
        _wait_for_preflight_controls(pause_event, cancel_event)
        root_path = Path(root)
        for filename in filenames:
            _wait_for_preflight_controls(pause_event, cancel_event)
            child = root_path / filename
            try:
                total += child.stat().st_size
            except OSError:
                continue
            counted_files += 1
            if on_progress is not None and (counted_files <= 8 or counted_files % 256 == 0):
                on_progress({
                    'stage': stage,
                    'current': counted_files,
                    'total': 0,
                    'current_path': _safe_relative_path(path, child),
                    'overall_percent': overall_percent,
                })
    return total


def _safe_relative_path(vault_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(vault_root.resolve()).as_posix()
    except Exception:
        return str(path)


def _encoded_len(text: str) -> int:
    return len((text or '').encode('utf-8'))


def _estimate_download_duration_seconds(model_name: str, runtime: str) -> int:
    size_mib = estimate_model_cache_bytes(model_name, runtime) / MIB
    speed_mib_per_second = 18 if (runtime or 'torch').lower() == 'torch' else 24
    return max(60, int(size_mib / speed_mib_per_second) + 1)


def _format_duration(seconds: int) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
