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

SEPIA_THEME_COLORS = {
    'bg': '#F2E8D8',
    'card': '#FBF4E8',
    'soft': '#EADCC7',
    'soft_2': '#F7EEDF',
    'ink': '#3C2F25',
    'muted': '#7A6657',
    'accent': '#8A5B32',
    'accent_dark': '#6E4726',
    'accent_soft': '#F1E2CF',
    'danger': '#B65432',
    'danger_dark': '#943E1C',
    'border': '#D6C3A9',
    'select': '#E7D8BF',
    'chip_ok_bg': '#E6F0DF',
    'chip_ok_fg': '#3F6A33',
    'chip_warn_bg': '#FAE8C8',
    'chip_warn_fg': '#8D5D00',
    'chip_neutral_bg': '#E9DFD0',
    'chip_neutral_fg': '#5D4E42',
    'input_bg': '#FFF9F1',
    'input_fg': '#3C2F25',
    'input_border': '#CDB99D',
    'secondary_active': '#E1D2BB',
    'tree_bg': '#FFF9F1',
    'tree_heading_bg': '#EADCC7',
    'search_current_bg': '#E8C76A',
    'query_idle_bg': '#EFE5D7',
    'query_idle_fg': '#5D4E42',
    'query_idle_border': '#CDB99D',
    'query_running_bg': '#E8EFE0',
    'query_running_fg': '#486738',
    'query_running_border': '#9EB88E',
    'query_blocked_bg': '#F9E7C9',
    'query_blocked_fg': '#8D5D00',
    'query_blocked_border': '#D9B06B',
    'query_done_bg': '#E6EDF8',
    'query_done_fg': '#365A91',
    'query_done_border': '#A7B9D8',
}

NORD_THEME_COLORS = {
    'bg': '#ECEFF4',
    'card': '#F8FAFC',
    'soft': '#E5E9F0',
    'soft_2': '#EEF2F7',
    'ink': '#2E3440',
    'muted': '#5E6A7E',
    'accent': '#5E81AC',
    'accent_dark': '#48688F',
    'accent_soft': '#E5ECF5',
    'danger': '#BF616A',
    'danger_dark': '#A64E57',
    'border': '#D8DEE9',
    'select': '#E1E8F2',
    'chip_ok_bg': '#E4F0EA',
    'chip_ok_fg': '#3B6B59',
    'chip_warn_bg': '#F8ECD6',
    'chip_warn_fg': '#8C6A23',
    'chip_neutral_bg': '#E9EEF4',
    'chip_neutral_fg': '#4C566A',
    'input_bg': '#FFFFFF',
    'input_fg': '#2E3440',
    'input_border': '#C7D0DD',
    'secondary_active': '#DCE4EF',
    'tree_bg': '#FFFFFF',
    'tree_heading_bg': '#E5E9F0',
    'search_current_bg': '#EBCB8B',
    'query_idle_bg': '#EEF2F7',
    'query_idle_fg': '#4C566A',
    'query_idle_border': '#C7D0DD',
    'query_running_bg': '#E3EDF8',
    'query_running_fg': '#48688F',
    'query_running_border': '#9FB8D8',
    'query_blocked_bg': '#FAE7E8',
    'query_blocked_fg': '#A64E57',
    'query_blocked_border': '#D9A0A6',
    'query_done_bg': '#E7EEF8',
    'query_done_fg': '#3B5D88',
    'query_done_border': '#ABC0DA',
}

SOLARIZED_LIGHT_THEME_COLORS = {
    'bg': '#FDF6E3',
    'card': '#FFFBF1',
    'soft': '#EEE8D5',
    'soft_2': '#F7F0DD',
    'ink': '#586E75',
    'muted': '#7B8B8F',
    'accent': '#268BD2',
    'accent_dark': '#1C6CA4',
    'accent_soft': '#E9F3FB',
    'danger': '#DC322F',
    'danger_dark': '#B62A27',
    'border': '#D6CFBA',
    'select': '#E8E1CC',
    'chip_ok_bg': '#E8F3E6',
    'chip_ok_fg': '#3C6D3A',
    'chip_warn_bg': '#F9E9C7',
    'chip_warn_fg': '#8A650A',
    'chip_neutral_bg': '#EEE8D5',
    'chip_neutral_fg': '#586E75',
    'input_bg': '#FFFDF7',
    'input_fg': '#586E75',
    'input_border': '#CCC5B2',
    'secondary_active': '#E6DFCB',
    'tree_bg': '#FFFDF7',
    'tree_heading_bg': '#EEE8D5',
    'search_current_bg': '#E8C75F',
    'query_idle_bg': '#F0EADA',
    'query_idle_fg': '#586E75',
    'query_idle_border': '#CCC5B2',
    'query_running_bg': '#E7F0F8',
    'query_running_fg': '#1C6CA4',
    'query_running_border': '#97BCD7',
    'query_blocked_bg': '#FBE4E3',
    'query_blocked_fg': '#B62A27',
    'query_blocked_border': '#D9A19F',
    'query_done_bg': '#EAF2F3',
    'query_done_fg': '#47727C',
    'query_done_border': '#A9C3C8',
}

