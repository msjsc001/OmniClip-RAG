from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ..ui_i18n import text, tooltip
from ..ui_shared import DEFAULT_PAGE_FILTER_RULES, merge_page_filter_defaults
from .filter_models import PageBlocklistTableModel
from .theme import ThemeState, scaled


class PageBlocklistDialog(QtWidgets.QDialog):
    rulesSaved = QtCore.Signal(str)

    def __init__(self, *, raw_rules: str, language_code: str, theme: ThemeState, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._language_code = language_code
        self._theme = theme
        self.setWindowTitle(self._tr('page_blocklist_window_title'))
        self.resize(920, 620)
        self.setMinimumSize(780, 480)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QtWidgets.QLabel(self._tr('page_blocklist_window_title'), self)
        title.setProperty('role', 'cardTitle')
        root.addWidget(title)

        body = QtWidgets.QLabel(self._tr('page_blocklist_window_body'), self)
        body.setProperty('role', 'subtitle')
        body.setWordWrap(True)
        root.addWidget(body)

        self.model = PageBlocklistTableModel(self._tr, self)
        self.model.set_rules_from_serialized(merge_page_filter_defaults(raw_rules))

        self.view = QtWidgets.QTableView(self)
        self.view.setModel(self.model)
        self.view.verticalHeader().setVisible(False)
        self.view.horizontalHeader().setStretchLastSection(False)
        self.view.horizontalHeader().setSectionResizeMode(PageBlocklistTableModel.COLUMN_ENABLED, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.view.horizontalHeader().setSectionResizeMode(PageBlocklistTableModel.COLUMN_PATTERN, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.view.horizontalHeader().setSectionResizeMode(PageBlocklistTableModel.COLUMN_ACTION, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked
            | QtWidgets.QAbstractItemView.EditTrigger.EditKeyPressed
            | QtWidgets.QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.view.clicked.connect(self._handle_view_clicked)
        self.view.setAlternatingRowColors(False)
        self.view.setMinimumHeight(max(scaled(theme, 320, minimum=260), 260))
        root.addWidget(self.view, 1)

        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(8)
        root.addLayout(actions)

        add_button = QtWidgets.QPushButton(self._tr('page_blocklist_add'), self)
        add_button.clicked.connect(self._add_rule)
        add_button.setToolTip(self._tip('page_blocklist_add'))
        actions.addWidget(add_button)

        reset_button = QtWidgets.QPushButton(self._tr('page_blocklist_reset_defaults'), self)
        reset_button.clicked.connect(self._reset_defaults)
        reset_button.setToolTip(self._tip('page_blocklist_reset_defaults'))
        actions.addWidget(reset_button)

        remove_button = QtWidgets.QPushButton(self._tr('page_blocklist_remove'), self)
        remove_button.clicked.connect(self._remove_current_rule)
        remove_button.setToolTip(self._tip('page_blocklist_remove'))
        actions.addWidget(remove_button)

        actions.addStretch(1)

        save_button = QtWidgets.QPushButton(self._tr('save_config'), self)
        save_button.setProperty('variant', 'primary')
        save_button.clicked.connect(self._save)
        save_button.setToolTip(self._tip('save_filters'))
        actions.addWidget(save_button)

    def _tr(self, key: str, **kwargs) -> str:
        return text(self._language_code, key, **kwargs)

    def _tip(self, key: str, **kwargs) -> str:
        return tooltip(self._language_code, key, **kwargs)

    def _handle_view_clicked(self, index: QtCore.QModelIndex) -> None:
        if index.column() != PageBlocklistTableModel.COLUMN_ACTION:
            return
        self.model.remove_rule(index.row())

    def _add_rule(self) -> None:
        row = self.model.add_rule()
        index = self.model.index(row, PageBlocklistTableModel.COLUMN_PATTERN)
        self.view.setCurrentIndex(index)
        self.view.edit(index)

    def _remove_current_rule(self) -> None:
        index = self.view.currentIndex()
        if not index.isValid():
            return
        self.model.remove_rule(index.row())

    def _reset_defaults(self) -> None:
        self.model.replace_rules(list(DEFAULT_PAGE_FILTER_RULES))

    def _save(self) -> None:
        serialized = self.model.serialized_rules()
        self.rulesSaved.emit(serialized)
        self.accept()


class SensitiveFilterDialog(QtWidgets.QDialog):
    rulesSaved = QtCore.Signal(bool, bool, str)

    def __init__(
        self,
        *,
        core_enabled: bool,
        extended_enabled: bool,
        custom_rules: str,
        language_code: str,
        theme: ThemeState,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._language_code = language_code
        self._theme = theme
        self.setWindowTitle(self._tr('sensitive_filter_window_title'))
        self.resize(840, 520)
        self.setMinimumSize(720, 440)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QtWidgets.QLabel(self._tr('sensitive_filter_window_title'), self)
        title.setProperty('role', 'cardTitle')
        root.addWidget(title)

        body = QtWidgets.QLabel(self._tr('sensitive_filter_window_body'), self)
        body.setProperty('role', 'subtitle')
        body.setWordWrap(True)
        root.addWidget(body)

        card = QtWidgets.QFrame(self)
        card.setProperty('card', True)
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(10)
        root.addWidget(card, 1)

        self.core_check = QtWidgets.QCheckBox(self._tr('rag_filter_core_label'), card)
        self.core_check.setChecked(core_enabled)
        self.core_check.setToolTip(self._tip('rag_filter_core'))
        card_layout.addWidget(self.core_check)

        self.extended_check = QtWidgets.QCheckBox(self._tr('rag_filter_extended_label'), card)
        self.extended_check.setChecked(extended_enabled)
        self.extended_check.setToolTip(self._tip('rag_filter_extended'))
        card_layout.addWidget(self.extended_check)

        custom_label = QtWidgets.QLabel(self._tr('rag_filter_custom_label'), card)
        custom_label.setProperty('role', 'muted')
        custom_label.setWordWrap(True)
        card_layout.addWidget(custom_label)

        self.custom_text = QtWidgets.QPlainTextEdit(card)
        self.custom_text.setPlainText(custom_rules)
        self.custom_text.setToolTip(self._tip('rag_filter_custom_rules'))
        self.custom_text.setMinimumHeight(max(scaled(theme, 200, minimum=160), 160))
        card_layout.addWidget(self.custom_text, 1)

        actions = QtWidgets.QHBoxLayout()
        root.addLayout(actions)
        actions.addStretch(1)

        save_button = QtWidgets.QPushButton(self._tr('save_config'), self)
        save_button.setProperty('variant', 'primary')
        save_button.setToolTip(self._tip('save_filters'))
        save_button.clicked.connect(self._save)
        actions.addWidget(save_button)

    def _tr(self, key: str, **kwargs) -> str:
        return text(self._language_code, key, **kwargs)

    def _tip(self, key: str, **kwargs) -> str:
        return tooltip(self._language_code, key, **kwargs)

    def _save(self) -> None:
        self.rulesSaved.emit(
            self.core_check.isChecked(),
            self.extended_check.isChecked(),
            self.custom_text.toPlainText().strip(),
        )
        self.accept()
