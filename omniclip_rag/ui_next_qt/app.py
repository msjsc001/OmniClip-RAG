from __future__ import annotations

import atexit
import ctypes
import logging
import os
import sys
import threading
import traceback
from pathlib import Path

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PySide6 import QtCore, QtGui, QtWidgets

from .. import __version__
from ..app_logging import configure_file_logging, install_exception_logging
from ..config import AppConfig, build_data_paths, default_data_root
from ..errors import ActiveDataRootUnavailableError
from ..headless.bootstrap import RuntimeBundle, apply_runtime_layout_if_needed, load_runtime_bundle
from ..runtime_recovery import mark_session_clean_exit, mark_session_running, mark_session_started, prepare_startup_recovery
from ..ui_i18n import data_root_reason_text, detect_system_language, text

APP_USER_MODEL_ID = 'msjsc001.OmniClipRAG.GUI'
APP_LOGGER = logging.getLogger(__name__)

_QT_MODULES: tuple[Any, Any, Any] | None = None


def _ensure_qt() -> tuple[Any, Any, Any]:
    global _QT_MODULES
    if _QT_MODULES is None:
        from PySide6 import QtCore as _QtCore, QtGui as _QtGui, QtWidgets as _QtWidgets
        _QT_MODULES = (_QtCore, _QtGui, _QtWidgets)
    return _QT_MODULES


def _startup_progress_dialog_class():
    QtCore, QtGui, QtWidgets = _ensure_qt()

    class StartupProgressDialog(QtWidgets.QDialog):
        """Small startup window so double-click launches give immediate feedback.

        Why: a normal movable/minimizable window is less confusing than a hidden cold
        start, and safer than a frameless splash for Windows desktop sessions.
        """

        def __init__(self) -> None:
            super().__init__(None, QtCore.Qt.WindowType.Window | QtCore.Qt.WindowType.WindowTitleHint | QtCore.Qt.WindowType.WindowMinimizeButtonHint)
            self.setWindowTitle('OmniClip RAG [开发态]' if not getattr(sys, 'frozen', False) else 'OmniClip RAG')
            self.setWindowModality(QtCore.Qt.WindowModality.NonModal)
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)
            self.setMinimumWidth(420)
            self.resize(460, 180)
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(18, 18, 18, 18)
            layout.setSpacing(10)

            self._title_label = QtWidgets.QLabel('OmniClip RAG [开发态]' if not getattr(sys, 'frozen', False) else 'OmniClip RAG', self)
            title_font = self._title_label.font()
            title_font.setPointSize(max(title_font.pointSize() + 3, 12))
            title_font.setBold(True)
            self._title_label.setFont(title_font)
            layout.addWidget(self._title_label)

            self._status_label = QtWidgets.QLabel('正在启动，请稍候...', self)
            self._status_label.setWordWrap(True)
            layout.addWidget(self._status_label)

            self._progress = QtWidgets.QProgressBar(self)
            self._progress.setRange(0, 0)
            self._progress.setTextVisible(False)
            self._progress.setFixedHeight(14)
            layout.addWidget(self._progress)

            self._detail_label = QtWidgets.QLabel('加载界面与运行环境时，这个窗口可以移动和最小化。', self)
            self._detail_label.setWordWrap(True)
            layout.addWidget(self._detail_label)

        def set_status(self, text: str, *, detail: str | None = None) -> None:
            self._status_label.setText(text)
            if detail is not None:
                self._detail_label.setText(detail)

    return StartupProgressDialog


def _startup_function_worker_class():
    QtCore, _QtGui, _QtWidgets = _ensure_qt()

    class StartupFunctionWorker(QtCore.QObject):
        """Tiny startup worker that avoids importing the heavy service tree too early.

        Why: the generic worker module imports OmniClipService and the whole retrieval
        stack at import time. This lightweight local worker lets the startup dialog
        appear first.
        """

        succeeded = QtCore.Signal(object)
        failed = QtCore.Signal(object, str, str)
        finished = QtCore.Signal()

        def __init__(self, *, fn) -> None:
            super().__init__()
            self._fn = fn
            self._thread: threading.Thread | None = None

        def start(self) -> None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

        def _run(self) -> None:
            try:
                self.succeeded.emit(self._fn())
            except Exception as exc:
                self.failed.emit(exc, str(exc).strip() or exc.__class__.__name__, traceback.format_exc())
            finally:
                self.finished.emit()

    return StartupFunctionWorker


