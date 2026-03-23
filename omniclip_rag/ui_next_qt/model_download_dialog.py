from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..ui_i18n import text
from .theme import ThemeState, scaled


class ModelDownloadDialog(QtWidgets.QDialog):
    def __init__(
        self,
        *,
        language_code: str,
        theme: ThemeState,
        context: dict[str, object],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._language_code = language_code
        self._theme = theme
        self._context = {str(key): value for key, value in dict(context).items()}
        self.setWindowTitle(self._tr('model_manual_dialog_window_title', model=self._value('model')))
        self.setModal(True)
        self.resize(scaled(theme, 920, minimum=800), scaled(theme, 840, minimum=720))

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        header_card, header_layout = self._make_card()
        root.addWidget(header_card)
        title = QtWidgets.QLabel(self._tr('model_manual_dialog_title', model=self._value('model')), header_card)
        title.setProperty('role', 'title')
        header_layout.addWidget(title)
        subtitle = QtWidgets.QLabel(self._tr('model_manual_dialog_subtitle', model=self._value('model')), header_card)
        subtitle.setProperty('role', 'subtitle')
        subtitle.setWordWrap(True)
        subtitle.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        header_layout.addWidget(subtitle)

        scroll = QtWidgets.QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        root.addWidget(scroll, 1)

        body = QtWidgets.QWidget(scroll)
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)
        scroll.setWidget(body)

        self._add_target_card(body_layout)
        self._add_cli_install_card(body_layout)
        self._add_source_card(
            body_layout,
            title=self._tr('model_manual_dialog_mirror_title'),
            url=self._value('mirror_url'),
            command=self._value('mirror_download_command'),
        )
        self._add_source_card(
            body_layout,
            title=self._tr('model_manual_dialog_official_title'),
            url=self._value('official_url'),
            command=self._value('official_download_command'),
        )
        self._add_finish_card(body_layout)
        body_layout.addStretch(1)

        footer = QtWidgets.QHBoxLayout()
        footer.setSpacing(8)
        root.addLayout(footer)
        copy_all_button = QtWidgets.QPushButton(self._tr('copy_all_button'), self)
        copy_all_button.setProperty('variant', 'secondary')
        copy_all_button.clicked.connect(self._copy_all)
        footer.addWidget(copy_all_button)
        open_target_button = QtWidgets.QPushButton(self._tr('open_target_folder_button'), self)
        open_target_button.setProperty('variant', 'secondary')
        open_target_button.clicked.connect(self._open_target_dir)
        footer.addWidget(open_target_button)
        footer.addStretch(1)
        close_button = QtWidgets.QPushButton(self._tr('acknowledge_button'), self)
        close_button.setProperty('variant', 'primary')
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)

    def _tr(self, key: str, **kwargs: object) -> str:
        return text(self._language_code, key, **kwargs)

    def _value(self, key: str) -> str:
        return str(self._context.get(key) or '').strip()

    def _make_card(self, title: str | None = None) -> tuple[QtWidgets.QFrame, QtWidgets.QVBoxLayout]:
        card = QtWidgets.QFrame(self)
        card.setProperty('card', True)
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        if title:
            label = QtWidgets.QLabel(title, card)
            label.setProperty('role', 'cardTitle')
            layout.addWidget(label)
        return card, layout

    def _add_target_card(self, layout: QtWidgets.QVBoxLayout) -> None:
        card, card_layout = self._make_card(self._tr('model_manual_dialog_target_title'))
        layout.addWidget(card)
        label = QtWidgets.QLabel(self._tr('model_manual_dialog_target_body'), card)
        label.setWordWrap(True)
        label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(label)
        card_layout.addWidget(self._make_code_box(self._value('model_dir'), minimum_lines=3))
        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(8)
        card_layout.addLayout(actions)
        copy_button = QtWidgets.QPushButton(self._tr('copy_path_button'), card)
        copy_button.setProperty('variant', 'secondary')
        copy_button.clicked.connect(lambda: self._copy_text(self._value('model_dir')))
        actions.addWidget(copy_button)
        open_button = QtWidgets.QPushButton(self._tr('open_target_folder_button'), card)
        open_button.setProperty('variant', 'secondary')
        open_button.clicked.connect(self._open_target_dir)
        actions.addWidget(open_button)
        actions.addStretch(1)

    def _add_cli_install_card(self, layout: QtWidgets.QVBoxLayout) -> None:
        card, card_layout = self._make_card(self._tr('model_manual_dialog_install_title'))
        layout.addWidget(card)
        label = QtWidgets.QLabel(self._tr('model_manual_dialog_install_body'), card)
        label.setWordWrap(True)
        label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(label)
        card_layout.addWidget(self._make_code_box(self._value('install_cli_command'), minimum_lines=3))
        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(8)
        card_layout.addLayout(actions)
        copy_button = QtWidgets.QPushButton(self._tr('copy_command_button'), card)
        copy_button.setProperty('variant', 'secondary')
        copy_button.clicked.connect(lambda: self._copy_text(self._value('install_cli_command')))
        actions.addWidget(copy_button)
        actions.addStretch(1)

    def _add_source_card(self, layout: QtWidgets.QVBoxLayout, *, title: str, url: str, command: str) -> None:
        card, card_layout = self._make_card(title)
        layout.addWidget(card)
        url_label = QtWidgets.QLabel(self._tr('model_manual_dialog_source_url'), card)
        url_label.setProperty('role', 'muted')
        card_layout.addWidget(url_label)
        card_layout.addWidget(self._make_code_box(url, minimum_lines=2))
        url_actions = QtWidgets.QHBoxLayout()
        url_actions.setSpacing(8)
        card_layout.addLayout(url_actions)
        copy_url_button = QtWidgets.QPushButton(self._tr('copy_url_button'), card)
        copy_url_button.setProperty('variant', 'secondary')
        copy_url_button.clicked.connect(lambda: self._copy_text(url))
        url_actions.addWidget(copy_url_button)
        open_link_button = QtWidgets.QPushButton(self._tr('open_link_button'), card)
        open_link_button.setProperty('variant', 'secondary')
        open_link_button.clicked.connect(lambda: self._open_url(url))
        url_actions.addWidget(open_link_button)
        url_actions.addStretch(1)
        command_label = QtWidgets.QLabel(self._tr('model_manual_dialog_source_command'), card)
        command_label.setProperty('role', 'muted')
        card_layout.addWidget(command_label)
        card_layout.addWidget(self._make_code_box(command, minimum_lines=4))
        command_actions = QtWidgets.QHBoxLayout()
        command_actions.setSpacing(8)
        card_layout.addLayout(command_actions)
        copy_command_button = QtWidgets.QPushButton(self._tr('copy_command_button'), card)
        copy_command_button.setProperty('variant', 'secondary')
        copy_command_button.clicked.connect(lambda: self._copy_text(command))
        command_actions.addWidget(copy_command_button)
        command_actions.addStretch(1)

    def _add_finish_card(self, layout: QtWidgets.QVBoxLayout) -> None:
        card, card_layout = self._make_card(self._tr('model_manual_dialog_finish_title'))
        layout.addWidget(card)
        label = QtWidgets.QLabel(self._tr('model_manual_dialog_finish_body', model=self._value('model')), card)
        label.setWordWrap(True)
        label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(label)

    def _make_code_box(self, text_value: str, *, minimum_lines: int = 2) -> QtWidgets.QPlainTextEdit:
        box = QtWidgets.QPlainTextEdit(self)
        box.setReadOnly(True)
        box.setPlainText(text_value)
        box.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth)
        box.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        box.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        font = QtGui.QFont('Consolas')
        font.setPointSize(max(int(round(10 * self._theme.scale_percent / 100.0)), 9))
        box.setFont(font)
        metrics = QtGui.QFontMetrics(font)
        line_height = metrics.lineSpacing()
        padding = scaled(self._theme, 24, minimum=20)
        box.setMinimumHeight(line_height * minimum_lines + padding)
        return box

    def _copy_text(self, text_value: str) -> None:
        QtGui.QGuiApplication.clipboard().setText(text_value)

    def _copy_all(self) -> None:
        self._copy_text(self._value('plain_text'))

    def _open_url(self, url: str) -> None:
        normalized = str(url or '').strip()
        if not normalized:
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(normalized))

    def _open_target_dir(self) -> None:
        target_dir = self._value('model_dir')
        if not target_dir:
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(target_dir))
