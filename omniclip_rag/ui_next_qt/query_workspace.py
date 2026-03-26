from __future__ import annotations

import html
import time
import logging
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from ..clipboard import copy_text
from ..config import ensure_data_paths, normalize_vault_path
from ..service import OmniClipService
from ..ui_i18n import text, tooltip
from ..ui_shared import collect_context_sections, count_enabled_page_filter_rules, query_progress_detail, render_query_limit_hint
from ..vector_index import get_local_model_dir, is_local_model_ready
from .query_table_model import QueryResultsTableModel
from .searchable_text_panel import SearchableTextPanel
from .theme import ThemeState, scaled
from .workers import MultiVaultQueryWorker, QueryTaskResult, QueryWorker

LOGGER = logging.getLogger(__name__)


class _SplitterFriendlyFrame(QtWidgets.QFrame):
    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(0, 0)


class QueryWorkspace(QtWidgets.QWidget):
    statusMessageChanged = QtCore.Signal(str)
    resultSummaryChanged = QtCore.Signal(str)
    pageBlocklistRequested = QtCore.Signal()
    sensitiveFilterRequested = QtCore.Signal()
    runtimeRepairRequested = QtCore.Signal()

    def __init__(
        self,
        *,
        config,
        paths,
        language_code: str,
        theme: ThemeState,
        runtime_snapshot_provider: Callable[[], tuple[object, object]] | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._paths = paths
        self._language_code = language_code
        self._theme = theme
        self._runtime_snapshot_provider = runtime_snapshot_provider
        self._busy = False
        self._external_blocked = False
        self._external_block_title = ''
        self._external_block_detail = ''
        self._query_last_completed_at = 0.0
        self._query_last_result_count = 0
        self._query_last_copied = False
        self._query_runtime_warnings: tuple[str, ...] = ()
        self._query_progress_payload: dict[str, object] | None = None
        self._current_query_text = ''
        self._current_context = ''
        self._context_sections: list[dict[str, object]] = []
        self._log_lines: list[str] = []
        self._query_limit_recommendation: dict[str, object] | None = None
        self._worker: QtCore.QObject | None = None
        self._pending_query_splitter_state: bytes | None = None
        self._pending_results_splitter_state: bytes | None = None
        self._splitter_restore_queued = False
        self._splitter_state_applied = False
        self._search_controls_collapsed = False
        self._expanded_query_splitter_sizes: list[int] | None = None
        self._search_card_default_margins: tuple[int, int, int, int] | None = None
        self._search_card_default_spacing = 0

        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(12)

        self.query_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical, self)
        self.query_splitter.setChildrenCollapsible(True)
        self.query_splitter.setOpaqueResize(False)
        root_layout.addWidget(self.query_splitter, 1)

        self._build_search_card()
        self._build_results_card()

        self.query_splitter.addWidget(self.search_card)
        self.query_splitter.addWidget(self.results_card)
        self.query_splitter.setCollapsible(0, True)
        self.query_splitter.setCollapsible(1, True)
        self.query_splitter.setStretchFactor(0, 0)
        self.query_splitter.setStretchFactor(1, 1)
        self._configure_splitter_child(self.search_card)
        self._configure_splitter_child(self.results_card)
        self._refresh_splitter_handles()

        self._apply_initial_values()
        self._refresh_page_blocklist_summary()
        self._refresh_query_limit_hint()
        self._refresh_context_selection_summary()
        self._refresh_page_sort_button()
        self._refresh_query_status_banner()
        self._append_log(self._tr('status_ready'))
        self.resultSummaryChanged.emit(self._tr('result_empty'))
        self.statusMessageChanged.emit(self._tr('status_ready'))

    def _tr(self, key: str, **kwargs) -> str:
        return text(self._language_code, key, **kwargs)

    def _tip(self, key: str, **kwargs) -> str:
        return tooltip(self._language_code, key, **kwargs)

    def _build_search_card(self) -> None:
        self.search_card, self.search_card_layout, self.search_header_widget, search_header = self._create_card(
            self._tr('search_title'),
            self._tr('search_subtitle'),
        )
        self._search_card_default_margins = self.search_card_layout.getContentsMargins()
        self._search_card_default_spacing = self.search_card_layout.spacing()

        self.query_status_banner = QtWidgets.QFrame(self.search_card)
        self.query_status_banner.setObjectName('QueryStatusBanner')
        self.query_status_banner.setProperty('mode', 'idle')
        banner_layout = QtWidgets.QVBoxLayout(self.query_status_banner)
        banner_layout.setContentsMargins(12, 8, 12, 8)
        banner_layout.setSpacing(4)
        self.query_status_title = QtWidgets.QLabel(self._tr('query_status_idle_title'), self.query_status_banner)
        self.query_status_title.setObjectName('QueryStatusTitle')
        self.query_status_title.setProperty('mode', 'idle')
        self.query_status_title.setWordWrap(True)
        banner_layout.addWidget(self.query_status_title)
        self.query_status_detail = QtWidgets.QLabel(self._tr('query_status_idle_detail'), self.query_status_banner)
        self.query_status_detail.setObjectName('QueryStatusDetail')
        self.query_status_detail.setProperty('mode', 'idle')
        self.query_status_detail.setWordWrap(True)
        banner_layout.addWidget(self.query_status_detail)
        search_header.addWidget(self.query_status_banner, 0, QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignTop)

        self.query_hint_label = QtWidgets.QLabel(self._tr('query_hint'), self.search_card)
        self.query_hint_label.setProperty('role', 'guide')
        self.query_hint_label.setWordWrap(True)
        self.search_card_layout.addWidget(self.query_hint_label)

        self.query_row_host = QtWidgets.QWidget(self.search_card)
        self.query_row_host.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        query_row = QtWidgets.QHBoxLayout(self.query_row_host)
        query_row.setContentsMargins(0, 0, 0, 0)
        query_row.setSpacing(10)
        self.search_card_layout.addWidget(self.query_row_host)

        self.query_edit = QtWidgets.QLineEdit(self.search_card)
        self.query_edit.setToolTip(self._tip('query'))
        self.query_edit.returnPressed.connect(self.search)
        query_row.addWidget(self.query_edit, 1)

        self.search_button = QtWidgets.QPushButton(self._tr('search_button'), self.search_card)
        self.search_button.setToolTip(self._tip('search'))
        self._set_button_variant(self.search_button, 'secondary')
        self.search_button.clicked.connect(self.search)
        query_row.addWidget(self.search_button)

        self.search_controls_toggle_button = QtWidgets.QPushButton(self._tr('search_controls_collapse'), self.search_card)
        self.search_controls_toggle_button.setToolTip(self._tip('search'))
        self._set_button_variant(self.search_controls_toggle_button, 'secondary')
        self.search_controls_toggle_button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)
        self.search_controls_toggle_button.clicked.connect(self._toggle_search_controls_collapsed)
        query_row.addWidget(self.search_controls_toggle_button)

        self.search_copy_button = QtWidgets.QPushButton(self._tr('search_copy_button'), self.search_card)
        self.search_copy_button.setToolTip(self._tip('search_copy'))
        self._set_button_variant(self.search_copy_button, 'primary')
        self.search_copy_button.clicked.connect(self.search_and_copy)
        self.search_copy_button.hide()

        self.copy_context_button = QtWidgets.QPushButton(self._tr('copy_context_button'), self.search_card)
        self.copy_context_button.setToolTip(self._tip('copy_context'))
        self._set_button_variant(self.copy_context_button, 'secondary')
        self.copy_context_button.clicked.connect(self.copy_current_context)
        self.copy_context_button.hide()

        self.search_details_widget = QtWidgets.QWidget(self.search_card)
        search_details_layout = QtWidgets.QVBoxLayout(self.search_details_widget)
        search_details_layout.setContentsMargins(0, 0, 0, 0)
        search_details_layout.setSpacing(10)
        self.search_card_layout.addWidget(self.search_details_widget)

        self.meta_widget = QtWidgets.QWidget(self.search_details_widget)
        meta_row = QtWidgets.QGridLayout(self.meta_widget)
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setHorizontalSpacing(10)
        meta_row.setVerticalSpacing(8)
        search_details_layout.addWidget(self.meta_widget)

        threshold_label = QtWidgets.QLabel(self._tr('score_threshold_label'), self.search_card)
        threshold_label.setProperty('role', 'muted')
        meta_row.addWidget(threshold_label, 0, 0)
        self.threshold_edit = QtWidgets.QLineEdit(self.search_card)
        self.threshold_edit.setToolTip(self._tip('score_threshold'))
        self.threshold_edit.setMaximumWidth(120)
        meta_row.addWidget(self.threshold_edit, 0, 1)

        limit_label = QtWidgets.QLabel(self._tr('limit_label'), self.search_card)
        limit_label.setProperty('role', 'muted')
        meta_row.addWidget(limit_label, 0, 2)
        self.limit_edit = QtWidgets.QLineEdit(self.search_card)
        self.limit_edit.setToolTip(self._tip('limit'))
        self.limit_edit.setMaximumWidth(120)
        meta_row.addWidget(self.limit_edit, 0, 3)

        self.query_limit_hint_label = QtWidgets.QLabel(self.search_card)
        self.query_limit_hint_label.setProperty('role', 'muted')
        self.query_limit_hint_label.setWordWrap(True)
        meta_row.addWidget(self.query_limit_hint_label, 1, 0, 1, 4)
        self.query_scope_label = QtWidgets.QLabel(self.search_card)
        self.query_scope_label.setProperty('role', 'muted')
        self.query_scope_label.setWordWrap(True)
        meta_row.addWidget(self.query_scope_label, 2, 0, 1, 4)

        self.source_widget = QtWidgets.QWidget(self.search_details_widget)
        source_row = QtWidgets.QHBoxLayout(self.source_widget)
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(10)
        search_details_layout.addWidget(self.source_widget)

        source_label = QtWidgets.QLabel(self._tr('query_source_filters_label'), self.search_card)
        source_label.setProperty('role', 'muted')
        source_row.addWidget(source_label)

        self.source_markdown_check = QtWidgets.QCheckBox(self._tr('query_source_markdown'), self.search_card)
        self.source_pdf_check = QtWidgets.QCheckBox(self._tr('query_source_pdf'), self.search_card)
        self.source_tika_check = QtWidgets.QCheckBox(self._tr('query_source_tika'), self.search_card)
        for widget in (self.source_markdown_check, self.source_pdf_check, self.source_tika_check):
            source_row.addWidget(widget)
        source_row.addStretch(1)

        self.query_runtime_hint_label = QtWidgets.QLabel(self.search_card)
        self.query_runtime_hint_label.setProperty('role', 'muted')
        self.query_runtime_hint_label.setWordWrap(True)
        self.query_runtime_hint_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.query_runtime_hint_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextBrowserInteraction)
        self.query_runtime_hint_label.setOpenExternalLinks(False)
        self.query_runtime_hint_label.linkActivated.connect(self._handle_runtime_hint_link)
        self.query_runtime_hint_label.setVisible(False)
        search_details_layout.addWidget(self.query_runtime_hint_label)

    def _build_results_card(self) -> None:
        self.results_card, results_layout, _results_header_widget, results_header = self._create_card(
            self._tr('results_title'),
            self._tr('results_subtitle'),
        )

        results_actions = QtWidgets.QHBoxLayout()
        results_actions.setSpacing(8)
        results_header.addLayout(results_actions)

        self.page_blocklist_button = QtWidgets.QPushButton(self._tr('page_blocklist_button'), self.results_card)
        self.page_blocklist_button.setToolTip(self._tip('page_blocklist'))
        self._set_button_variant(self.page_blocklist_button, 'secondary')
        self.page_blocklist_button.clicked.connect(self.pageBlocklistRequested.emit)
        results_actions.addWidget(self.page_blocklist_button)

        self.sensitive_filter_button = QtWidgets.QPushButton(self._tr('sensitive_filter_button'), self.results_card)
        self.sensitive_filter_button.setToolTip(self._tip('sensitive_filter'))
        self._set_button_variant(self.sensitive_filter_button, 'secondary')
        self.sensitive_filter_button.clicked.connect(self.sensitiveFilterRequested.emit)
        results_actions.addWidget(self.sensitive_filter_button)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setSpacing(10)
        results_layout.addLayout(toolbar)

        self.context_toggle_button = QtWidgets.QPushButton(self._tr('context_select_all'), self.results_card)
        self.context_toggle_button.setToolTip(self._tip('context_select_toggle'))
        self._set_button_variant(self.context_toggle_button, 'secondary')
        self.context_toggle_button.clicked.connect(self.toggle_all_context_rows)
        toolbar.addWidget(self.context_toggle_button)

        self.page_sort_button = QtWidgets.QPushButton(self._tr('page_sort_button'), self.results_card)
        self.page_sort_button.setToolTip(self._tip('page_sort'))
        self._set_button_variant(self.page_sort_button, 'secondary')
        self.page_sort_button.clicked.connect(self.toggle_page_sort)
        toolbar.addWidget(self.page_sort_button)

        self.page_blocklist_summary_label = QtWidgets.QLabel(self.results_card)
        self.page_blocklist_summary_label.setProperty('role', 'muted')
        toolbar.addWidget(self.page_blocklist_summary_label)

        self.context_selection_label = QtWidgets.QLabel(self.results_card)
        self.context_selection_label.setProperty('role', 'muted')
        toolbar.addWidget(self.context_selection_label, 1)
        toolbar.addStretch(1)

        self.results_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical, self.results_card)
        self.results_splitter.setChildrenCollapsible(True)
        self.results_splitter.setOpaqueResize(False)
        results_layout.addWidget(self.results_splitter, 1)

        table_host = QtWidgets.QWidget(self.results_card)
        table_layout = QtWidgets.QVBoxLayout(table_host)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(0)
        self.results_model = QueryResultsTableModel(self._tr, table_host)
        self.results_model.selectionChanged.connect(self._on_context_selection_changed)
        self.results_model.orderingChanged.connect(self._on_results_ordering_changed)
        self.table_view = QtWidgets.QTableView(table_host)
        self.table_view.setModel(self.results_model)
        self.table_view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_view.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table_view.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_view.setSortingEnabled(False)
        self.table_view.verticalHeader().setVisible(False)
        self.table_view.horizontalHeader().sectionClicked.connect(self._sort_by_column)
        self.table_view.clicked.connect(self._handle_table_clicked)
        self.table_view.selectionModel().selectionChanged.connect(self._on_table_selection_changed)
        self.table_view.setMinimumHeight(0)
        table_layout.addWidget(self.table_view)

        details_host = QtWidgets.QWidget(self.results_card)
        details_layout = QtWidgets.QVBoxLayout(details_host)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(0)
        self.detail_tabs = QtWidgets.QTabWidget(details_host)
        self.detail_tabs.setMinimumHeight(0)
        details_layout.addWidget(self.detail_tabs)

        self.preview_panel = SearchableTextPanel(empty_text=self._tr('preview_empty'), theme=self._theme, tr=self._tr, parent=details_host)
        self.preview_panel.search_edit.setToolTip(self._tip('text_search_entry'))
        self.preview_panel.search_button.setToolTip(self._tip('text_search_button'))
        self.preview_panel.next_button.setToolTip(self._tip('text_search_next'))
        self.context_panel = SearchableTextPanel(empty_text=self._tr('context_empty'), theme=self._theme, tr=self._tr, parent=details_host)
        self.context_panel.search_edit.setToolTip(self._tip('text_search_entry'))
        self.context_panel.search_button.setToolTip(self._tip('text_search_button'))
        self.context_panel.next_button.setToolTip(self._tip('text_search_next'))
        self.log_panel = SearchableTextPanel(empty_text=self._tr('log_empty'), theme=self._theme, tr=self._tr, parent=details_host)
        self.log_panel.search_edit.setToolTip(self._tip('text_search_entry'))
        self.log_panel.search_button.setToolTip(self._tip('text_search_button'))
        self.log_panel.next_button.setToolTip(self._tip('text_search_next'))
        self.context_panel.set_header_visible(True)
        self.context_jump_combo = QtWidgets.QComboBox(self.context_panel.header_widget)
        self.context_jump_combo.setToolTip(self._tip('context_jump'))
        self.context_jump_combo.currentTextChanged.connect(self._jump_to_context_section)
        self.context_panel.header_layout.addWidget(self.context_jump_combo)
        self.context_panel.header_layout.addStretch(1)
        self.context_jump_summary_label = QtWidgets.QLabel(self._tr('context_jump_summary_empty'), self.context_panel.header_widget)
        self.context_jump_summary_label.setProperty('role', 'muted')
        self.context_panel.header_layout.addWidget(self.context_jump_summary_label)

        self.detail_tabs.addTab(self.preview_panel, self._tr('tab_preview'))
        self.detail_tabs.addTab(self.context_panel, self._tr('tab_context'))
        self.detail_tabs.addTab(self.log_panel, self._tr('tab_log'))

        self.results_splitter.addWidget(table_host)
        self.results_splitter.addWidget(details_host)
        self.results_splitter.setCollapsible(0, True)
        self.results_splitter.setCollapsible(1, True)
        self.results_splitter.setStretchFactor(0, 1)
        self.results_splitter.setStretchFactor(1, 1)
        self._configure_splitter_child(table_host)
        self._configure_splitter_child(details_host)
        self._refresh_results_columns()
        self._refresh_splitter_handles()

    def _create_card(
        self,
        title: str,
        subtitle: str,
    ) -> tuple[QtWidgets.QFrame, QtWidgets.QVBoxLayout, QtWidgets.QWidget, QtWidgets.QHBoxLayout]:
        card = _SplitterFriendlyFrame(self)
        card.setProperty('card', True)
        layout = QtWidgets.QVBoxLayout(card)
        margin = scaled(self._theme, 14, minimum=10)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(10)

        header = QtWidgets.QWidget(card)
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)
        layout.addWidget(header)

        title_layout = QtWidgets.QVBoxLayout()
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(6)
        header_layout.addLayout(title_layout, 1)

        title_label = QtWidgets.QLabel(title, header)
        title_label.setProperty('role', 'cardTitle')
        title_layout.addWidget(title_label)

        subtitle_label = QtWidgets.QLabel(subtitle, header)
        subtitle_label.setProperty('role', 'subtitle')
        subtitle_label.setWordWrap(True)
        title_layout.addWidget(subtitle_label)

        return card, layout, header, header_layout

    def _set_button_variant(self, button: QtWidgets.QPushButton, variant: str) -> None:
        button.setProperty('variant', variant)
        style = button.style()
        style.unpolish(button)
        style.polish(button)
        button.update()

    def _configure_splitter_child(self, widget: QtWidgets.QWidget) -> None:
        widget.setMinimumSize(0, 0)
        widget.setMaximumSize(16777215, 16777215)
        policy = widget.sizePolicy()
        policy.setHorizontalPolicy(QtWidgets.QSizePolicy.Policy.Expanding)
        policy.setVerticalPolicy(QtWidgets.QSizePolicy.Policy.Ignored)
        widget.setSizePolicy(policy)

    def _refresh_splitter_handles(self) -> None:
        handle_width = max(scaled(self._theme, 10, minimum=8), 8)
        for splitter in (self.query_splitter, self.results_splitter):
            splitter.setHandleWidth(handle_width)
            try:
                splitter.handle(1).setCursor(QtCore.Qt.CursorShape.SplitVCursor)
            except Exception:
                continue

    def _apply_initial_values(self) -> None:
        self.limit_edit.setText(str(int(getattr(self._config, 'query_limit', 15) or 15)))
        threshold = float(getattr(self._config, 'query_score_threshold', 35.0) or 35.0)
        self.threshold_edit.setText(str(int(threshold)) if threshold.is_integer() else str(threshold))
        self.source_markdown_check.setChecked(True)
        self.source_pdf_check.setChecked(True)
        self.source_tika_check.setChecked(True)
        self._query_runtime_warnings = ()
        self._search_controls_collapsed = bool(getattr(self._config, 'qt_query_controls_collapsed', False))
        self._refresh_query_runtime_hint()
        self._apply_search_controls_collapsed()
        self.preview_panel.set_text(self._tr('preview_empty'))
        self.context_panel.set_text(self._tr('context_empty'))
        self.log_panel.set_text(self._tr('log_empty'))
        self._refresh_query_scope_label()

    def default_query_splitter_sizes(self) -> list[int]:
        return [max(scaled(self._theme, 250, minimum=220), 220), max(scaled(self._theme, 620, minimum=440), 440)]

    def default_results_splitter_sizes(self) -> list[int]:
        return [max(scaled(self._theme, 340, minimum=220), 220), max(scaled(self._theme, 360, minimum=220), 220)]

    def restore_splitter_states(self, *, query_state: bytes | None, results_state: bytes | None) -> None:
        self._pending_query_splitter_state = query_state
        self._pending_results_splitter_state = results_state
        self._schedule_splitter_restore()

    def _schedule_splitter_restore(self) -> None:
        if self._splitter_restore_queued:
            return
        self._splitter_restore_queued = True
        QtCore.QTimer.singleShot(0, self._apply_pending_splitter_states)

    def _apply_pending_splitter_states(self) -> None:
        self._splitter_restore_queued = False
        query_state = self._pending_query_splitter_state
        results_state = self._pending_results_splitter_state
        self._pending_query_splitter_state = None
        self._pending_results_splitter_state = None
        restored_query = False
        restored_results = False
        if query_state:
            restored_query = self.query_splitter.restoreState(QtCore.QByteArray(query_state))
        if results_state:
            restored_results = self.results_splitter.restoreState(QtCore.QByteArray(results_state))
        if not restored_query or min(self.query_splitter.sizes() or [0]) <= 0:
            self.query_splitter.setSizes(self.default_query_splitter_sizes())
        if not restored_results or min(self.results_splitter.sizes() or [0]) <= 0:
            self.results_splitter.setSizes(self.default_results_splitter_sizes())
        self._splitter_state_applied = True
        QtCore.QTimer.singleShot(0, lambda: self._apply_query_splitter_compaction(collapsed=self._search_controls_collapsed))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_search_controls_collapsed()
        if not self._splitter_state_applied or self._pending_query_splitter_state is not None or self._pending_results_splitter_state is not None:
            self._schedule_splitter_restore()

    def query_splitter_state(self) -> bytes:
        return bytes(self.query_splitter.saveState())

    def results_splitter_state(self) -> bytes:
        return bytes(self.results_splitter.saveState())

    def update_runtime(self, *, config, paths) -> None:
        self._config = config
        self._paths = paths
        self.clear_runtime_feedback()
        self._refresh_page_blocklist_summary()
        self._refresh_query_limit_hint()
        self._refresh_query_scope_label()

    def clear_runtime_feedback(self) -> None:
        self._query_runtime_warnings = ()
        self._refresh_query_runtime_hint()

    def set_runtime_snapshot_provider(self, provider: Callable[[], tuple[object, object]] | None) -> None:
        self._runtime_snapshot_provider = provider

    def update_theme(self, theme: ThemeState) -> None:
        self._theme = theme
        self.preview_panel.set_theme(theme)
        self.context_panel.set_theme(theme)
        self.log_panel.set_theme(theme)
        self._refresh_results_columns()
        self._refresh_splitter_handles()
        self._apply_search_controls_collapsed()
        if min(self.query_splitter.sizes() or [0]) <= 0 or min(self.results_splitter.sizes() or [0]) <= 0:
            self._splitter_state_applied = False
            self._schedule_splitter_restore()

    def snapshot_view_state(self) -> dict[str, object]:
        return {
            'query_text': self.query_edit.text(),
            'threshold_text': self.threshold_edit.text(),
            'limit_text': self.limit_edit.text(),
            'source_markdown_checked': self.source_markdown_check.isChecked(),
            'source_pdf_checked': self.source_pdf_check.isChecked(),
            'source_tika_checked': self.source_tika_check.isChecked(),
            'detail_tab_index': self.detail_tabs.currentIndex(),
            'search_controls_collapsed': self._search_controls_collapsed,
            'query_splitter_state': self.query_splitter_state(),
            'results_splitter_state': self.results_splitter_state(),
            'hits': self.results_model.hits(),
            'selected_chunk_ids': [getattr(hit, 'chunk_id', '') for hit in self.results_model.selected_hits()],
            'selected_chunk_id': self._selected_chunk_id(),
            'current_query_text': self._current_query_text,
            'query_runtime_warnings': list(self._query_runtime_warnings),
            'log_lines': list(self._log_lines),
            'external_blocked': self._external_blocked,
            'external_block_title': self._external_block_title,
            'external_block_detail': self._external_block_detail,
        }

    def restore_view_state(self, state: dict[str, object] | None) -> None:
        payload = dict(state or {})
        self.query_edit.setText(str(payload.get('query_text') or ''))
        self.threshold_edit.setText(str(payload.get('threshold_text') or self.threshold_edit.text()))
        self.limit_edit.setText(str(payload.get('limit_text') or self.limit_edit.text()))
        self.source_markdown_check.setChecked(bool(payload.get('source_markdown_checked', True)))
        self.source_pdf_check.setChecked(bool(payload.get('source_pdf_checked', True)))
        self.source_tika_check.setChecked(bool(payload.get('source_tika_checked', True)))
        self._search_controls_collapsed = bool(payload.get('search_controls_collapsed', getattr(self._config, 'qt_query_controls_collapsed', False)))
        query_state = payload.get('query_splitter_state')
        results_state = payload.get('results_splitter_state')
        if isinstance(query_state, (bytes, bytearray)) or isinstance(results_state, (bytes, bytearray)):
            self.restore_splitter_states(
                query_state=bytes(query_state) if isinstance(query_state, (bytes, bytearray)) else None,
                results_state=bytes(results_state) if isinstance(results_state, (bytes, bytearray)) else None,
            )
        hits = list(payload.get('hits') or [])
        self._query_runtime_warnings = tuple(str(item).strip() for item in (payload.get('query_runtime_warnings') or []) if str(item).strip())
        self._refresh_query_runtime_hint()
        if hits:
            self.results_model.set_results(hits)
            self.results_model.set_selected_chunk_ids(payload.get('selected_chunk_ids') or [])
            self._current_query_text = str(payload.get('current_query_text') or '')
            self._restore_selection_by_chunk_id(str(payload.get('selected_chunk_id') or ''))
            self._rebuild_context_view()
        else:
            self.results_model.clear()
            self._current_query_text = ''
            self.preview_panel.set_text(self._tr('preview_empty'))
            self.context_panel.set_text(self._tr('context_empty'))
        self._log_lines = [str(item) for item in (payload.get('log_lines') or []) if str(item).strip()]
        self.log_panel.set_text('\n'.join(self._log_lines) if self._log_lines else self._tr('log_empty'))
        self.set_external_block_state(
            blocked=bool(payload.get('external_blocked')),
            title=str(payload.get('external_block_title') or ''),
            detail=str(payload.get('external_block_detail') or ''),
        )
        detail_tab_index = int(payload.get('detail_tab_index', 0) or 0)
        if 0 <= detail_tab_index < self.detail_tabs.count():
            self.detail_tabs.setCurrentIndex(detail_tab_index)
        self._refresh_query_limit_hint()
        self._refresh_context_selection_summary()
        self._refresh_page_sort_button()
        self._apply_search_controls_collapsed()
        self._refresh_query_scope_label()

    def _selected_markdown_vaults_from_config(self, config) -> tuple[str, ...]:
        vault_path = normalize_vault_path(getattr(config, 'vault_path', ''))
        ordered: list[str] = []
        seen: set[str] = set()
        raw_values = list(getattr(config, 'md_selected_vault_paths', ()) or ()) or ([vault_path] if vault_path else [])
        for raw_value in raw_values:
            normalized = normalize_vault_path(raw_value)
            lowered = normalized.lower()
            if not normalized or lowered in seen:
                continue
            seen.add(lowered)
            ordered.append(normalized)
        return tuple(ordered)

    def _refresh_query_scope_label(self, config=None) -> None:
        config_value = config or self._config
        selected_vaults = self._selected_markdown_vaults_from_config(config_value)
        current_vault = normalize_vault_path(getattr(config_value, 'vault_path', ''))
        if len(selected_vaults) <= 1:
            self.query_scope_label.clear()
            self.query_scope_label.setVisible(False)
            return
        self.query_scope_label.setText(
            self._tr(
                'query_scope_summary',
                current=(Path(current_vault).name or current_vault or self._tr('none_value')),
                count=len(selected_vaults),
            )
        )
        self.query_scope_label.setVisible(True)

    def search_controls_collapsed(self) -> bool:
        return bool(self._search_controls_collapsed)

    def _toggle_search_controls_collapsed(self) -> None:
        if not self._search_controls_collapsed:
            current_sizes = list(self.query_splitter.sizes())
            if len(current_sizes) == 2 and min(current_sizes) > 0:
                self._expanded_query_splitter_sizes = current_sizes
        self._search_controls_collapsed = not self._search_controls_collapsed
        self._apply_search_controls_collapsed()

    def _collapsed_search_card_height(self) -> int:
        margins = self.search_card.layout().contentsMargins()
        vertical_padding = margins.top() + margins.bottom()
        row_height = max(self.query_row_host.sizeHint().height(), self.query_edit.sizeHint().height(), self.search_controls_toggle_button.sizeHint().height())
        return row_height + vertical_padding + 8

    def _apply_query_splitter_compaction(self, *, collapsed: bool) -> None:
        if not self.isVisible():
            return
        if collapsed:
            collapsed_height = self._collapsed_search_card_height()
            current_sizes = list(self.query_splitter.sizes())
            total_height = sum(current_sizes) if current_sizes else max(self.query_splitter.height(), sum(self.default_query_splitter_sizes()))
            bottom_height = max(total_height - collapsed_height, 180)
            self.query_splitter.setSizes([collapsed_height, bottom_height])
            return
        restore_sizes = self._expanded_query_splitter_sizes
        if restore_sizes and len(restore_sizes) == 2 and min(restore_sizes) > 0:
            self.query_splitter.setSizes(restore_sizes)
            return
        current_sizes = list(self.query_splitter.sizes())
        if self._splitter_state_applied and len(current_sizes) == 2 and min(current_sizes) > 0:
            return
        self.query_splitter.setSizes(self.default_query_splitter_sizes())

    def _apply_search_controls_collapsed(self) -> None:
        collapsed = bool(self._search_controls_collapsed)
        for widget in (
            self.search_header_widget,
            self.query_status_banner,
            self.query_hint_label,
            self.search_details_widget,
        ):
            widget.setVisible(not collapsed)
        self.search_controls_toggle_button.setText(self._tr('search_controls_expand') if collapsed else self._tr('search_controls_collapse'))
        if collapsed:
            collapsed_margin = scaled(self._theme, 10, minimum=8)
            self.search_card.layout().setContentsMargins(collapsed_margin, collapsed_margin, collapsed_margin, collapsed_margin)
            self.search_card.layout().setSpacing(4)
            self.search_card.setMinimumHeight(self._collapsed_search_card_height())
            self.search_card.setMaximumHeight(self._collapsed_search_card_height())
        else:
            if self._search_card_default_margins is not None:
                self.search_card.layout().setContentsMargins(*self._search_card_default_margins)
            self.search_card.layout().setSpacing(self._search_card_default_spacing)
            self.search_card.setMinimumHeight(0)
            self.search_card.setMaximumHeight(16777215)
        self.search_card.updateGeometry()
        QtCore.QTimer.singleShot(0, lambda collapsed=collapsed: self._apply_query_splitter_compaction(collapsed=collapsed))
        self.statusMessageChanged.emit(self.query_status_title.text() or self._tr('status_ready'))
        self.resultSummaryChanged.emit(self._tr('query_hits', count=self.results_model.total_count()) if self.results_model.total_count() else self._tr('result_empty'))

    def set_external_block_state(self, *, blocked: bool, title: str = '', detail: str = '') -> None:
        self._external_blocked = blocked
        self._external_block_title = title
        self._external_block_detail = detail
        self._refresh_query_status_banner()

    def rerun_current_query(self) -> None:
        if self._busy or self._external_blocked or not self.query_edit.text().strip():
            return
        self.search()

    def show_log_tab(self) -> None:
        self.detail_tabs.setCurrentIndex(2)

    def append_external_log(self, message: str) -> None:
        self._append_log(message, persist=False)

    def search(self) -> None:
        self._start_query(copy_result=False)

    def search_and_copy(self) -> None:
        self._start_query(copy_result=True)

    def copy_current_context(self) -> None:
        self._rebuild_context_view()
        if not self._current_context.strip():
            QtWidgets.QMessageBox.information(self, self._tr('copy_empty_title'), self._tr('copy_empty_body'))
            return
        copy_text(self._current_context)
        self.statusMessageChanged.emit(self._tr('status_context_copied'))
        self._append_log(self._tr('log_context_copied'))

    def toggle_all_context_rows(self) -> None:
        self.results_model.toggle_all_selection()

    def toggle_page_sort(self) -> None:
        chunk_id = self._selected_chunk_id()
        self.results_model.toggle_page_sort()
        self._restore_selection_by_chunk_id(chunk_id)
        self._refresh_page_sort_button()

    def _sort_by_column(self, column: int) -> None:
        chunk_id = self._selected_chunk_id()
        self.results_model.sort_by_column(column)
        self._restore_selection_by_chunk_id(chunk_id)
        self._refresh_page_sort_button()

    def _handle_table_clicked(self, index: QtCore.QModelIndex) -> None:
        if index.column() == QueryResultsTableModel.COLUMN_INCLUDE:
            current = self.results_model.data(index, QtCore.Qt.ItemDataRole.CheckStateRole)
            next_state = QtCore.Qt.CheckState.Unchecked if current == QtCore.Qt.CheckState.Checked else QtCore.Qt.CheckState.Checked
            self.results_model.setData(index, next_state, QtCore.Qt.ItemDataRole.CheckStateRole)

    def _on_table_selection_changed(self, *_args) -> None:
        row = self._selected_row()
        if row is None:
            return
        self._show_preview_for_row(row)

    def _on_context_selection_changed(self) -> None:
        self._refresh_context_selection_summary()
        self._rebuild_context_view()

    def _on_results_ordering_changed(self) -> None:
        header = self.table_view.horizontalHeader()
        if self.results_model.page_sort_active or self.results_model.sort_column is None:
            header.setSortIndicatorShown(False)
        else:
            header.setSortIndicatorShown(True)
            order = QtCore.Qt.SortOrder.DescendingOrder if self.results_model.sort_reverse else QtCore.Qt.SortOrder.AscendingOrder
            header.setSortIndicator(self.results_model.sort_column, order)
        self._refresh_page_sort_button()

    def _selected_row(self) -> int | None:
        model_index = self.table_view.currentIndex()
        if not model_index.isValid():
            return None
        return model_index.row()

    def _selected_chunk_id(self) -> str | None:
        row = self._selected_row()
        hit = self.results_model.hit_at(row) if row is not None else None
        return getattr(hit, 'chunk_id', None) if hit is not None else None

    def _restore_selection_by_chunk_id(self, chunk_id: str | None) -> None:
        row = self.results_model.row_for_chunk_id(chunk_id)
        if row is None:
            row = 0 if self.results_model.rowCount() > 0 else None
        if row is None:
            self.preview_panel.set_text(self._tr('preview_empty'))
            return
        model_index = self.results_model.index(row, QueryResultsTableModel.COLUMN_TITLE)
        self.table_view.setCurrentIndex(model_index)
        self.table_view.selectRow(row)
        self._show_preview_for_row(row)

    def _show_preview_for_row(self, row: int) -> None:
        hit = self.results_model.hit_at(row)
        if hit is None:
            self.preview_panel.set_text(self._tr('preview_empty'))
            return
        none_value = self._tr('none_value')
        preview_text = (
            f"{self._tr('col_page')}：{getattr(hit, 'title', none_value)}\n"
            f"{self._tr('col_anchor')}：{getattr(hit, 'anchor', none_value)}\n"
            f"{self._tr('col_source')}：{getattr(hit, 'source_path', none_value)}\n"
            f"{self._tr('col_score')}：{float(getattr(hit, 'score', 0.0) or 0.0):.1f}/100\n"
            f"{self._tr('col_reason')}：{getattr(hit, 'reason', '') or self._tr('reason_fallback')}\n\n"
            f"{self._tr('preview_excerpt_label')}\n{getattr(hit, 'preview_text', '') or none_value}\n\n"
            f"{self._tr('preview_full_label')}\n{getattr(hit, 'display_text', '') or getattr(hit, 'rendered_text', '') or none_value}"
        )
        self.preview_panel.set_text(preview_text)

    def _selected_hits(self) -> list[object]:
        return self.results_model.selected_hits()

    def _rebuild_context_view(self) -> None:
        if not self._current_query_text and not self.results_model.total_count():
            self._current_context = ''
            self.context_panel.set_text(self._tr('context_empty'))
            self._refresh_context_jump_controls()
            return
        self._current_context = OmniClipService.compose_context_pack_text(
            self._current_query_text,
            self._selected_hits(),
            export_mode=getattr(self._config, 'context_export_mode', 'standard'),
            language=self._language_code,
        )
        self.context_panel.set_text(self._current_context or self._tr('context_empty'))
        self._refresh_context_jump_controls()

    def _refresh_context_jump_controls(self) -> None:
        self._context_sections = collect_context_sections(self._current_context, translate=self._tr)
        total_notes = len(self._context_sections)
        total_fragments = sum(int(section.get('fragments') or 0) for section in self._context_sections)
        self.context_jump_summary_label.setText(
            self._tr('context_jump_summary', notes=total_notes, fragments=total_fragments)
            if total_notes
            else self._tr('context_jump_summary_empty')
        )
        current_text = self.context_jump_combo.currentText().strip()
        values = [str(section.get('display') or '') for section in self._context_sections]
        self.context_jump_combo.blockSignals(True)
        self.context_jump_combo.clear()
        self.context_jump_combo.addItems(values)
        if current_text in values:
            self.context_jump_combo.setCurrentText(current_text)
        elif values:
            self.context_jump_combo.setCurrentIndex(0)
        self.context_jump_combo.blockSignals(False)

    def _jump_to_context_section(self, display_text: str) -> None:
        selected = str(display_text or '').strip()
        if not selected:
            return
        for section in self._context_sections:
            if str(section.get('display')) != selected:
                continue
            self.context_panel.scroll_to_line(int(section.get('line') or 1))
            return

    def _refresh_context_selection_summary(self) -> None:
        total = self.results_model.total_count()
        selected = self.results_model.selected_count()
        if total <= 0:
            self.context_selection_label.setText(self._tr('context_selection_empty'))
            self.context_toggle_button.setText(self._tr('context_select_all'))
            self.context_toggle_button.setEnabled(False)
        else:
            self.context_selection_label.setText(self._tr('context_selection_summary', selected=selected, total=total))
            self.context_toggle_button.setText(self._tr('context_clear_all') if self.results_model.are_all_selected() else self._tr('context_select_all'))
            self.context_toggle_button.setEnabled(True)
        self._refresh_query_status_banner()

    def _refresh_page_sort_button(self) -> None:
        self.page_sort_button.setText(self._tr('page_sort_restore_button') if self.results_model.page_sort_active else self._tr('page_sort_button'))
        self.page_sort_button.setEnabled(self.results_model.total_count() > 0)

    def _deserialize_page_blocklist_rules(self) -> list[tuple[bool, str]]:
        parsed: list[tuple[bool, str]] = []
        for raw_line in str(getattr(self._config, 'page_blocklist_rules', '') or '').splitlines():
            line = raw_line.strip()
            if not line or line.startswith('#'):
                continue
            enabled = True
            pattern = line
            if '\t' in line:
                flag, rest = line.split('\t', 1)
                if flag in {'0', '1'}:
                    enabled = flag == '1'
                    pattern = rest.strip()
            if pattern:
                parsed.append((enabled, pattern))
        return parsed

    def _refresh_page_blocklist_summary(self) -> None:
        enabled, total = count_enabled_page_filter_rules(getattr(self._config, 'page_blocklist_rules', '') or '')
        self.page_blocklist_summary_label.setText(self._tr('page_blocklist_summary', enabled=enabled, total=total))

    def _refresh_query_limit_hint(self) -> None:
        self.query_limit_hint_label.setText(
            render_query_limit_hint(
                self._query_limit_recommendation,
                current_limit=self.limit_edit.text().strip(),
                translate=self._tr,
            )
        )

    def _set_query_status(self, *, mode: str, title: str, detail: str) -> None:
        for widget in (self.query_status_banner, self.query_status_title, self.query_status_detail):
            widget.setProperty('mode', mode)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()
        self.query_status_title.setText(title)
        self.query_status_detail.setText(detail)

    def _refresh_query_status_banner(self) -> None:
        self.query_status_banner.setVisible(not self._search_controls_collapsed)
        if self._busy:
            payload = self._query_progress_payload or {}
            percent = float(payload.get('overall_percent') or 0.0)
            self._set_query_status(
                mode='running',
                title=self._tr('query_status_running_title', percent=percent),
                detail=query_progress_detail(payload, translate=self._tr),
            )
            return
        if self._external_blocked:
            self._set_query_status(
                mode='blocked',
                title=self._external_block_title or self._tr('query_status_blocked_title'),
                detail=self._external_block_detail or self._tr('query_status_blocked_detail'),
            )
            return
        if self._query_last_completed_at > 0:
            title_key = 'query_status_done_title_copied' if self._query_last_copied else 'query_status_done_title'
            completed_at = time.strftime('%H:%M', time.localtime(self._query_last_completed_at))
            self._set_query_status(
                mode='done',
                title=self._tr(title_key, time=completed_at),
                detail=self._tr('query_status_done_detail', count=self._query_last_result_count),
            )
            return
        self._set_query_status(
            mode='idle',
            title=self._tr('query_status_idle_title'),
            detail=self._tr('query_status_idle_detail'),
        )

    def _refresh_query_runtime_hint(self) -> None:
        warnings = list(self._query_runtime_warnings)
        if not warnings:
            self.query_runtime_hint_label.clear()
            self.query_runtime_hint_label.setVisible(False)
            return
        lines = []
        for item in warnings:
            message = html.escape(self._tr(f'query_runtime_warning_{item}')).replace('\n', '<br/>')
            if item == 'markdown_vector_runtime_unavailable':
                message = f"{message} <a href=\"runtime-repair\">{html.escape(self._tr('query_runtime_repair_link'))}</a>"
            lines.append(message)
        self.query_runtime_hint_label.setText('<br/>'.join(lines))
        self.query_runtime_hint_label.setVisible(not self._search_controls_collapsed)

    def _handle_runtime_hint_link(self, href: str) -> None:
        if str(href).strip().lower() == 'runtime-repair':
            self.runtimeRepairRequested.emit()

    def _backend_enabled(self, config) -> bool:
        return (config.vector_backend or 'disabled').strip().lower() not in {'', 'disabled', 'none', 'off'}

    def _selected_query_families(self) -> tuple[str, ...]:
        families: list[str] = []
        if self.source_markdown_check.isChecked():
            families.append('markdown')
        if self.source_pdf_check.isChecked():
            families.append('pdf')
        if self.source_tika_check.isChecked():
            families.append('tika')
        return tuple(families)

    def _validate_query_request(self, *, copy_result: bool):
        if self._busy:
            QtWidgets.QMessageBox.information(self, self._tr('busy_title'), self._tr('busy_body'))
            return None
        if self._external_blocked:
            QtWidgets.QMessageBox.information(self, self._tr('cannot_start_title'), self._external_block_detail or self._tr('stop_watch_first_body'))
            return None
        query_text = self.query_edit.text().strip()
        if not query_text:
            QtWidgets.QMessageBox.information(self, self._tr('empty_query_title'), self._tr('empty_query_body'))
            return None
        allowed_families = self._selected_query_families()
        if not allowed_families:
            QtWidgets.QMessageBox.information(self, self._tr('query_source_none_title'), self._tr('query_source_none_body'))
            return None
        try:
            score_threshold = float(self.threshold_edit.text().strip() or '0')
            limit = int(self.limit_edit.text().strip() or '15')
        except ValueError:
            QtWidgets.QMessageBox.critical(self, self._tr('cannot_start_title'), self._tr('number_invalid'))
            return None
        if limit <= 0 or score_threshold < 0:
            QtWidgets.QMessageBox.critical(self, self._tr('cannot_start_title'), self._tr('number_invalid'))
            return None
        config = self._config
        paths = self._paths
        if self._runtime_snapshot_provider is not None:
            try:
                provided_config, provided_paths = self._runtime_snapshot_provider()
                if provided_config is not None and provided_paths is not None:
                    config = provided_config
                    paths = provided_paths
                    self._config = config
                    self._paths = paths
            except Exception:
                LOGGER.exception('Failed to collect the latest runtime snapshot for query validation; falling back to cached config.')
        vault_path = normalize_vault_path(getattr(config, 'vault_path', ''))
        if not vault_path:
            QtWidgets.QMessageBox.information(self, self._tr('not_ready_title'), self._tr('choose_vault_first'))
            return None
        selected_vaults = self._selected_markdown_vaults_from_config(config)
        if 'markdown' in allowed_families and not selected_vaults:
            QtWidgets.QMessageBox.information(self, self._tr('not_ready_title'), self._tr('choose_vault_first'))
            return None
        config = replace(
            config,
            vault_path=vault_path,
            query_limit=limit,
            query_score_threshold=score_threshold,
            md_selected_vault_paths=list(selected_vaults),
        )
        paths = ensure_data_paths(getattr(config, 'data_root', None), vault_path)
        if self._backend_enabled(config) and not is_local_model_ready(config, paths):
            model_dir = get_local_model_dir(config, paths)
            QtWidgets.QMessageBox.warning(
                self,
                self._tr('cannot_start_title'),
                f"{self._tr('model_missing')}\n\n{model_dir}\n\n{self._tr('workspace_subtitle')}",
            )
            return None
        return query_text, score_threshold, config, paths, copy_result, allowed_families

    def _start_query(self, *, copy_result: bool) -> None:
        prepared = self._validate_query_request(copy_result=copy_result)
        if prepared is None:
            return
        query_text, score_threshold, config, paths, copy_result, allowed_families = prepared
        selected_vaults = self._selected_markdown_vaults_from_config(config)
        self._busy = True
        self._query_progress_payload = {'overall_percent': 0.0, 'stage_status': 'prepare'}
        self.search_button.setEnabled(False)
        self.search_copy_button.setEnabled(False)
        self.copy_context_button.setEnabled(False)
        self.page_blocklist_button.setEnabled(False)
        self.sensitive_filter_button.setEnabled(False)
        self.statusMessageChanged.emit(f"{self._tr('search_button')}…")
        self._append_log(f"{self._tr('search_button')}：{query_text}")
        self._refresh_query_scope_label(config)
        self._refresh_query_status_banner()
        if len(selected_vaults) > 1:
            worker = MultiVaultQueryWorker(
                config=replace(config, md_selected_vault_paths=list(selected_vaults)),
                query_text=query_text,
                copy_result=copy_result,
                score_threshold=score_threshold,
                allowed_families=allowed_families,
            )
        else:
            worker = QueryWorker(
                config=config,
                paths=paths,
                query_text=query_text,
                copy_result=copy_result,
                score_threshold=score_threshold,
                allowed_families=allowed_families,
            )
        worker.progress.connect(self._on_query_progress)
        worker.succeeded.connect(self._on_query_success)
        worker.failed.connect(self._on_query_failure)
        worker.finished.connect(self._on_query_finished)
        self._worker = worker
        worker.start()

    def _on_query_progress(self, payload: object) -> None:
        if isinstance(payload, dict):
            self._query_progress_payload = payload
            self._refresh_query_status_banner()

    def _on_query_success(self, payload: object) -> None:
        if not isinstance(payload, QueryTaskResult):
            return
        result = payload.result
        hits = list(getattr(result, 'hits', []))
        self._current_query_text = payload.query_text
        self._query_last_completed_at = time.time()
        self._query_last_result_count = len(hits)
        self._query_last_copied = payload.copied
        insights = getattr(result, 'insights', None)
        self._query_runtime_warnings = tuple(getattr(insights, 'runtime_warnings', ()) or ())
        self._refresh_query_runtime_hint()
        self._query_limit_recommendation = asdict(insights.recommendation) if getattr(insights, 'recommendation', None) is not None else None
        self._refresh_query_limit_hint()
        self.results_model.set_results(hits)
        if hits:
            self._restore_selection_by_chunk_id(getattr(hits[0], 'chunk_id', None))
        else:
            self.preview_panel.set_text(self._tr('no_results'))
        self._rebuild_context_view()
        self.resultSummaryChanged.emit(self._tr('query_hits', count=len(hits)))
        self.statusMessageChanged.emit(self._tr('status_query_copied') if payload.copied else self._tr('status_query_done'))
        self._append_log(self._tr('log_query_done', query=payload.query_text, count=len(hits)))
        persist_trace = bool(getattr(self._config, 'query_trace_logging_enabled', False))
        for trace_line in tuple(getattr(insights, 'trace_lines', ()) or ()):
            self._append_log(trace_line, persist=persist_trace)
        for warning in self._query_runtime_warnings:
            self._append_log(self._tr(f'query_runtime_warning_{warning}'))
        reranker = getattr(insights, 'reranker', None)
        if reranker is not None and getattr(reranker, 'enabled', False):
            if getattr(reranker, 'applied', False):
                self._append_log(self._tr('log_reranker_applied', device=reranker.resolved_device, count=reranker.reranked_count))
            else:
                self._append_log(self._tr('log_reranker_skipped', reason=reranker.skipped_reason or self._tr('none_value')))
        self.detail_tabs.setCurrentIndex(1)
        self._refresh_query_status_banner()

    def _on_query_failure(self, message: str, traceback_text: str) -> None:
        self._query_runtime_warnings = ()
        self._refresh_query_runtime_hint()
        error_body = message.strip() or traceback_text.strip() or self._tr('cannot_start_title')
        self.statusMessageChanged.emit(f"{self._tr('search_button')}：{error_body}")
        if traceback_text.strip():
            self._append_log(traceback_text.strip())
        QtWidgets.QMessageBox.critical(self, self._tr('search_button'), error_body)
        self.show_log_tab()

    def _on_query_finished(self) -> None:
        self._busy = False
        self.search_button.setEnabled(True)
        self.search_copy_button.setEnabled(True)
        self.copy_context_button.setEnabled(True)
        self.page_blocklist_button.setEnabled(True)
        self.sensitive_filter_button.setEnabled(True)
        self._worker = None
        self._refresh_query_status_banner()

    def _append_log(self, message: str, *, persist: bool = True) -> None:
        text_value = str(message or '').strip()
        if not text_value:
            return
        if persist:
            LOGGER.info('%s', text_value)
        self._log_lines.append(text_value)
        if len(self._log_lines) == 1:
            self.log_panel.set_text(text_value)
        else:
            self.log_panel.append_text(text_value)

    def _refresh_results_columns(self) -> None:
        widths = [
            max(scaled(self._theme, 72, minimum=60), 60),
            max(scaled(self._theme, 108, minimum=90), 90),
            max(scaled(self._theme, 230, minimum=180), 180),
            max(scaled(self._theme, 220, minimum=180), 180),
            max(scaled(self._theme, 320, minimum=240), 240),
            max(scaled(self._theme, 100, minimum=90), 90),
        ]
        for column, width in enumerate(widths):
            self.table_view.setColumnWidth(column, width)