def __getattr__(name: str):
    if name == 'StartupProgressDialog':
        value = _startup_progress_dialog_class()
        globals()[name] = value
        return value
    if name == 'StartupFunctionWorker':
        value = _startup_function_worker_class()
        globals()[name] = value
        return value
    raise AttributeError(name)

def _stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _trace_startup(message: str) -> None:
    if os.environ.get('OMNICLIP_STARTUP_TRACE', '') == '1':
        _stderr(f'[startup] {message}')


def _install_exception_logging() -> None:
    install_exception_logging()


def _normalize_qpa_platform() -> None:
    platform_name = str(os.environ.get('QT_QPA_PLATFORM', '') or '').strip().lower()
    if platform_name != 'offscreen':
        return
    if os.environ.get('OMNICLIP_ALLOW_OFFSCREEN', '') == '1':
        _stderr('QT_QPA_PLATFORM=offscreen is explicitly allowed for this run.')
        return
    os.environ.pop('QT_QPA_PLATFORM', None)
    _stderr('Detected QT_QPA_PLATFORM=offscreen during interactive launch; reset it so the desktop window can appear normally.')


def _startup_runtime_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent / 'runtime'
    return Path(__file__).resolve().parents[2] / 'runtime'


def _prepare_startup_bundle() -> tuple[RuntimeBundle, list[str]]:
    applied_components: list[str] = []
    try:
        applied_components = list(apply_runtime_layout_if_needed())
    except OSError as exc:
        _stderr(f'Pending runtime update could not be applied during startup: {exc}')
    bundle = load_runtime_bundle()
    return bundle, applied_components


