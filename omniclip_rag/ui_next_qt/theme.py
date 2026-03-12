from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6 import QtGui, QtWidgets

LIGHT_THEME_COLORS = {
    'bg': '#F5F7FA',
    'card': '#FFFFFF',
    'soft': '#EEF3F7',
    'soft_2': '#F8FAFC',
    'ink': '#16202A',
    'muted': '#667085',
    'accent': '#0F7B6C',
    'accent_dark': '#0B5D52',
    'accent_soft': '#E6F5F2',
    'danger': '#B54708',
    'danger_dark': '#93370D',
    'border': '#D7E0E8',
    'select': '#E3F4EF',
    'chip_ok_bg': '#E7F6EC',
    'chip_ok_fg': '#166534',
    'chip_warn_bg': '#FFF4DD',
    'chip_warn_fg': '#9A6700',
    'chip_neutral_bg': '#EEF3F7',
    'chip_neutral_fg': '#344054',
    'input_bg': '#FFFFFF',
    'input_fg': '#16202A',
    'input_border': '#D7E0E8',
    'secondary_active': '#E5ECF3',
    'tree_bg': '#FFFFFF',
    'tree_heading_bg': '#EEF3F7',
    'search_current_bg': '#FDE68A',
    'query_idle_bg': '#F3F6FA',
    'query_idle_fg': '#344054',
    'query_idle_border': '#D7E0E8',
    'query_running_bg': '#E6F5F2',
    'query_running_fg': '#0B5D52',
    'query_running_border': '#9BD8CB',
    'query_blocked_bg': '#FFF4DD',
    'query_blocked_fg': '#9A6700',
    'query_blocked_border': '#F2C97D',
    'query_done_bg': '#EAF2FF',
    'query_done_fg': '#1D4ED8',
    'query_done_border': '#A7C4FF',
}

DARK_THEME_COLORS = {
    'bg': '#0E1620',
    'card': '#16212C',
    'soft': '#1C2A38',
    'soft_2': '#101A24',
    'ink': '#E7F0F7',
    'muted': '#9CB1C4',
    'accent': '#2DB39B',
    'accent_dark': '#8AE7D8',
    'accent_soft': '#143B36',
    'danger': '#D66A4A',
    'danger_dark': '#F08A6D',
    'border': '#304152',
    'select': '#22483F',
    'chip_ok_bg': '#143B36',
    'chip_ok_fg': '#8AE7D8',
    'chip_warn_bg': '#4A3614',
    'chip_warn_fg': '#F6CB6B',
    'chip_neutral_bg': '#223141',
    'chip_neutral_fg': '#D0D8E2',
    'input_bg': '#0C141D',
    'input_fg': '#E7F0F7',
    'input_border': '#425567',
    'secondary_active': '#263646',
    'tree_bg': '#0F1822',
    'tree_heading_bg': '#1C2A38',
    'search_current_bg': '#6F5510',
    'query_idle_bg': '#1B2734',
    'query_idle_fg': '#D0D8E2',
    'query_idle_border': '#405366',
    'query_running_bg': '#143B36',
    'query_running_fg': '#8AE7D8',
    'query_running_border': '#2A8C7C',
    'query_blocked_bg': '#4A3614',
    'query_blocked_fg': '#F6CB6B',
    'query_blocked_border': '#8C6730',
    'query_done_bg': '#16314B',
    'query_done_fg': '#9FD3FF',
    'query_done_border': '#2D5E86',
}


@dataclass(slots=True)
class ThemeState:
    theme_code: str
    effective_theme: str
    scale_percent: int
    colors: dict[str, str]


def detect_system_theme_mode() -> str:
    if sys.platform != 'win32':
        return 'light'
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize',
        ) as key:
            apps_use_light_theme, _ = winreg.QueryValueEx(key, 'AppsUseLightTheme')
        return 'light' if int(apps_use_light_theme) else 'dark'
    except Exception:
        return 'light'


def build_theme(theme_code: str, scale_percent: int) -> ThemeState:
    normalized_theme = str(theme_code or 'system').strip().lower() or 'system'
    if normalized_theme not in {'system', 'light', 'dark'}:
        normalized_theme = 'system'
    scale = max(80, min(int(scale_percent or 100), 200))
    effective_theme = detect_system_theme_mode() if normalized_theme == 'system' else normalized_theme
    colors = dict(DARK_THEME_COLORS if effective_theme == 'dark' else LIGHT_THEME_COLORS)
    return ThemeState(
        theme_code=normalized_theme,
        effective_theme=effective_theme,
        scale_percent=scale,
        colors=colors,
    )


