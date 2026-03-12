from __future__ import annotations

import ctypes
import faulthandler
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .. import __version__
from ..config import AppConfig, ensure_data_paths, load_config, normalize_ui_scale_percent, normalize_ui_theme, normalize_vault_path
from ..ui_i18n import normalize_language
from .main_window import MainWindow
from .theme import apply_application_style, build_theme


APP_USER_MODEL_ID = 'msjsc001.OmniClipRAG'


@dataclass(slots=True)
class RuntimeBundle:
    config: AppConfig
    paths: object
    language_code: str
    theme_code: str
    scale_percent: int


def _stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _trace_startup(message: str) -> None:
    if os.environ.get('OMNICLIP_STARTUP_TRACE', '') == '1':
        _stderr(f'[startup] {message}')


def _install_exception_logging() -> None:
    pass


def _normalize_qpa_platform() -> None:
    platform_name = str(os.environ.get('QT_QPA_PLATFORM', '') or '').strip().lower()
    if platform_name != 'offscreen':
        return
    if os.environ.get('OMNICLIP_ALLOW_OFFSCREEN', '') == '1':
        _stderr('QT_QPA_PLATFORM=offscreen is explicitly allowed for this run.')
        return
    os.environ.pop('QT_QPA_PLATFORM', None)
    _stderr('Detected QT_QPA_PLATFORM=offscreen during interactive launch; reset it so the desktop window can appear normally.')


def main() -> int:
    _install_exception_logging()
    _normalize_qpa_platform()
    _set_windows_app_user_model_id()
    try:
        _trace_startup('create QApplication')
        QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        _trace_startup('load runtime bundle')
        bundle = load_runtime_bundle()
        _trace_startup('build theme')
        theme = build_theme(bundle.theme_code, bundle.scale_percent)
        apply_application_style(app, theme)
        _trace_startup('load icon')
        icon = _load_app_icon()
        if icon is not None:
            app.setWindowIcon(icon)
        _trace_startup('build main window')
        window = MainWindow(
            config=bundle.config,
            paths=bundle.paths,
            language_code=bundle.language_code,
            theme=theme,
            version=__version__,
        )
        MainWindow._register_live_window(window)
        if icon is not None:
            window.setWindowIcon(icon)
        _trace_startup('show main window')
        window.show()
        window.raise_()
        window.activateWindow()
        _trace_startup('main window shown')
        QtCore.QTimer.singleShot(60, window.config_workspace.schedule_device_probe)
        QtCore.QTimer.singleShot(180, window.config_workspace.schedule_initial_status_load)
        return app.exec()
    except Exception as exc:
        _stderr(f'Qt startup failed: {exc.__class__.__name__}: {exc}')
        traceback.print_exc()
        return 1


def _set_windows_app_user_model_id() -> None:
    if sys.platform != 'win32':
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def load_runtime_bundle() -> RuntimeBundle:
    global_paths = ensure_data_paths()
    config = load_config(global_paths)
    if config is None:
        config = AppConfig(vault_path='', data_root=str(global_paths.global_root))
    active_vault = normalize_vault_path(config.vault_path)
    paths = ensure_data_paths(config.data_root or str(global_paths.global_root), active_vault or None)
    config.data_root = str(paths.global_root)
    if active_vault and active_vault not in config.vault_paths:
        config.vault_paths.insert(0, active_vault)
    language_code = normalize_language(config.ui_language)
    theme_code = normalize_ui_theme(config.ui_theme)
    scale_percent = normalize_ui_scale_percent(config.ui_scale_percent, 100)
    return RuntimeBundle(
        config=config,
        paths=paths,
        language_code=language_code,
        theme_code=theme_code,
        scale_percent=scale_percent,
    )


def _load_app_icon() -> QtGui.QIcon | None:
    icon = QtGui.QIcon()
    for candidate in ('app_icon.ico', 'app_icon.png', 'app_icon_32.png'):
        path = _resource_path(candidate)
        if path.exists():
            icon.addFile(str(path))
    return icon if not icon.isNull() else None


def _resource_path(name: str) -> Path:
    if getattr(sys, 'frozen', False):
        base = Path(getattr(sys, '_MEIPASS', Path.cwd()))
    else:
        base = Path(__file__).resolve().parents[2]
    return base / 'resources' / name
