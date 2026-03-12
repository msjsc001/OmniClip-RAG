from __future__ import annotations

from PySide6 import QtGui, QtWidgets

from .theme import ThemeState


class SearchableTextPanel(QtWidgets.QWidget):
    def __init__(self, *, empty_text: str, theme: ThemeState, tr, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._tr = tr
        self._matches: list[tuple[int, int]] = []
        self._current_match_index = -1
        self._empty_text = empty_text

        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(10)

        self.header_widget = QtWidgets.QWidget(self)
        self.header_layout = QtWidgets.QHBoxLayout(self.header_widget)
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.setSpacing(8)
        self.header_widget.setVisible(False)
        root_layout.addWidget(self.header_widget)

        self.text_edit = QtWidgets.QPlainTextEdit(self)
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlainText(empty_text)
        self.text_edit.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth)
        root_layout.addWidget(self.text_edit, 1)

        footer = QtWidgets.QWidget(self)
        footer_layout = QtWidgets.QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(8)
        root_layout.addWidget(footer)

        footer_layout.addStretch(1)

        self.search_status_label = QtWidgets.QLabel(self._tr('text_search_empty'), footer)
        self.search_status_label.setProperty('role', 'muted')
        footer_layout.addWidget(self.search_status_label)

        self.search_edit = QtWidgets.QLineEdit(footer)
        self.search_edit.setMinimumWidth(220)
        self.search_edit.returnPressed.connect(self.find_text)
        footer_layout.addWidget(self.search_edit)

        self.search_button = QtWidgets.QPushButton(self._tr('text_search_button'), footer)
        self.search_button.clicked.connect(self.find_text)
        footer_layout.addWidget(self.search_button)

        self.next_button = QtWidgets.QPushButton(self._tr('text_search_next'), footer)
        self.next_button.clicked.connect(self.find_next)
        footer_layout.addWidget(self.next_button)

    def set_theme(self, theme: ThemeState) -> None:
        self._theme = theme
        self._apply_highlights()

    def set_header_visible(self, visible: bool) -> None:
        self.header_widget.setVisible(visible)

    def set_text(self, text_value: str) -> None:
        self.text_edit.setPlainText(text_value or self._empty_text)
        self._reset_search_state(reapply=False)

    def append_text(self, text_value: str) -> None:
        current = self.text_edit.toPlainText().strip()
        if not current or current == self._empty_text:
            self.set_text(text_value)
            return
        self.text_edit.appendPlainText(text_value)
        self._reset_search_state(reapply=False)

    def plain_text(self) -> str:
        text_value = self.text_edit.toPlainText()
        return '' if text_value == self._empty_text else text_value

    def clear(self) -> None:
        self.set_text(self._empty_text)

    def find_text(self) -> None:
        query = self.search_edit.text().strip()
        if not query:
            self._reset_search_state(reapply=False)
            self.search_status_label.setText(self._tr('text_search_empty'))
            return
        self._matches = self._match_offsets(self.text_edit.toPlainText(), query)
        self._current_match_index = 0 if self._matches else -1
        self._apply_highlights()

    def find_next(self) -> None:
        query = self.search_edit.text().strip()
        if not query:
            self.search_status_label.setText(self._tr('text_search_empty'))
            return
        if not self._matches:
            self.find_text()
            return
        self._current_match_index = (self._current_match_index + 1) % len(self._matches)
        self._apply_highlights()

    def scroll_to_line(self, line_no: int) -> None:
        block = self.text_edit.document().findBlockByLineNumber(max(line_no - 1, 0))
        if not block.isValid():
            return
        cursor = self.text_edit.textCursor()
        cursor.setPosition(block.position())
        self.text_edit.setTextCursor(cursor)
        self.text_edit.centerCursor()

    def _reset_search_state(self, *, reapply: bool) -> None:
        self._matches = []
        self._current_match_index = -1
        self.search_status_label.setText(self._tr('text_search_empty'))
        if reapply and self.search_edit.text().strip():
            self.find_text()
        else:
            self.text_edit.setExtraSelections([])

    def _match_offsets(self, haystack: str, needle: str) -> list[tuple[int, int]]:
        haystack_folded = haystack.casefold()
        needle_folded = needle.casefold()
        if not needle_folded:
            return []
        matches: list[tuple[int, int]] = []
        start = 0
        while True:
            found = haystack_folded.find(needle_folded, start)
            if found < 0:
                break
            end = found + len(needle)
            matches.append((found, end))
            start = end
        return matches

    def _selection(self, start: int, end: int, *, background: str) -> QtWidgets.QTextEdit.ExtraSelection:
        cursor = self.text_edit.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QtGui.QTextCursor.MoveMode.KeepAnchor)
        selection = QtWidgets.QTextEdit.ExtraSelection()
        selection.cursor = cursor
        fmt = QtGui.QTextCharFormat()
        fmt.setBackground(QtGui.QColor(background))
        fmt.setForeground(QtGui.QColor(self._theme.colors['ink']))
        selection.format = fmt
        return selection

    def _apply_highlights(self) -> None:
        if not self._matches:
            self.text_edit.setExtraSelections([])
            if self.search_edit.text().strip():
                self.search_status_label.setText(self._tr('text_search_none'))
            return
        selections = [
            self._selection(start, end, background=self._theme.colors['accent_soft'])
            for start, end in self._matches
        ]
        current_index = max(self._current_match_index, 0)
        current_index = min(current_index, len(self._matches) - 1)
        current_start, current_end = self._matches[current_index]
        selections.append(self._selection(current_start, current_end, background=self._theme.colors['search_current_bg']))
        self.text_edit.setExtraSelections(selections)
        cursor = self.text_edit.textCursor()
        cursor.setPosition(current_start)
        cursor.setPosition(current_end, QtGui.QTextCursor.MoveMode.KeepAnchor)
        self.text_edit.setTextCursor(cursor)
        self.text_edit.centerCursor()
        self.search_status_label.setText(self._tr('text_search_status', index=current_index + 1, total=len(self._matches)))
