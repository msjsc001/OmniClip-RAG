from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


APP_NAME = "OmniClip RAG"


@dataclass(slots=True)
class DataPaths:
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

    @property
    def vault_dir(self) -> Path:
        return Path(self.vault_path).expanduser().resolve()


def default_data_root() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / "AppData" / "Roaming" / APP_NAME


def ensure_data_paths(custom_root: str | None = None) -> DataPaths:
    if custom_root:
        return _create_data_paths(Path(custom_root).expanduser().resolve())

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
            return _create_data_paths(candidate)
        except OSError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError("无法创建 OmniClip 数据目录。")


def _create_data_paths(root: Path) -> DataPaths:
    state_dir = root / "state"
    logs_dir = root / "logs"
    cache_dir = root / "cache"
    exports_dir = root / "exports"
    for directory in (root, state_dir, logs_dir, cache_dir, exports_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return DataPaths(
        root=root,
        state_dir=state_dir,
        logs_dir=logs_dir,
        cache_dir=cache_dir,
        exports_dir=exports_dir,
        config_file=root / "config.json",
        sqlite_file=state_dir / "omniclip.sqlite3",
    )


def save_config(config: AppConfig, paths: DataPaths) -> None:
    paths.config_file.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_config(paths: DataPaths) -> AppConfig | None:
    if not paths.config_file.exists():
        return None
    payload = json.loads(paths.config_file.read_text(encoding="utf-8"))
    return AppConfig(**payload)