def main() -> int:
    QtCore, QtGui, QtWidgets = _ensure_qt()
    StartupProgressDialog = _startup_progress_dialog_class()
    StartupFunctionWorker = _startup_function_worker_class()
    _install_exception_logging()
    _normalize_qpa_platform()
    _set_windows_app_user_model_id()
    startup_dialog: StartupProgressDialog | None = None
    startup_worker: StartupFunctionWorker | None = None
    startup_window: MainWindow | None = None
    startup_exit_code = 0
    startup_language = detect_system_language()

    def _tr(key: str, **kwargs) -> str:
        return text(startup_language, key, **kwargs)

    def _start_startup_worker(status_text: str, detail_text: str) -> None:
        nonlocal startup_worker
        if startup_dialog is not None:
            startup_dialog.set_status(status_text, detail=detail_text)
            startup_dialog.show()
            app.processEvents()
        startup_worker = StartupFunctionWorker(fn=_prepare_startup_bundle)
        startup_worker.succeeded.connect(_handle_startup_success)
        startup_worker.failed.connect(_handle_startup_failure)
        startup_worker.finished.connect(lambda: None)
        startup_worker.start()

    def _handle_directory_unavailable(error: ActiveDataRootUnavailableError, traceback_text: str) -> None:
        nonlocal startup_exit_code, startup_dialog, startup_window
        APP_LOGGER.error('Qt startup blocked by unavailable active data root: %s\n%s', error, traceback_text)
        _stderr(f'Qt startup blocked: {error}')
        reason_text = data_root_reason_text(startup_language, error.reason, error.detail)
        recovery_root = error.path or str(default_data_root())
        if startup_dialog is not None:
            startup_dialog.set_status(_tr('data_root_unavailable_title'), detail=recovery_root)
            app.processEvents()
        from .main_window import MainWindow
        from .theme import apply_application_style, build_theme

        placeholder_paths = build_data_paths(Path(default_data_root()))
        recovery_config = AppConfig(
            vault_path='',
            data_root=str(placeholder_paths.global_root),
            ui_language=startup_language,
        )
        theme = build_theme(recovery_config.ui_theme, recovery_config.ui_scale_percent)
        apply_application_style(app, theme)
        icon = _load_app_icon()
        if icon is not None:
            app.setWindowIcon(icon)
            if startup_dialog is not None:
                startup_dialog.setWindowIcon(icon)
        startup_window = MainWindow(
            config=recovery_config,
            paths=placeholder_paths,
            language_code=startup_language,
            theme=theme,
            version=__version__,
            recovery_mode=True,
            recovery_context={
                'path': recovery_root,
                'reason_code': error.reason,
                'reason_text': reason_text,
            },
        )
        MainWindow._register_live_window(startup_window)
        if icon is not None:
            startup_window.setWindowIcon(icon)
        startup_window.show()
        startup_window.raise_()
        startup_window.activateWindow()
        if startup_dialog is not None:
            startup_dialog.close()
            startup_dialog = None

    def _handle_startup_failure(exc: object, message: str, traceback_text: str) -> None:
        nonlocal startup_dialog, startup_exit_code
        if isinstance(exc, ActiveDataRootUnavailableError):
            _handle_directory_unavailable(exc, traceback_text)
            return
        startup_exit_code = 1
        APP_LOGGER.error('Qt startup failed with an unhandled exception: %s\n%s', message, traceback_text)
        _stderr(f'Qt startup failed: {message}')
        if startup_dialog is not None:
            startup_dialog.close()
            startup_dialog = None
        app.exit(1)

    def _handle_startup_success(payload: object) -> None:
        nonlocal startup_dialog, startup_window
        bundle, applied_components = payload
        configure_file_logging(bundle.paths, bundle.config)
        APP_LOGGER.info('Qt app bootstrap started: language=%s theme=%s scale=%s data_root=%s.', bundle.language_code, bundle.theme_code, bundle.scale_percent, getattr(bundle.paths, 'global_root', ''))
        if applied_components:
            APP_LOGGER.info('Applied pending runtime updates during startup: %s', ', '.join(applied_components))
        recovery = prepare_startup_recovery(bundle.paths)
        mark_session_started(bundle.paths, version=__version__, safe_startup=bool(recovery.get('safe_startup')))
        atexit.register(lambda paths=bundle.paths: mark_session_clean_exit(paths))
        if recovery.get('safe_startup'):
            APP_LOGGER.warning('Previous session ended unexpectedly or hit memory pressure; OmniClip is starting in safe startup mode.')
        if startup_dialog is not None:
            startup_dialog.set_status('正在加载界面主题...', detail='你可以移动或最小化这个启动窗口。')
            app.processEvents()
        _trace_startup('build theme')
        from .theme import apply_application_style, build_theme
        theme = build_theme(bundle.theme_code, bundle.scale_percent)
        apply_application_style(app, theme)
        _trace_startup('load icon')
        icon = _load_app_icon()
        if icon is not None:
            app.setWindowIcon(icon)
            if startup_dialog is not None:
                startup_dialog.setWindowIcon(icon)
        if startup_dialog is not None:
            startup_dialog.set_status('正在准备主界面...', detail='完成后会自动切换到主窗口。')
            app.processEvents()
        _trace_startup('import main window')
        from .main_window import MainWindow
        _trace_startup('build main window')
        startup_window = MainWindow(
            config=bundle.config,
            paths=bundle.paths,
            language_code=bundle.language_code,
            theme=theme,
            version=__version__,
        )
        MainWindow._register_live_window(startup_window)
        if icon is not None:
            startup_window.setWindowIcon(icon)
        _trace_startup('show main window')
        startup_window.show()
        startup_window.raise_()
        startup_window.activateWindow()
        if startup_dialog is not None:
            startup_dialog.close()
            startup_dialog = None
        mark_session_running(bundle.paths)
        app.aboutToQuit.connect(lambda paths=bundle.paths: mark_session_clean_exit(paths))
        _trace_startup('main window shown')
        APP_LOGGER.info('Qt main window shown successfully.')
        startup_safe_mode = bool(recovery.get('safe_startup'))
        QtCore.QTimer.singleShot(60, lambda safe=startup_safe_mode, workspace=startup_window.config_workspace: workspace.schedule_startup_background_tasks(safe_mode=safe, initial_status_delay_ms=120))

    _trace_startup('create QApplication')
    QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    _apply_app_identity(app)
    startup_dialog = StartupProgressDialog()
    icon = _load_app_icon()
    if icon is not None:
        startup_dialog.setWindowIcon(icon)
        app.setWindowIcon(icon)
    pending_root = _startup_runtime_dir() / '.pending'
    if pending_root.exists():
        startup_dialog.set_status('正在应用已下载的 runtime 更新...', detail='这一步可能需要一点时间，请不要重复双击。')
    else:
        startup_dialog.set_status('正在加载配置与运行状态...', detail='首次冷启动可能稍慢，请不要重复双击。')
    startup_dialog.show()
    app.processEvents()

    _start_startup_worker(startup_dialog._status_label.text(), startup_dialog._detail_label.text())
    exit_code = app.exec()
    return startup_exit_code or exit_code


def _set_windows_app_user_model_id() -> None:
    if sys.platform != 'win32':
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def _apply_app_identity(app) -> None:
    try:
        app.setApplicationName('OmniClip RAG')
        app.setApplicationDisplayName('OmniClip RAG')
        app.setOrganizationName('msjsc001')
    except Exception:
        pass

def _load_app_icon() -> 'QtGui.QIcon | None':
    _QtCore, QtGui, _QtWidgets = _ensure_qt()
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