SOLARIZED_DARK_THEME_COLORS = {
    'bg': '#002B36',
    'card': '#073642',
    'soft': '#0B3E4C',
    'soft_2': '#06303B',
    'ink': '#EEE8D5',
    'muted': '#93A1A1',
    'accent': '#2AA198',
    'accent_dark': '#5CC3BB',
    'accent_soft': '#0F4A4E',
    'danger': '#DC322F',
    'danger_dark': '#F26A67',
    'border': '#1F4D58',
    'select': '#12424D',
    'chip_ok_bg': '#0F4A3A',
    'chip_ok_fg': '#8FD4B3',
    'chip_warn_bg': '#4B3A14',
    'chip_warn_fg': '#F0C96A',
    'chip_neutral_bg': '#123C48',
    'chip_neutral_fg': '#D5D0C0',
    'input_bg': '#052A34',
    'input_fg': '#EEE8D5',
    'input_border': '#2A5661',
    'secondary_active': '#124450',
    'tree_bg': '#052A34',
    'tree_heading_bg': '#0B3E4C',
    'search_current_bg': '#745800',
    'query_idle_bg': '#113943',
    'query_idle_fg': '#D5D0C0',
    'query_idle_border': '#355C66',
    'query_running_bg': '#0F4A4E',
    'query_running_fg': '#8CD9D1',
    'query_running_border': '#2D8F89',
    'query_blocked_bg': '#4B2626',
    'query_blocked_fg': '#F49A97',
    'query_blocked_border': '#8E4A49',
    'query_done_bg': '#163F49',
    'query_done_fg': '#A6D5DD',
    'query_done_border': '#3B7480',
}

GRAPHITE_THEME_COLORS = {
    'bg': '#16181C',
    'card': '#20242A',
    'soft': '#272C33',
    'soft_2': '#1A1E24',
    'ink': '#ECEFF4',
    'muted': '#A7B0BC',
    'accent': '#7AA2F7',
    'accent_dark': '#A4C2FF',
    'accent_soft': '#21324C',
    'danger': '#F7768E',
    'danger_dark': '#FF9AAF',
    'border': '#343A43',
    'select': '#2A313B',
    'chip_ok_bg': '#203629',
    'chip_ok_fg': '#9BD0A7',
    'chip_warn_bg': '#4A3A22',
    'chip_warn_fg': '#F2C97A',
    'chip_neutral_bg': '#2B3139',
    'chip_neutral_fg': '#D4DAE3',
    'input_bg': '#14181E',
    'input_fg': '#ECEFF4',
    'input_border': '#3B4350',
    'secondary_active': '#313843',
    'tree_bg': '#15191F',
    'tree_heading_bg': '#262B33',
    'search_current_bg': '#796100',
    'query_idle_bg': '#242A31',
    'query_idle_fg': '#D4DAE3',
    'query_idle_border': '#46505D',
    'query_running_bg': '#23354E',
    'query_running_fg': '#B8D1FF',
    'query_running_border': '#5379B8',
    'query_blocked_bg': '#4E2A31',
    'query_blocked_fg': '#F5B2BE',
    'query_blocked_border': '#8A5661',
    'query_done_bg': '#21333B',
    'query_done_fg': '#A9D7E3',
    'query_done_border': '#4A7A88',
}

THEME_COLOR_MAP = {
    'light': LIGHT_THEME_COLORS,
    'dark': DARK_THEME_COLORS,
    'sepia': SEPIA_THEME_COLORS,
    'nord': NORD_THEME_COLORS,
    'solarized-light': SOLARIZED_LIGHT_THEME_COLORS,
    'solarized-dark': SOLARIZED_DARK_THEME_COLORS,
    'graphite': GRAPHITE_THEME_COLORS,
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
    if normalized_theme not in {'system', *THEME_COLOR_MAP.keys()}:
        normalized_theme = 'system'
    scale = max(80, min(int(scale_percent or 100), 200))
    effective_theme = detect_system_theme_mode() if normalized_theme == 'system' else normalized_theme
    resolved_theme = detect_system_theme_mode() if normalized_theme == 'system' else normalized_theme
    colors = dict(THEME_COLOR_MAP.get(resolved_theme, LIGHT_THEME_COLORS))
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
    QLabel[role='warning'] {{
        color: {colors['danger_dark']};
        font-weight: 600;
    }}
    QLabel[role='warningTitle'] {{
        color: {colors['danger_dark']};
        font-size: {card_title_size}pt;
        font-weight: 700;
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
