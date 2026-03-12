from __future__ import annotations

from PySide6 import QtCore

from ..ui_shared import deserialize_page_filter_rules


class PageBlocklistTableModel(QtCore.QAbstractTableModel):
    COLUMN_ENABLED = 0
    COLUMN_PATTERN = 1
    COLUMN_ACTION = 2

    def __init__(self, tr, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._tr = tr
        self._rows: list[dict[str, object]] = []

    def rowCount(self, parent: QtCore.QModelIndex | QtCore.QPersistentModelIndex = QtCore.QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QtCore.QModelIndex | QtCore.QPersistentModelIndex = QtCore.QModelIndex()) -> int:
        return 0 if parent.isValid() else 3

    def headerData(self, section: int, orientation: QtCore.Qt.Orientation, role: int = QtCore.Qt.ItemDataRole.DisplayRole):
        if orientation != QtCore.Qt.Orientation.Horizontal or role != QtCore.Qt.ItemDataRole.DisplayRole:
            return None
        headers = {
            self.COLUMN_ENABLED: self._tr('page_blocklist_enabled'),
            self.COLUMN_PATTERN: self._tr('page_blocklist_regex'),
            self.COLUMN_ACTION: self._tr('page_blocklist_remove'),
        }
        return headers.get(section)

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        column = index.column()
        if column == self.COLUMN_ENABLED:
            if role == QtCore.Qt.ItemDataRole.CheckStateRole:
                return QtCore.Qt.CheckState.Checked if bool(row['enabled']) else QtCore.Qt.CheckState.Unchecked
            if role == QtCore.Qt.ItemDataRole.DisplayRole:
                return ''
            return None
        if column == self.COLUMN_PATTERN:
            if role in {QtCore.Qt.ItemDataRole.DisplayRole, QtCore.Qt.ItemDataRole.EditRole}:
                return str(row['pattern'])
            return None
        if column == self.COLUMN_ACTION and role == QtCore.Qt.ItemDataRole.DisplayRole:
            return self._tr('page_blocklist_remove')
        return None

    def flags(self, index: QtCore.QModelIndex) -> QtCore.Qt.ItemFlag:
        if not index.isValid():
            return QtCore.Qt.ItemFlag.NoItemFlags
        base = QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
        if index.column() == self.COLUMN_ENABLED:
            return base | QtCore.Qt.ItemFlag.ItemIsUserCheckable
        if index.column() == self.COLUMN_PATTERN:
            return base | QtCore.Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self, index: QtCore.QModelIndex, value, role: int = QtCore.Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid() or index.row() >= len(self._rows):
            return False
        row = self._rows[index.row()]
        if index.column() == self.COLUMN_ENABLED and role == QtCore.Qt.ItemDataRole.CheckStateRole:
            row['enabled'] = value == QtCore.Qt.CheckState.Checked
            self.dataChanged.emit(index, index, [QtCore.Qt.ItemDataRole.CheckStateRole])
            return True
        if index.column() == self.COLUMN_PATTERN and role == QtCore.Qt.ItemDataRole.EditRole:
            row['pattern'] = str(value or '').strip()
            self.dataChanged.emit(index, index, [QtCore.Qt.ItemDataRole.DisplayRole, QtCore.Qt.ItemDataRole.EditRole])
            return True
        return False

    def set_rules_from_serialized(self, raw_rules: str) -> None:
        self.beginResetModel()
        self._rows = [
            {'enabled': bool(enabled), 'pattern': pattern}
            for enabled, pattern in deserialize_page_filter_rules(raw_rules)
        ]
        self.endResetModel()

    def add_rule(self, *, enabled: bool = True, pattern: str = '') -> int:
        row = len(self._rows)
        self.beginInsertRows(QtCore.QModelIndex(), row, row)
        self._rows.append({'enabled': bool(enabled), 'pattern': str(pattern or '').strip()})
        self.endInsertRows()
        return row

    def remove_rule(self, row: int) -> None:
        if row < 0 or row >= len(self._rows):
            return
        self.beginRemoveRows(QtCore.QModelIndex(), row, row)
        self._rows.pop(row)
        self.endRemoveRows()

    def replace_rules(self, rules: list[tuple[bool, str]]) -> None:
        self.beginResetModel()
        self._rows = [
            {'enabled': bool(enabled), 'pattern': str(pattern or '').strip()}
            for enabled, pattern in rules
            if str(pattern or '').strip()
        ]
        self.endResetModel()

    def serialized_rules(self) -> str:
        lines: list[str] = []
        for row in self._rows:
            pattern = str(row['pattern'] or '').strip()
            if not pattern:
                continue
            flag = '1' if bool(row['enabled']) else '0'
            lines.append(f'{flag}\t{pattern}')
        return '\n'.join(lines)
