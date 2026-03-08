from __future__ import annotations

from .models import SpaceEstimate
from .ui_i18n import normalize_language


_REPORT_TEXTS = {
    "zh-CN": {
        "title": "[建库空间与时间预检查]",
        "risk": "风险等级",
        "can_proceed": "是否建议继续",
        "yes": "是",
        "no": "否",
        "vault_files": "笔记文件",
        "vault_size": "笔记原始体积",
        "parsed_chunks": "解析后片段数",
        "refs": "引用数",
        "sqlite": "SQLite 预估",
        "fts": "FTS 预估",
        "vector": "向量库预估",
        "model": "模型缓存预估",
        "temp": "临时峰值预留",
        "margin": "安全余量",
        "build_time": "首轮建库时间",
        "download_time": "首次下载额外时间",
        "state_used": "当前状态目录已占用",
        "cache_used": "当前模型缓存已占用",
        "required": "需要可用空间",
        "available": "当前可用空间",
        "headroom": "剩余头寸",
        "notes": "说明",
        "summary_empty": "最近还没有空间时间预检查记录。",
        "summary": "风险：{risk} | 需要：{required} | 建库：{build}",
        "summary_with_download": "风险：{risk} | 需要：{required} | 建库：{build} | 下载：{download}",
    },
    "en": {
        "title": "[Index Build Space and Time Precheck]",
        "risk": "Risk level",
        "can_proceed": "Recommended to continue",
        "yes": "Yes",
        "no": "No",
        "vault_files": "Vault files",
        "vault_size": "Vault raw size",
        "parsed_chunks": "Parsed chunks",
        "refs": "References",
        "sqlite": "Estimated SQLite",
        "fts": "Estimated FTS",
        "vector": "Estimated vector store",
        "model": "Estimated model cache",
        "temp": "Peak temp reserve",
        "margin": "Safety margin",
        "build_time": "First full-build time",
        "download_time": "Extra first-download time",
        "state_used": "Current state directory used",
        "cache_used": "Current model cache used",
        "required": "Required free space",
        "available": "Available free space",
        "headroom": "Remaining headroom",
        "notes": "Notes",
        "summary_empty": "No space/time precheck has been recorded yet.",
        "summary": "Risk: {risk} | Required: {required} | Build: {build}",
        "summary_with_download": "Risk: {risk} | Required: {required} | Build: {build} | Download: {download}",
    },
}


def _catalog(language: str | None) -> dict[str, str]:
    code = normalize_language(language)
    return _REPORT_TEXTS.get(code, _REPORT_TEXTS['en'])


def format_bytes(value: int) -> str:
    negative = value < 0
    value = abs(int(value))
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    size = float(value)
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    prefix = '-' if negative else ''
    return f"{prefix}{size:.2f} {unit}"


def format_duration(seconds: int) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_space_report(report: SpaceEstimate, language: str | None = None) -> str:
    labels = _catalog(language)
    lines = [
        labels['title'],
        f"{labels['risk']}：{report.risk_level}",
        f"{labels['can_proceed']}：{labels['yes'] if report.can_proceed else labels['no']}",
        f"{labels['vault_files']}：{report.vault_file_count}",
        f"{labels['vault_size']}：{format_bytes(report.vault_total_bytes)}",
        f"{labels['parsed_chunks']}：{report.parsed_chunk_count}",
        f"{labels['refs']}：{report.ref_count}",
        f"{labels['sqlite']}：{format_bytes(report.estimated_sqlite_bytes)}",
        f"{labels['fts']}：{format_bytes(report.estimated_fts_bytes)}",
        f"{labels['vector']}：{format_bytes(report.estimated_vector_bytes)}",
        f"{labels['model']}：{format_bytes(report.estimated_model_bytes)}",
        f"{labels['temp']}：{format_bytes(report.estimated_peak_temp_bytes)}",
        f"{labels['margin']}：{format_bytes(report.safety_margin_bytes)}",
        f"{labels['build_time']}：{format_duration(report.estimated_build_seconds)}",
        f"{labels['state_used']}：{format_bytes(report.current_state_bytes)}",
        f"{labels['cache_used']}：{format_bytes(report.current_model_cache_bytes)}",
        f"{labels['required']}：{format_bytes(report.required_free_bytes)}",
        f"{labels['available']}：{format_bytes(report.available_free_bytes)}",
        f"{labels['headroom']}：{format_bytes(report.headroom_bytes)}",
    ]
    if report.estimated_download_seconds > 0:
        lines.append(f"{labels['download_time']}：{format_duration(report.estimated_download_seconds)}")
    lines.append(f"{labels['notes']}：")
    for note in report.notes:
        lines.append(f"- {note}")
    return '\n'.join(lines)


def summarize_preflight(report: SpaceEstimate | None, language: str | None = None) -> str:
    labels = _catalog(language)
    if report is None:
        return labels['summary_empty']
    if report.estimated_download_seconds > 0:
        return labels['summary_with_download'].format(
            risk=report.risk_level,
            required=format_bytes(report.required_free_bytes),
            build=format_duration(report.estimated_build_seconds),
            download=format_duration(report.estimated_download_seconds),
        )
    return labels['summary'].format(
        risk=report.risk_level,
        required=format_bytes(report.required_free_bytes),
        build=format_duration(report.estimated_build_seconds),
    )
