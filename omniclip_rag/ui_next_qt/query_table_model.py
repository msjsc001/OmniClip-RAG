from __future__ import annotations

from PySide6 import QtCore

from ..ui_shared.query_helpers import sort_hits_by_page_average, sort_text_value


class QueryResultsTableModel(QtCore.QAbstractTableModel):
    selectionChanged = QtCore.Signal()
    orderingChanged = QtCore.Signal()

    COLUMN_INCLUDE = 0
    COLUMN_TITLE = 1
    COLUMN_REASON = 2
    COLUMN_ANCHOR = 3
    COLUMN_SCORE = 4

    def __init__(self, tr, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._tr = tr
        self._hits: list[object] = []
        self._selected_chunk_ids: set[str] = set()
        self._header_keys = ('col_include', 'col_page', 'col_reason', 'col_anchor', 'col_score')
        self._sort_column: int | None = None
        self._sort_reverse = False
        self._page_sort_active = False
        self._restore_order: list[str] = []
        self._restore_sort_column: int | None = None
        self._restore_sort_reverse = False

    @property
    def sort_column(self) -> int | None:
        return self._sort_column

    @property
    def sort_reverse(self) -> bool:
        return self._sort_reverse

    @property
    def page_sort_active(self) -> bool:
        return self._page_sort_active

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._hits)

    def columnCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._header_keys)

    def headerData(self, section: int, orientation: QtCore.Qt.Orientation, role: int = QtCore.Qt.ItemDataRole.DisplayRole):
        if orientation != QtCore.Qt.Orientation.Horizontal:
            return super().headerData(section, orientation, role)
        if role == QtCore.Qt.ItemDataRole.DisplayRole and 0 <= section < len(self._header_keys):
            return self._tr(self._header_keys[section])
        if role == QtCore.Qt.ItemDataRole.TextAlignmentRole and section in {self.COLUMN_INCLUDE, self.COLUMN_SCORE}:
            return int(QtCore.Qt.AlignmentFlag.AlignCenter)
        return super().headerData(section, orientation, role)

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._hits)):
            return None
        hit = self._hits[index.row()]
        column = index.column()
        if role == QtCore.Qt.ItemDataRole.CheckStateRole and column == self.COLUMN_INCLUDE:
            return QtCore.Qt.CheckState.Checked if getattr(hit, 'chunk_id', '') in self._selected_chunk_ids else QtCore.Qt.CheckState.Unchecked
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            if column == self.COLUMN_INCLUDE:
                return ''
            if column == self.COLUMN_TITLE:
                return getattr(hit, 'title', '')
            if column == self.COLUMN_REASON:
                return getattr(hit, 'reason', '') or self._tr('reason_fallback')
            if column == self.COLUMN_ANCHOR:
                return getattr(hit, 'anchor', '')
            if column == self.COLUMN_SCORE:
                return f"{float(getattr(hit, 'score', 0.0) or 0.0):.1f}"
        if role == QtCore.Qt.ItemDataRole.TextAlignmentRole and column in {self.COLUMN_INCLUDE, self.COLUMN_SCORE}:
            return int(QtCore.Qt.AlignmentFlag.AlignCenter)
        if role == QtCore.Qt.ItemDataRole.UserRole:
            return getattr(hit, 'chunk_id', '')
        return None

    def flags(self, index: QtCore.QModelIndex) -> QtCore.Qt.ItemFlag:
        if not index.isValid():
            return QtCore.Qt.ItemFlag.NoItemFlags
        flags = QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled
        if index.column() == self.COLUMN_INCLUDE:
            flags |= QtCore.Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(self, index: QtCore.QModelIndex, value, role: int = QtCore.Qt.ItemDataRole.EditRole) -> bool:
        if role != QtCore.Qt.ItemDataRole.CheckStateRole or index.column() != self.COLUMN_INCLUDE:
            return False
        row = index.row()
        if not (0 <= row < len(self._hits)):
            return False
        chunk_id = getattr(self._hits[row], 'chunk_id', '')
        if not chunk_id:
            return False
        if value == QtCore.Qt.CheckState.Checked:
            self._selected_chunk_ids.add(chunk_id)
        else:
            self._selected_chunk_ids.discard(chunk_id)
        self.dataChanged.emit(index, index, [QtCore.Qt.ItemDataRole.CheckStateRole])
        self.selectionChanged.emit()
        return True

    def refresh_headers(self) -> None:
        self.headerDataChanged.emit(QtCore.Qt.Orientation.Horizontal, 0, len(self._header_keys) - 1)

    def set_results(self, hits: list[object]) -> None:
        self.beginResetModel()
        self._hits = list(hits)
        self._selected_chunk_ids = {getattr(hit, 'chunk_id', '') for hit in self._hits if getattr(hit, 'chunk_id', '')}
        self._sort_column = None
        self._sort_reverse = False
        self._page_sort_active = False
        self._restore_order = []
        self._restore_sort_column = None
        self._restore_sort_reverse = False
        self.endResetModel()
        self.selectionChanged.emit()
        self.orderingChanged.emit()

    def clear(self) -> None:
        self.set_results([])

    def hits(self) -> list[object]:
        return list(self._hits)

    def hit_at(self, row: int):
        if 0 <= row < len(self._hits):
            return self._hits[row]
        return None

    def row_for_chunk_id(self, chunk_id: str | None) -> int | None:
        if not chunk_id:
            return None
        for index, hit in enumerate(self._hits):
            if getattr(hit, 'chunk_id', None) == chunk_id:
                return index
        return None

    def selected_hits(self) -> list[object]:
        return [hit for hit in self._hits if getattr(hit, 'chunk_id', '') in self._selected_chunk_ids]

    def set_selected_chunk_ids(self, chunk_ids: set[str] | list[str]) -> None:
        normalized = {str(item).strip() for item in chunk_ids if str(item).strip()}
        self._selected_chunk_ids = normalized
        if self._hits:
            top_left = self.index(0, self.COLUMN_INCLUDE)
            bottom_right = self.index(len(self._hits) - 1, self.COLUMN_INCLUDE)
            self.dataChanged.emit(top_left, bottom_right, [QtCore.Qt.ItemDataRole.CheckStateRole])
        self.selectionChanged.emit()

    def selected_count(self) -> int:
        current_ids = {getattr(hit, 'chunk_id', '') for hit in self._hits if getattr(hit, 'chunk_id', '')}
        return len(current_ids & self._selected_chunk_ids)

    def total_count(self) -> int:
        return len(self._hits)

    def are_all_selected(self) -> bool:
        current_ids = {getattr(hit, 'chunk_id', '') for hit in self._hits if getattr(hit, 'chunk_id', '')}
        return bool(current_ids) and current_ids <= self._selected_chunk_ids

    def toggle_all_selection(self) -> None:
        if not self._hits:
            return
        current_ids = {getattr(hit, 'chunk_id', '') for hit in self._hits if getattr(hit, 'chunk_id', '')}
        if current_ids and current_ids <= self._selected_chunk_ids:
            self._selected_chunk_ids.difference_update(current_ids)
        else:
            self._selected_chunk_ids.update(current_ids)
        top_left = self.index(0, self.COLUMN_INCLUDE)
        bottom_right = self.index(len(self._hits) - 1, self.COLUMN_INCLUDE)
        self.dataChanged.emit(top_left, bottom_right, [QtCore.Qt.ItemDataRole.CheckStateRole])
        self.selectionChanged.emit()

    def toggle_page_sort(self) -> None:
        if not self._hits:
            return
        self.layoutAboutToBeChanged.emit()
        if self._page_sort_active:
            self._restore_saved_order()
            self._sort_column = self._restore_sort_column
            self._sort_reverse = self._restore_sort_reverse
            self._page_sort_active = False
            self._restore_order = []
            self._restore_sort_column = None
            self._restore_sort_reverse = False
        else:
            self._restore_order = [getattr(hit, 'chunk_id', '') for hit in self._hits]
            self._restore_sort_column = self._sort_column
            self._restore_sort_reverse = self._sort_reverse
            self._page_sort_active = True
            self._sort_column = None
            self._sort_reverse = False
            self._hits = sort_hits_by_page_average(self._hits)
        self.layoutChanged.emit()
        self.orderingChanged.emit()

    def sort_by_column(self, column: int) -> None:
        if not self._hits:
            return
        self.layoutAboutToBeChanged.emit()
        if self._page_sort_active:
            self._restore_saved_order()
            self._page_sort_active = False
            self._restore_order = []
            self._restore_sort_column = None
            self._restore_sort_reverse = False
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = column in {self.COLUMN_INCLUDE, self.COLUMN_SCORE}
        self._hits.sort(key=lambda hit: self._sort_value(hit, column), reverse=self._sort_reverse)
        self.layoutChanged.emit()
        self.orderingChanged.emit()

    def _restore_saved_order(self) -> None:
        if not self._restore_order:
            return
        order = {chunk_id: index for index, chunk_id in enumerate(self._restore_order)}
        fallback = len(order)
        self._hits.sort(
            key=lambda hit: (
                order.get(getattr(hit, 'chunk_id', ''), fallback),
                sort_text_value(getattr(hit, 'title', '')),
                sort_text_value(getattr(hit, 'anchor', '')),
            )
        )

    def _sort_value(self, hit, column: int):
        if column == self.COLUMN_INCLUDE:
            return (1 if getattr(hit, 'chunk_id', '') in self._selected_chunk_ids else 0, sort_text_value(getattr(hit, 'title', '')))
        if column == self.COLUMN_SCORE:
            return float(getattr(hit, 'score', 0.0) or 0.0)
        if column == self.COLUMN_TITLE:
            return sort_text_value(getattr(hit, 'title', ''))
        if column == self.COLUMN_REASON:
            return sort_text_value(getattr(hit, 'reason', '') or self._tr('reason_fallback'))
        if column == self.COLUMN_ANCHOR:
            return sort_text_value(getattr(hit, 'anchor', ''))
        return sort_text_value(getattr(hit, 'title', ''))
