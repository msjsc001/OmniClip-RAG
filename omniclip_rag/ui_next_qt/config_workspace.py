from __future__ import annotations
import sys
import time
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any
from PySide6 import QtCore, QtGui, QtWidgets
from ..app_logging import LOG_BACKUP_COUNT, configure_file_logging
from ..build_control import ResourceSample, format_resource_sample, normalize_build_resource_profile
from ..config import (
    DEFAULT_LOG_FILE_SIZE_MB,
    LOG_FILE_SIZE_MB_MAX,
    LOG_FILE_SIZE_MB_MIN,
    AppConfig,
    UI_SCALE_PERCENT_MAX,
    UI_SCALE_PERCENT_MIN,
    WATCH_RESOURCE_PEAK_OPTIONS,
    default_data_root,
    ensure_data_paths,
    load_config,
    normalize_ui_scale_percent,
    normalize_ui_theme,
    normalize_vault_path,
    normalize_watch_resource_peak_percent,
    normalize_log_file_size_mb,
    save_config,
)
from ..formatting import format_bytes, format_duration, format_space_report, summarize_preflight
from ..preflight import estimate_model_cache_bytes
from ..service import WATCHDOG_AVAILABLE, OmniClipService
from ..ui_i18n import text, tooltip
from ..ui_shared import merge_page_filter_defaults
from ..vector_index import detect_acceleration, get_local_model_dir, is_local_model_ready, model_download_guidance_context, resolve_vector_device, runtime_dependency_issue, runtime_guidance_context
from ..reranker import get_local_reranker_dir, is_local_reranker_ready
from .filter_dialogs import PageBlocklistDialog, SensitiveFilterDialog
from .theme import ThemeState, scaled
from .runtime_guidance_dialog import RuntimeGuidanceDialog
from .model_download_dialog import ModelDownloadDialog
from .workers import FunctionWorker, ServiceTaskWorker, WatchWorker
REPO_URL = 'https://github.com/msjsc001/OmniClip-RAG'
LOGGER = logging.getLogger(__name__)
class ConfigWorkspace(QtWidgets.QWidget):
    statusMessageChanged = QtCore.Signal(str)
    resultSummaryChanged = QtCore.Signal(str)
    runtimeConfigChanged = QtCore.Signal(object, object)
    queryBlockStateChanged = QtCore.Signal(bool, str, str)
    queryReplayRequested = QtCore.Signal()
    logMessageAdded = QtCore.Signal(str)
    showQueryLogRequested = QtCore.Signal()
    uiPreferencesChanged = QtCore.Signal(str, int)
    def __init__(self, *, config, paths, language_code: str, theme: ThemeState, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._paths = paths
        self._language_code = language_code
        self._theme = theme
        self._saved_vaults = list(getattr(config, 'vault_paths', []))
        self._busy = False
        self._watch_active = False
        self._watch_stopping = False
        self._watch_mode = 'watchdog'
        self._status_snapshot: dict[str, object] | None = None
        self._current_report = None
        self._latest_preflight_snapshot: dict[str, object] | None = None
        self._task_worker: ServiceTaskWorker | None = None
        self._task_success_handler = None
        self._task_outcome_kind: str | None = None
        self._task_outcome_payload: object = None
        self._task_outcome_message = ''
        self._task_outcome_traceback = ''
        self._task_started_at = 0.0
        self._task_paused_started_at = 0.0
        self._task_paused_total_seconds = 0.0
        self._task_last_eta_text = self._tr('task_eta_idle')
        self._latest_task_progress: dict[str, object] | None = None
        self._active_task_key: str | None = None
        self._watch_worker: WatchWorker | None = None
        self._acceleration_payload: dict[str, object] | None = None
        self._device_probe_worker: FunctionWorker | None = None
        self._device_probe_scheduled = False
        self._device_runtime_prompt_suppressed = False
        self._live_runtime_sync_suppressed = False
        self._initial_status_worker: ServiceTaskWorker | None = None
        self._initial_status_scheduled = False
        self._resume_prompt_workspace_id: str | None = None
        self._rebuild_pause_event = __import__('threading').Event()
        self._rebuild_cancel_event = __import__('threading').Event()
        self._task_timer = QtCore.QTimer(self)
        self._task_timer.setInterval(500)
        self._task_timer.timeout.connect(self._tick_task_feedback)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        header_card = QtWidgets.QFrame(self)
        header_card.setProperty('card', True)
        header_layout = QtWidgets.QHBoxLayout(header_card)
        header_layout.setContentsMargins(12, 12, 12, 12)
        header_layout.setSpacing(10)
        root.addWidget(header_card)
        title_layout = QtWidgets.QVBoxLayout()
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(6)
        header_layout.addLayout(title_layout, 1)
        workspace_title = QtWidgets.QLabel(self._tr('workspace_title'), header_card)
        workspace_title.setProperty('role', 'cardTitle')
        title_layout.addWidget(workspace_title)
        workspace_subtitle = QtWidgets.QLabel(self._tr('workspace_subtitle'), header_card)
        workspace_subtitle.setProperty('role', 'subtitle')
        workspace_subtitle.setWordWrap(True)
        title_layout.addWidget(workspace_subtitle)
        help_button = QtWidgets.QPushButton(self._tr('help_updates'), header_card)
        help_button.setProperty('variant', 'secondary')
        help_button.setToolTip(self._tip('help_updates'))
        help_button.clicked.connect(self._open_help_and_updates)
        header_layout.addWidget(help_button, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        self.sub_tabs = QtWidgets.QTabWidget(self)
        root.addWidget(self.sub_tabs, 1)
        self.start_page, self.start_body = self._make_scroll_tab()
        self.settings_page, self.settings_body = self._make_scroll_tab()
        self.ui_page, self.ui_body = self._make_scroll_tab()
        self.retrieval_page, self.retrieval_body = self._make_scroll_tab()
        self.data_page, self.data_body = self._make_scroll_tab()
        self.sub_tabs.addTab(self.start_page, self._tr('left_tab_start'))
        self.sub_tabs.addTab(self.settings_page, self._tr('left_tab_settings'))
        self.sub_tabs.addTab(self.ui_page, self._tr('left_tab_ui'))
        self.sub_tabs.addTab(self.retrieval_page, self._tr('left_tab_retrieval'))
        self.sub_tabs.addTab(self.data_page, self._tr('left_tab_data'))
        self._build_start_page(self.start_body)
        self._build_settings_page(self.settings_body)
        self._build_ui_page(self.ui_body)
        self._build_retrieval_page(self.retrieval_body)
        self._build_data_page(self.data_body)
        self.device_combo.currentTextChanged.connect(self._on_device_selection_changed)
        self.backend_combo.currentTextChanged.connect(self._on_runtime_sensitive_setting_changed)
        self.runtime_combo.currentTextChanged.connect(self._on_runtime_sensitive_setting_changed)
        self.model_edit.textChanged.connect(self._on_model_text_changed)
        self.reranker_enabled_check.toggled.connect(self._on_live_runtime_preferences_changed)
        self.export_ai_check.toggled.connect(self._on_live_runtime_preferences_changed)
        self.reranker_model_edit.textChanged.connect(self._on_live_runtime_preferences_changed)
        self.reranker_batch_cpu_edit.textChanged.connect(self._on_live_runtime_preferences_changed)
        self.reranker_batch_cuda_edit.textChanged.connect(self._on_live_runtime_preferences_changed)
        self._apply_config_to_controls(self._config, self._paths)
        self._refresh_status_summary(snapshot=None)
    def _tr(self, key: str, **kwargs) -> str:
        return text(self._language_code, key, **kwargs)
    def _tip(self, key: str, **kwargs) -> str:
        return tooltip(self._language_code, key, **kwargs)
    def _set_button_variant(self, button: QtWidgets.QPushButton, variant: str) -> None:
        button.setProperty('variant', variant)
        style = button.style()
        style.unpolish(button)
        style.polish(button)
        button.update()
    def _make_card(self, title_key: str | None = None) -> tuple[QtWidgets.QFrame, QtWidgets.QVBoxLayout]:
        card = QtWidgets.QFrame(self)
        card.setProperty('card', True)
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        if title_key:
            title = QtWidgets.QLabel(self._tr(title_key), card)
            title.setProperty('role', 'cardTitle')
            layout.addWidget(title)
        return card, layout
    def _make_scroll_tab(self) -> tuple[QtWidgets.QWidget, QtWidgets.QWidget]:
        page = QtWidgets.QWidget(self.sub_tabs)
        page_layout = QtWidgets.QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        page_layout.addWidget(scroll)
        body = QtWidgets.QWidget(scroll)
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        return page, body
    def _insert_card(self, parent: QtWidgets.QWidget, index: int, card: QtWidgets.QFrame) -> None:
        parent.layout().insertWidget(index, card)
    def _current_task_elapsed_seconds(self) -> float:
        if not self._task_started_at:
            return 0.0
        paused_extra = max(time.time() - self._task_paused_started_at, 0.0) if self._task_paused_started_at else 0.0
        return max(time.time() - self._task_started_at - self._task_paused_total_seconds - paused_extra, 0.0)
    def _format_elapsed(self, elapsed_seconds: float) -> str:
        total_seconds = max(0, int(elapsed_seconds))
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f'{hours:02d}:{minutes:02d}:{seconds:02d}'
        return f'{minutes:02d}:{seconds:02d}'
    def _build_start_page(self, parent: QtWidgets.QWidget) -> None:
        quick_card, quick_layout = self._make_card('quick_start_title')
        self._insert_card(parent, 0, quick_card)
        top_row = QtWidgets.QHBoxLayout()
        quick_layout.addLayout(top_row)
        subtitle = QtWidgets.QLabel(self._tr('quick_start_subtitle'), quick_card)
        subtitle.setProperty('role', 'subtitle')
        subtitle.setWordWrap(True)
        top_row.addWidget(subtitle, 1)
        self.quick_start_button = QtWidgets.QPushButton(quick_card)
        self._set_button_variant(self.quick_start_button, 'secondary')
        self.quick_start_button.setToolTip(self._tip('quick_start_toggle'))
        self.quick_start_button.clicked.connect(self._toggle_quick_start)
        top_row.addWidget(self.quick_start_button)
        self.quick_steps_widget = QtWidgets.QWidget(quick_card)
        steps_layout = QtWidgets.QVBoxLayout(self.quick_steps_widget)
        steps_layout.setContentsMargins(0, 0, 0, 0)
        steps_layout.setSpacing(8)
        for index, key in enumerate(('step_1', 'step_2', 'step_3'), start=1):
            step = QtWidgets.QLabel(self._tr(key), self.quick_steps_widget)
            step.setWordWrap(True)
            step.setProperty('role', 'guide')
            steps_layout.addWidget(step)
        quick_layout.addWidget(self.quick_steps_widget)
        chips = QtWidgets.QHBoxLayout()
        chips.setSpacing(8)
        quick_layout.addLayout(chips)
        self.vault_chip = QtWidgets.QLabel(quick_card)
        self.model_chip = QtWidgets.QLabel(quick_card)
        self.index_chip = QtWidgets.QLabel(quick_card)
        for chip in (self.vault_chip, self.model_chip, self.index_chip):
            chip.setMargin(8)
            chips.addWidget(chip)
        chips.addStretch(1)
        workspace_card, workspace_layout = self._make_card()
        self._insert_card(parent, 1, workspace_card)
        form = QtWidgets.QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        workspace_layout.addLayout(form)
        saved_caption = QtWidgets.QLabel(self._tr('saved_vaults_label'), workspace_card)
        saved_caption.setProperty('role', 'muted')
        form.addWidget(saved_caption, 0, 0)
        self.saved_vault_combo = QtWidgets.QComboBox(workspace_card)
        self.saved_vault_combo.currentTextChanged.connect(self._on_saved_vault_selected)
        self.saved_vault_combo.setToolTip(self._tip('saved_vaults'))
        form.addWidget(self.saved_vault_combo, 0, 1)
        remove_button = QtWidgets.QPushButton(self._tr('remove_saved_vault'), workspace_card)
        self._set_button_variant(remove_button, 'secondary')
        remove_button.setToolTip(self._tip('remove_saved_vault'))
        remove_button.clicked.connect(self._remove_selected_vault)
        form.addWidget(remove_button, 0, 2)
        vault_label = QtWidgets.QLabel(self._tr('vault_label'), workspace_card)
        vault_label.setProperty('role', 'muted')
        form.addWidget(vault_label, 1, 0)
        self.vault_edit = QtWidgets.QLineEdit(workspace_card)
        self.vault_edit.setToolTip(self._tip('vault'))
        form.addWidget(self.vault_edit, 1, 1)
        browse_vault = QtWidgets.QPushButton('...', workspace_card)
        browse_vault.setToolTip(self._tip('browse_vault'))
        self._set_button_variant(browse_vault, 'secondary')
        browse_vault.clicked.connect(self._browse_vault)
        form.addWidget(browse_vault, 1, 2)
        data_label = QtWidgets.QLabel(self._tr('data_dir_label'), workspace_card)
        data_label.setProperty('role', 'muted')
        form.addWidget(data_label, 2, 0)
        self.data_dir_edit = QtWidgets.QLineEdit(workspace_card)
        self.data_dir_edit.setToolTip(self._tip('data_dir'))
        form.addWidget(self.data_dir_edit, 2, 1)
        browse_data = QtWidgets.QPushButton('...', workspace_card)
        browse_data.setToolTip(self._tip('browse_data'))
        self._set_button_variant(browse_data, 'secondary')
        browse_data.clicked.connect(self._browse_data_root)
        form.addWidget(browse_data, 2, 2)
        self.workspace_summary_label = QtWidgets.QLabel(workspace_card)
        self.workspace_summary_label.setWordWrap(True)
        self.workspace_summary_label.setProperty('role', 'guide')
        workspace_layout.addWidget(self.workspace_summary_label)
        actions_card, actions_layout = self._make_card()
        self._insert_card(parent, 2, actions_card)
        action_row = QtWidgets.QGridLayout()
        action_row.setHorizontalSpacing(8)
        action_row.setVerticalSpacing(8)
        actions_layout.addLayout(action_row)
        self.preflight_button = QtWidgets.QPushButton(self._tr('preflight_button'), actions_card)
        self.preflight_button.setToolTip(self._tip('preflight'))
        self._set_button_variant(self.preflight_button, 'secondary')
        self.preflight_button.clicked.connect(self._run_preflight)
        action_row.addWidget(self.preflight_button, 0, 0)
        self.bootstrap_button = QtWidgets.QPushButton(self._tr('bootstrap_button'), actions_card)
        self.bootstrap_button.setToolTip(self._tip('bootstrap'))
        self._set_button_variant(self.bootstrap_button, 'secondary')
        self.bootstrap_button.clicked.connect(self._run_bootstrap_model)
        action_row.addWidget(self.bootstrap_button, 0, 1)
        self.rebuild_button = QtWidgets.QPushButton(self._tr('rebuild_button'), actions_card)
        self.rebuild_button.setToolTip(self._tip('rebuild'))
        self._set_button_variant(self.rebuild_button, 'primary')
        self.rebuild_button.clicked.connect(self._run_rebuild)
        action_row.addWidget(self.rebuild_button, 1, 0)
        self.watch_button = QtWidgets.QPushButton(actions_card)
        self.watch_button.setToolTip(self._tip('watch'))
        self.watch_button.clicked.connect(self._toggle_watch)
        action_row.addWidget(self.watch_button, 1, 1)
        status_card, status_layout = self._make_card()
        self._insert_card(parent, 3, status_card)
        stat_row = QtWidgets.QHBoxLayout()
        stat_row.setSpacing(10)
        status_layout.addLayout(stat_row)
        self.files_value = self._make_stat_card(stat_row, 'stat_files')
        self.chunks_value = self._make_stat_card(stat_row, 'stat_chunks')
        self.refs_value = self._make_stat_card(stat_row, 'stat_refs')
        self.preflight_label = QtWidgets.QLabel(status_card)
        self.preflight_label.setWordWrap(True)
        self.preflight_label.setProperty('role', 'muted')
        status_layout.addWidget(self.preflight_label)
        self.preflight_notice_label = QtWidgets.QLabel(status_card)
        self.preflight_notice_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.preflight_notice_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextBrowserInteraction)
        self.preflight_notice_label.setOpenExternalLinks(False)
        self.preflight_notice_label.linkActivated.connect(self._on_preflight_notice_link)
        self.preflight_notice_label.setWordWrap(True)
        self.preflight_notice_label.setProperty('role', 'guide')
        self.preflight_notice_label.setVisible(False)
        status_layout.addWidget(self.preflight_notice_label)
        self.watch_summary_label = QtWidgets.QLabel(status_card)
        self.watch_summary_label.setWordWrap(True)
        self.watch_summary_label.setProperty('role', 'muted')
        status_layout.addWidget(self.watch_summary_label)
        task_card, task_layout = self._make_card()
        self._insert_card(parent, 4, task_card)
        self.task_progress = QtWidgets.QProgressBar(task_card)
        self.task_progress.setRange(0, 100)
        self.task_progress.setValue(0)
        self.task_progress.setTextVisible(False)
        task_layout.addWidget(self.task_progress)
        task_actions = QtWidgets.QHBoxLayout()
        task_layout.addLayout(task_actions)
        task_actions.addStretch(1)
        self.rebuild_pause_button = QtWidgets.QPushButton(self._tr('pause_rebuild'), task_card)
        self.rebuild_pause_button.setToolTip(self._tip('pause_rebuild'))
        self._set_button_variant(self.rebuild_pause_button, 'secondary')
        self.rebuild_pause_button.clicked.connect(self._toggle_rebuild_pause)
        task_actions.addWidget(self.rebuild_pause_button)
        self.rebuild_cancel_button = QtWidgets.QPushButton(self._tr('cancel_rebuild_confirm_title'), task_card)
        self.rebuild_cancel_button.setToolTip(self._tip('cancel_rebuild'))
        self._set_button_variant(self.rebuild_cancel_button, 'danger')
        self.rebuild_cancel_button.clicked.connect(self._cancel_rebuild)
        task_actions.addWidget(self.rebuild_cancel_button)
        self.task_state_label = QtWidgets.QLabel(task_card)
        self.task_percent_label = QtWidgets.QLabel(task_card)
        self.task_elapsed_label = QtWidgets.QLabel(task_card)
        self.task_eta_label = QtWidgets.QLabel(task_card)
        self.task_detail_label = QtWidgets.QLabel(task_card)
        self.task_detail_label.setWordWrap(True)
        self.task_detail_label.setProperty('role', 'muted')
        for widget in (self.task_state_label, self.task_percent_label, self.task_elapsed_label, self.task_eta_label):
            task_layout.addWidget(widget)
        task_layout.addWidget(self.task_detail_label)
        self.task_state_label.setText(self._tr('task_idle'))
        self.task_percent_label.setText(self._tr('task_percent_idle'))
        self.task_elapsed_label.setText(self._tr('task_elapsed', value='00:00'))
        self.task_eta_label.setText(self._tr('task_eta_idle'))
        self.task_detail_label.setText(self._tr('task_idle_detail'))
    def _build_settings_page(self, parent: QtWidgets.QWidget) -> None:
        card, layout = self._make_card()
        self._insert_card(parent, 0, card)
        subtitle = QtWidgets.QLabel(self._tr('settings_subtitle'), card)
        subtitle.setProperty('role', 'subtitle')
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        self.device_summary_label = QtWidgets.QLabel(card)
        self.device_summary_label.setProperty('role', 'guide')
        self.device_summary_label.setWordWrap(True)
        layout.addWidget(self.device_summary_label)
        form = QtWidgets.QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        layout.addLayout(form)
        self.backend_combo = QtWidgets.QComboBox(card)
        self.backend_combo.setToolTip(self._tip('backend'))
        self.backend_combo.addItems(['lancedb', 'disabled'])
        self.model_edit = QtWidgets.QLineEdit(card)
        self.model_edit.setToolTip(self._tip('model'))
        self.runtime_combo = QtWidgets.QComboBox(card)
        self.runtime_combo.setToolTip(self._tip('runtime'))
        self.runtime_combo.addItems(['torch', 'onnx'])
        self.device_combo = QtWidgets.QComboBox(card)
        self.device_combo.setToolTip(self._tip('device'))
        for code in ('auto', 'cpu'):
            self.device_combo.addItem(self._device_option_label(code), code)
        self.interval_edit = QtWidgets.QLineEdit(card)
        self.interval_edit.setToolTip(self._tip('interval'))
        self.build_profile_combo = QtWidgets.QComboBox(card)
        self.build_profile_combo.setToolTip(self._tip('build_resource_profile'))
        self.build_profile_combo.addItems(self._build_profile_choices())
        self.watch_peak_combo = QtWidgets.QComboBox(card)
        self.watch_peak_combo.setToolTip(self._tip('watch_resource_peak'))
        self.watch_peak_combo.addItems(self._watch_peak_choices())
        rows = [
            (0, 'backend_label', self.backend_combo),
            (1, 'model_label', self.model_edit),
            (2, 'runtime_label', self.runtime_combo),
            (3, 'device_label', self.device_combo),
            (5, 'interval_label', self.interval_edit),
            (6, 'build_resource_profile_label', self.build_profile_combo),
            (7, 'watch_resource_peak_label', self.watch_peak_combo),
        ]
        for row_index, label_key, widget in rows:
            label = QtWidgets.QLabel(self._tr(label_key), card)
            label.setProperty('role', 'muted')
            form.addWidget(label, row_index, 0)
            form.addWidget(widget, row_index, 1)
        self.device_runtime_status_label = QtWidgets.QLabel(card)
        self.device_runtime_status_label.setWordWrap(True)
        self.device_runtime_status_label.setProperty('role', 'guide')
        self.device_runtime_status_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addWidget(self.device_runtime_status_label, 4, 1)
        actions = QtWidgets.QHBoxLayout()
        layout.addLayout(actions)
        recommended_button = QtWidgets.QPushButton(self._tr('apply_recommended'), card)
        recommended_button.setToolTip(self._tip('recommended'))
        self._set_button_variant(recommended_button, 'secondary')
        recommended_button.clicked.connect(self._apply_recommended)
        actions.addWidget(recommended_button)
        load_button = QtWidgets.QPushButton(self._tr('load_config'), card)
        load_button.setToolTip(self._tip('load_config'))
        self._set_button_variant(load_button, 'secondary')
        load_button.clicked.connect(self._load_config_from_current_dir)
        actions.addWidget(load_button)
        save_button = QtWidgets.QPushButton(self._tr('save_config'), card)
        save_button.setToolTip(self._tip('save_config'))
        self._set_button_variant(save_button, 'primary')
        save_button.clicked.connect(self._save_only)
        actions.addWidget(save_button)
        actions.addStretch(1)
        self.advanced_toggle_button = QtWidgets.QPushButton(card)
        self._set_button_variant(self.advanced_toggle_button, 'secondary')
        self.advanced_toggle_button.clicked.connect(self._toggle_advanced)
        layout.addWidget(self.advanced_toggle_button)
        self.advanced_widget = QtWidgets.QWidget(card)
        advanced_layout = QtWidgets.QVBoxLayout(self.advanced_widget)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(8)
        self.local_only_check = QtWidgets.QCheckBox(self._tr('local_only_label'), self.advanced_widget)
        self.local_only_check.setToolTip(self._tip('local_only'))
        self.force_check = QtWidgets.QCheckBox(self._tr('force_label'), self.advanced_widget)
        self.force_check.setToolTip(self._tip('force'))
        self.polling_check = QtWidgets.QCheckBox(self._tr('polling_label'), self.advanced_widget)
        self.polling_check.setToolTip(self._tip('polling'))
        advanced_layout.addWidget(self.local_only_check)
        advanced_layout.addWidget(self.force_check)
        advanced_layout.addWidget(self.polling_check)
        layout.addWidget(self.advanced_widget)
        refresh_button = QtWidgets.QPushButton(self._tr('refresh_button'), card)
        self._set_button_variant(refresh_button, 'secondary')
        refresh_button.clicked.connect(self._run_refresh)
        layout.addWidget(refresh_button, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
    def _build_ui_page(self, parent: QtWidgets.QWidget) -> None:
        card, layout = self._make_card()
        self._insert_card(parent, 0, card)
        subtitle = QtWidgets.QLabel(self._tr('ui_subtitle'), card)
        subtitle.setProperty('role', 'subtitle')
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        form = QtWidgets.QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        layout.addLayout(form)
        scale_label = QtWidgets.QLabel(self._tr('ui_scale_label'), card)
        scale_label.setProperty('role', 'muted')
        form.addWidget(scale_label, 0, 0)
        self.ui_scale_spin = QtWidgets.QSpinBox(card)
        self.ui_scale_spin.setToolTip(self._tip('ui_scale'))
        self.ui_scale_spin.setRange(UI_SCALE_PERCENT_MIN, UI_SCALE_PERCENT_MAX)
        self.ui_scale_spin.setSuffix('%')
        form.addWidget(self.ui_scale_spin, 0, 1)
        theme_label = QtWidgets.QLabel(self._tr('ui_theme_label'), card)
        theme_label.setProperty('role', 'muted')
        form.addWidget(theme_label, 1, 0)
        self.ui_theme_combo = QtWidgets.QComboBox(card)
        self.ui_theme_combo.setToolTip(self._tip('ui_theme'))
        self.ui_theme_combo.addItems(self._ui_theme_choices())
        form.addWidget(self.ui_theme_combo, 1, 1)
        hint = QtWidgets.QLabel(self._tr('ui_scale_hint'), card)
        hint.setProperty('role', 'muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        apply_button = QtWidgets.QPushButton(self._tr('apply_ui_button'), card)
        apply_button.setToolTip(self._tip('apply_ui'))
        self._set_button_variant(apply_button, 'primary')
        apply_button.clicked.connect(self._apply_ui_preferences)
        layout.addWidget(apply_button, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
    def _build_retrieval_page(self, parent: QtWidgets.QWidget) -> None:
        card, layout = self._make_card()
        self._insert_card(parent, 0, card)
        subtitle = QtWidgets.QLabel(self._tr('retrieval_subtitle'), card)
        subtitle.setProperty('role', 'subtitle')
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        self.reranker_state_label = QtWidgets.QLabel(card)
        self.reranker_state_label.setWordWrap(True)
        layout.addWidget(self.reranker_state_label)
        self.reranker_enabled_check = QtWidgets.QCheckBox(self._tr('reranker_enable_label'), card)
        self.reranker_enabled_check.setToolTip(self._tip('reranker_enable'))
        self.export_ai_check = QtWidgets.QCheckBox(self._tr('export_ai_collab_label'), card)
        self.export_ai_check.setToolTip(self._tip('export_ai_collab'))
        layout.addWidget(self.reranker_enabled_check)
        layout.addWidget(self.export_ai_check)
        form = QtWidgets.QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        layout.addLayout(form)
        self.reranker_model_edit = QtWidgets.QLineEdit(card)
        self.reranker_model_edit.setToolTip(self._tip('reranker_model'))
        self.reranker_batch_cpu_edit = QtWidgets.QLineEdit(card)
        self.reranker_batch_cpu_edit.setToolTip(self._tip('reranker_batch_cpu'))
        self.reranker_batch_cuda_edit = QtWidgets.QLineEdit(card)
        self.reranker_batch_cuda_edit.setToolTip(self._tip('reranker_batch_cuda'))
        retrieval_rows = [
            ('reranker_model_label', self.reranker_model_edit),
            ('reranker_batch_cpu_label', self.reranker_batch_cpu_edit),
            ('reranker_batch_cuda_label', self.reranker_batch_cuda_edit),
        ]
        for row_index, (label_key, widget) in enumerate(retrieval_rows):
            label = QtWidgets.QLabel(self._tr(label_key), card)
            label.setProperty('role', 'muted')
            form.addWidget(label, row_index, 0)
            form.addWidget(widget, row_index, 1)
        actions = QtWidgets.QHBoxLayout()
        layout.addLayout(actions)
        download_button = QtWidgets.QPushButton(self._tr('bootstrap_reranker_button'), card)
        download_button.setToolTip(self._tip('bootstrap_reranker'))
        self._set_button_variant(download_button, 'primary')
        download_button.clicked.connect(self._run_bootstrap_reranker)
        actions.addWidget(download_button)
        refresh_button = QtWidgets.QPushButton(self._tr('refresh_button'), card)
        self._set_button_variant(refresh_button, 'secondary')
        refresh_button.clicked.connect(self._run_refresh)
        actions.addWidget(refresh_button)
        actions.addStretch(1)
    def _build_data_page(self, parent: QtWidgets.QWidget) -> None:
        card, layout = self._make_card()
        self._insert_card(parent, 0, card)
        subtitle = QtWidgets.QLabel(self._tr('data_subtitle'), card)
        subtitle.setProperty('role', 'subtitle')
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        self.data_workspace_label = QtWidgets.QLabel(card)
        self.data_workspace_label.setProperty('role', 'guide')
        self.data_workspace_label.setWordWrap(True)
        layout.addWidget(self.data_workspace_label)
        buttons = QtWidgets.QGridLayout()
        buttons.setHorizontalSpacing(8)
        buttons.setVerticalSpacing(8)
        layout.addLayout(buttons)
        open_vault = QtWidgets.QPushButton(self._tr('open_vault'), card)
        open_vault.setToolTip(self._tip('open_vault'))
        self._set_button_variant(open_vault, 'secondary')
        open_vault.clicked.connect(self._open_vault_dir)
        buttons.addWidget(open_vault, 0, 0)
        open_data = QtWidgets.QPushButton(self._tr('open_data'), card)
        open_data.setToolTip(self._tip('open_data'))
        self._set_button_variant(open_data, 'secondary')
        open_data.clicked.connect(self._open_data_dir)
        buttons.addWidget(open_data, 0, 1)
        open_exports = QtWidgets.QPushButton(self._tr('open_exports'), card)
        open_exports.setToolTip(self._tip('open_exports'))
        self._set_button_variant(open_exports, 'secondary')
        open_exports.clicked.connect(self._open_exports_dir)
        buttons.addWidget(open_exports, 1, 0)
        open_logs = QtWidgets.QPushButton(self._tr('open_logs'), card)
        open_logs.setToolTip(self._tip('open_logs'))
        self._set_button_variant(open_logs, 'secondary')
        open_logs.clicked.connect(self._open_logs_dir)
        buttons.addWidget(open_logs, 1, 1)
        log_title = QtWidgets.QLabel(self._tr('log_settings_title'), card)
        log_title.setProperty('role', 'cardTitle')
        layout.addWidget(log_title)
        log_hint = QtWidgets.QLabel(self._tr('log_settings_hint', default=DEFAULT_LOG_FILE_SIZE_MB, backups=LOG_BACKUP_COUNT + 1), card)
        log_hint.setProperty('role', 'muted')
        log_hint.setWordWrap(True)
        layout.addWidget(log_hint)
        log_form = QtWidgets.QGridLayout()
        log_form.setHorizontalSpacing(10)
        log_form.setVerticalSpacing(8)
        layout.addLayout(log_form)
        log_limit_label = QtWidgets.QLabel(self._tr('log_size_limit_label'), card)
        log_limit_label.setProperty('role', 'muted')
        log_form.addWidget(log_limit_label, 0, 0)
        self.log_size_spin = QtWidgets.QSpinBox(card)
        self.log_size_spin.setRange(LOG_FILE_SIZE_MB_MIN, LOG_FILE_SIZE_MB_MAX)
        self.log_size_spin.setSuffix(' MB')
        self.log_size_spin.setToolTip(self._tip('log_size_limit'))
        log_form.addWidget(self.log_size_spin, 0, 1)
        save_log_button = QtWidgets.QPushButton(self._tr('apply_log_settings'), card)
        save_log_button.setToolTip(self._tip('apply_log_settings'))
        self._set_button_variant(save_log_button, 'secondary')
        save_log_button.clicked.connect(self._save_log_preferences)
        log_form.addWidget(save_log_button, 0, 2)
        self.log_storage_summary_label = QtWidgets.QLabel(card)
        self.log_storage_summary_label.setProperty('role', 'guide')
        self.log_storage_summary_label.setWordWrap(True)
        layout.addWidget(self.log_storage_summary_label)
        self.clear_index_check = QtWidgets.QCheckBox(self._tr('clear_index_label'), card)
        self.clear_index_check.setToolTip(self._tip('clear'))
        self.clear_logs_check = QtWidgets.QCheckBox(self._tr('clear_logs_label'), card)
        self.clear_logs_check.setToolTip(self._tip('clear'))
        self.clear_cache_check = QtWidgets.QCheckBox(self._tr('clear_cache_label'), card)
        self.clear_cache_check.setToolTip(self._tip('clear'))
        self.clear_exports_check = QtWidgets.QCheckBox(self._tr('clear_exports_label'), card)
        self.clear_exports_check.setToolTip(self._tip('clear'))
        layout.addWidget(self.clear_index_check)
        layout.addWidget(self.clear_logs_check)
        layout.addWidget(self.clear_cache_check)
        layout.addWidget(self.clear_exports_check)
        clear_button = QtWidgets.QPushButton(self._tr('clear_button'), card)
        clear_button.setToolTip(self._tip('clear'))
        self._set_button_variant(clear_button, 'danger')
        clear_button.clicked.connect(self._run_clear)
        layout.addWidget(clear_button, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
    def _make_stat_card(self, parent_layout: QtWidgets.QHBoxLayout, label_key: str) -> QtWidgets.QLabel:
        card = QtWidgets.QFrame(self)
        card.setProperty('card', True)
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)
        label = QtWidgets.QLabel(self._tr(label_key), card)
        label.setProperty('role', 'muted')
        layout.addWidget(label)
        value = QtWidgets.QLabel('0', card)
        value.setProperty('role', 'title')
        layout.addWidget(value)
        parent_layout.addWidget(card, 1)
        return value
    def _ui_theme_label(self, code: str) -> str:
        return self._tr(f'ui_theme_{normalize_ui_theme(code)}')
    def _ui_theme_code(self, label: str) -> str:
        mapping = {
            self._tr('ui_theme_system'): 'system',
            self._tr('ui_theme_light'): 'light',
            self._tr('ui_theme_dark'): 'dark',
            'system': 'system',
            'light': 'light',
            'dark': 'dark',
        }
        return mapping.get(str(label or '').strip(), normalize_ui_theme(getattr(self._config, 'ui_theme', 'system')))
    def _ui_theme_choices(self) -> list[str]:
        return [self._ui_theme_label('system'), self._ui_theme_label('light'), self._ui_theme_label('dark')]
    def _build_profile_label(self, profile: str) -> str:
        return self._tr(f'build_profile_{normalize_build_resource_profile(profile)}')
    def _build_profile_code(self, label: str) -> str:
        mapping = {
            self._tr('build_profile_quiet'): 'quiet',
            self._tr('build_profile_balanced'): 'balanced',
            self._tr('build_profile_peak'): 'peak',
            'quiet': 'quiet',
            'balanced': 'balanced',
            'peak': 'peak',
        }
        return mapping.get(str(label or '').strip(), 'balanced')
    def _build_profile_choices(self) -> list[str]:
        return [self._build_profile_label('quiet'), self._build_profile_label('balanced'), self._build_profile_label('peak')]
    def _watch_peak_label(self, value: object) -> str:
        normalized = normalize_watch_resource_peak_percent(value, 15)
        return self._tr('watch_peak_option', value=normalized)
    def _watch_peak_choices(self) -> list[str]:
        return [self._watch_peak_label(value) for value in WATCH_RESOURCE_PEAK_OPTIONS]
    def _watch_peak_value(self, label: str) -> int:
        normalized = str(label or '').strip()
        for value in WATCH_RESOURCE_PEAK_OPTIONS:
            localized = self._watch_peak_label(value)
            if normalized == localized or normalized == str(value):
                return int(value)
        return normalize_watch_resource_peak_percent(normalized, 15)
    def _normalize_device_code(self, value: str | None) -> str:
        normalized = str(value or '').strip().lower()
        if normalized in {'auto', 'cpu', 'cuda'}:
            return normalized
        return 'auto'
    def _device_option_label(self, code: str) -> str:
        return self._tr(f"device_option_{self._normalize_device_code(code)}")
    def _device_option_code(self, value: str | None) -> str:
        normalized = str(value or '').strip()
        mapping = {
            self._tr('device_option_auto'): 'auto',
            self._tr('device_option_cpu'): 'cpu',
            self._tr('device_option_cuda'): 'cuda',
            'auto': 'auto',
            'cpu': 'cpu',
            'cuda': 'cuda',
        }
        return mapping.get(normalized, self._normalize_device_code(normalized))
    def _device_choices(self, codes: list[str]) -> list[str]:
        return [self._device_option_label(code) for code in codes]
    def _current_device_value(self) -> str:
        if not hasattr(self, 'device_combo') or self.device_combo.count() == 0:
            return self._normalize_device_code(getattr(self._config, 'vector_device', 'auto'))
        data = self.device_combo.currentData()
        if isinstance(data, str) and data.strip():
            return self._device_option_code(data)
        return self._device_option_code(self.device_combo.currentText())
    def _set_device_value(self, value: str | None) -> None:
        code = self._device_option_code(value)
        index = self.device_combo.findData(code)
        if index >= 0:
            self.device_combo.setCurrentIndex(index)
            return
        fallback = self.device_combo.findData('auto')
        if fallback >= 0:
            self.device_combo.setCurrentIndex(fallback)
        elif self.device_combo.count() > 0:
            self.device_combo.setCurrentIndex(0)
    def _current_index_state(self, snapshot: dict[str, object] | None = None) -> str:
        source = snapshot if isinstance(snapshot, dict) else (self._status_snapshot if isinstance(self._status_snapshot, dict) else {})
        raw = str(source.get('index_state') or '').strip().lower()
        if raw in {'ready', 'missing', 'pending'}:
            return raw
        if isinstance(source.get('pending_rebuild'), dict):
            return 'pending'
        if bool(source.get('index_ready')):
            return 'ready'
        stats = source.get('stats') or {}
        return 'ready' if int(stats.get('chunks', 0) or 0) > 0 else 'missing'
    def _index_ready(self, snapshot: dict[str, object] | None = None) -> bool:
        return self._current_index_state(snapshot) == 'ready'
    def _watch_allowed(self, snapshot: dict[str, object] | None = None) -> bool:
        source = snapshot if isinstance(snapshot, dict) else self._status_snapshot
        if isinstance(source, dict) and 'watch_allowed' in source:
            return bool(source.get('watch_allowed'))
        return self._index_ready(snapshot)
    def _default_watch_summary(self) -> str:
        watch_text = self._tr('watch_ready') if WATCHDOG_AVAILABLE else self._tr('watch_fallback')
        backend = (self.backend_combo.currentText().strip() if hasattr(self, 'backend_combo') else getattr(self._config, 'vector_backend', 'disabled')) or 'disabled'
        return self._tr('vector_watch_summary', backend=backend, watch_text=watch_text)
    def _watch_mode_label(self, mode: str | bool) -> str:
        use_polling = mode if isinstance(mode, bool) else str(mode).strip().lower() == 'polling'
        return self._tr('watch_mode_polling') if use_polling else self._tr('watch_mode_watchdog')
    def _device_summary(self) -> str:
        acceleration = dict(self._acceleration_payload or {})
        if not acceleration:
            return self._tr('device_summary_detecting')
        requested = self._current_device_value() if hasattr(self, 'device_combo') else self._normalize_device_code(getattr(self._config, 'vector_device', 'auto'))
        resolved = resolve_vector_device(requested)
        gpu_name = str(acceleration.get('gpu_name') or acceleration.get('cuda_name') or '').strip()
        nvcc_version = str(acceleration.get('nvcc_version') or '').strip()
        runtime_complete = bool(acceleration.get('runtime_complete', True))
        if acceleration.get('cuda_available') and runtime_complete:
            return self._tr('device_summary_cuda_ready', gpu=gpu_name or 'NVIDIA GPU', resolved=resolved)
        if acceleration.get('gpu_present'):
            if not acceleration.get('torch_available'):
                if nvcc_version:
                    return self._tr('device_summary_gpu_runtime_missing_with_nvcc', gpu=gpu_name or 'NVIDIA GPU', cuda=nvcc_version)
                return self._tr('device_summary_gpu_runtime_missing', gpu=gpu_name or 'NVIDIA GPU')
            if not acceleration.get('sentence_transformers_available') or not runtime_complete:
                return self._tr('device_summary_gpu_runtime_incomplete', gpu=gpu_name or 'NVIDIA GPU')
            return self._tr('device_summary_gpu_detected_no_cuda', gpu=gpu_name or 'NVIDIA GPU')
        return self._tr('device_summary_cpu_only')

    def _runtime_available_for_device(self, device_name: str) -> bool:
        try:
            config, _paths = self._collect_config(False)
        except Exception:
            config = replace(self._config)
        probe_config = replace(config, vector_device=device_name)
        return runtime_dependency_issue(probe_config) is None

    def _actual_mode_text(self) -> str:
        requested = self._current_device_value() if hasattr(self, 'device_combo') else self._normalize_device_code(getattr(self._config, 'vector_device', 'auto'))
        resolved = resolve_vector_device(requested)
        if resolved == 'cuda' and self._runtime_available_for_device(requested):
            return self._tr('device_status_mode_gpu')
        return self._tr('device_status_mode_cpu')

    def _device_runtime_status_text(self) -> str:
        acceleration = dict(self._acceleration_payload or {})
        if not acceleration:
            return self._tr('device_summary_detecting')
        gpu_name = str(acceleration.get('gpu_name') or acceleration.get('cuda_name') or '').strip()
        nvcc_version = str(acceleration.get('nvcc_version') or '').strip()
        runtime_exists = bool(acceleration.get('runtime_exists'))
        runtime_complete = bool(acceleration.get('runtime_complete'))
        missing_items = [str(item) for item in (acceleration.get('runtime_missing_items') or []) if str(item).strip()]
        gpu_value = gpu_name if acceleration.get('gpu_present') else self._tr('device_status_value_not_detected')
        if acceleration.get('cuda_available'):
            cuda_value = self._tr('device_status_value_cuda_ready', version=nvcc_version or str(acceleration.get('torch_version') or '').strip() or self._tr('none_value'))
        elif acceleration.get('nvcc_available'):
            cuda_value = self._tr('device_status_value_cuda_toolkit_only', version=nvcc_version or self._tr('none_value'))
        else:
            cuda_value = self._tr('device_status_value_not_detected')
        runtime_dir_value = self._tr('device_status_value_yes') if runtime_exists else self._tr('device_status_value_no')
        runtime_integrity_value = self._tr('device_status_value_complete') if runtime_complete else self._tr('device_status_value_incomplete')
        cpu_value = self._tr('device_status_value_yes') if self._runtime_available_for_device('cpu') else self._tr('device_status_value_cpu_unavailable')
        lines = [
            self._tr('device_runtime_status_title'),
            self._tr('device_runtime_status_gpu', value=gpu_value),
            self._tr('device_runtime_status_cuda', value=cuda_value),
            self._tr('device_runtime_status_runtime', value=runtime_dir_value),
            self._tr('device_runtime_status_integrity', value=runtime_integrity_value),
            self._tr('device_runtime_status_cpu', value=cpu_value),
            self._tr('device_runtime_status_mode', value=self._actual_mode_text()),
        ]
        if missing_items:
            lines.append(self._tr('device_runtime_status_runtime_missing_items', items=', '.join(missing_items)))
        return '\n'.join(lines)

    def _collect_vault_paths(self, active_vault: str = '') -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for raw_value in [active_vault, *self._saved_vaults]:
            normalized = normalize_vault_path(raw_value)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
        return ordered
    def _set_saved_vaults(self, vaults: list[str], active_vault: str = '') -> None:
        ordered: list[str] = []
        seen: set[str] = set()
        for raw_value in ([active_vault] if active_vault else []) + list(vaults):
            normalized = normalize_vault_path(raw_value)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
        self._saved_vaults = ordered
        self.saved_vault_combo.blockSignals(True)
        self.saved_vault_combo.clear()
        self.saved_vault_combo.addItems(self._saved_vaults)
        if active_vault:
            self.saved_vault_combo.setCurrentText(normalize_vault_path(active_vault))
        elif self._saved_vaults:
            self.saved_vault_combo.setCurrentIndex(0)
        self.saved_vault_combo.blockSignals(False)
    def _refresh_workspace_summary(self) -> None:
        vault = normalize_vault_path(self.vault_edit.text().strip())
        data_root = self.data_dir_edit.text().strip() or str(default_data_root())
        if not vault:
            summary = self._tr('workspace_empty')
        else:
            try:
                paths = ensure_data_paths(data_root, vault)
                summary = self._tr('workspace_current', vault=Path(vault).name or vault, workspace=paths.root, shared=paths.shared_root)
            except OSError:
                summary = self._tr('workspace_pending', vault=Path(vault).name or vault)
        self.workspace_summary_label.setText(summary)
        self.data_workspace_label.setText(summary)
        self._refresh_log_storage_summary()
    def _set_chip_style(self, widget: QtWidgets.QLabel, *, ok: bool = False, warn: bool = False) -> None:
        colors = self._theme.colors
        if ok:
            bg = colors['chip_ok_bg']
            fg = colors['chip_ok_fg']
        elif warn:
            bg = colors['chip_warn_bg']
            fg = colors['chip_warn_fg']
        else:
            bg = colors['chip_neutral_bg']
            fg = colors['chip_neutral_fg']
        widget.setStyleSheet(f'background:{bg}; color:{fg}; border-radius:8px; padding:6px 10px;')
    def _current_model_name(self) -> str:
        if hasattr(self, 'model_edit'):
            value = self.model_edit.text().strip()
            if value:
                return value
        return str(getattr(self._config, 'vector_model', 'BAAI/bge-m3') or 'BAAI/bge-m3').strip() or 'BAAI/bge-m3'

    def _bootstrap_button_text(self) -> str:
        return self._tr('bootstrap_button_named', model=self._current_model_name())

    def _refresh_model_download_text(self) -> None:
        if hasattr(self, 'bootstrap_button'):
            self.bootstrap_button.setText(self._bootstrap_button_text())

    def _refresh_overview_chips(self) -> None:
        try:
            vault_path = Path(self.vault_edit.text().strip()).expanduser()
            vault_ready = bool(self.vault_edit.text().strip()) and vault_path.exists() and vault_path.is_dir()
        except OSError:
            vault_ready = False
        self.vault_chip.setText(self._tr('vault_ready') if vault_ready else self._tr('vault_missing'))
        self._set_chip_style(self.vault_chip, ok=vault_ready)
        model_ready = self._is_model_ready()
        model_name = self._current_model_name()
        self.model_chip.setText(self._tr('model_ready_named', model=model_name) if model_ready else self._tr('model_missing_named', model=model_name))
        self._set_chip_style(self.model_chip, ok=model_ready, warn=not model_ready)
        index_state = self._current_index_state()
        self.index_chip.setText(self._tr(f'index_{index_state}'))
        self._set_chip_style(self.index_chip, ok=index_state == 'ready', warn=index_state == 'pending')
    def _refresh_device_options(self, payload: dict[str, object] | None = None) -> None:
        if isinstance(payload, dict):
            self._acceleration_payload = dict(payload)
        options_source = dict(self._acceleration_payload or {})
        options = [self._normalize_device_code(item) for item in (options_source.get('device_options') or ['auto', 'cpu'])]
        if options_source.get('gpu_present') and 'cuda' not in options:
            options.append('cuda')
        current = self._current_device_value() if self.device_combo.count() else ''
        requested = self._normalize_device_code(getattr(self._config, 'vector_device', 'auto'))
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for code in options:
            self.device_combo.addItem(self._device_option_label(code), code)
        preferred = current if current in options else (requested if requested in options else ('auto' if 'auto' in options else options[0]))
        self._set_device_value(preferred)
        self.device_combo.blockSignals(False)
        self.device_summary_label.setText(self._device_summary())
        self.device_runtime_status_label.setText(self._device_runtime_status_text())
    def _vector_backend_enabled(self) -> bool:
        backend = self.backend_combo.currentText().strip() if hasattr(self, 'backend_combo') else getattr(self._config, 'vector_backend', 'disabled')
        return (backend or 'disabled').strip().lower() not in {'', 'disabled', 'none', 'off'}
    def _runtime_missing_for_cuda(self) -> bool:
        payload = runtime_guidance_context(self.runtime_combo.currentText().strip() or 'torch', 'cuda', force_refresh=True)
        if not payload.get('gpu_present'):
            return False
        if not payload.get('cuda_available'):
            return True
        try:
            config, _paths = self._collect_config(False)
        except Exception:
            config = replace(self._config)
        config = replace(config, vector_device='cuda')
        return runtime_dependency_issue(config) is not None
    def _runtime_guidance_auto_popup_allowed(self) -> bool:
        return not self._device_runtime_prompt_suppressed and not self._busy and not self._watch_active
    def _ensure_vector_runtime_ready(self, config: AppConfig) -> bool:
        message = runtime_dependency_issue(config)
        if not message:
            return True
        self._append_log(message, focus_log=True)
        if not self._show_runtime_guidance_from_error(message):
            self._show_runtime_install_guidance(self._normalize_device_code(getattr(config, 'vector_device', 'auto')), extra_detail=message)
        return False
    def _show_runtime_install_guidance(self, requested_device: str, *, extra_detail: str = '') -> None:
        context = runtime_guidance_context(
            self.runtime_combo.currentText().strip() or 'torch',
            requested_device,
            force_refresh=True,
            extra_detail=extra_detail,
        )
        LOGGER.info('Opening runtime guidance dialog: requested_device=%s busy=%s watch_active=%s runtime_complete=%s.', requested_device, self._busy, self._watch_active, context.get('runtime_complete'))
        self.statusMessageChanged.emit(self._tr('status_runtime_guidance_opened'))
        dialog = RuntimeGuidanceDialog(language_code=self._language_code, theme=self._theme, context=context, parent=self)
        dialog.exec()
    def _show_runtime_guidance_from_error(self, message: str) -> bool:
        text_value = str(message or '').strip()
        if not text_value:
            return False
        if '当前还不能开始本地语义建库或向量查询。' not in text_value and 'Local semantic' not in text_value:
            return False
        requested = self._current_device_value() if hasattr(self, 'device_combo') else self._normalize_device_code(getattr(self._config, 'vector_device', 'auto'))
        if requested not in {'auto', 'gpu', 'cuda'}:
            requested = 'auto'
        baseline = runtime_guidance_context(self.runtime_combo.currentText().strip() or 'torch', requested, force_refresh=True)
        baseline_text = str(baseline.get('plain_text') or '').strip()
        extra_detail = ''
        if baseline_text and text_value.startswith(baseline_text):
            extra_detail = text_value[len(baseline_text):].strip()
        elif text_value != baseline_text:
            extra_detail = text_value
        self._show_runtime_install_guidance(requested, extra_detail=extra_detail)
        return True
    def _on_model_text_changed(self, _value: str) -> None:
        self._refresh_model_download_text()
        self._refresh_overview_chips()
        self._on_live_runtime_preferences_changed()

    def _on_live_runtime_preferences_changed(self, *_args) -> None:
        if self._live_runtime_sync_suppressed:
            return
        try:
            config, paths = self._collect_config(False)
        except Exception:
            return
        self._config = config
        self._paths = paths
        self.runtimeConfigChanged.emit(self._config, self._paths)

    def _on_device_selection_changed(self, _value: str) -> None:
        self.device_summary_label.setText(self._device_summary())
        self.device_runtime_status_label.setText(self._device_runtime_status_text())
        self._on_live_runtime_preferences_changed()
        if not self._runtime_guidance_auto_popup_allowed():
            return
        requested = self._current_device_value()
        if requested != 'cuda':
            return
        if not self._vector_backend_enabled() or not self._runtime_missing_for_cuda():
            return
        self._show_runtime_install_guidance(requested)

    def _on_runtime_sensitive_setting_changed(self, _value: str) -> None:
        self.device_summary_label.setText(self._device_summary())
        self.device_runtime_status_label.setText(self._device_runtime_status_text())
        self._on_live_runtime_preferences_changed()
        if not self._runtime_guidance_auto_popup_allowed():
            return
        requested = self._current_device_value() if hasattr(self, 'device_combo') else 'auto'
        if requested != 'cuda':
            return
        if not self._vector_backend_enabled() or not self._runtime_missing_for_cuda():
            return
        self._show_runtime_install_guidance(requested)
    def _refresh_reranker_state(self, payload: dict[str, object] | None = None) -> None:
        ready = None
        model_name = self.reranker_model_edit.text().strip() or 'BAAI/bge-reranker-v2-m3'
        if isinstance(payload, dict):
            if 'reranker_ready' in payload:
                ready = bool(payload.get('reranker_ready'))
            if payload.get('reranker_model'):
                model_name = str(payload.get('reranker_model'))
        if ready is None:
            try:
                config, paths = self._collect_config(False)
                ready = is_local_reranker_ready(config, paths)
            except Exception:
                ready = False
        self.reranker_state_label.setText(self._tr('reranker_ready') if ready else self._tr('reranker_missing'))
        self.reranker_state_label.setProperty('role', 'guide' if ready else 'muted')
    def _merge_status_snapshot(self, snapshot: dict[str, object] | None, *, stats: dict[str, object] | None = None) -> dict[str, object] | None:
        merged = dict(snapshot) if isinstance(snapshot, dict) else {}
        if stats is not None:
            merged_stats = dict(merged.get('stats') or {})
            for key in ('files', 'chunks', 'refs'):
                merged_stats[key] = int(stats.get(key, merged_stats.get(key, 0)) or 0)
            merged['stats'] = merged_stats
        return merged or None
    def _refresh_preflight_notice(self) -> None:
        show_notice = self._current_report is not None or self._latest_preflight_snapshot is not None
        self.preflight_notice_label.setText(self._tr('preflight_success_notice') if show_notice else '')
        self.preflight_notice_label.setVisible(show_notice)
    def _refresh_status_summary(self, snapshot: dict[str, object] | None) -> None:
        if isinstance(snapshot, dict):
            self._status_snapshot = snapshot
            stats = snapshot.get('stats') or {}
            self.files_value.setText(str(int(stats.get('files', 0) or 0)))
            self.chunks_value.setText(str(int(stats.get('chunks', 0) or 0)))
            self.refs_value.setText(str(int(stats.get('refs', 0) or 0)))
            latest = snapshot.get('latest_preflight')
            self._latest_preflight_snapshot = dict(latest) if isinstance(latest, dict) else None
        else:
            self._status_snapshot = None
            self.files_value.setText('0')
            self.chunks_value.setText('0')
            self.refs_value.setText('0')
            self._latest_preflight_snapshot = None
        if self._current_report is not None:
            self.preflight_label.setText(summarize_preflight(self._current_report, self._language_code))
        elif self._latest_preflight_snapshot is not None:
            latest = self._latest_preflight_snapshot
            self.preflight_label.setText(self._tr('recent_preflight', risk=latest.get('risk_level'), required=format_bytes(int(latest.get('required_free_bytes', 0))), available=format_bytes(int(latest.get('available_free_bytes', 0)))))
        else:
            self.preflight_label.setText(self._tr('preflight_empty'))
        self._refresh_preflight_notice()
        if self._watch_active:
            try:
                seconds = float(self.interval_edit.text().strip() or '2.0')
            except ValueError:
                seconds = 2.0
            self.watch_summary_label.setText(self._tr('watch_running', mode=self._watch_mode_label(self._watch_mode), seconds=seconds))
        else:
            self.watch_summary_label.setText(self._default_watch_summary())
        self._refresh_workspace_summary()
        self._refresh_overview_chips()
        self._refresh_reranker_state(snapshot)
        self._refresh_watch_button()
        self._refresh_task_controls()
        self._emit_query_block_state()
        self._emit_result_summary()
    def _emit_result_summary(self) -> None:
        self.resultSummaryChanged.emit(
            f"{self._tr('stat_files')} {self.files_value.text()} · {self._tr('stat_chunks')} {self.chunks_value.text()} · {self._tr('stat_refs')} {self.refs_value.text()}"
        )
    def _emit_query_block_state(self) -> None:
        if self._busy and self._active_task_key:
            self.queryBlockStateChanged.emit(True, self._tr('query_status_blocked_title'), self._tr('query_status_blocked_detail_task', task=self._tr(self._active_task_key)))
            return
        if self._watch_active:
            self.queryBlockStateChanged.emit(True, self._tr('query_status_blocked_title'), self._tr('query_status_blocked_detail_watch'))
            return
        if not self._index_ready():
            self.queryBlockStateChanged.emit(True, self._tr('query_status_blocked_title'), self._tr('query_status_blocked_detail_index'))
            return
        self.queryBlockStateChanged.emit(False, '', '')
    def _append_log(self, message: str, *, focus_log: bool = False) -> None:
        text_value = str(message or '').strip()
        if not text_value:
            return
        LOGGER.info('%s', text_value)
        self.logMessageAdded.emit(text_value)
        if focus_log:
            self.showQueryLogRequested.emit()
    def _on_preflight_notice_link(self, _target: str) -> None:
        self.showQueryLogRequested.emit()
    def _refresh_watch_button(self) -> None:
        self.watch_button.setText(self._tr('watch_stop') if self._watch_active else self._tr('watch_start'))
        self._set_button_variant(self.watch_button, 'danger' if self._watch_active else 'primary')
        index_state = self._current_index_state()
        if self._watch_active:
            self.watch_button.setEnabled(not self._watch_stopping)
            self.watch_button.setToolTip(self._tip('watch'))
            return
        self.watch_button.setEnabled(not self._busy)
        if index_state == 'ready':
            self.watch_button.setToolTip(self._tip('watch'))
        elif index_state == 'pending':
            self.watch_button.setToolTip(self._tr('watch_start_blocked_pending_body'))
        else:
            self.watch_button.setToolTip(self._tr('watch_start_blocked_missing_body'))
    def _apply_config_to_controls(self, config: AppConfig, paths) -> None:
        self._config = config
        self._paths = paths
        self._device_runtime_prompt_suppressed = True
        self._live_runtime_sync_suppressed = True
        try:
            self.vault_edit.setText(config.vault_path)
            self.data_dir_edit.setText(config.data_root)
            self._set_saved_vaults(config.vault_paths, active_vault=config.vault_path)
            self.backend_combo.setCurrentText(config.vector_backend or 'disabled')
            self.model_edit.setText(config.vector_model)
            self.runtime_combo.setCurrentText(config.vector_runtime)
            self._refresh_device_options(self._acceleration_payload)
            self._set_device_value(config.vector_device or 'auto')
            self.interval_edit.setText(str(config.poll_interval_seconds))
            self.build_profile_combo.setCurrentText(self._build_profile_label(getattr(config, 'build_resource_profile', 'balanced')))
            self.watch_peak_combo.setCurrentText(self._watch_peak_label(getattr(config, 'watch_resource_peak_percent', 15)))
            self.log_size_spin.setValue(normalize_log_file_size_mb(getattr(config, 'log_file_size_mb', DEFAULT_LOG_FILE_SIZE_MB), DEFAULT_LOG_FILE_SIZE_MB))
            self.local_only_check.setChecked(config.vector_local_files_only)
            self.force_check.setChecked(False)
            self.polling_check.setChecked(False)
            self.ui_scale_spin.setValue(normalize_ui_scale_percent(getattr(config, 'ui_scale_percent', 100), 100))
            self.ui_theme_combo.setCurrentText(self._ui_theme_label(getattr(config, 'ui_theme', 'system')))
            self.reranker_enabled_check.setChecked(getattr(config, 'reranker_enabled', False))
            self.export_ai_check.setChecked(getattr(config, 'context_export_mode', 'standard') == 'ai-collab')
            self.reranker_model_edit.setText(getattr(config, 'reranker_model', 'BAAI/bge-reranker-v2-m3'))
            self.reranker_batch_cpu_edit.setText(str(getattr(config, 'reranker_batch_size_cpu', 4)))
            self.reranker_batch_cuda_edit.setText(str(getattr(config, 'reranker_batch_size_cuda', 8)))
            self._refresh_model_download_text()
            self._refresh_quick_start_visibility()
            self._refresh_advanced_visibility()
            self._refresh_workspace_summary()
        finally:
            self._device_runtime_prompt_suppressed = False
            self._live_runtime_sync_suppressed = False
        self.device_summary_label.setText(self._device_summary())
        self.device_runtime_status_label.setText(self._device_runtime_status_text())
        self.runtimeConfigChanged.emit(self._config, self._paths)
    def _collect_config(self, require_vault: bool) -> tuple[AppConfig, Any]:
        vault = normalize_vault_path(self.vault_edit.text().strip())
        if require_vault:
            if not vault:
                raise ValueError(self._tr('choose_vault_first'))
            vault_path = Path(vault).expanduser().resolve()
            if not vault_path.exists() or not vault_path.is_dir():
                raise ValueError(self._tr('vault_invalid'))
            vault = str(vault_path)
        data_root = self.data_dir_edit.text().strip() or str(default_data_root())
        paths = ensure_data_paths(data_root, vault or None)
        interval = float(self.interval_edit.text().strip() or '2.0')
        reranker_batch_cpu = int(self.reranker_batch_cpu_edit.text().strip() or '4')
        reranker_batch_cuda = int(self.reranker_batch_cuda_edit.text().strip() or '8')
        if interval <= 0 or reranker_batch_cpu <= 0 or reranker_batch_cuda <= 0:
            raise ValueError(self._tr('number_invalid'))
        config = AppConfig(
            vault_path=vault,
            vault_paths=self._collect_vault_paths(vault),
            data_root=str(paths.global_root),
            query_limit=int(getattr(self._config, 'query_limit', 15) or 15),
            query_score_threshold=float(getattr(self._config, 'query_score_threshold', 35.0) or 35.0),
            poll_interval_seconds=interval,
            build_resource_profile=self._build_profile_code(self.build_profile_combo.currentText()),
            watch_resource_peak_percent=self._watch_peak_value(self.watch_peak_combo.currentText()),
            log_file_size_mb=normalize_log_file_size_mb(self.log_size_spin.value(), DEFAULT_LOG_FILE_SIZE_MB),
            vector_backend=self.backend_combo.currentText().strip() or 'disabled',
            vector_model=self.model_edit.text().strip() or 'BAAI/bge-m3',
            vector_runtime=self.runtime_combo.currentText().strip() or 'torch',
            vector_device=self._current_device_value(),
            vector_local_files_only=self.local_only_check.isChecked(),
            reranker_enabled=self.reranker_enabled_check.isChecked(),
            reranker_model=self.reranker_model_edit.text().strip() or 'BAAI/bge-reranker-v2-m3',
            reranker_batch_size_cpu=reranker_batch_cpu,
            reranker_batch_size_cuda=reranker_batch_cuda,
            context_export_mode='ai-collab' if self.export_ai_check.isChecked() else 'standard',
            rag_filter_core_enabled=getattr(self._config, 'rag_filter_core_enabled', True),
            rag_filter_extended_enabled=getattr(self._config, 'rag_filter_extended_enabled', False),
            rag_filter_custom_rules=getattr(self._config, 'rag_filter_custom_rules', ''),
            page_blocklist_rules=getattr(self._config, 'page_blocklist_rules', ''),
            ui_language=getattr(self._config, 'ui_language', self._language_code),
            ui_theme=self._ui_theme_code(self.ui_theme_combo.currentText()),
            ui_scale_percent=self.ui_scale_spin.value(),
            ui_quick_start_expanded=getattr(self._config, 'ui_quick_start_expanded', True),
            ui_window_geometry=getattr(self._config, 'ui_window_geometry', ''),
            ui_main_sash=getattr(self._config, 'ui_main_sash', 900),
            ui_right_sash=getattr(self._config, 'ui_right_sash', 280),
            ui_results_sash=getattr(self._config, 'ui_results_sash', 300),
            qt_window_geometry=getattr(self._config, 'qt_window_geometry', ''),
            qt_query_splitter_state=getattr(self._config, 'qt_query_splitter_state', ''),
            qt_results_splitter_state=getattr(self._config, 'qt_results_splitter_state', ''),
        )
        return config, paths
    def _refresh_quick_start_visibility(self) -> None:
        expanded = bool(getattr(self._config, 'ui_quick_start_expanded', True))
        self.quick_steps_widget.setVisible(expanded)
        self.quick_start_button.setText(self._tr('quick_start_hide') if expanded else self._tr('quick_start_show'))
    def _toggle_quick_start(self) -> None:
        self._config.ui_quick_start_expanded = not bool(getattr(self._config, 'ui_quick_start_expanded', True))
        self._refresh_quick_start_visibility()
    def _refresh_advanced_visibility(self) -> None:
        expanded = bool(getattr(self, '_show_advanced', True))
        self.advanced_widget.setVisible(expanded)
        self.advanced_toggle_button.setText(self._tr('advanced_hide') if expanded else self._tr('advanced_show'))
    def _toggle_advanced(self) -> None:
        self._show_advanced = not bool(getattr(self, '_show_advanced', True))
        self._refresh_advanced_visibility()
    def schedule_device_probe(self, delay_ms: int = 0, *, safe_mode: bool = False) -> None:
        if self._device_probe_scheduled or self._device_probe_worker is not None:
            return
        self._device_probe_scheduled = True
        if delay_ms > 0:
            QtCore.QTimer.singleShot(delay_ms, lambda safe=safe_mode: self._start_device_probe(safe_mode=safe))
            return
        self._start_device_probe(safe_mode=safe_mode)
    def _start_device_probe(self, *, safe_mode: bool = False) -> None:
        self._device_probe_scheduled = False
        if self._device_probe_worker is not None:
            return
        worker = FunctionWorker(fn=lambda: detect_acceleration(safe_mode=safe_mode))
        worker.succeeded.connect(self._on_device_probe_success)
        worker.failed.connect(self._on_device_probe_failed)
        worker.finished.connect(self._on_device_probe_finished)
        self._device_probe_worker = worker
        worker.start()
    def _on_device_probe_success(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        self._refresh_device_options(payload)
    def _on_device_probe_failed(self, message: str, traceback_text: str) -> None:
        print(f'Qt device probe failed: {message}', file=sys.stderr, flush=True)
        if traceback_text.strip():
            print(traceback_text.strip(), file=sys.stderr, flush=True)
        self.device_summary_label.setText(self._tr('device_summary_detecting'))
    def _on_device_probe_finished(self) -> None:
        self._device_probe_worker = None
    def schedule_initial_status_load(self, delay_ms: int = 0) -> None:
        if self._initial_status_scheduled or self._initial_status_worker is not None:
            return
        self._initial_status_scheduled = True
        if delay_ms > 0:
            QtCore.QTimer.singleShot(delay_ms, self._load_initial_status)
            return
        self._load_initial_status()
    def _load_initial_status(self) -> None:
        self._initial_status_scheduled = False
        if self._initial_status_worker is not None or self._busy or self._watch_active:
            return
        try:
            config, paths = self._collect_config(False)
        except Exception:
            self.statusMessageChanged.emit(self._tr('status_ready'))
            self._refresh_status_summary(snapshot=None)
            self._emit_query_block_state()
            return
        def runner(service, emit, pause, cancel):
            return {
                'status': service.status_snapshot(),
                'config': config,
                'paths': paths,
            }
        worker = ServiceTaskWorker(config=config, paths=paths, runner=runner)
        worker.succeeded.connect(self._on_initial_status_success)
        worker.runtimeError.connect(self._on_initial_status_runtime_error)
        worker.failed.connect(self._on_initial_status_failed)
        worker.finished.connect(self._on_initial_status_finished)
        self._initial_status_worker = worker
        worker.start()
    def _on_initial_status_success(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        config = payload.get('config')
        paths = payload.get('paths')
        snapshot = payload.get('status')
        if isinstance(snapshot, dict):
            self._refresh_status_summary(snapshot)
            self.statusMessageChanged.emit(self._tr('status_refresh_done'))
            self._append_log(self._tr('log_status_done'))
            if config is not None and paths is not None:
                self._offer_resume_rebuild(config, paths, snapshot)
        else:
            self._refresh_status_summary(snapshot=None)
        self._emit_query_block_state()
    def _on_initial_status_runtime_error(self, message: str) -> None:
        print(f'Qt initial status load failed: {message}', file=sys.stderr, flush=True)
        self.statusMessageChanged.emit(self._tr('status_ready'))
        self._append_log(message, focus_log=True)
        self._emit_query_block_state()
    def _on_initial_status_failed(self, message: str, traceback_text: str) -> None:
        print(f'Qt initial status load failed: {message}', file=sys.stderr, flush=True)
        if traceback_text.strip():
            print(traceback_text.strip(), file=sys.stderr, flush=True)
        self.statusMessageChanged.emit(self._tr('status_ready'))
        self._append_log(traceback_text.strip() or message, focus_log=True)
        self._emit_query_block_state()
    def _on_initial_status_finished(self) -> None:
        self._initial_status_worker = None
    def _open_help_and_updates(self) -> None:
        answer = QtWidgets.QMessageBox.question(self, self._tr('help_updates_confirm_title'), self._tr('help_updates_confirm_body'))
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._open_url(REPO_URL)
    def _open_url(self, url: str) -> None:
        if not QtGui.QDesktopServices.openUrl(QtCore.QUrl(url)):
            QtWidgets.QMessageBox.warning(self, self._tr('help_updates'), self._tr('help_failed'))
    def _browse_vault(self) -> None:
        selected = QtWidgets.QFileDialog.getExistingDirectory(self, self._tr('vault_label'), self.vault_edit.text().strip() or str(Path.home()))
        if not selected:
            return
        self._activate_vault(selected, refresh_status=True)
        self._append_log(self._tr('log_vault_selected', vault=Path(selected).name or selected))
    def _browse_data_root(self) -> None:
        selected = QtWidgets.QFileDialog.getExistingDirectory(self, self._tr('data_dir_label'), self.data_dir_edit.text().strip() or str(default_data_root()))
        if not selected:
            return
        self.data_dir_edit.setText(str(Path(selected).expanduser().resolve()))
        self._load_config_from_current_dir()
    def _on_saved_vault_selected(self, value: str) -> None:
        selected = normalize_vault_path(value)
        if selected:
            self._activate_vault(selected, refresh_status=True)
    def _activate_vault(self, vault: str, *, refresh_status: bool) -> None:
        normalized = normalize_vault_path(vault)
        if not normalized:
            return
        self.vault_edit.setText(normalized)
        self._current_report = None
        self._status_snapshot = None
        self._latest_preflight_snapshot = None
        self._set_saved_vaults(self._saved_vaults + [normalized], active_vault=normalized)
        self._refresh_workspace_summary()
        self._refresh_status_summary(snapshot=None)
        if refresh_status and not self._busy and not self._watch_active:
            self._load_initial_status()
    def _remove_selected_vault(self) -> None:
        selected = normalize_vault_path(self.saved_vault_combo.currentText().strip() or self.vault_edit.text().strip())
        if not selected:
            QtWidgets.QMessageBox.information(self, self._tr('not_ready_title'), self._tr('saved_vault_missing'))
            return
        remaining = [vault for vault in self._saved_vaults if vault != selected]
        next_active = remaining[0] if remaining else ''
        self._current_report = None
        self._status_snapshot = None
        self._latest_preflight_snapshot = None
        self._set_saved_vaults(remaining, active_vault=next_active)
        self.vault_edit.setText(next_active)
        self._refresh_workspace_summary()
        if next_active and not self._busy and not self._watch_active:
            self._load_initial_status()
        else:
            self.statusMessageChanged.emit(self._tr('status_ready'))
            self._refresh_status_summary(snapshot=None)
        self._append_log(self._tr('log_vault_removed', vault=Path(selected).name or selected))
    def _save_only(self) -> None:
        try:
            config, paths = self._collect_config(False)
            save_config(config, paths)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self._tr('save_failed_title'), str(exc))
            return
        self._apply_config_to_controls(config, paths)
        self.statusMessageChanged.emit(self._tr('status_saved', path=paths.config_file))
        self._append_log(self._tr('log_saved_config', path=paths.config_file))
        self._refresh_status_summary(self._status_snapshot)
    def _load_config_from_current_dir(self) -> None:
        active_vault = self.vault_edit.text().strip() or None
        paths = ensure_data_paths(self.data_dir_edit.text().strip() or str(default_data_root()), active_vault)
        config = load_config(paths)
        if config is None:
            self._current_report = None
            self._status_snapshot = None
            self._latest_preflight_snapshot = None
            self._set_saved_vaults([], active_vault=active_vault or '')
            self._refresh_workspace_summary()
            self._refresh_status_summary(snapshot=None)
            return
        self._apply_config_to_controls(config, paths)
        self._append_log(self._tr('log_loaded_config', path=paths.config_file))
        self._load_initial_status()
    def _apply_recommended(self) -> None:
        self.backend_combo.setCurrentText('lancedb')
        self.model_edit.setText('BAAI/bge-m3')
        self.runtime_combo.setCurrentText('torch')
        self._refresh_device_options(self._acceleration_payload)
        self._set_device_value('auto')
        self.interval_edit.setText('2.0')
        self.build_profile_combo.setCurrentText(self._build_profile_label('balanced'))
        self.watch_peak_combo.setCurrentText(self._watch_peak_label(15))
        self.log_size_spin.setValue(DEFAULT_LOG_FILE_SIZE_MB)
        self.local_only_check.setChecked(False)
        self.force_check.setChecked(False)
        self.polling_check.setChecked(False)
        self.reranker_enabled_check.setChecked(False)
        self.export_ai_check.setChecked(False)
        self.reranker_model_edit.setText('BAAI/bge-reranker-v2-m3')
        self.reranker_batch_cpu_edit.setText('4')
        self.reranker_batch_cuda_edit.setText('8')
        self._config.rag_filter_core_enabled = True
        self._config.rag_filter_extended_enabled = False
        self._config.rag_filter_custom_rules = ''
        self._config.page_blocklist_rules = merge_page_filter_defaults('')
        self._refresh_status_summary(self._status_snapshot)
        self.statusMessageChanged.emit(self._tr('status_recommended'))
    def update_theme(self, theme: ThemeState) -> None:
        self._theme = theme
    def snapshot_view_state(self) -> dict[str, object]:
        return {
            'sub_tab_index': self.sub_tabs.currentIndex(),
            'vault_text': self.vault_edit.text(),
            'data_dir_text': self.data_dir_edit.text(),
            'saved_vaults': list(self._saved_vaults),
            'backend': self.backend_combo.currentText(),
            'model_text': self.model_edit.text(),
            'runtime': self.runtime_combo.currentText(),
            'device': self._current_device_value(),
            'interval_text': self.interval_edit.text(),
            'build_profile': self._build_profile_code(self.build_profile_combo.currentText()),
            'watch_peak': self._watch_peak_value(self.watch_peak_combo.currentText()),
            'log_size_mb': normalize_log_file_size_mb(self.log_size_spin.value(), DEFAULT_LOG_FILE_SIZE_MB),
            'local_only': self.local_only_check.isChecked(),
            'force': self.force_check.isChecked(),
            'polling': self.polling_check.isChecked(),
            'ui_scale': self.ui_scale_spin.value(),
            'ui_theme': self._ui_theme_code(self.ui_theme_combo.currentText()),
            'reranker_enabled': self.reranker_enabled_check.isChecked(),
            'export_ai': self.export_ai_check.isChecked(),
            'reranker_model_text': self.reranker_model_edit.text(),
            'reranker_batch_cpu_text': self.reranker_batch_cpu_edit.text(),
            'reranker_batch_cuda_text': self.reranker_batch_cuda_edit.text(),
            'current_report': self._current_report,
            'status_snapshot': self._status_snapshot,
        }
    def restore_view_state(self, state: dict[str, object] | None) -> None:
        payload = dict(state or {})
        self._device_runtime_prompt_suppressed = True
        try:
            self.vault_edit.setText(str(payload.get('vault_text') or self.vault_edit.text()))
            self.data_dir_edit.setText(str(payload.get('data_dir_text') or self.data_dir_edit.text()))
            self._set_saved_vaults(list(payload.get('saved_vaults') or self._saved_vaults), active_vault=self.vault_edit.text().strip())
            self.backend_combo.setCurrentText(str(payload.get('backend') or self.backend_combo.currentText()))
            self.model_edit.setText(str(payload.get('model_text') or self.model_edit.text()))
            self.runtime_combo.setCurrentText(str(payload.get('runtime') or self.runtime_combo.currentText()))
            self._refresh_device_options(self._acceleration_payload)
            self._set_device_value(payload.get('device') or self._current_device_value())
            self.interval_edit.setText(str(payload.get('interval_text') or self.interval_edit.text()))
            self.build_profile_combo.setCurrentText(self._build_profile_label(payload.get('build_profile') or self._build_profile_code(self.build_profile_combo.currentText())))
            self.watch_peak_combo.setCurrentText(self._watch_peak_label(payload.get('watch_peak') or self._watch_peak_value(self.watch_peak_combo.currentText())))
            self.log_size_spin.setValue(normalize_log_file_size_mb(payload.get('log_size_mb', self.log_size_spin.value()), DEFAULT_LOG_FILE_SIZE_MB))
            self.local_only_check.setChecked(bool(payload.get('local_only', self.local_only_check.isChecked())))
            self.force_check.setChecked(bool(payload.get('force', self.force_check.isChecked())))
            self.polling_check.setChecked(bool(payload.get('polling', self.polling_check.isChecked())))
            self.ui_scale_spin.setValue(int(payload.get('ui_scale', self.ui_scale_spin.value()) or self.ui_scale_spin.value()))
            self.ui_theme_combo.setCurrentText(self._ui_theme_label(payload.get('ui_theme') or self._ui_theme_code(self.ui_theme_combo.currentText())))
            self.reranker_enabled_check.setChecked(bool(payload.get('reranker_enabled', self.reranker_enabled_check.isChecked())))
            self.export_ai_check.setChecked(bool(payload.get('export_ai', self.export_ai_check.isChecked())))
            self.reranker_model_edit.setText(str(payload.get('reranker_model_text') or self.reranker_model_edit.text()))
            self.reranker_batch_cpu_edit.setText(str(payload.get('reranker_batch_cpu_text') or self.reranker_batch_cpu_edit.text()))
            self.reranker_batch_cuda_edit.setText(str(payload.get('reranker_batch_cuda_text') or self.reranker_batch_cuda_edit.text()))
            self._current_report = payload.get('current_report')
            status_snapshot = payload.get('status_snapshot') if isinstance(payload.get('status_snapshot'), dict) else self._status_snapshot
            self._refresh_status_summary(status_snapshot)
            sub_tab_index = int(payload.get('sub_tab_index', 0) or 0)
            if 0 <= sub_tab_index < self.sub_tabs.count():
                self.sub_tabs.setCurrentIndex(sub_tab_index)
        finally:
            self._device_runtime_prompt_suppressed = False
    def _apply_ui_preferences(self) -> None:
        try:
            config, paths = self._collect_config(False)
            config.ui_theme = self._ui_theme_code(self.ui_theme_combo.currentText())
            config.ui_scale_percent = self.ui_scale_spin.value()
            save_config(config, paths)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self._tr('save_failed_title'), str(exc))
            return
        self._apply_config_to_controls(config, paths)
        self.uiPreferencesChanged.emit(config.ui_theme, config.ui_scale_percent)
        self.statusMessageChanged.emit(self._tr('status_saved', path=paths.config_file))
        self._append_log(self._tr('log_saved_config', path=paths.config_file))
    def _is_model_ready(self) -> bool:
        try:
            config, paths = self._collect_config(False)
        except Exception:
            return False
        return is_local_model_ready(config, paths)
    def _open_local_dir(self, path: Path) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))
    def _open_vault_dir(self) -> None:
        try:
            config, _paths = self._collect_config(True)
        except Exception as exc:
            QtWidgets.QMessageBox.information(self, self._tr('not_ready_title'), str(exc))
            return
        self._open_local_dir(config.vault_dir)
    def _open_data_dir(self) -> None:
        try:
            _config, paths = self._collect_config(True)
        except Exception as exc:
            QtWidgets.QMessageBox.information(self, self._tr('not_ready_title'), str(exc))
            return
        self._open_local_dir(paths.root)
    def _open_exports_dir(self) -> None:
        try:
            _config, paths = self._collect_config(True)
        except Exception as exc:
            QtWidgets.QMessageBox.information(self, self._tr('not_ready_title'), str(exc))
            return
        self._open_local_dir(paths.exports_dir)
    def _open_logs_dir(self) -> None:
        try:
            _config, paths = self._collect_config(False)
        except Exception as exc:
            QtWidgets.QMessageBox.information(self, self._tr('not_ready_title'), str(exc))
            return
        self._open_local_dir(paths.logs_dir)
    def _log_directory_bytes(self, directory: Path) -> int:
        if not directory.exists():
            return 0
        total = 0
        for child in directory.rglob('*'):
            if not child.is_file():
                continue
            try:
                total += child.stat().st_size
            except OSError:
                continue
        return total
    def _refresh_log_storage_summary(self) -> None:
        if not hasattr(self, 'log_storage_summary_label'):
            return
        try:
            _config, paths = self._collect_config(False)
        except Exception:
            self.log_storage_summary_label.setText(self._tr('log_storage_summary_unavailable', limit=normalize_log_file_size_mb(getattr(self._config, 'log_file_size_mb', DEFAULT_LOG_FILE_SIZE_MB), DEFAULT_LOG_FILE_SIZE_MB), backups=LOG_BACKUP_COUNT + 1))
            return
        limit_mb = normalize_log_file_size_mb(self.log_size_spin.value() if hasattr(self, 'log_size_spin') else getattr(self._config, 'log_file_size_mb', DEFAULT_LOG_FILE_SIZE_MB), DEFAULT_LOG_FILE_SIZE_MB)
        total_bytes = self._log_directory_bytes(paths.logs_dir)
        self.log_storage_summary_label.setText(self._tr('log_storage_summary', path=paths.logs_dir, size=format_bytes(total_bytes), limit=limit_mb, backups=LOG_BACKUP_COUNT + 1))
    def _save_log_preferences(self) -> None:
        try:
            config, paths = self._collect_config(False)
            config.log_file_size_mb = normalize_log_file_size_mb(self.log_size_spin.value(), DEFAULT_LOG_FILE_SIZE_MB)
            save_config(config, paths)
            configure_file_logging(paths, config)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self._tr('save_failed_title'), str(exc))
            return
        self._apply_config_to_controls(config, paths)
        self._refresh_log_storage_summary()
        self.statusMessageChanged.emit(self._tr('status_log_settings_saved', limit=config.log_file_size_mb))
        self._append_log(self._tr('log_log_settings_saved', limit=config.log_file_size_mb, path=paths.logs_dir))
    def open_page_blocklist_dialog(self) -> None:
        dialog = PageBlocklistDialog(raw_rules=getattr(self._config, 'page_blocklist_rules', ''), language_code=self._language_code, theme=self._theme, parent=self)
        dialog.rulesSaved.connect(self._on_page_blocklist_saved)
        dialog.exec()
    def _on_page_blocklist_saved(self, serialized_rules: str) -> None:
        self._config.page_blocklist_rules = serialized_rules
        try:
            config, paths = self._collect_config(False)
            config.page_blocklist_rules = serialized_rules
            save_config(config, paths)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self._tr('save_failed_title'), str(exc))
            return
        self._apply_config_to_controls(config, paths)
        self.statusMessageChanged.emit(self._tr('status_page_blocklist_saved'))
        self._append_log(self._tr('log_page_blocklist_saved', enabled=sum(1 for line in serialized_rules.splitlines() if line.startswith('1\t')), total=len([line for line in serialized_rules.splitlines() if line.strip()])))
        self.queryReplayRequested.emit()
    def open_sensitive_filter_dialog(self) -> None:
        dialog = SensitiveFilterDialog(
            core_enabled=getattr(self._config, 'rag_filter_core_enabled', True),
            extended_enabled=getattr(self._config, 'rag_filter_extended_enabled', False),
            custom_rules=getattr(self._config, 'rag_filter_custom_rules', ''),
            language_code=self._language_code,
            theme=self._theme,
            parent=self,
        )
        dialog.rulesSaved.connect(self._on_sensitive_filters_saved)
        dialog.exec()
    def _on_sensitive_filters_saved(self, core_enabled: bool, extended_enabled: bool, custom_rules: str) -> None:
        self._config.rag_filter_core_enabled = core_enabled
        self._config.rag_filter_extended_enabled = extended_enabled
        self._config.rag_filter_custom_rules = custom_rules
        try:
            config, paths = self._collect_config(False)
            config.rag_filter_core_enabled = core_enabled
            config.rag_filter_extended_enabled = extended_enabled
            config.rag_filter_custom_rules = custom_rules
            save_config(config, paths)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self._tr('save_failed_title'), str(exc))
            return
        self._apply_config_to_controls(config, paths)
        self.statusMessageChanged.emit(self._tr('status_sensitive_filters_saved'))
        self._append_log(self._tr('log_sensitive_filters_saved'))
        self.queryReplayRequested.emit()
    def _ask_yes_no_cancel(self, title: str, body: str) -> QtWidgets.QMessageBox.StandardButton:
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(body)
        box.setIcon(QtWidgets.QMessageBox.Icon.Question)
        box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No | QtWidgets.QMessageBox.StandardButton.Cancel)
        return QtWidgets.QMessageBox.StandardButton(box.exec())
    def _manual_model_context(self, config: AppConfig, paths) -> dict[str, str]:
        context = model_download_guidance_context(config, paths)
        context['size'] = format_bytes(estimate_model_cache_bytes(config.vector_model, config.vector_runtime))
        context['plain_text'] = self._tr(
            'manual_model_hint',
            size=context['size'],
            model=context['model'],
            model_dir=context['model_dir'],
            official_url=context['official_url'],
            mirror_url=context['mirror_url'],
            install_cli_command=context['install_cli_command'],
            official_download_command=context['official_download_command'],
            mirror_download_command=context['mirror_download_command'],
        )
        return context

    def _manual_model_hint(self, config: AppConfig, paths) -> str:
        return self._manual_model_context(config, paths)['plain_text']
    def _manual_reranker_hint(self, config: AppConfig, paths) -> str:
        model_dir = get_local_reranker_dir(config, paths)
        model_dir.mkdir(parents=True, exist_ok=True)
        return self._tr('manual_reranker_hint', mirror_url='https://hf-mirror.com/', hf_url=f'https://huggingface.co/{config.reranker_model}', model=config.reranker_model, size=format_bytes(estimate_model_cache_bytes(config.reranker_model, config.vector_runtime)), model_dir=model_dir)
    def _choose_model_download_mode(self, task_label: str, config: AppConfig, paths) -> str | None:
        model_dir = get_local_model_dir(config, paths)
        model_dir.mkdir(parents=True, exist_ok=True)
        choice = self._ask_yes_no_cancel(self._tr('model_prompt_title'), self._tr('model_download_choice_body', task=task_label, model=config.vector_model, size=format_bytes(estimate_model_cache_bytes(config.vector_model, config.vector_runtime)), model_dir=model_dir))
        if choice == QtWidgets.QMessageBox.StandardButton.Yes:
            self._append_log(self._tr('log_model_download_prompt'))
            return 'auto'
        if choice == QtWidgets.QMessageBox.StandardButton.No:
            context = self._manual_model_context(config, paths)
            dialog = ModelDownloadDialog(language_code=self._language_code, theme=self._theme, context=context, parent=self)
            dialog.exec()
            self.statusMessageChanged.emit(self._tr('status_manual_download_waiting'))
            self._append_log(self._tr('log_manual_download_hint', model=config.vector_model))
            return 'manual'
        self.statusMessageChanged.emit(self._tr('model_prompt_declined'))
        self._append_log(self._tr('log_model_download_declined'))
        return None
    def _choose_reranker_download_mode(self, task_label: str, config: AppConfig, paths) -> str | None:
        model_dir = get_local_reranker_dir(config, paths)
        model_dir.mkdir(parents=True, exist_ok=True)
        choice = self._ask_yes_no_cancel(self._tr('model_prompt_title'), self._tr('reranker_download_choice_body', task=task_label, model=config.reranker_model, size=format_bytes(estimate_model_cache_bytes(config.reranker_model, config.vector_runtime)), model_dir=model_dir))
        if choice == QtWidgets.QMessageBox.StandardButton.Yes:
            self._append_log(self._tr('log_reranker_download_prompt'))
            return 'auto'
        if choice == QtWidgets.QMessageBox.StandardButton.No:
            QtWidgets.QMessageBox.information(self, self._tr('reranker_manual_title'), self._manual_reranker_hint(config, paths))
            self.statusMessageChanged.emit(self._tr('status_manual_download_waiting'))
            self._append_log(self._tr('log_manual_reranker_hint', model=config.reranker_model))
            return 'manual'
        self.statusMessageChanged.emit(self._tr('status_reranker_download_declined'))
        self._append_log(self._tr('log_reranker_download_declined'))
        return None
    def _prepare_model_for_followup(self, label_key: str, require_vault: bool, followup) -> bool:
        try:
            config, paths = self._collect_config(require_vault)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self._tr('cannot_start_title'), str(exc))
            return False
        choice = self._choose_model_download_mode(self._tr(label_key), config, paths)
        if choice != 'auto':
            return False
        if config.vector_local_files_only and not is_local_model_ready(config, paths):
            allow_remote = QtWidgets.QMessageBox.question(self, self._tr('model_prompt_title'), self._tr('model_prompt_local_only', manual_hint=self._manual_model_hint(config, paths)))
            if allow_remote != QtWidgets.QMessageBox.StandardButton.Yes:
                self.statusMessageChanged.emit(self._tr('model_prompt_declined'))
                self._append_log(self._tr('log_model_download_declined'))
                return False
            self.local_only_check.setChecked(False)
        self._run_bootstrap_model(followup=followup)
        return True
    def _task_profile(self, label_key: str, config: AppConfig, paths) -> tuple[str, str]:
        if label_key == 'preflight_button':
            return self._tr('task_eta_preflight'), self._tr('task_detail_preflight')
        if label_key == 'bootstrap_button':
            if is_local_model_ready(config, paths):
                return self._tr('task_eta_bootstrap_cached'), self._tr('task_detail_bootstrap_cached')
            return self._tr('task_eta_bootstrap_download'), self._tr('task_detail_bootstrap_download', model=config.vector_model)
        if label_key == 'bootstrap_reranker_button':
            if is_local_reranker_ready(config, paths):
                return self._tr('task_eta_bootstrap_cached'), self._tr('task_detail_reranker_cached')
            return self._tr('task_eta_bootstrap_download'), self._tr('task_detail_reranker_download', model=config.reranker_model)
        if label_key in {'rebuild_button', 'resume_rebuild_task'}:
            return self._tr('task_eta_rebuild'), self._tr('task_detail_rebuild')
        if label_key == 'refresh_button':
            return self._tr('task_eta_refresh'), self._tr('task_detail_refresh')
        if label_key == 'clear_button':
            return self._tr('task_eta_refresh'), self._tr('task_detail_refresh')
        return self._tr('task_eta_unknown'), self._tr('task_detail_unknown')
    def _is_rebuild_task(self, label_key: str | None) -> bool:
        return label_key in {'rebuild_button', 'resume_rebuild_task'}
    def _refresh_task_controls(self) -> None:
        visible = self._busy and self._is_rebuild_task(self._active_task_key)
        self.rebuild_pause_button.setVisible(visible)
        self.rebuild_cancel_button.setVisible(visible)
        if not visible:
            return
        paused = self._rebuild_pause_event.is_set()
        self.rebuild_pause_button.setText(self._tr('resume_rebuild_button') if paused else self._tr('pause_rebuild'))
        self._set_button_variant(self.rebuild_pause_button, 'primary' if paused else 'secondary')
    def _set_task_progress_busy(self) -> None:
        self.task_progress.setRange(0, 0)
        self.task_progress.setValue(0)
        self.task_progress.setFormat('')
        self.task_progress.setTextVisible(False)
    def _set_task_progress_counts(self, current: int, total: int, percent: float) -> None:
        safe_total = max(int(total), 1)
        safe_current = max(0, min(int(current), safe_total))
        safe_percent = max(0.0, min(float(percent), 100.0))
        self.task_progress.setRange(0, 100)
        self.task_progress.setValue(int(round(safe_percent)))
        self.task_progress.setFormat(f'{safe_current}/{safe_total} · {safe_percent:.0f}%')
        self.task_progress.setTextVisible(True)
    def _set_task_progress_percent(self, percent: float, *, format_text: str = '') -> None:
        safe_percent = max(0.0, min(float(percent), 100.0))
        self.task_progress.setRange(0, 100)
        self.task_progress.setValue(int(round(safe_percent)))
        self.task_progress.setFormat(format_text or f'{safe_percent:.0f}%')
        self.task_progress.setTextVisible(True)
    def _start_task_feedback(self, label_key: str, config: AppConfig, paths) -> None:
        eta_text, detail_text = self._task_profile(label_key, config, paths)
        self._active_task_key = label_key
        self._latest_task_progress = None
        self._rebuild_pause_event.clear()
        self._rebuild_cancel_event.clear()
        self._task_started_at = time.time()
        self._task_paused_started_at = 0.0
        self._task_paused_total_seconds = 0.0
        self._task_last_eta_text = self._tr('task_eta_label', value=eta_text)
        self.task_state_label.setText(self._tr('task_running', task=self._tr(label_key)))
        self.task_detail_label.setText(detail_text)
        self.task_percent_label.setText(self._tr('task_percent_idle'))
        self.task_elapsed_label.setText(self._tr('task_elapsed', value='00:00'))
        self.task_eta_label.setText(self._task_last_eta_text)
        self._set_task_progress_busy()
        self._task_timer.start()
        self._refresh_task_controls()
        self._emit_query_block_state()
    def _freeze_task_progress_visual(self) -> None:
        payload = dict(self._latest_task_progress or {})
        stage = str(payload.get('stage') or '').strip().lower()
        current = int(payload.get('current', 0) or 0)
        total = int(payload.get('total', 0) or 0)
        percent = float(payload.get('overall_percent', 0.0) or 0.0)
        if total > 0:
            if percent <= 0.0 and current > 0:
                percent = (current / max(total, 1)) * 100.0
            if stage == 'vectorizing':
                encoded = max(0, min(int(payload.get('encoded_count', current) or current), total))
                written = max(0, min(int(payload.get('written_count', current) or current), total))
                self._set_task_progress_counts(written, total, percent)
                self.task_percent_label.setText(self._tr('task_percent_vector_label', percent=percent, written=written, total=total))
            else:
                self._set_task_progress_counts(current, total, percent)
                self.task_percent_label.setText(self._tr('task_percent_label', percent=percent, current=current, total=total))
            return
        self._set_task_progress_percent(percent)
        if payload:
            stage_map = {
                'preflight_scan': 'task_stage_preflight_scan',
                'preflight_finalize': 'task_stage_preflight_finalize',
                'rendering': 'resume_phase_rendering',
                'vectorizing': 'resume_phase_vectorizing',
            }
            stage_key = stage_map.get(stage)
            if stage_key is not None:
                self.task_percent_label.setText(self._tr('task_percent_stage', stage=self._tr(stage_key)))
            else:
                self.task_percent_label.setText(self._tr('task_percent_idle'))
        else:
            self.task_percent_label.setText(self._tr('task_percent_idle'))
    def _stop_task_feedback(self) -> None:
        self._task_timer.stop()
        self._rebuild_pause_event.clear()
        self._rebuild_cancel_event.clear()
        self._latest_task_progress = None
        self._active_task_key = None
        self._task_started_at = 0.0
        self._task_paused_started_at = 0.0
        self._task_paused_total_seconds = 0.0
        self.task_state_label.setText(self._tr('task_idle'))
        self.task_detail_label.setText(self._tr('task_idle_detail'))
        self.task_percent_label.setText(self._tr('task_percent_idle'))
        self.task_elapsed_label.setText(self._tr('task_elapsed', value='00:00'))
        self.task_eta_label.setText(self._tr('task_eta_idle'))
        self.task_progress.setRange(0, 100)
        self.task_progress.setValue(0)
        self.task_progress.setFormat('')
        self.task_progress.setTextVisible(False)
        self._refresh_task_controls()
        self._emit_query_block_state()
    def _tick_task_feedback(self) -> None:
        if not self._busy:
            return
        if not (self._rebuild_pause_event.is_set() and self._is_rebuild_task(self._active_task_key)):
            self.task_elapsed_label.setText(self._tr('task_elapsed', value=self._format_elapsed(self._current_task_elapsed_seconds())))
    def _render_vector_tuning(self, payload: dict[str, object]) -> str:
        encode_batch = int(payload.get('encode_batch_size', 0) or 0)
        write_batch = int(payload.get('write_batch_size', 0) or 0)
        if encode_batch <= 0 and write_batch <= 0:
            return ''
        sample_payload = payload.get('resource_sample')
        metrics = self._tr('none_value')
        if isinstance(sample_payload, dict):
            try:
                metrics = format_resource_sample(ResourceSample(**sample_payload)) or self._tr('none_value')
            except Exception:
                metrics = self._tr('none_value')
        return self._tr('task_detail_rebuild_vector_tuning', profile=self._build_profile_label(str(payload.get('build_profile', 'balanced'))), encode_batch=encode_batch, write_batch=write_batch, metrics=metrics, action=self._tr(f"vector_tuning_action_{str(payload.get('tuning_action', 'steady')).strip().lower() or 'steady'}"), reason=self._tr(f"vector_tuning_reason_{str(payload.get('tuning_reason', 'stable')).strip().lower() or 'stable'}"), encoded_count=int(payload.get('encoded_count', 0) or 0), written_count=int(payload.get('written_count', 0) or 0), queue_depth=int(payload.get('write_queue_depth', 0) or 0), queue_capacity=int(payload.get('write_queue_capacity', 0) or 0), flush_count=int(payload.get('write_flush_count', 0) or 0), prepare_seconds=f"{float(payload.get('prepare_elapsed_total_ms', 0.0) or 0.0) / 1000.0:.1f}", write_seconds=f"{float(payload.get('write_elapsed_total_ms', 0.0) or 0.0) / 1000.0:.1f}")
    def _update_task_progress(self, payload: dict[str, object]) -> None:
        self._latest_task_progress = payload
        stage = str(payload.get('stage') or '').strip().lower()
        current = int(payload.get('current', 0) or 0)
        total = int(payload.get('total', 0) or 0)
        percent = float(payload.get('overall_percent', 0.0) or 0.0)
        encoded = max(0, min(int(payload.get('encoded_count', current) or current), total)) if total > 0 else max(0, int(payload.get('encoded_count', current) or current))
        written = max(0, min(int(payload.get('written_count', current) or current), total)) if total > 0 else max(0, int(payload.get('written_count', current) or current))
        if total > 0:
            if percent <= 0.0 and current > 0:
                percent = (current / max(total, 1)) * 100.0
            if stage == 'vectorizing':
                self._set_task_progress_counts(written, total, percent)
                self.task_percent_label.setText(self._tr('task_percent_vector_label', percent=percent, written=written, total=total))
            else:
                self._set_task_progress_counts(current, total, percent)
                self.task_percent_label.setText(self._tr('task_percent_label', percent=percent, current=current, total=total))
        else:
            self._set_task_progress_busy()
            if stage in {'rendering', 'vectorizing'}:
                self.task_percent_label.setText(self._tr('task_percent_stage', stage=self._tr(f'resume_phase_{stage}' if stage != 'vectorizing' else 'resume_phase_vectorizing')))
            elif stage == 'preflight_scan':
                self.task_percent_label.setText(self._tr('task_percent_stage', stage=self._tr('task_stage_preflight_scan')))
            elif stage == 'preflight_finalize':
                percent = percent or 98.0
                self._set_task_progress_percent(percent)
                self.task_percent_label.setText(self._tr('task_percent_stage', stage=self._tr('task_stage_preflight_finalize')))
            else:
                self.task_percent_label.setText(self._tr('task_percent_idle'))
        eta_seconds = payload.get('eta_seconds')
        if eta_seconds is not None and not (self._rebuild_pause_event.is_set() and self._is_rebuild_task(self._active_task_key)):
            self._task_last_eta_text = self._tr('task_eta_label', value=format_duration(int(max(float(eta_seconds), 0.0))))
            self.task_eta_label.setText(self._task_last_eta_text)
        if self._rebuild_pause_event.is_set() and self._is_rebuild_task(self._active_task_key):
            self._refresh_task_controls()
            return
        if stage == 'preflight_scan':
            self.task_detail_label.setText(self._tr('task_detail_preflight_scan', current=max(current, 0), path=str(payload.get('current_path') or self._tr('none_value'))))
        elif stage == 'preflight' and total > 0:
            self.task_detail_label.setText(self._tr('task_detail_preflight_progress', current=current, total=total, path=str(payload.get('current_path') or self._tr('none_value'))))
        elif stage == 'preflight_finalize':
            self.task_detail_label.setText(self._tr('task_detail_preflight_finalize', path=str(payload.get('current_path') or self._tr('none_value'))))
        elif stage == 'indexing' and total > 0:
            self.task_detail_label.setText(self._tr('task_detail_rebuild_progress', current=current, total=total, path=str(payload.get('current_path') or self._tr('none_value'))))
        elif stage == 'rendering':
            self.task_detail_label.setText(self._tr('task_detail_rebuild_rendering_progress', current=current, total=total) if total > 0 else self._tr('task_detail_rebuild_rendering'))
        elif stage == 'vectorizing':
            stage_status = str(payload.get('stage_status') or '').strip().lower()
            if stage_status == 'loading_model' and current <= 0:
                self._set_task_progress_busy()
                self.task_detail_label.setText(self._tr('task_detail_rebuild_vector_loading'))
            else:
                if stage_status == 'recovering':
                    detail = self._tr('task_detail_rebuild_vector_recovering', encoded=encoded, written=written, total=total)
                elif stage_status == 'backpressure':
                    detail = self._tr('task_detail_rebuild_vector_backpressure', encoded=encoded, written=written, total=total)
                elif stage_status == 'flushing':
                    detail = self._tr('task_detail_rebuild_vector_flushing', encoded=encoded, written=written, total=total)
                else:
                    detail = self._tr('task_detail_rebuild_vectorizing_counts', encoded=encoded, written=written, total=total)
                tuning = self._render_vector_tuning(payload)
                detail_text = f'{detail}\n{tuning}' if tuning else detail
                if payload.get('watchdog_stalled'):
                    detail_text = f"{detail_text}\n{self._tr('task_detail_rebuild_watchdog', seconds=float(payload.get('watchdog_wait_seconds', 0.0) or 0.0), report=str(payload.get('watchdog_report_path') or self._tr('none_value')))}"
                self.task_detail_label.setText(detail_text)
        elif payload.get('watchdog_stalled'):
            self.task_detail_label.setText(self._tr('task_detail_rebuild_watchdog', seconds=float(payload.get('watchdog_wait_seconds', 0.0) or 0.0), report=str(payload.get('watchdog_report_path') or self._tr('none_value'))))
        self._refresh_task_controls()
    def _start_service_task(self, label_key: str, runner, on_success, *, require_vault: bool) -> None:
        if self._busy:
            QtWidgets.QMessageBox.information(self, self._tr('busy_title'), self._tr('busy_body'))
            return
        if self._watch_active:
            QtWidgets.QMessageBox.information(self, self._tr('stop_watch_first_title'), self._tr('stop_watch_first_body'))
            return
        try:
            config, paths = self._collect_config(require_vault)
            save_config(config, paths)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self._tr('cannot_start_title'), str(exc))
            return
        self._apply_config_to_controls(config, paths)
        self._busy = True
        self.statusMessageChanged.emit(f"{self._tr(label_key)}…")
        self._task_success_handler = on_success
        self._task_outcome_kind = None
        self._task_outcome_payload = None
        self._task_outcome_message = ''
        self._task_outcome_traceback = ''
        self._start_task_feedback(label_key, config, paths)
        worker = ServiceTaskWorker(config=config, paths=paths, runner=runner, pause_event=self._rebuild_pause_event if self._is_rebuild_task(label_key) else None, cancel_event=self._rebuild_cancel_event if self._is_rebuild_task(label_key) else None)
        worker.progress.connect(self._on_task_progress)
        worker.succeeded.connect(self._on_task_success)
        worker.cancelled.connect(self._on_task_cancelled)
        worker.runtimeError.connect(self._on_task_runtime_error)
        worker.failed.connect(self._on_task_failed)
        worker.finished.connect(self._on_task_finished)
        self._task_worker = worker
        worker.start()
    def _on_task_progress(self, payload: object) -> None:
        if isinstance(payload, dict):
            self._update_task_progress(payload)
    def _on_task_success(self, payload: object) -> None:
        self._task_outcome_kind = 'success'
        self._task_outcome_payload = payload
    def _on_task_cancelled(self, snapshot: object) -> None:
        self._task_outcome_kind = 'cancelled'
        self._task_outcome_payload = snapshot
    def _on_task_runtime_error(self, message: str) -> None:
        self._task_outcome_kind = 'runtime-error'
        self._task_outcome_message = message
    def _on_task_failed(self, message: str, traceback_text: str) -> None:
        self._task_outcome_kind = 'failed'
        self._task_outcome_message = message
        self._task_outcome_traceback = traceback_text
    def _on_task_finished(self) -> None:
        label_key = self._active_task_key
        handler = self._task_success_handler
        outcome_kind = self._task_outcome_kind
        outcome_payload = self._task_outcome_payload
        outcome_message = self._task_outcome_message
        outcome_traceback = self._task_outcome_traceback
        self._busy = False
        self._task_worker = None
        self._task_success_handler = None
        self._task_outcome_kind = None
        self._task_outcome_payload = None
        self._task_outcome_message = ''
        self._task_outcome_traceback = ''
        self._stop_task_feedback()
        if outcome_kind == 'success':
            if handler is not None:
                handler(outcome_payload)
            return
        if outcome_kind == 'cancelled':
            if isinstance(outcome_payload, dict):
                self._refresh_status_summary(outcome_payload)
            if self._is_rebuild_task(label_key):
                self.statusMessageChanged.emit(self._tr('status_rebuild_cancelled'))
                self._append_log(self._tr('log_rebuild_cancelled'))
            else:
                self.statusMessageChanged.emit(self._tr('status_failed', label=self._tr(label_key or 'refresh_button')))
            return
        if outcome_kind == 'runtime-error':
            self.statusMessageChanged.emit(self._tr('status_failed', label=self._tr(label_key or 'refresh_button')))
            self._append_log(outcome_message, focus_log=True)
            if not self._show_runtime_guidance_from_error(outcome_message):
                QtWidgets.QMessageBox.critical(self, self._tr(label_key or 'refresh_button'), outcome_message)
            return
        if outcome_kind == 'failed':
            self.statusMessageChanged.emit(self._tr('status_failed', label=self._tr(label_key or 'refresh_button')))
            self._append_log(outcome_traceback.strip() or outcome_message, focus_log=True)
            QtWidgets.QMessageBox.critical(self, self._tr(label_key or 'refresh_button'), outcome_message or outcome_traceback)
    def _toggle_rebuild_pause(self) -> None:
        if not self._busy or not self._is_rebuild_task(self._active_task_key):
            return
        if self._rebuild_pause_event.is_set():
            if self._task_paused_started_at:
                self._task_paused_total_seconds += max(time.time() - self._task_paused_started_at, 0.0)
            self._task_paused_started_at = 0.0
            self._rebuild_pause_event.clear()
            self.statusMessageChanged.emit(self._tr('status_rebuild_resumed'))
            self._append_log(self._tr('log_rebuild_resumed'))
            self.task_state_label.setText(self._tr('task_running', task=self._tr(self._active_task_key or 'rebuild_button')))
            if self._latest_task_progress is not None:
                self._update_task_progress(dict(self._latest_task_progress))
        else:
            self._rebuild_pause_event.set()
            self._task_paused_started_at = time.time()
            self._freeze_task_progress_visual()
            self.statusMessageChanged.emit(self._tr('status_rebuild_paused'))
            self._append_log(self._tr('log_rebuild_paused'))
            self.task_state_label.setText(self._tr('task_paused', task=self._tr(self._active_task_key or 'rebuild_button')))
            self.task_detail_label.setText(self._tr('task_detail_rebuild_paused'))
            self.task_eta_label.setText(self._tr('task_eta_paused', value=self._task_last_eta_text))
        self._refresh_task_controls()
    def _cancel_rebuild(self) -> None:
        if not self._busy or not self._is_rebuild_task(self._active_task_key):
            return
        answer = QtWidgets.QMessageBox.question(self, self._tr('cancel_rebuild_confirm_title'), self._tr('cancel_rebuild_confirm_body'))
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        if self._task_paused_started_at:
            self._task_paused_total_seconds += max(time.time() - self._task_paused_started_at, 0.0)
            self._task_paused_started_at = 0.0
        self._rebuild_pause_event.clear()
        self._rebuild_cancel_event.set()
        self._freeze_task_progress_visual()
        self.task_state_label.setText(self._tr('task_state_cancelling'))
        self.task_detail_label.setText(self._tr('task_detail_rebuild_cancelling'))
        self.statusMessageChanged.emit(self._tr('status_rebuild_cancel_requested'))
        self._append_log(self._tr('log_rebuild_cancel_requested'))
        self._refresh_task_controls()
    def _offer_resume_rebuild(self, config: AppConfig, paths, payload) -> None:
        pending = payload.get('pending_rebuild') if isinstance(payload, dict) else None
        if not isinstance(pending, dict):
            return
        workspace_id = str(payload.get('workspace_id') or paths.root.name)
        if self._resume_prompt_workspace_id == workspace_id:
            return
        self._resume_prompt_workspace_id = workspace_id
        phase_label = self._tr(f"resume_phase_{str(pending.get('phase') or 'indexing').strip().lower()}")
        self._append_log(self._tr('log_resume_found', completed=int(pending.get('completed', 0) or 0), total=int(pending.get('total', 0) or 0), phase=phase_label))
        answer = QtWidgets.QMessageBox.question(self, self._tr('resume_rebuild_title'), self._tr('resume_rebuild_body', phase=phase_label, completed=int(pending.get('completed', 0) or 0), total=int(pending.get('total', 0) or 0)))
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            self._append_log(self._tr('log_resume_continue'))
            self._run_rebuild(resume=True)
            return
        service = OmniClipService(config, paths)
        try:
            service.discard_pending_rebuild()
            snapshot = service.status_snapshot()
        finally:
            service.close()
        self._refresh_status_summary(snapshot)
        self.statusMessageChanged.emit(self._tr('status_resume_discarded'))
        self._append_log(self._tr('log_resume_discarded'))
    def _run_preflight(self) -> None:
        self._start_service_task('preflight_button', lambda service, emit, pause, cancel: {'report': service.estimate_space(on_progress=emit, pause_event=pause, cancel_event=cancel), 'status': service.status_snapshot()}, self._after_preflight, require_vault=True)
    def _run_bootstrap_model(self, *, followup=None) -> None:
        try:
            config, paths = self._collect_config(True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self._tr('cannot_start_title'), str(exc))
            return
        if is_local_model_ready(config, paths):
            self.statusMessageChanged.emit(self._tr('status_model_already_ready'))
            self._append_log(self._tr('log_model_already_ready', model=config.vector_model))
            QtWidgets.QMessageBox.information(self, self._tr('model_ready_title'), self._tr('model_ready_body', model=config.vector_model))
            return
        choice = self._choose_model_download_mode(self._bootstrap_button_text(), config, paths)
        if choice != 'auto':
            return
        force = self.force_check.isChecked()
        def runner(service, emit, pause, cancel):
            report = service.estimate_space(on_progress=emit, pause_event=pause, cancel_event=cancel)
            if not report.can_proceed and not force:
                return {'blocked': True, 'report': report}
            return {'blocked': False, 'report': report, 'result': service.bootstrap_model(), 'status': service.status_snapshot()}
        def after(payload):
            self._after_bootstrap(payload)
            if followup is not None and not payload.get('blocked'):
                followup()
        self._start_service_task('bootstrap_button', runner, after, require_vault=True)
    def _run_bootstrap_reranker(self) -> None:
        try:
            config, paths = self._collect_config(False)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self._tr('cannot_start_title'), str(exc))
            return
        if is_local_reranker_ready(config, paths):
            self.statusMessageChanged.emit(self._tr('status_reranker_already_ready'))
            self._append_log(self._tr('log_reranker_already_ready', model=config.reranker_model))
            QtWidgets.QMessageBox.information(self, self._tr('reranker_ready_title'), self._tr('reranker_ready_body', model=config.reranker_model))
            return
        choice = self._choose_reranker_download_mode(self._tr('bootstrap_reranker_button'), config, paths)
        if choice != 'auto':
            return
        self._start_service_task('bootstrap_reranker_button', lambda service, emit, pause, cancel: {'result': service.bootstrap_reranker(), 'status': service.status_snapshot()}, self._after_bootstrap_reranker, require_vault=False)
    def _run_rebuild(self, *, resume: bool = False) -> None:
        try:
            config, paths = self._collect_config(True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self._tr('cannot_start_title'), str(exc))
            return
        backend_enabled = (config.vector_backend or 'disabled').strip().lower() not in {'', 'disabled', 'none', 'off'}
        resolved_device = resolve_vector_device(config.vector_device)
        if backend_enabled and str(config.vector_device or '').strip().lower() == 'cuda' and resolved_device != 'cuda':
            self._append_log(self._tr('log_rebuild_cuda_fell_back_to_cpu'))
        if backend_enabled and not self._ensure_vector_runtime_ready(config):
            return
        if backend_enabled and not is_local_model_ready(config, paths):
            if self._prepare_model_for_followup('resume_rebuild_task' if resume else 'rebuild_button', True, lambda: self._run_rebuild(resume=resume)):
                return
            return
        if not resume:
            service = OmniClipService(config, paths)
            try:
                pending = service.pending_rebuild()
            finally:
                service.close()
            if isinstance(pending, dict):
                phase_label = self._tr(f"resume_phase_{str(pending.get('phase') or 'indexing').strip().lower()}")
                answer = QtWidgets.QMessageBox.question(self, self._tr('resume_rebuild_title'), self._tr('resume_rebuild_body', phase=phase_label, completed=int(pending.get('completed', 0) or 0), total=int(pending.get('total', 0) or 0)))
                if answer == QtWidgets.QMessageBox.StandardButton.Yes:
                    self._append_log(self._tr('log_resume_continue'))
                    self._run_rebuild(resume=True)
                    return
                service = OmniClipService(config, paths)
                try:
                    service.discard_pending_rebuild()
                finally:
                    service.close()
            if self._index_ready(self._status_snapshot):
                answer = QtWidgets.QMessageBox.question(self, self._tr('rebuild_confirm_existing_title'), self._tr('rebuild_confirm_existing_body'))
                if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                    return
        force = self.force_check.isChecked()
        label_key = 'resume_rebuild_task' if resume else 'rebuild_button'
        def runner(service, emit, pause, cancel):
            report = None
            if not resume:
                report = service.estimate_space(on_progress=emit, pause_event=pause, cancel_event=cancel)
                if not report.can_proceed and not force:
                    return {'blocked': True, 'report': report}
            stats = service.rebuild_index(resume=resume, on_progress=emit, pause_event=pause, cancel_event=cancel)
            return {'blocked': False, 'report': report, 'stats': stats, 'status': service.status_snapshot(), 'resumed': resume}
        self._start_service_task(label_key, runner, self._after_rebuild, require_vault=True)
    def _run_refresh(self) -> None:
        self._start_service_task('refresh_button', lambda service, emit, pause, cancel: service.status_snapshot(), self._after_status, require_vault=False)
    def _run_clear(self) -> None:
        if not any((self.clear_index_check.isChecked(), self.clear_logs_check.isChecked(), self.clear_cache_check.isChecked(), self.clear_exports_check.isChecked())):
            QtWidgets.QMessageBox.information(self, self._tr('clear_pick_title'), self._tr('clear_pick_body'))
            return
        answer = QtWidgets.QMessageBox.question(self, self._tr('clear_confirm_title'), self._tr('clear_confirm_body'))
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        def runner(service, emit, pause, cancel):
            service.clear_data(clear_index=self.clear_index_check.isChecked(), clear_logs=self.clear_logs_check.isChecked(), clear_cache=self.clear_cache_check.isChecked(), clear_exports=self.clear_exports_check.isChecked())
            return service.status_snapshot()
        self._start_service_task('clear_button', runner, self._after_clear, require_vault=True)
    def _toggle_watch(self) -> None:
        if self._watch_active:
            if self._watch_worker is not None:
                self._watch_worker.stop()
            self._watch_stopping = True
            self.statusMessageChanged.emit(self._tr('status_watch_stopping'))
            self._append_log(self._tr('log_watch_requested_stop'))
            self._refresh_watch_button()
            return
        if self._busy:
            QtWidgets.QMessageBox.information(self, self._tr('busy_title'), self._tr('busy_body'))
            return
        try:
            config, paths = self._collect_config(True)
            save_config(config, paths)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self._tr('watch_start_failed_title'), str(exc))
            return
        service = OmniClipService(config, paths)
        try:
            snapshot = service.status_snapshot()
        finally:
            service.close()
        self._refresh_status_summary(snapshot)
        index_state = self._current_index_state(snapshot)
        if index_state != 'ready':
            body_key = 'watch_start_blocked_pending_body' if index_state == 'pending' else 'watch_start_blocked_missing_body'
            message = self._tr(body_key)
            self.statusMessageChanged.emit(message)
            QtWidgets.QMessageBox.information(self, self._tr('watch_start_blocked_title'), message)
            return
        backend_enabled = (config.vector_backend or 'disabled').strip().lower() not in {'', 'disabled', 'none', 'off'}
        resolved_device = resolve_vector_device(config.vector_device)
        if backend_enabled and str(config.vector_device or '').strip().lower() == 'cuda' and resolved_device != 'cuda':
            self._append_log(self._tr('log_rebuild_cuda_fell_back_to_cpu'))
        if backend_enabled and not self._ensure_vector_runtime_ready(config):
            return
        if backend_enabled and not is_local_model_ready(config, paths):
            self._prepare_model_for_followup('watch_start', True, self._toggle_watch)
            return
        self._apply_config_to_controls(config, paths)
        self._watch_active = True
        self._watch_stopping = False
        self._watch_mode = 'polling' if self.polling_check.isChecked() or not WATCHDOG_AVAILABLE else 'watchdog'
        self.statusMessageChanged.emit(self._tr('status_watch_running'))
        self._append_log(self._tr('log_watch_started', mode=self._watch_mode_label(self._watch_mode)))
        self._refresh_status_summary(self._status_snapshot)
        worker = WatchWorker(config=config, paths=paths, interval=config.poll_interval_seconds, force_polling=self.polling_check.isChecked())
        worker.updated.connect(self._on_watch_updated)
        worker.failed.connect(self._on_watch_failed)
        worker.stopped.connect(self._on_watch_stopped)
        worker.finished.connect(self._on_watch_finished)
        self._watch_worker = worker
        worker.start()
    def _on_watch_updated(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        stats = payload.get('stats', {})
        if self._status_snapshot is None:
            self._status_snapshot = {}
        self._status_snapshot = dict(self._status_snapshot)
        self._status_snapshot['stats'] = stats
        self._refresh_status_summary(self._status_snapshot)
        events = payload.get('events', []) or []
        for event in events:
            kind = str(event.get('kind') or '').strip().lower()
            if kind == 'vault_offline':
                self._append_log(self._tr('log_watch_vault_offline', reason=str(event.get('reason') or self._tr('none_value'))), focus_log=True)
            elif kind == 'vault_recovered':
                self._append_log(self._tr('log_watch_vault_recovered'))
            elif kind == 'repair':
                self._append_log(self._tr('log_watch_repaired', paths=int(event.get('paths', 0) or 0), vector_paths=int(event.get('vector_paths', 0) or 0), vector_chunk_ids=int(event.get('vector_chunk_ids', 0) or 0)))
            elif kind == 'batch_retry':
                self._append_log(self._tr('log_watch_batch_retry', changed=', '.join(event.get('changed', [])[:3]) or self._tr('none_value'), deleted=', '.join(event.get('deleted', [])[:3]) or self._tr('none_value'), error=str(event.get('error') or self._tr('none_value'))), focus_log=True)
        if not payload.get('note_only'):
            self.statusMessageChanged.emit(self._tr('status_watch_update'))
            self._append_log(self._tr('log_watch_update', changed=', '.join(payload.get('changed', [])[:3]) or self._tr('none_value'), deleted=', '.join(payload.get('deleted', [])[:3]) or self._tr('none_value')))
    def _on_watch_failed(self, message: str, traceback_text: str) -> None:
        self.statusMessageChanged.emit(self._tr('status_watch_error'))
        self._append_log(self._tr('log_watch_error'), focus_log=True)
        self._append_log(traceback_text.strip() or message, focus_log=True)
        QtWidgets.QMessageBox.critical(self, self._tr('watch_start_failed_title'), message or traceback_text)
    def _on_watch_stopped(self, raw_mode: str) -> None:
        self._watch_active = False
        self._watch_stopping = False
        self._watch_mode = raw_mode
        self.watch_summary_label.setText(self._tr('watch_stopped', mode=self._watch_mode_label(raw_mode)))
        self.statusMessageChanged.emit(self._tr('status_watch_stopped'))
        self._append_log(self._tr('log_watch_stopped'))
        self._refresh_watch_button()
        self._emit_query_block_state()
    def _on_watch_finished(self) -> None:
        self._watch_worker = None
        self._refresh_status_summary(self._status_snapshot)
    def _after_preflight(self, payload) -> None:
        report = payload['report']
        self._current_report = report
        self._refresh_status_summary(self._merge_status_snapshot(payload.get('status')))
        self.statusMessageChanged.emit(self._tr('status_preflight_done'))
        self._append_log(self._tr('log_preflight_done'))
        self._append_log(format_space_report(report, self._language_code))
    def _after_bootstrap(self, payload) -> None:
        report = payload.get('report')
        if report is not None:
            self._current_report = report
        stats = payload['stats']
        snapshot = self._merge_status_snapshot(payload.get('status'), stats=stats)
        self._refresh_status_summary(snapshot)
        if payload.get('blocked'):
            self.statusMessageChanged.emit(self._tr('bootstrap_blocked_title'))
            self._append_log(self._tr('log_bootstrap_blocked'))
            QtWidgets.QMessageBox.warning(self, self._tr('bootstrap_blocked_title'), self._tr('bootstrap_blocked_body'))
            return
        result = payload['result']
        self.statusMessageChanged.emit(self._tr('status_bootstrap_done'))
        self._append_log(self._tr('log_bootstrap_done', model=result.get('model'), dimension=result.get('dimension'), cache=format_bytes(int(result.get('cache_bytes', 0)))))
        self._refresh_status_summary(snapshot)
    def _after_bootstrap_reranker(self, payload) -> None:
        result = payload['result']
        self._refresh_status_summary(payload.get('status'))
        self.statusMessageChanged.emit(self._tr('status_reranker_ready'))
        self._append_log(self._tr('log_reranker_ready', model=result.get('model')))
    def _after_rebuild(self, payload) -> None:
        report = payload.get('report')
        if report is not None:
            self._current_report = report
        stats = payload['stats']
        snapshot = self._merge_status_snapshot(payload.get('status'), stats=stats)
        self._refresh_status_summary(snapshot)
        if payload.get('blocked'):
            self.statusMessageChanged.emit(self._tr('rebuild_blocked_title'))
            self._append_log(self._tr('log_rebuild_blocked'))
            QtWidgets.QMessageBox.warning(self, self._tr('rebuild_blocked_title'), self._tr('rebuild_blocked_body'))
            return
        duplicate_count = int(stats.get('duplicate_block_ids', 0) or 0)
        if duplicate_count:
            self.statusMessageChanged.emit(self._tr('status_rebuild_done_duplicates', count=duplicate_count))
            self._append_log(self._tr('log_duplicate_block_ids', count=duplicate_count), focus_log=True)
        else:
            self.statusMessageChanged.emit(self._tr('status_rebuild_done'))
        self._append_log(self._tr('log_rebuild_done', files=stats['files'], chunks=stats['chunks'], refs=stats['refs']))
    def _after_status(self, payload) -> None:
        self._refresh_status_summary(payload)
        self.statusMessageChanged.emit(self._tr('status_refresh_done'))
        self._append_log(self._tr('log_status_done'))
        try:
            config, paths = self._collect_config(False)
        except Exception:
            return
        self._offer_resume_rebuild(config, paths, payload)
    def _after_clear(self, payload) -> None:
        self.clear_index_check.setChecked(False)
        self.clear_logs_check.setChecked(False)
        self.clear_cache_check.setChecked(False)
        self.clear_exports_check.setChecked(False)
        self._refresh_status_summary(payload)
        self.statusMessageChanged.emit(self._tr('status_clear_done'))
        self._append_log(self._tr('log_clear_done'))