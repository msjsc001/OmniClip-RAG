from __future__ import annotations

import sys

from dataclasses import replace

from PySide6 import QtCore, QtGui, QtWidgets

from ..config import save_config
from ..ui_i18n import language_code_from_label, language_label, text, tooltip
from .config_workspace import ConfigWorkspace
from .query_workspace import QueryWorkspace
from .theme import ThemeState, apply_application_style, build_theme


class MainWindow(QtWidgets.QMainWindow):
    def __init__(
        self,
        *,
        config,
        paths,
        language_code: str,
        theme: ThemeState,
        version: str,
        recovery_mode: bool = False,
        recovery_context: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._paths = paths
        self._language_code = language_code
        self._theme = theme
        self._version = version
        self._recovery_mode = bool(recovery_mode)
        self._recovery_context = dict(recovery_context or {})
        self._query_status_message = self._tr('status_ready')
        self._config_status_message = self._recovery_status_text() if self._recovery_mode else self._tr('status_ready')
        self._query_result_summary = self._tr('result_empty')
        self._config_result_summary = ''
        self._header_collapsed = bool(getattr(self._config, 'qt_header_collapsed', False))
        self._replacement_window: MainWindow | None = None

        self.setWindowTitle(self._window_title_text())
        self.resize(1560, 1000)
        self.setMinimumSize(1320, 860)

        self._build_shell()
        self._refresh_header_collapsed()
        self._on_runtime_config_changed(self._config, self._paths)
        QtCore.QTimer.singleShot(0, self._restore_window_state)

    @staticmethod
    def _register_live_window(window: 'MainWindow') -> None:
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        windows = list(getattr(app, '_omniclip_live_windows', []))
        if window not in windows:
            windows.append(window)
            setattr(app, '_omniclip_live_windows', windows)

        def _cleanup(*_args) -> None:
            current = list(getattr(app, '_omniclip_live_windows', []))
            if window in current:
                current.remove(window)
                setattr(app, '_omniclip_live_windows', current)

        window.destroyed.connect(_cleanup)

    def _window_title_text(self) -> str:
        title = self._tr('title')
        if getattr(sys, 'frozen', False):
            return title
        return f"{title} [开发态]"

    def _build_shell(self) -> None:
        central = QtWidgets.QWidget(self)
        central.setObjectName('AppRoot')
        self.setCentralWidget(central)

        root_layout = QtWidgets.QVBoxLayout(central)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(8)

        self.header_card = QtWidgets.QFrame(central)
        self.header_card.setObjectName('HeaderCard')
        self.header_card.setProperty('card', True)
        header_layout = QtWidgets.QHBoxLayout(self.header_card)
        header_layout.setContentsMargins(12, 12, 12, 12)
        header_layout.setSpacing(12)
        root_layout.addWidget(self.header_card)

        title_layout = QtWidgets.QVBoxLayout()
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(6)
        header_layout.addLayout(title_layout, 1)

        title_row = QtWidgets.QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(12)
        title_layout.addLayout(title_row)

        self.title_label = QtWidgets.QLabel(self._window_title_text(), self.header_card)
        self.title_label.setProperty('role', 'title')
        title_row.addWidget(self.title_label)

        self.tagline_label = QtWidgets.QLabel(self._tr('tagline'), self.header_card)
        self.tagline_label.setProperty('role', 'subtitle')
        title_row.addWidget(self.tagline_label)
        title_row.addStretch(1)

        self.guide_label = QtWidgets.QLabel(self._tr('header_guide'), self.header_card)
        self.guide_label.setProperty('role', 'guide')
        self.guide_label.setWordWrap(True)
        title_layout.addWidget(self.guide_label)

        self.recovery_banner = QtWidgets.QLabel(self.header_card)
        self.recovery_banner.setProperty('role', 'warning')
        self.recovery_banner.setWordWrap(True)
        self.recovery_banner.setVisible(self._recovery_mode)
        if self._recovery_mode:
            self.recovery_banner.setText(
                self._tr(
                    'data_root_recovery_banner_body',
                    path=self._recovery_context.get('path') or self._tr('none_value'),
                    reason=self._recovery_context.get('reason_text') or self._tr('none_value'),
                )
            )
            title_layout.addWidget(self.recovery_banner)

        controls_layout = QtWidgets.QVBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)
        header_layout.addLayout(controls_layout)

        self.version_label = QtWidgets.QLabel(self._tr('version', version=self._version), self.header_card)
        self.version_label.setProperty('role', 'muted')
        controls_layout.addWidget(self.version_label, 0, QtCore.Qt.AlignmentFlag.AlignRight)
        controls_layout.addStretch(1)

        controls_bottom = QtWidgets.QHBoxLayout()
        controls_bottom.setContentsMargins(0, 0, 0, 0)
        controls_bottom.setSpacing(8)
        controls_layout.addLayout(controls_bottom)

        self.language_caption = QtWidgets.QLabel(self._tr('language'), self.header_card)
        self.language_caption.setProperty('role', 'muted')
        controls_bottom.addWidget(self.language_caption, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)

        self.language_combo = QtWidgets.QComboBox(self.header_card)
        self.language_combo.setToolTip(self._tip('language_switch'))
        for code in ('zh-CN', 'en'):
            self.language_combo.addItem(language_label(code), code)
        self.language_combo.setCurrentText(language_label(self._language_code))
        self.language_combo.currentTextChanged.connect(self._on_language_changed)
        controls_bottom.addWidget(self.language_combo, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)

        self.header_toggle_button = QtWidgets.QToolButton(self.header_card)
        self.header_toggle_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header_toggle_button.setAutoRaise(False)
        self.header_toggle_button.setToolTip(self._tip('header_toggle'))
        self.header_toggle_button.clicked.connect(self._toggle_header_collapsed)
        controls_bottom.addWidget(self.header_toggle_button, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)

        self.main_tabs = QtWidgets.QTabWidget(central)
        self.main_tabs.currentChanged.connect(self._sync_status_bar)
        root_layout.addWidget(self.main_tabs, 1)

        self.config_workspace = ConfigWorkspace(
            config=self._config,
            paths=self._paths,
            language_code=self._language_code,
            theme=self._theme,
            parent=self.main_tabs,
            recovery_mode=self._recovery_mode,
            recovery_path=self._recovery_context.get('path', ''),
            recovery_reason_code=self._recovery_context.get('reason_code', ''),
            recovery_reason_text=self._recovery_context.get('reason_text', ''),
        )
        self.config_workspace.statusMessageChanged.connect(self._set_config_status_message)
        self.config_workspace.resultSummaryChanged.connect(self._set_config_result_summary)
        self.config_workspace.runtimeConfigChanged.connect(self._on_runtime_config_changed)
        self.config_workspace.uiPreferencesChanged.connect(self.apply_ui_preferences)
        self.config_workspace.tooltipPreferencesChanged.connect(self.apply_tooltip_preferences)
        if not self._recovery_mode:
            self.query_workspace = QueryWorkspace(
                config=self._config,
                paths=self._paths,
                language_code=self._language_code,
                theme=self._theme,
                parent=self.main_tabs,
            )
            self.query_workspace.statusMessageChanged.connect(self._set_query_status_message)
            self.query_workspace.resultSummaryChanged.connect(self._set_query_result_summary)
            self.main_tabs.addTab(self.query_workspace, self._tr('main_tab_query'))
            self.config_workspace.queryBlockStateChanged.connect(
                lambda blocked, title, detail: self.query_workspace.set_external_block_state(blocked=blocked, title=title, detail=detail)
            )
            self.config_workspace.queryReplayRequested.connect(self.query_workspace.rerun_current_query)
            self.config_workspace.logMessageAdded.connect(self.query_workspace.append_external_log)
            self.config_workspace.showQueryLogRequested.connect(self._show_query_log_tab)
            self.query_workspace.pageBlocklistRequested.connect(self.config_workspace.open_page_blocklist_dialog)
            self.query_workspace.sensitiveFilterRequested.connect(self.config_workspace.open_sensitive_filter_dialog)
            self.query_workspace.runtimeRepairRequested.connect(self._show_runtime_management)
            self.query_workspace.set_runtime_snapshot_provider(self.config_workspace.current_runtime_snapshot)
        else:
            self.query_workspace = None
        self.main_tabs.addTab(self.config_workspace, self._tr('main_tab_config'))
        if self._recovery_mode:
            self.main_tabs.setCurrentWidget(self.config_workspace)

        self.status_label = QtWidgets.QLabel(self._config_status_message if self._recovery_mode else self._query_status_message, self)
        self.status_label.setProperty('role', 'muted')
        self.result_label = QtWidgets.QLabel(self._config_result_summary if self._recovery_mode else self._query_result_summary, self)
        self.result_label.setProperty('role', 'muted')
        status_bar = QtWidgets.QStatusBar(self)
        status_bar.addWidget(self.status_label, 1)
        status_bar.addPermanentWidget(self.result_label)
        self.setStatusBar(status_bar)

    def _tr(self, key: str, **kwargs) -> str:
        return text(self._language_code, key, **kwargs)

    def _tip(self, key: str, **kwargs) -> str:
        return tooltip(self._language_code, key, **kwargs)

    def _recovery_status_text(self) -> str:
        if not self._recovery_mode:
            return self._tr('status_ready')
        return self._tr(
            'data_root_recovery_status',
            path=self._recovery_context.get('path') or self._tr('none_value'),
            reason=self._recovery_context.get('reason_text') or self._tr('none_value'),
        )

    def _refresh_header_collapsed(self) -> None:
        collapsed = self._header_collapsed
        self.tagline_label.setVisible(not collapsed)
        self.guide_label.setVisible(not collapsed)
        self.version_label.setVisible(not collapsed)
        self.header_toggle_button.setText(self._tr('header_expand') if collapsed else self._tr('header_collapse'))
        self.header_toggle_button.setArrowType(QtCore.Qt.ArrowType.DownArrow if collapsed else QtCore.Qt.ArrowType.UpArrow)
        layout = self.header_card.layout()
        if isinstance(layout, QtWidgets.QHBoxLayout):
            layout.setContentsMargins(12, 8, 12, 8) if collapsed else layout.setContentsMargins(12, 12, 12, 12)
        self.header_card.updateGeometry()
        self.adjustSize()

    def _toggle_header_collapsed(self) -> None:
        self._header_collapsed = not self._header_collapsed
        self._config.qt_header_collapsed = self._header_collapsed
        self._refresh_header_collapsed()
        if not self._recovery_mode:
            self._save_config_safely(self._config, self._paths)

    def _restore_window_state(self) -> None:
        geometry = self._decode_state(getattr(self._config, 'qt_window_geometry', ''))
        if geometry:
            self.restoreGeometry(QtCore.QByteArray(geometry))
        self._ensure_window_visible()
        if self.query_workspace is not None:
            self.query_workspace.restore_splitter_states(
                query_state=self._decode_state(getattr(self._config, 'qt_query_splitter_state', '')),
                results_state=self._decode_state(getattr(self._config, 'qt_results_splitter_state', '')),
            )
        self._sync_status_bar()

    def _ensure_window_visible(self) -> None:
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        frame = self.frameGeometry()
        screens = [screen.availableGeometry() for screen in app.screens()]
        if not screens:
            return
        if any(geometry.intersects(frame) for geometry in screens):
            return
        target = screens[0]
        width = min(max(self.width(), 1100), max(target.width() - 40, 1100))
        height = min(max(self.height(), 760), max(target.height() - 40, 760))
        self.resize(width, height)
        self.move(target.center().x() - self.width() // 2, target.center().y() - self.height() // 2)

    def _decode_state(self, raw_value: str) -> bytes | None:
        value = str(raw_value or '').strip()
        if not value:
            return None
        try:
            return bytes(QtCore.QByteArray.fromBase64(value.encode('ascii')))
        except Exception:
            return None

    def _encode_state(self, value: bytes) -> str:
        return bytes(QtCore.QByteArray(value).toBase64()).decode('ascii') if value else ''

    def _active_workspace_key(self) -> str:
        if self._recovery_mode:
            return 'config'
        if not hasattr(self, 'config_workspace'):
            return 'query'
        return 'config' if self.main_tabs.currentWidget() is self.config_workspace else 'query'

    def _sync_status_bar(self, *_args) -> None:
        if not hasattr(self, 'status_label') or not hasattr(self, 'result_label'):
            return
        if self._active_workspace_key() == 'config':
            self.status_label.setText(self._config_status_message)
            self.result_label.setText(self._config_result_summary)
        else:
            self.status_label.setText(self._query_status_message)
            self.result_label.setText(self._query_result_summary)

    def _set_query_status_message(self, value: str) -> None:
        self._query_status_message = value
        self._sync_status_bar()

    def _set_query_result_summary(self, value: str) -> None:
        self._query_result_summary = value
        self._sync_status_bar()

    def _show_query_log_tab(self) -> None:
        if self.query_workspace is None:
            return
        self.main_tabs.setCurrentWidget(self.query_workspace)
        self.query_workspace.show_log_tab()
        self._sync_status_bar()

    def _show_runtime_management(self) -> None:
        self.main_tabs.setCurrentWidget(self.config_workspace)
        self.config_workspace.focus_runtime_management()
        self._sync_status_bar()

    def _set_config_status_message(self, value: str) -> None:
        self._config_status_message = value
        self._sync_status_bar()

    def _set_config_result_summary(self, value: str) -> None:
        self._config_result_summary = value
        self._sync_status_bar()

    def _on_runtime_config_changed(self, config, paths) -> None:
        self._config = config
        self._paths = paths
        if self.query_workspace is not None:
            self.query_workspace.update_runtime(config=config, paths=paths)

    def _can_switch_language(self) -> tuple[bool, str, str]:
        if (self.query_workspace is not None and getattr(self.query_workspace, '_busy', False)) or getattr(self.config_workspace, '_busy', False):
            return False, self._tr('busy_title'), self._tr('busy_body')
        if getattr(self.config_workspace, '_watch_active', False):
            return False, self._tr('stop_watch_first_title'), self._tr('stop_watch_first_body')
        return True, '', ''

    def _reset_language_combo(self) -> None:
        self.language_combo.blockSignals(True)
        self.language_combo.setCurrentText(language_label(self._language_code))
        self.language_combo.blockSignals(False)

    def _on_language_changed(self, value: str) -> None:
        new_language_code = language_code_from_label(value)
        if new_language_code == self._language_code:
            return
        allowed, title, body = self._can_switch_language()
        if not allowed:
            self._reset_language_combo()
            QtWidgets.QMessageBox.information(self, title, body)
            return
        self._replace_window_for_language(new_language_code)

    def _snapshot_window_state(self) -> dict[str, object]:
        payload = {
            'geometry': bytes(self.saveGeometry()),
            'main_tab_index': self.main_tabs.currentIndex(),
            'header_collapsed': self._header_collapsed,
            'config_workspace': self.config_workspace.snapshot_view_state(),
        }
        if self.query_workspace is not None:
            payload['query_workspace'] = self.query_workspace.snapshot_view_state()
        return payload

    def _build_replacement_runtime(self, language_code: str) -> tuple[object, object]:
        if self._recovery_mode:
            config = replace(self._config)
            paths = self._paths
        else:
            try:
                config, paths = self.config_workspace._collect_config(False)
            except Exception:
                config = replace(self._config)
                paths = self._paths
            try:
                config.query_limit = int(self.query_workspace.limit_edit.text().strip() or config.query_limit)
            except Exception:
                pass
            try:
                config.query_score_threshold = float(self.query_workspace.threshold_edit.text().strip() or config.query_score_threshold)
            except Exception:
                pass
        config.ui_language = language_code
        config.qt_window_geometry = self._encode_state(bytes(self.saveGeometry()))
        if self.query_workspace is not None:
            config.qt_query_splitter_state = self._encode_state(self.query_workspace.query_splitter_state())
            config.qt_results_splitter_state = self._encode_state(self.query_workspace.results_splitter_state())
            config.qt_query_controls_collapsed = self.query_workspace.search_controls_collapsed()
        config.qt_header_collapsed = self._header_collapsed
        return config, paths

    def _apply_window_snapshot(self, snapshot: dict[str, object]) -> None:
        geometry = snapshot.get('geometry')
        if isinstance(geometry, (bytes, bytearray)):
            self.restoreGeometry(QtCore.QByteArray(bytes(geometry)))
        self._header_collapsed = bool(snapshot.get('header_collapsed', self._header_collapsed))
        self._config.qt_header_collapsed = self._header_collapsed
        self._refresh_header_collapsed()
        if self.query_workspace is not None:
            self.query_workspace.restore_view_state(snapshot.get('query_workspace') if isinstance(snapshot.get('query_workspace'), dict) else None)
        self.config_workspace.restore_view_state(snapshot.get('config_workspace') if isinstance(snapshot.get('config_workspace'), dict) else None)
        main_tab_index = int(snapshot.get('main_tab_index', 0) or 0)
        if 0 <= main_tab_index < self.main_tabs.count():
            self.main_tabs.setCurrentIndex(main_tab_index)
        self._sync_status_bar()

    def _replace_window_for_language(self, language_code: str) -> None:
        snapshot = self._snapshot_window_state()
        config, paths = self._build_replacement_runtime(language_code)
        self._config = config
        self._paths = paths
        self._language_code = language_code
        if not self._recovery_mode:
            self._save_config_safely(config, paths)
        replacement = MainWindow(
            config=config,
            paths=paths,
            language_code=language_code,
            theme=self._theme,
            version=self._version,
            recovery_mode=self._recovery_mode,
            recovery_context=self._recovery_context,
        )
        self._replacement_window = replacement
        self._register_live_window(replacement)
        replacement.show()
        replacement.raise_()
        replacement.activateWindow()
        QtCore.QTimer.singleShot(0, lambda snap=snapshot, win=replacement: win._apply_window_snapshot(snap))
        if not self._recovery_mode:
            QtCore.QTimer.singleShot(60, replacement.config_workspace.schedule_device_probe)
            QtCore.QTimer.singleShot(180, replacement.config_workspace.schedule_initial_status_load)
        self.close()

    def _save_config_safely(self, config, paths) -> None:
        try:
            save_config(config, paths)
        except Exception:
            pass

    def apply_ui_preferences(self, theme_code: str, scale_percent: int) -> None:
        self._config.ui_theme = theme_code
        self._config.ui_scale_percent = scale_percent
        theme = build_theme(theme_code, scale_percent)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            apply_application_style(app, theme, tooltips_enabled=bool(getattr(self._config, 'ui_tooltips_enabled', True)))
        self._theme = theme
        if self.query_workspace is not None:
            self.query_workspace.update_theme(theme)
        self.config_workspace.update_theme(theme)

    def apply_tooltip_preferences(self, enabled: bool) -> None:
        self._config.ui_tooltips_enabled = bool(enabled)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            apply_application_style(app, self._theme, tooltips_enabled=bool(enabled))

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            self.config_workspace.shutdown_extension_runtimes()
        except Exception:
            pass
        if not self._recovery_mode and self.query_workspace is not None:
            try:
                self._config.query_limit = int(self.query_workspace.limit_edit.text().strip() or self._config.query_limit)
            except Exception:
                pass
            try:
                self._config.query_score_threshold = float(self.query_workspace.threshold_edit.text().strip() or self._config.query_score_threshold)
            except Exception:
                pass
            self._config.qt_window_geometry = self._encode_state(bytes(self.saveGeometry()))
            self._config.qt_query_splitter_state = self._encode_state(self.query_workspace.query_splitter_state())
            self._config.qt_results_splitter_state = self._encode_state(self.query_workspace.results_splitter_state())
            self._config.qt_query_controls_collapsed = self.query_workspace.search_controls_collapsed()
            self._config.qt_header_collapsed = self._header_collapsed
            self._save_config_safely(self._config, self._paths)
        super().closeEvent(event)
