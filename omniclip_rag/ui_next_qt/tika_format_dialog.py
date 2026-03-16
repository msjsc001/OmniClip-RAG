from __future__ import annotations

from dataclasses import replace

from PySide6 import QtCore, QtGui, QtWidgets

from ..extensions.models import TikaFormatSelection, TikaFormatSupportTier
from ..ui_i18n import text


class TikaFormatDialog(QtWidgets.QDialog):
    """Searchable and tiered Tika format picker.

    Stage 1 only manages user selection state. Runtime validation and parser
    execution land in later phases.
    """

    def __init__(self, *, selections: list[TikaFormatSelection], language_code: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._language_code = language_code
        self._selections = [replace(item) for item in selections if item.format_id.lower() != 'pdf']
        self.setWindowTitle(self._tr('extensions_tika_format_dialog_title'))
        self.resize(760, 520)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self.search_edit = QtWidgets.QLineEdit(self)
        self.search_edit.setPlaceholderText(self._tr('extensions_tika_format_search_placeholder'))
        self.search_edit.textChanged.connect(self._apply_search)
        root.addWidget(self.search_edit)

        self.tree = QtWidgets.QTreeWidget(self)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        root.addWidget(self.tree, 1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._populate_tree()

    def _tr(self, key: str, **kwargs) -> str:
        return text(self._language_code, key, **kwargs)

    def _populate_tree(self) -> None:
        self.tree.clear()
        groups = [
            (TikaFormatSupportTier.RECOMMENDED, self._tr('extensions_tika_group_recommended')),
            (TikaFormatSupportTier.UNKNOWN, self._tr('extensions_tika_group_unknown')),
            (TikaFormatSupportTier.POOR, self._tr('extensions_tika_group_poor')),
        ]
        grouped: dict[TikaFormatSupportTier, QtWidgets.QTreeWidgetItem] = {}
        for tier, label in groups:
            item = QtWidgets.QTreeWidgetItem([label])
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            grouped[tier] = item
            self.tree.addTopLevelItem(item)
        for selection in self._selections:
            parent = grouped[selection.tier]
            text_value = selection.display_name
            if selection.tier == TikaFormatSupportTier.UNKNOWN:
                text_value = f"{text_value} {self._tr('extensions_tika_item_unknown_suffix')}"
            elif selection.tier == TikaFormatSupportTier.POOR:
                text_value = f"{text_value} {self._tr('extensions_tika_item_poor_suffix')}"
            item = QtWidgets.QTreeWidgetItem([text_value])
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, selection.format_id)
            if selection.tier == TikaFormatSupportTier.POOR:
                item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
                item.setForeground(0, QtGui.QBrush(QtGui.QColor('#8b8f99')))
            else:
                item.setFlags(
                    QtCore.Qt.ItemFlag.ItemIsEnabled
                    | QtCore.Qt.ItemFlag.ItemIsSelectable
                    | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(0, QtCore.Qt.CheckState.Checked if selection.enabled else QtCore.Qt.CheckState.Unchecked)
            parent.addChild(item)
        self.tree.expandAll()
        self._apply_search(self.search_edit.text())

    def _apply_search(self, raw_query: str) -> None:
        query = str(raw_query or '').strip().lower()
        for parent_index in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(parent_index)
            visible_children = 0
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                haystack = f"{child.text(0)} {child.data(0, QtCore.Qt.ItemDataRole.UserRole) or ''}".lower()
                hidden = bool(query) and query not in haystack
                child.setHidden(hidden)
                if not hidden:
                    visible_children += 1
            parent.setHidden(visible_children == 0)

    def selected_formats(self) -> list[TikaFormatSelection]:
        selection_by_id = {item.format_id: replace(item) for item in self._selections}
        for parent_index in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(parent_index)
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                format_id = str(child.data(0, QtCore.Qt.ItemDataRole.UserRole) or '').strip().lower()
                if not format_id:
                    continue
                selection = selection_by_id.get(format_id)
                if selection is None:
                    continue
                if selection.tier == TikaFormatSupportTier.POOR:
                    selection.enabled = False
                else:
                    selection.enabled = child.checkState(0) == QtCore.Qt.CheckState.Checked
                selection_by_id[format_id] = selection
        return [selection_by_id[item.format_id] for item in self._selections if item.format_id in selection_by_id]

    def accept(self) -> None:
        self._selections = self.selected_formats()
        super().accept()
