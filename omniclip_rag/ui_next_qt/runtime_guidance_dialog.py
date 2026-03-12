from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .theme import ThemeState, scaled


class RuntimeGuidanceDialog(QtWidgets.QDialog):
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
        self._context = dict(context)
        self.setWindowTitle('CUDA(N卡GPU) 运行环境引导')
        self.setModal(True)
        self.resize(scaled(theme, 860, minimum=760), scaled(theme, 760, minimum=680))

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        header_card, header_layout = self._make_card()
        root.addWidget(header_card)
        title = QtWidgets.QLabel('当前还不能启用 CUDA(N卡GPU)', header_card)
        title.setProperty('role', 'title')
        header_layout.addWidget(title)
        subtitle = QtWidgets.QLabel(
            '程序已经打开了，但显卡加速这部分还没准备好。把下面两步做完后，再回来选 CUDA 就可以了。',
            header_card,
        )
        subtitle.setProperty('role', 'subtitle')
        subtitle.setWordWrap(True)
        subtitle.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        header_layout.addWidget(subtitle)

        badge_row = QtWidgets.QHBoxLayout()
        badge_row.setSpacing(8)
        header_layout.addLayout(badge_row)
        badge_row.addWidget(self._make_badge(f"第一步：{self._context.get('cuda_step_status') or '待处理'}", ready=bool(self._context.get('cuda_available') or self._context.get('nvcc_available'))))
        badge_row.addWidget(self._make_badge(f"第二步：{self._context.get('runtime_step_status') or '待处理'}", ready=bool(self._context.get('runtime_complete'))))
        badge_row.addStretch(1)

        scroll = QtWidgets.QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        root.addWidget(scroll, 1)

        body = QtWidgets.QWidget(scroll)
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)
        scroll.setWidget(body)

        self._build_body(body_layout)
        body_layout.addStretch(1)

        footer = QtWidgets.QHBoxLayout()
        footer.setSpacing(8)
        root.addLayout(footer)
        copy_button = QtWidgets.QPushButton('复制全部', self)
        copy_button.setProperty('variant', 'secondary')
        copy_button.clicked.connect(self._copy_all)
        footer.addWidget(copy_button)
        open_dir_button = QtWidgets.QPushButton('打开程序目录', self)
        open_dir_button.setProperty('variant', 'secondary')
        open_dir_button.clicked.connect(self._open_app_dir)
        footer.addWidget(open_dir_button)
        close_button = QtWidgets.QPushButton('我知道了', self)
        close_button.setProperty('variant', 'primary')
        close_button.clicked.connect(self.accept)
        footer.addStretch(1)
        footer.addWidget(close_button)

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

    def _make_badge(self, text_value: str, *, ready: bool) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text_value, self)
        label.setMargin(0)
        label.setProperty('role', 'badge')
        colors = self._theme.colors
        if ready:
            bg = colors['chip_ok_bg']
            fg = colors['chip_ok_fg']
        else:
            bg = colors['chip_warn_bg']
            fg = colors['chip_warn_fg']
        label.setStyleSheet(f'background:{bg}; color:{fg}; border-radius:999px; padding:6px 12px; font-weight:600;')
        return label

    def _build_body(self, layout: QtWidgets.QVBoxLayout) -> None:
        self._add_text_card(
            layout,
            '怎么了',
            '你当前选择的是 CUDA(N卡GPU)，但显卡加速这部分还没准备好。现在要么还没检测到可直接使用的 CUDA 条件，要么当前程序目录下的 runtime 还没安装完整。',
        )
        self._add_text_card(
            layout,
            '为什么',
            '这个轻量发布包只带主程序，不内置 PyTorch、LanceDB、sentence-transformers、pyarrow 这类大型运行时。主程序可以先打开，但要做本地语义建库、向量查询或 GPU 加速，还需要把外置运行时补齐。',
        )
        self._add_cuda_step_card(layout)
        self._add_runtime_step_card(layout)
        self._add_current_status_card(layout)
        self._add_text_card(
            layout,
            '如果只使用CPU',
            '也可以继续选择 lancedb，只是不走 CUDA 加速。如果后面仍要使用本地语义建库或向量查询，还是先完成上面的 runtime 安装步骤。',
        )
        extra_detail = str(self._context.get('extra_detail') or '').strip()
        if extra_detail:
            self._add_text_card(layout, '补充信息', extra_detail)

    def _add_text_card(self, layout: QtWidgets.QVBoxLayout, title: str, body: str) -> None:
        card, card_layout = self._make_card(title)
        layout.addWidget(card)
        label = QtWidgets.QLabel(body, card)
        label.setWordWrap(True)
        label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(label)

    def _add_cuda_step_card(self, layout: QtWidgets.QVBoxLayout) -> None:
        card, card_layout = self._make_card('第一步：安装或确认 CUDA 环境')
        layout.addWidget(card)
        card_layout.addWidget(self._make_badge(str(self._context.get('cuda_step_status') or '待处理'), ready=bool(self._context.get('cuda_available') or self._context.get('nvcc_available'))))
        intro = QtWidgets.QLabel('如果你还没装好 NVIDIA / CUDA 条件，先参考下面这个官方链接。已经检测到的话，这一步通常可以跳过。', card)
        intro.setWordWrap(True)
        intro.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(intro)
        link_box = self._make_code_box(str(self._context.get('cuda_guide_url') or ''))
        card_layout.addWidget(link_box)
        detail = QtWidgets.QLabel('做完后会发生什么：程序会重新识别系统里的 NVIDIA / CUDA 条件；如果第二步还没做，本地向量功能仍然不能直接运行。', card)
        detail.setProperty('role', 'muted')
        detail.setWordWrap(True)
        detail.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(detail)
        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(8)
        card_layout.addLayout(actions)
        copy_link_button = QtWidgets.QPushButton('复制链接', card)
        copy_link_button.setProperty('variant', 'secondary')
        copy_link_button.clicked.connect(lambda: self._copy_text(str(self._context.get('cuda_guide_url') or '')))
        actions.addWidget(copy_link_button)
        open_link_button = QtWidgets.QPushButton('打开链接', card)
        open_link_button.setProperty('variant', 'secondary')
        open_link_button.clicked.connect(self._open_cuda_guide)
        actions.addWidget(open_link_button)
        actions.addStretch(1)

    def _add_runtime_step_card(self, layout: QtWidgets.QVBoxLayout) -> None:
        card, card_layout = self._make_card('第二步：在 Windows 终端里安装 runtime')
        layout.addWidget(card)
        card_layout.addWidget(self._make_badge(str(self._context.get('runtime_step_status') or '待处理'), ready=bool(self._context.get('runtime_complete'))))
        intro = QtWidgets.QLabel('请在 Windows 终端里运行下面这条命令。命令里的程序目录会按当前安装位置自动生成，不是写死某台机器的路径。', card)
        intro.setWordWrap(True)
        intro.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(intro)
        command_box = self._make_code_box(str(self._context.get('install_command') or ''), minimum_lines=4)
        card_layout.addWidget(command_box)
        install_note = QtWidgets.QLabel(
            f"会安装到：{self._context.get('runtime_dir')}\n安装后会补齐 PyTorch、LanceDB、sentence-transformers、pyarrow、onnxruntime 等本地运行时；重启程序后就能正常执行本地语义建库、向量查询和 GPU 加速。\n预计落盘：{self._context.get('disk_usage')}；预计下载：{self._context.get('download_usage')}",
            card,
        )
        install_note.setProperty('role', 'muted')
        install_note.setWordWrap(True)
        install_note.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(install_note)
        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(8)
        card_layout.addLayout(actions)
        copy_command_button = QtWidgets.QPushButton('复制命令', card)
        copy_command_button.setProperty('variant', 'secondary')
        copy_command_button.clicked.connect(lambda: self._copy_text(str(self._context.get('install_command') or '')))
        actions.addWidget(copy_command_button)
        open_dir_button = QtWidgets.QPushButton('打开程序目录', card)
        open_dir_button.setProperty('variant', 'secondary')
        open_dir_button.clicked.connect(self._open_app_dir)
        actions.addWidget(open_dir_button)
        actions.addStretch(1)

    def _add_current_status_card(self, layout: QtWidgets.QVBoxLayout) -> None:
        card, card_layout = self._make_card('当前状态')
        layout.addWidget(card)
        lines = list(self._context.get('current_status_lines') or [])
        status_box = self._make_code_box('\n'.join(str(item) for item in lines if str(item).strip()), minimum_lines=7)
        card_layout.addWidget(status_box)

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
        self._copy_text(str(self._context.get('plain_text') or ''))

    def _open_cuda_guide(self) -> None:
        url = str(self._context.get('cuda_guide_url') or '').strip()
        if not url:
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    def _open_app_dir(self) -> None:
        app_dir = str(self._context.get('app_dir') or '').strip()
        if not app_dir:
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(app_dir))
