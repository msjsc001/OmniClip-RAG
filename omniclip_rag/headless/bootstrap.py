from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from ..app_logging import configure_file_logging
from ..config import (
    AppConfig,
    DataPaths,
    ensure_data_paths,
    load_config,
    normalize_ui_scale_percent,
    normalize_ui_theme,
    normalize_vault_path,
)
from ..runtime_layout import apply_pending_runtime_updates
from ..service import OmniClipService
from ..ui_i18n import normalize_language


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class RuntimeBundle:
    config: AppConfig
    paths: DataPaths
    language_code: str
    theme_code: str
    scale_percent: int


@dataclass(slots=True)
class HeadlessContext:
    bundle: RuntimeBundle
    service: OmniClipService
    applied_components: tuple[str, ...] = ()

    def close(self) -> None:
        self.service.close()


def startup_runtime_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent / 'runtime'
    return Path(__file__).resolve().parents[2] / 'runtime'


def apply_runtime_layout_if_needed() -> list[str]:
    if not getattr(sys, 'frozen', False):
        return []
    runtime_dir = startup_runtime_dir()
    try:
        return list(apply_pending_runtime_updates(runtime_dir))
    except OSError as exc:
        LOGGER.warning('Pending runtime update could not be applied during startup: %s', exc)
        return []


def load_runtime_bundle(*, data_root: str | None = None, vault_path: str | None = None) -> RuntimeBundle:
    global_paths = ensure_data_paths(str(data_root).strip() if str(data_root or '').strip() else None)
    loaded = load_config(global_paths)
    if loaded is None:
        loaded = AppConfig(vault_path='', data_root=str(global_paths.global_root))

    override_vault = normalize_vault_path(vault_path)
    active_vault = override_vault or normalize_vault_path(loaded.vault_path)
    active_data_root = str(data_root or loaded.data_root or global_paths.global_root).strip() or str(global_paths.global_root)
    paths = ensure_data_paths(active_data_root, active_vault or None)

    language_code = normalize_language(loaded.ui_language)
    theme_code = normalize_ui_theme(loaded.ui_theme)
    scale_percent = normalize_ui_scale_percent(loaded.ui_scale_percent, 100)
    config = replace(
        loaded,
        vault_path=active_vault,
        data_root=str(paths.global_root),
        ui_language=language_code,
        ui_theme=theme_code,
        ui_scale_percent=scale_percent,
    )
    if active_vault and active_vault not in config.vault_paths:
        config.vault_paths.insert(0, active_vault)
    return RuntimeBundle(
        config=config,
        paths=paths,
        language_code=language_code,
        theme_code=theme_code,
        scale_percent=scale_percent,
    )


def create_headless_context(
    *,
    data_root: str | None = None,
    vault_path: str | None = None,
    apply_runtime_updates: bool = True,
) -> HeadlessContext:
    applied_components = tuple(apply_runtime_layout_if_needed()) if apply_runtime_updates else ()
    bundle = load_runtime_bundle(data_root=data_root, vault_path=vault_path)
    configure_file_logging(bundle.paths, bundle.config)
    service = OmniClipService(bundle.config, bundle.paths)
    return HeadlessContext(bundle=bundle, service=service, applied_components=applied_components)
