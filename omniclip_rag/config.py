from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .ui_i18n import detect_system_language, normalize_language


APP_NAME = "OmniClip RAG"
DEFAULT_WORKSPACE_ID = "default"
DEFAULT_UI_THEME = "system"
SUPPORTED_UI_THEMES = {"system", "light", "dark"}
UI_SCALE_PERCENT_MIN = 80
UI_SCALE_PERCENT_MAX = 200
WATCH_RESOURCE_PEAK_OPTIONS = (5, 10, 15, 20, 30, 40, 50, 60, 70, 80, 90)


@dataclass(slots=True)
class DataPaths:
    global_root: Path
    shared_root: Path
    workspaces_dir: Path
    workspace_id: str
    root: Path
    state_dir: Path
    logs_dir: Path
    cache_dir: Path
    exports_dir: Path
    config_file: Path
    sqlite_file: Path


@dataclass(slots=True)
class AppConfig:
    vault_path: str
    data_root: str
    vault_paths: list[str] = field(default_factory=list)
    ignore_dirs: list[str] = field(
        default_factory=lambda: [
            ".git",
            ".obsidian",
            ".trash",
            "node_modules",
            ".venv",
            "__pycache__",
        ]
    )
    query_limit: int = 15
    query_score_threshold: float = 35.0
    poll_interval_seconds: float = 2.0
    vector_backend: str = "disabled"
    vector_model: str = "BAAI/bge-m3"
    vector_candidate_limit: int = 24
    vector_device: str = "auto"
    vector_runtime: str = "torch"
    vector_batch_size: int = 16
    build_resource_profile: str = "balanced"
    watch_resource_peak_percent: int = 15
    vector_local_files_only: bool = False
    reranker_enabled: bool = False
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_batch_size_cpu: int = 4
    reranker_batch_size_cuda: int = 8
    reranker_max_chars: int = 1200
    context_export_mode: str = "standard"
    rag_filter_core_enabled: bool = True
    rag_filter_extended_enabled: bool = False
    rag_filter_custom_rules: str = ""
    page_blocklist_rules: str = ""
    ui_language: str = field(default_factory=detect_system_language)
    ui_theme: str = DEFAULT_UI_THEME
    ui_scale_percent: int = 100
    ui_quick_start_expanded: bool = True
    ui_window_geometry: str = ''
    ui_main_sash: int = 900
    ui_right_sash: int = 280
    ui_results_sash: int = 300
    qt_window_geometry: str = ''
    qt_query_splitter_state: str = ''
    qt_results_splitter_state: str = ''
    qt_header_collapsed: bool = False

    @property
    def vault_dir(self) -> Path:
        return Path(self.vault_path).expanduser().resolve()


def default_data_root() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / "AppData" / "Roaming" / APP_NAME


def default_local_data_root() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / APP_NAME
    return Path.home() / "AppData" / "Local" / APP_NAME


def temp_data_root() -> Path:
    temp_root = os.environ.get("TEMP") or os.environ.get("TMP")
    if temp_root:
        return Path(temp_root) / APP_NAME
    return Path.home() / "AppData" / "Local" / "Temp" / APP_NAME


def normalize_vault_path(vault_path: str | Path | None) -> str:
    if vault_path is None:
        return ""
    raw_value = str(vault_path).strip()
    if not raw_value:
        return ""
    candidate = Path(raw_value).expanduser()
    try:
        return str(candidate.resolve(strict=False))
    except TypeError:
        return str(candidate.resolve())
    except OSError:
        return str(candidate.absolute())


def normalize_ui_theme(value: str | None) -> str:
    normalized = str(value or DEFAULT_UI_THEME).strip().lower()
    aliases = {
        "system": "system",
        "auto": "system",
        "follow-system": "system",
        "follow_system": "system",
        "light": "light",
        "day": "light",
        "dark": "dark",
        "night": "dark",
    }
    theme = aliases.get(normalized, DEFAULT_UI_THEME)
    return theme if theme in SUPPORTED_UI_THEMES else DEFAULT_UI_THEME


def normalize_ui_scale_percent(value: object, default: int = 100) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = int(default)
    return max(UI_SCALE_PERCENT_MIN, min(UI_SCALE_PERCENT_MAX, parsed))


def normalize_watch_resource_peak_percent(value: object, default: int = 15) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = int(default)
    if parsed in WATCH_RESOURCE_PEAK_OPTIONS:
        return parsed
    ordered = sorted(WATCH_RESOURCE_PEAK_OPTIONS)
    for candidate in ordered:
        if parsed <= candidate:
            return candidate
    return ordered[-1]