def scaled(theme: ThemeState, value: int, *, minimum: int = 0) -> int:
    factor = theme.scale_percent / 100.0
    return max(int(round(value * factor)), minimum)


def apply_application_style(app: QtWidgets.QApplication, theme: ThemeState) -> None:
    colors = theme.colors
    app.setStyle('Fusion')
    font = QtGui.QFont('Segoe UI', max(int(round(10 * theme.scale_percent / 100.0)), 9))
    app.setFont(font)
    palette = app.palette()
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(colors['bg']))
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(colors['ink']))
    palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(colors['input_bg']))
    palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor(colors['soft_2']))
    palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(colors['input_fg']))
    palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor(colors['soft']))
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor(colors['ink']))
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor(colors['select']))
    palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor(colors['ink']))
    app.setPalette(palette)
    app.setStyleSheet(build_stylesheet(theme))


def build_stylesheet(theme: ThemeState) -> str:
    colors = theme.colors
    radius = scaled(theme, 10, minimum=8)
    padding_y = scaled(theme, 8, minimum=6)
    padding_x = scaled(theme, 14, minimum=10)
    header_padding = 0
    font_size = max(int(round(10 * theme.scale_percent / 100.0)), 9)
    title_size = max(int(round(17 * theme.scale_percent / 100.0)), 12)
    card_title_size = max(int(round(12 * theme.scale_percent / 100.0)), 10)
    return f"""
    QWidget {{
        color: {colors['ink']};
        font-family: 'Segoe UI';
        font-size: {font_size}pt;
    }}
    QWidget#AppRoot {{
        background: {colors['bg']};
    }}
    QFrame[card='true'] {{
        background: {colors['card']};
        border: 1px solid {colors['border']};
        border-radius: {radius}px;
    }}
    QLabel[role='title'] {{
        font-size: {title_size}pt;
        font-weight: 700;
    }}
    QLabel[role='subtitle'] {{
        color: {colors['muted']};
    }}
    QLabel[role='guide'] {{
        color: {colors['accent_dark']};
    }}
    QLabel[role='cardTitle'] {{
        font-size: {card_title_size}pt;
        font-weight: 600;
    }}
    QLabel[role='muted'] {{
        color: {colors['muted']};
    }}
    QLabel[role='badge'] {{
        background: {colors['accent_soft']};
        color: {colors['accent_dark']};
        border-radius: {max(radius - 2, 6)}px;
        padding: {scaled(theme, 5, minimum=4)}px {scaled(theme, 10, minimum=8)}px;
        font-weight: 600;
    }}
    QPushButton, QToolButton {{
        border: 1px solid {colors['border']};
        border-radius: {radius}px;
        padding: {padding_y}px {padding_x}px;
        background: {colors['soft']};
        color: {colors['ink']};
    }}
    QPushButton:hover, QToolButton:hover {{
        background: {colors['secondary_active']};
    }}
    QPushButton[variant='primary'] {{
        background: {colors['accent']};
        border-color: {colors['accent']};
        color: #FFFFFF;
        font-weight: 600;
    }}
    QPushButton[variant='primary']:hover {{
        background: {colors['accent_dark']};
        border-color: {colors['accent_dark']};
    }}
    QPushButton[variant='danger'] {{
        background: {colors['danger']};
        border-color: {colors['danger']};
        color: #FFFFFF;
        font-weight: 600;
    }}
    QPushButton[variant='danger']:hover {{
        background: {colors['danger_dark']};
        border-color: {colors['danger_dark']};
    }}
    QLineEdit, QComboBox, QPlainTextEdit, QTableView {{
        background: {colors['input_bg']};
        color: {colors['input_fg']};
        border: 1px solid {colors['input_border']};
        border-radius: {radius}px;
    }}
    QLineEdit, QComboBox {{
        padding: {scaled(theme, 7, minimum=5)}px;
    }}
    QComboBox QAbstractItemView {{
        background: {colors['input_bg']};
        color: {colors['input_fg']};
        border: 1px solid {colors['input_border']};
        selection-background-color: {colors['select']};
        selection-color: {colors['ink']};
        outline: none;
    }}
    QComboBox QAbstractItemView::item {{
        background: {colors['input_bg']};
        color: {colors['input_fg']};
        padding: {scaled(theme, 7, minimum=5)}px;
    }}
    QComboBox QAbstractItemView::item:hover {{
        background: {colors['select']};
        color: {colors['ink']};
    }}
    QComboBox QAbstractItemView::item:selected {{
        background: {colors['select']};
        color: {colors['ink']};
    }}
    QPlainTextEdit {{
        padding: {scaled(theme, 6, minimum=4)}px;
        selection-background-color: {colors['select']};
        selection-color: {colors['ink']};
    }}
    QHeaderView::section {{
        background: {colors['tree_heading_bg']};
        color: {colors['ink']};
        border: none;
        border-bottom: 1px solid {colors['border']};
        padding: {scaled(theme, 8, minimum=6)}px;
        font-weight: 600;
    }}
    QTableView {{
        background: {colors['tree_bg']};
        gridline-color: {colors['border']};
        selection-background-color: {colors['select']};
        selection-color: {colors['ink']};
    }}
    QTableCornerButton::section {{
        background: {colors['tree_heading_bg']};
        border: none;
        border-bottom: 1px solid {colors['border']};
    }}
    QTabWidget::pane {{
        border: 1px solid {colors['border']};
        background: {colors['card']};
        border-radius: {radius}px;
        top: -1px;
    }}
    QTabBar::tab {{
        background: {colors['soft']};
        color: {colors['ink']};
        border: 1px solid {colors['border']};
        padding: {scaled(theme, 8, minimum=6)}px {scaled(theme, 14, minimum=10)}px;
        border-top-left-radius: {radius}px;
        border-top-right-radius: {radius}px;
        margin-right: 4px;
    }}
    QTabBar::tab:selected {{
        background: {colors['card']};
    }}
    QStatusBar {{
        background: {colors['card']};
        border-top: 1px solid {colors['border']};
    }}
    QSplitter::handle {{
        background: {colors['soft']};
    }}
    QSplitter::handle:hover {{
        background: {colors['border']};
    }}
    QScrollBar:vertical {{
        background: {colors['soft_2']};
        width: {scaled(theme, 12, minimum=10)}px;
        margin: 2px;
        border-radius: {max(radius - 4, 4)}px;
    }}
    QScrollBar::handle:vertical {{
        background: {colors['border']};
        border-radius: {max(radius - 4, 4)}px;
        min-height: 24px;
    }}
    QFrame#HeaderCard {{
        padding: {header_padding}px;
    }}
    QFrame#QueryStatusBanner[mode='idle'] {{
        background: {colors['query_idle_bg']};
        border: 1px solid {colors['query_idle_border']};
        border-radius: {radius}px;
    }}
    QFrame#QueryStatusBanner[mode='running'] {{
        background: {colors['query_running_bg']};
        border: 1px solid {colors['query_running_border']};
        border-radius: {radius}px;
    }}
    QFrame#QueryStatusBanner[mode='blocked'] {{
        background: {colors['query_blocked_bg']};
        border: 1px solid {colors['query_blocked_border']};
        border-radius: {radius}px;
    }}
    QFrame#QueryStatusBanner[mode='done'] {{
        background: {colors['query_done_bg']};
        border: 1px solid {colors['query_done_border']};
        border-radius: {radius}px;
    }}
    QLabel#QueryStatusTitle[mode='idle'], QLabel#QueryStatusDetail[mode='idle'] {{
        color: {colors['query_idle_fg']};
    }}
    QLabel#QueryStatusTitle[mode='running'], QLabel#QueryStatusDetail[mode='running'] {{
        color: {colors['query_running_fg']};
    }}
    QLabel#QueryStatusTitle[mode='blocked'], QLabel#QueryStatusDetail[mode='blocked'] {{
        color: {colors['query_blocked_fg']};
    }}
    QLabel#QueryStatusTitle[mode='done'], QLabel#QueryStatusDetail[mode='done'] {{
        color: {colors['query_done_fg']};
    }}
    """
