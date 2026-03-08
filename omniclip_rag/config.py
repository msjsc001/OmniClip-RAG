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
    query_limit: int = 8
    poll_interval_seconds: float = 2.0
    vector_backend: str = "disabled"
    vector_model: str = "BAAI/bge-m3"
    vector_candidate_limit: int = 24
    vector_device: str = "cpu"
    vector_runtime: str = "torch"
    vector_batch_size: int = 16
    vector_local_files_only: bool = False
    ui_language: str = field(default_factory=detect_system_language)
    ui_window_geometry: str = ''
    ui_main_sash: int = 500
    ui_right_sash: int = 190
    ui_results_sash: int = 320

    @property
    def vault_dir(self) -> Path:
        return Path(self.vault_path).expanduser().resolve()


def default_data_root() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / "AppData" / "Roaming" / APP_NAME


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

    candidates: list[Path] = [default_data_root()]
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / APP_NAME)
    candidates.append(Path.cwd() / "local_appdata")

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
