from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .config import AppConfig, DataPaths
from .models import ParsedFile, SpaceEstimate
from .parser import parse_markdown_file
from .vector_index import is_local_model_ready


GIB = 1024 ** 3
MIB = 1024 ** 2


def estimate_storage_for_vault(
    config: AppConfig,
    paths: DataPaths,
    files: list[Path] | None = None,
) -> SpaceEstimate:
    files = files or _scan_vault(config)

    vault_total_bytes = 0
    parsed_chunk_count = 0
    ref_count = 0
    logseq_file_count = 0
    markdown_file_count = 0
    raw_chunk_bytes = 0
    anchor_bytes = 0
    title_bytes = 0
    property_bytes = 0

    for path in files:
        parsed = parse_markdown_file(config.vault_dir, path)
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

    file_count = len(files)
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
        estimated_model_bytes = _estimate_model_cache_bytes(config.vector_model, config.vector_runtime)

    current_state_bytes = _directory_size(paths.state_dir)
    model_cache_dir = paths.cache_dir / "models"
    current_model_cache_bytes = _directory_size(model_cache_dir)

    additional_index_bytes = max(estimated_sqlite_bytes + estimated_fts_bytes + estimated_vector_bytes - current_state_bytes, 0)
    additional_model_bytes = max(estimated_model_bytes - current_model_cache_bytes, 0)
    estimated_peak_temp_bytes = max(
        int(estimated_rendered_bytes * 0.18 + estimated_vector_bytes * 0.12),
        int(1536 * MIB) if vector_enabled else int(256 * MIB),
    )
    safety_margin_bytes = max(int(vault_total_bytes * 0.25), int(estimated_sqlite_bytes * 0.12), int(1 * GIB))

    available_free_bytes = shutil.disk_usage(paths.root).free
    required_free_bytes = additional_index_bytes + additional_model_bytes + estimated_peak_temp_bytes + safety_margin_bytes

    notes = [
        f"已对 {file_count} 个 Markdown 文件做解析级预估，不是只按文件体积拍脑袋。",
        f"目标写入盘：{paths.root.drive or paths.root}",
        f"Logseq 文件占比约 {logseq_ratio:.0%}，引用密度 {ref_density:.2f}。",
        f"向量后端：{config.vector_backend}，模型：{config.vector_model}。",
    ]
    if vector_enabled and "bge-m3" in (config.vector_model or "").lower():
        notes.append("bge-m3 在 Windows 本地首轮落盘按保守值估算，建议至少预留 8-10 GB 空闲。")

    can_proceed = available_free_bytes >= required_free_bytes
    risk_level = "ok"
    if not can_proceed:
        risk_level = "insufficient"
        notes.append("可用空间低于预估需求，默认不建议直接开始建库。")
    elif available_free_bytes < int(required_free_bytes * 1.2):
        risk_level = "tight"
        notes.append("空间刚够，但余量偏紧，建议先清理磁盘再跑首轮建库。")
    else:
        notes.append("空间余量充足，可以开始首轮建库。")

    if vector_enabled and config.vector_local_files_only and not is_local_model_ready(config, paths):
        can_proceed = False
        risk_level = "blocked"
        if current_model_cache_bytes == 0:
            notes.append("当前启用了 vector_local_files_only，但本地模型缓存为空，首轮向量建库会直接失败。")
        else:
            notes.append("当前启用了 vector_local_files_only，但本地模型缓存不完整，建议先重新运行 bootstrap-model。")

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
        notes=notes,
    )


def _scan_vault(config: AppConfig) -> list[Path]:
    ignore = set(config.ignore_dirs)
    files: list[Path] = []
    for path in config.vault_dir.rglob("*.md"):
        if any(part in ignore for part in path.parts):
            continue
        files.append(path.resolve())
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


def _estimate_model_cache_bytes(model_name: str, runtime: str) -> int:
    lowered = (model_name or "").lower()
    runtime = (runtime or "torch").lower()
    if "bge-m3" in lowered:
        return int(4.6 * GIB) if runtime == "torch" else int(3.4 * GIB)
    if "small" in lowered:
        return int(0.8 * GIB)
    if "base" in lowered:
        return int(1.4 * GIB)
    return int(2.0 * GIB)


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total


def _encoded_len(text: str) -> int:
    return len((text or "").encode("utf-8"))