def workspace_id_for_vault(vault_path: str | Path | None) -> str:
    normalized = normalize_vault_path(vault_path)
    if not normalized:
        return DEFAULT_WORKSPACE_ID
    name = Path(normalized).name or "vault"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-._") or "vault"
    digest = hashlib.sha1(normalized.lower().encode("utf-8")).hexdigest()[:10]
    return f"{slug}-{digest}"


def ensure_data_paths(custom_root: str | None = None, vault_path: str | Path | None = None) -> DataPaths:
    if custom_root:
        return _create_data_paths(Path(custom_root).expanduser().resolve(), vault_path=vault_path)

    candidates: list[Path] = [default_data_root(), default_local_data_root(), temp_data_root()]

    last_error: OSError | None = None
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        try:
            return _create_data_paths(candidate, vault_path=vault_path)
        except OSError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError("无法创建 OmniClip 数据目录。")


def _create_data_paths(global_root: Path, vault_path: str | Path | None = None) -> DataPaths:
    global_root = global_root.resolve()
    shared_root = global_root / "shared"
    workspaces_dir = global_root / "workspaces"
    workspace_id = workspace_id_for_vault(vault_path)
    workspace_root = workspaces_dir / workspace_id
    state_dir = workspace_root / "state"
    exports_dir = workspace_root / "exports"
    logs_dir = shared_root / "logs"
    cache_dir = shared_root / "cache"
    for directory in (global_root, shared_root, workspaces_dir, workspace_root, state_dir, exports_dir, logs_dir, cache_dir):
        directory.mkdir(parents=True, exist_ok=True)

    _migrate_legacy_workspace_data(workspace_root, shared_root)

    return DataPaths(
        global_root=global_root,
        shared_root=shared_root,
        workspaces_dir=workspaces_dir,
        workspace_id=workspace_id,
        root=workspace_root,
        state_dir=state_dir,
        logs_dir=logs_dir,
        cache_dir=cache_dir,
        exports_dir=exports_dir,
        config_file=global_root / "config.json",
        sqlite_file=state_dir / "omniclip.sqlite3",
    )


def save_config(config: AppConfig, paths: DataPaths) -> None:
    config.vault_path = normalize_vault_path(config.vault_path)
    config.vault_paths = _clean_vault_paths(config.vault_paths, active_vault=config.vault_path)
    config.data_root = str(paths.global_root)
    payload = asdict(config)
    payload["ui_language"] = normalize_language(payload.get("ui_language"))
    payload["ui_theme"] = normalize_ui_theme(payload.get("ui_theme"))
    payload["ui_scale_percent"] = normalize_ui_scale_percent(payload.get("ui_scale_percent"), config.ui_scale_percent)
    payload["watch_resource_peak_percent"] = normalize_watch_resource_peak_percent(
        payload.get("watch_resource_peak_percent"),
        getattr(config, "watch_resource_peak_percent", 15),
)
    paths.config_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_config(paths: DataPaths) -> AppConfig | None:
    if not paths.config_file.exists():
        return None
    payload = json.loads(paths.config_file.read_text(encoding="utf-8"))
    allowed = {item.name for item in fields(AppConfig)}
    cleaned = {key: value for key, value in payload.items() if key in allowed}
    cleaned["vault_path"] = normalize_vault_path(cleaned.get("vault_path"))
    cleaned["vault_paths"] = _clean_vault_paths(cleaned.get("vault_paths") or [], active_vault=cleaned["vault_path"])
    cleaned["data_root"] = str(paths.global_root)
    cleaned["ui_language"] = normalize_language(cleaned.get("ui_language"))
    cleaned["ui_theme"] = normalize_ui_theme(cleaned.get("ui_theme"))
    cleaned["ui_scale_percent"] = normalize_ui_scale_percent(cleaned.get("ui_scale_percent"), 100)
    cleaned["watch_resource_peak_percent"] = normalize_watch_resource_peak_percent(
        cleaned.get("watch_resource_peak_percent"),
        15,
)
    return AppConfig(**cleaned)


def _clean_vault_paths(vault_paths: list[str], active_vault: str | None = None) -> list[str]:
    ordered = []
    seen: set[str] = set()
    values: list[str] = []
    if active_vault:
        values.append(active_vault)
    values.extend(vault_paths or [])
    for value in values:
        normalized = normalize_vault_path(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _migrate_legacy_workspace_data(workspace_root: Path, shared_root: Path) -> None:
    for legacy_name, shared_name in (("cache", "cache"), ("logs", "logs")):
        legacy_dir = workspace_root / legacy_name
        shared_dir = shared_root / shared_name
        if not legacy_dir.exists() or not legacy_dir.is_dir():
            continue
        shared_dir.mkdir(parents=True, exist_ok=True)
        for item in legacy_dir.iterdir():
            target = shared_dir / item.name
            if target.exists():
                continue
            shutil.move(str(item), str(target))
        try:
            legacy_dir.rmdir()
        except OSError:
            pass
