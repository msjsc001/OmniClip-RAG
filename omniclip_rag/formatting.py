from __future__ import annotations

from .models import SpaceEstimate


def format_bytes(value: int) -> str:
    negative = value < 0
    value = abs(int(value))
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    prefix = "-" if negative else ""
    return f"{prefix}{size:.2f} {unit}"


def format_space_report(report: SpaceEstimate) -> str:
    lines = [
        "[建库空间预检查]",
        f"风险等级：{report.risk_level}",
        f"是否建议继续：{'是' if report.can_proceed else '否'}",
        f"笔记文件：{report.vault_file_count}",
        f"笔记原始体积：{format_bytes(report.vault_total_bytes)}",
        f"解析后片段数：{report.parsed_chunk_count}",
        f"引用数：{report.ref_count}",
        f"SQLite 预估：{format_bytes(report.estimated_sqlite_bytes)}",
        f"FTS 预估：{format_bytes(report.estimated_fts_bytes)}",
        f"向量库预估：{format_bytes(report.estimated_vector_bytes)}",
        f"模型缓存预估：{format_bytes(report.estimated_model_bytes)}",
        f"临时峰值预留：{format_bytes(report.estimated_peak_temp_bytes)}",
        f"安全余量：{format_bytes(report.safety_margin_bytes)}",
        f"当前状态目录已占用：{format_bytes(report.current_state_bytes)}",
        f"当前模型缓存已占用：{format_bytes(report.current_model_cache_bytes)}",
        f"需要可用空间：{format_bytes(report.required_free_bytes)}",
        f"当前可用空间：{format_bytes(report.available_free_bytes)}",
        f"剩余头寸：{format_bytes(report.headroom_bytes)}",
        "说明：",
    ]
    for note in report.notes:
        lines.append(f"- {note}")
    return "\n".join(lines)


def summarize_preflight(report: SpaceEstimate | None) -> str:
    if report is None:
        return "最近还没有空间预检查记录。"
    return (
        f"风险：{report.risk_level} | "
        f"需要：{format_bytes(report.required_free_bytes)} | "
        f"可用：{format_bytes(report.available_free_bytes)}"
    )
