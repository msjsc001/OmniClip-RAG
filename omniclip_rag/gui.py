from __future__ import annotations

import ctypes
from dataclasses import asdict
import locale
import re
import queue
import sys
import threading
import time
import traceback
import webbrowser
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .clipboard import copy_text
from .config import (
    AppConfig,
    UI_SCALE_PERCENT_MAX,
    UI_SCALE_PERCENT_MIN,
    WATCH_RESOURCE_PEAK_OPTIONS,
    default_data_root,
    ensure_data_paths,
    load_config,
    normalize_ui_scale_percent,
    normalize_ui_theme,
    normalize_vault_path,
    normalize_watch_resource_peak_percent,
    save_config,
)
from .errors import BuildCancelledError, RuntimeDependencyError
from .build_control import format_resource_sample, normalize_build_resource_profile, ResourceSample
from .formatting import format_bytes, format_duration, format_space_report, summarize_preflight
from .preflight import estimate_model_cache_bytes
from .service import WATCHDOG_AVAILABLE, OmniClipService
from .ui_i18n import language_code_from_label, language_label, normalize_language, text, tooltip
from .ui_tooltip import ToolTip
from .reranker import get_local_reranker_dir, is_local_reranker_ready
from .vector_index import detect_acceleration, get_device_options, get_local_model_dir, is_local_model_ready, resolve_vector_device

APP_TITLE = "OmniClip RAG · 方寸引"
APP_VERSION = "V0.2.1"
REPO_URL = "https://github.com/msjsc001/OmniClip-RAG"
_CONTEXT_PAGE_RE = re.compile(r'^# 笔记名：(.*)$')
_CONTEXT_FRAGMENT_RE = re.compile(r'^笔记片段\d+：$')
UI_QUEUE_BATCH_SIZE = 24
UI_QUEUE_FAST_POLL_MS = 20
UI_QUEUE_IDLE_POLL_MS = 120
UI_CONTEXT_DEFER_MS = 45
UI_CONTEXT_DEFER_HIT_THRESHOLD = 8
UI_LAYOUT_DEFER_MS = 24
UI_WINDOW_INTERACTION_SETTLE_MS = 140
UI_NOTEBOOK_LAYOUT_DEFER_MS = 18
UI_DEFAULT_SCALE_PERCENT = 100
DEFAULT_PAGE_FILTER_RULES: tuple[tuple[bool, str], ...] = (
    (True, r"^2026-.*\.android$"),
    (True, r"^.*\.sync-conflict-\d{8}-\d{6}-[A-Z0-9]+$"),
    (True, r"^\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2}\.\d{3}Z\.(?:Desktop|android)$"),
    (True, r"^hls__.*?_\d+_\d+_\d+_\d+$"),
)

LIGHT_THEME_COLORS = {
    "bg": "#F5F7FA",
    "card": "#FFFFFF",
    "soft": "#EEF3F7",
    "soft_2": "#F8FAFC",
    "ink": "#16202A",
    "muted": "#667085",
    "accent": "#0F7B6C",
    "accent_dark": "#0B5D52",
    "accent_soft": "#E6F5F2",
    "danger": "#B54708",
    "danger_dark": "#93370D",
    "border": "#D7E0E8",
    "select": "#E3F4EF",
    "chip_ok_bg": "#E7F6EC",
    "chip_ok_fg": "#166534",
    "chip_warn_bg": "#FFF4DD",
    "chip_warn_fg": "#9A6700",
    "chip_neutral_bg": "#EEF3F7",
    "chip_neutral_fg": "#344054",
    "input_bg": "#FFFFFF",
    "input_fg": "#16202A",
    "input_border": "#D7E0E8",
    "secondary_active": "#E5ECF3",
    "tree_bg": "#FFFFFF",
    "tree_heading_bg": "#EEF3F7",
    "search_current_bg": "#FDE68A",
    "query_idle_bg": "#F3F6FA",
    "query_idle_fg": "#344054",
    "query_idle_border": "#D7E0E8",
    "query_running_bg": "#E6F5F2",
    "query_running_fg": "#0B5D52",
    "query_running_border": "#9BD8CB",
    "query_blocked_bg": "#FFF4DD",
    "query_blocked_fg": "#9A6700",
    "query_blocked_border": "#F2C97D",
    "query_done_bg": "#EAF2FF",
    "query_done_fg": "#1D4ED8",
    "query_done_border": "#A7C4FF",
}

DARK_THEME_COLORS = {
    "bg": "#0E1620",
    "card": "#16212C",
    "soft": "#1C2A38",
    "soft_2": "#101A24",
    "ink": "#E7F0F7",
    "muted": "#9CB1C4",
    "accent": "#2DB39B",
    "accent_dark": "#8AE7D8",
    "accent_soft": "#143B36",
    "danger": "#D66A4A",
    "danger_dark": "#F08A6D",
    "border": "#304152",
    "select": "#22483F",
    "chip_ok_bg": "#143B36",
    "chip_ok_fg": "#8AE7D8",
    "chip_warn_bg": "#4A3614",
    "chip_warn_fg": "#F6CB6B",
    "chip_neutral_bg": "#223141",
    "chip_neutral_fg": "#D0D8E2",
    "input_bg": "#0C141D",
    "input_fg": "#E7F0F7",
    "input_border": "#425567",
    "secondary_active": "#263646",
    "tree_bg": "#0F1822",
    "tree_heading_bg": "#1C2A38",
    "search_current_bg": "#6F5510",
    "query_idle_bg": "#1B2734",
    "query_idle_fg": "#D0D8E2",
    "query_idle_border": "#405366",
    "query_running_bg": "#143B36",
    "query_running_fg": "#8AE7D8",
    "query_running_border": "#2A8C7C",
    "query_blocked_bg": "#4A3614",
    "query_blocked_fg": "#F6CB6B",
    "query_blocked_border": "#8C6730",
    "query_done_bg": "#16314B",
    "query_done_fg": "#9FD3FF",
    "query_done_border": "#2D5E86",
}


def _detect_system_theme_mode() -> str:
    if sys.platform != "win32":
        return "light"
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            apps_use_light_theme, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if int(apps_use_light_theme) else "dark"
    except Exception:
        return "light"


def _serialize_page_filter_rules(rules: list[tuple[bool, str]] | tuple[tuple[bool, str], ...]) -> str:
    lines: list[str] = []
    for enabled, pattern in rules:
        rule = str(pattern or "").strip()
        if not rule:
            continue
        lines.append(f"{'1' if enabled else '0'}\t{rule}")
    return "\n".join(lines)


def _deserialize_page_filter_rules(raw_rules: str) -> list[tuple[bool, str]]:
    parsed: list[tuple[bool, str]] = []
    for raw_line in (raw_rules or "").splitlines():
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


def _merge_page_filter_defaults(raw_rules: str) -> str:
    parsed = _deserialize_page_filter_rules(raw_rules)
    existing = {pattern for _enabled, pattern in parsed}
    merged = list(parsed)
    for enabled, pattern in DEFAULT_PAGE_FILTER_RULES:
        if pattern not in existing:
            merged.append((enabled, pattern))
    return _serialize_page_filter_rules(merged)


def _enable_high_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("msjsc001.OmniClipRAG")
    except Exception:
        pass


def _resource_path(name: str) -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path.cwd()))
    else:
        base = Path(__file__).resolve().parents[1]
    return base / "resources" / name


class OmniClipDesktopApp:
    def __init__(self) -> None:
        _enable_high_dpi_awareness()
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("1560x1000")
        self.root.minsize(1320, 860)

        self.queue: queue.Queue[tuple] = queue.Queue()
        self.busy = False
        self.watch_thread: threading.Thread | None = None
        self.watch_stop: threading.Event | None = None
        self.current_hits = []
        self.current_context = ""
        self.current_report = None
        self.latest_preflight_snapshot: dict | None = None
        self.context_view_text = ""
        self.log_lines: list[str] = []
        self.tooltips: list[ToolTip] = []
        self.icon_image: tk.PhotoImage | None = None
        self.header_icon: tk.PhotoImage | None = None
        self.page_blocklist_window: tk.Toplevel | None = None
        self.sensitive_filter_window: tk.Toplevel | None = None
        self.limit_label_tooltip: ToolTip | None = None
        self.limit_entry_tooltip: ToolTip | None = None
        self.query_limit_recommendation: dict[str, object] | None = None
        self.reranker_state_label: tk.Label | None = None

        self._init_style()
        self._init_vars()
        self._load_window_icons()
        self._render_ui()
        self._load_initial_config()
        self.queue_after_id = self.root.after(120, self._drain_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def run(self) -> int:
        self.root.mainloop()
        return 0

    def _scaled_size(self, size: int, *, minimum: int = 8) -> int:
        scale = getattr(self, 'ui_scale_percent', UI_DEFAULT_SCALE_PERCENT) / UI_DEFAULT_SCALE_PERCENT
        return max(int(round(size * scale)), minimum)

    def _scaled_px(self, value: int, *, minimum: int = 0) -> int:
        scale = getattr(self, 'ui_scale_percent', UI_DEFAULT_SCALE_PERCENT) / UI_DEFAULT_SCALE_PERCENT
        return max(int(round(value * scale)), minimum)

    def _theme_colors(self, theme_name: str) -> dict[str, str]:
        return dict(DARK_THEME_COLORS if theme_name == 'dark' else LIGHT_THEME_COLORS)

    def _ui_theme_label(self, code: str) -> str:
        return self._tr(f'ui_theme_{normalize_ui_theme(code)}')

    def _ui_theme_code(self, label: str) -> str:
        mapping = {
            self._tr('ui_theme_system'): 'system',
            self._tr('ui_theme_light'): 'light',
            self._tr('ui_theme_dark'): 'dark',
            'system': 'system',
            'light': 'light',
            'dark': 'dark',
        }
        return mapping.get(str(label or '').strip(), normalize_ui_theme(getattr(self, 'ui_theme', 'system')))

    def _ui_theme_choices(self) -> list[str]:
        return [self._ui_theme_label('system'), self._ui_theme_label('light'), self._ui_theme_label('dark')]

    def _parse_ui_scale_percent(self, raw_value: str) -> int:
        stripped = str(raw_value or '').strip()
        try:
            value = int(stripped)
        except ValueError as exc:
            raise ValueError(self._tr('ui_scale_invalid')) from exc
        if value < UI_SCALE_PERCENT_MIN or value > UI_SCALE_PERCENT_MAX:
            raise ValueError(self._tr('ui_scale_invalid'))
        return value

    def _apply_visual_preferences(
        self,
        *,
        theme_code: str | None = None,
        scale_percent: int | None = None,
        rebuild_ui: bool = False,
    ) -> bool:
        requested_theme = normalize_ui_theme(theme_code if theme_code is not None else getattr(self, 'ui_theme', 'system'))
        requested_scale = normalize_ui_scale_percent(scale_percent if scale_percent is not None else getattr(self, 'ui_scale_percent', UI_DEFAULT_SCALE_PERCENT), UI_DEFAULT_SCALE_PERCENT)
        effective_theme = _detect_system_theme_mode() if requested_theme == 'system' else requested_theme
        changed = (
            requested_theme != getattr(self, 'ui_theme', 'system')
            or requested_scale != getattr(self, 'ui_scale_percent', UI_DEFAULT_SCALE_PERCENT)
            or effective_theme != getattr(self, 'effective_ui_theme', 'light')
        )
        self.ui_theme = requested_theme
        self.ui_scale_percent = requested_scale
        self.effective_ui_theme = effective_theme
        self.colors = self._theme_colors(effective_theme)
        self.root.configure(bg=self.colors['bg'])

        default_font_size = self._scaled_size(10)
        fixed_font_size = self._scaled_size(10)
        self.root.option_add('*Font', f'{{Segoe UI}} {default_font_size}')
        self.root.option_add('*TCombobox*Listbox.font', f'{{Segoe UI}} {default_font_size}')
        self.root.option_add('*TCombobox*Listbox.background', self.colors['input_bg'])
        self.root.option_add('*TCombobox*Listbox.foreground', self.colors['input_fg'])
        self.root.option_add('*TCombobox*Listbox.selectBackground', self.colors['accent'])
        self.root.option_add('*TCombobox*Listbox.selectForeground', '#FFFFFF')

        default_font = tkfont.nametofont('TkDefaultFont')
        default_font.configure(family='Segoe UI', size=default_font_size)
        text_font = tkfont.nametofont('TkTextFont')
        text_font.configure(family='Segoe UI', size=default_font_size)
        fixed_font = tkfont.nametofont('TkFixedFont')
        fixed_font.configure(family='Consolas', size=fixed_font_size)

        self.fonts = {
            'header_title': ('Segoe UI Semibold', self._scaled_size(17, minimum=12)),
            'header_subtitle': ('Segoe UI', self._scaled_size(10)),
            'guide': ('Segoe UI', self._scaled_size(10)),
            'card_title': ('Segoe UI Semibold', self._scaled_size(12, minimum=10)),
            'body': ('Segoe UI', self._scaled_size(10)),
            'small': ('Segoe UI', self._scaled_size(9)),
            'small_bold': ('Segoe UI Semibold', self._scaled_size(9)),
            'chip': ('Segoe UI Semibold', self._scaled_size(9)),
            'value': ('Segoe UI Semibold', self._scaled_size(15, minimum=11)),
        }

        self.style.theme_use('clam')
        self.style.configure(
            'Primary.TButton',
            background=self.colors['accent'],
            foreground='#FFFFFF',
            borderwidth=0,
            padding=(self._scaled_px(14, minimum=10), self._scaled_px(10, minimum=8)),
            font=('Segoe UI Semibold', self._scaled_size(10)),
        )
        self.style.map('Primary.TButton', background=[('active', self.colors['accent_dark'])])
        self.style.configure(
            'Secondary.TButton',
            background=self.colors['soft'],
            foreground=self.colors['ink'],
            bordercolor=self.colors['border'],
            padding=(self._scaled_px(14, minimum=10), self._scaled_px(10, minimum=8)),
            font=('Segoe UI', self._scaled_size(10)),
        )
        self.style.map('Secondary.TButton', background=[('active', self.colors['secondary_active'])])
        self.style.configure(
            'Danger.TButton',
            background=self.colors['danger'],
            foreground='#FFFFFF',
            borderwidth=0,
            padding=(self._scaled_px(14, minimum=10), self._scaled_px(10, minimum=8)),
            font=('Segoe UI Semibold', self._scaled_size(10)),
        )
        self.style.map('Danger.TButton', background=[('active', self.colors['danger_dark'])])
        self.style.configure(
            'Field.TEntry',
            fieldbackground=self.colors['input_bg'],
            foreground=self.colors['input_fg'],
            bordercolor=self.colors['input_border'],
            lightcolor=self.colors['input_border'],
            darkcolor=self.colors['input_border'],
            padding=self._scaled_px(7, minimum=5),
        )
        self.style.configure(
            'Query.TEntry',
            fieldbackground=self.colors['input_bg'],
            foreground=self.colors['input_fg'],
            bordercolor=self.colors['input_border'],
            lightcolor=self.colors['input_border'],
            darkcolor=self.colors['input_border'],
            padding=self._scaled_px(10, minimum=7),
        )
        self.style.configure(
            'Field.TCombobox',
            fieldbackground=self.colors['input_bg'],
            foreground=self.colors['input_fg'],
            bordercolor=self.colors['input_border'],
            lightcolor=self.colors['input_border'],
            darkcolor=self.colors['input_border'],
            padding=self._scaled_px(5, minimum=4),
        )
        self.style.map(
            'Field.TCombobox',
            fieldbackground=[('readonly', self.colors['input_bg'])],
            foreground=[('readonly', self.colors['input_fg'])],
        )
        self.style.configure('Plain.TCheckbutton', background=self.colors['card'], foreground=self.colors['ink'], font=('Segoe UI', self._scaled_size(10)))
        self.style.configure(
            'App.Treeview',
            background=self.colors['tree_bg'],
            fieldbackground=self.colors['tree_bg'],
            foreground=self.colors['ink'],
            rowheight=self._scaled_px(30, minimum=26),
            bordercolor=self.colors['border'],
            relief='flat',
        )
        self.style.map('App.Treeview', background=[('selected', self.colors['select'])], foreground=[('selected', self.colors['ink'])])
        self.style.configure(
            'App.Treeview.Heading',
            background=self.colors['tree_heading_bg'],
            foreground=self.colors['ink'],
            font=('Segoe UI Semibold', self._scaled_size(10)),
            relief='flat',
        )
        self.style.map('App.Treeview.Heading', background=[('active', self.colors['soft'])])
        self.style.configure('App.TNotebook', background=self.colors['card'], borderwidth=0)
        self.style.configure(
            'App.TNotebook.Tab',
            background=self.colors['soft'],
            foreground=self.colors['ink'],
            padding=(self._scaled_px(14, minimum=10), self._scaled_px(8, minimum=6)),
            font=('Segoe UI', self._scaled_size(10)),
        )
        self.style.map('App.TNotebook.Tab', background=[('selected', self.colors['card'])])
        self.style.configure(
            'Horizontal.TProgressbar',
            background=self.colors['accent'],
            troughcolor=self.colors['soft'],
            bordercolor=self.colors['border'],
            lightcolor=self.colors['accent'],
            darkcolor=self.colors['accent'],
        )
        self.style.configure(
            'TScrollbar',
            background=self.colors['soft'],
            troughcolor=self.colors['soft_2'],
            bordercolor=self.colors['border'],
            arrowcolor=self.colors['muted'],
        )
        if rebuild_ui and changed and hasattr(self, 'main_tabs'):
            self._render_ui()
        return changed

    def _init_style(self) -> None:
        self.ui_theme = 'system'
        self.ui_scale_percent = UI_DEFAULT_SCALE_PERCENT
        self.effective_ui_theme = 'light'
        self.style = ttk.Style(self.root)
        self._apply_visual_preferences(theme_code=self.ui_theme, scale_percent=self.ui_scale_percent, rebuild_ui=False)

    def _init_vars(self) -> None:
        self.language_code = normalize_language(None)
        self.language_var = tk.StringVar(value=language_label(self.language_code))
        self.ui_theme_var = tk.StringVar(value=self._ui_theme_label(self.ui_theme))
        self.ui_scale_var = tk.StringVar(value=str(self.ui_scale_percent))
        self.vault_var = tk.StringVar()
        self.saved_vault_var = tk.StringVar()
        self.saved_vaults: list[str] = []
        self.data_dir_var = tk.StringVar(value=str(default_data_root()))
        self.backend_var = tk.StringVar(value="lancedb")
        self.model_var = tk.StringVar(value="BAAI/bge-m3")
        self.runtime_var = tk.StringVar(value="torch")
        self.device_var = tk.StringVar(value="auto")
        self.device_var.trace_add('write', lambda *_args: self._refresh_query_limit_guidance())
        self.device_summary_var = tk.StringVar(value="")
        self.limit_var = tk.StringVar(value="15")
        self.limit_var.trace_add('write', lambda *_args: self._refresh_query_limit_guidance())
        self.score_threshold_var = tk.StringVar(value="35")
        self.interval_var = tk.StringVar(value="2.0")
        self.build_resource_profile_var = tk.StringVar(value=self._build_profile_label('balanced'))
        self.watch_resource_peak_var = tk.StringVar(value=self._watch_peak_label(15))
        self.query_var = tk.StringVar()
        self.query_status_title_var = tk.StringVar(value=self._tr("query_status_idle_title"))
        self.query_status_detail_var = tk.StringVar(value=self._tr("query_status_idle_detail"))
        self.query_status_mode = "idle"
        self.query_last_completed_at = 0.0
        self.query_last_result_count = 0
        self.query_last_copied = False
        self.context_selection_var = tk.StringVar(value="")
        self.context_toggle_var = tk.StringVar(value=self._tr("context_select_all"))
        self.result_sort_column: str | None = None
        self.result_sort_reverse = False
        self.result_page_sort_active = False
        self.result_page_sort_restore_order: list[str] = []
        self.result_page_sort_restore_column: str | None = None
        self.result_page_sort_restore_reverse = False
        self.page_sort_var = tk.StringVar(value=self._tr("page_sort_button"))
        self.local_only_var = tk.BooleanVar(value=False)
        self.rag_filter_core_var = tk.BooleanVar(value=True)
        self.reranker_enabled_var = tk.BooleanVar(value=False)
        self.reranker_model_var = tk.StringVar(value='BAAI/bge-reranker-v2-m3')
        self.reranker_batch_cpu_var = tk.StringVar(value='4')
        self.reranker_batch_cuda_var = tk.StringVar(value='8')
        self.reranker_state_var = tk.StringVar(value='')
        self.context_export_ai_collab_var = tk.BooleanVar(value=False)
        self.context_export_ai_collab_var.trace_add('write', lambda *_args: self._request_context_refresh())
        self.reranker_enabled_var.trace_add('write', lambda *_args: self._refresh_query_limit_guidance())
        self.rag_filter_extended_var = tk.BooleanVar(value=False)
        self.rag_filter_custom_rules_var = tk.StringVar(value="")
        self.page_blocklist_rules_var = tk.StringVar(value=_merge_page_filter_defaults(""))
        self.page_blocklist_summary_var = tk.StringVar(value="")
        self.context_jump_var = tk.StringVar(value="")
        self.context_jump_summary_var = tk.StringVar(value=self._tr("context_jump_summary_empty"))
        self.context_jump_options: list[dict[str, object]] = []
        self.text_search_state: dict[str, dict[str, object]] = {}
        self.force_var = tk.BooleanVar(value=False)
        self.polling_var = tk.BooleanVar(value=False)
        self.show_advanced_var = tk.BooleanVar(value=True)
        self.advanced_button_var = tk.StringVar(value=self._tr("advanced_hide"))
        self.quick_start_expanded_var = tk.BooleanVar(value=True)
        self.quick_start_button_var = tk.StringVar(value=self._tr("quick_start_hide"))
        self.clear_index_var = tk.BooleanVar(value=False)
        self.clear_logs_var = tk.BooleanVar(value=False)
        self.clear_cache_var = tk.BooleanVar(value=False)
        self.clear_exports_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value=self._tr("status_ready"))
        self.ui_layout_has_user_state = False
        self.preflight_var = tk.StringVar(value=self._tr("preflight_empty"))
        self.preflight_notice_var = tk.StringVar(value="")
        self.watch_var = tk.StringVar(value=self._default_watch_summary())
        self.files_var = tk.StringVar(value="0")
        self.chunks_var = tk.StringVar(value="0")
        self.refs_var = tk.StringVar(value="0")
        self.result_var = tk.StringVar(value=self._tr("result_empty"))
        self.query_limit_hint_var = tk.StringVar(value=self._tr("query_limit_hint_idle"))
        self.vault_state_var = tk.StringVar(value=self._tr("vault_missing"))
        self.model_state_var = tk.StringVar(value=self._tr("model_missing"))
        self.index_state_var = tk.StringVar(value=self._tr("index_missing"))
        self.current_workspace_var = tk.StringVar(value=self._tr("workspace_empty"))
        self.task_state_var = tk.StringVar(value=self._tr("task_idle"))
        self.task_detail_var = tk.StringVar(value=self._tr("task_idle_detail"))
        self.task_percent_var = tk.StringVar(value=self._tr("task_percent_idle"))
        self.task_elapsed_var = tk.StringVar(value=self._tr("task_elapsed", value="00:00"))
        self.task_eta_var = tk.StringVar(value=self._tr("task_eta_idle"))
        self.rebuild_pause_var = tk.StringVar(value=self._tr("pause_rebuild"))
        self.task_started_at = 0.0
        self.task_paused_started_at = 0.0
        self.task_paused_total_seconds = 0.0
        self.task_last_eta_text = self._tr("task_eta_idle")
        self.latest_task_progress: dict[str, object] | None = None
        self.rebuild_pause_event = threading.Event()
        self.rebuild_cancel_event = threading.Event()
        self.task_after_id: str | None = None
        self.queue_after_id: str | None = None
        self.context_after_id: str | None = None
        self.layout_after_id: str | None = None
        self.capture_after_id: str | None = None
        self.ui_interaction_after_id: str | None = None
        self.deferred_ui_after_ids: dict[str, str] = {}
        self.notebook_layout_states: dict[str, dict[str, object]] = {}
        self.canvas_sync_states: dict[str, dict[str, object]] = {}
        self.responsive_wrap_groups: dict[str, dict[str, object]] = {}
        self.ui_interaction_active = False
        self.last_root_size: tuple[int, int] = (0, 0)
        self.active_task_key: str | None = None
        self.active_task_config: AppConfig | None = None
        self.resume_prompt_workspace_id: str | None = None
        self.status_snapshot: dict[str, object] | None = None
        self.watch_stop_requested = False
        self.current_query_text = ""
        self.selected_chunk_ids: set[str] = set()
        self.device_options = get_device_options()
        self.ui_window_geometry = "1560x1000"
        self.ui_main_sash = 900
        self.ui_right_sash = 280
        self.ui_results_sash = 300
        self.ui_layout_has_user_state = False

    def _tr(self, key: str, **kwargs) -> str:
        return text(self.language_code, key, **kwargs)

    def _tip(self, key: str, **kwargs) -> str:
        return tooltip(self.language_code, key, **kwargs)

    def _build_profile_label(self, profile: str) -> str:
        normalized = normalize_build_resource_profile(profile)
        return self._tr(f'build_profile_{normalized}')

    def _build_profile_code(self, label: str) -> str:
        normalized = str(label or '').strip()
        mapping = {
            self._tr('build_profile_quiet'): 'quiet',
            self._tr('build_profile_balanced'): 'balanced',
            self._tr('build_profile_peak'): 'peak',
            'quiet': 'quiet',
            'balanced': 'balanced',
            'peak': 'peak',
        }
        return mapping.get(normalized, 'balanced')

    def _build_profile_choices(self) -> list[str]:
        return [
            self._build_profile_label('quiet'),
            self._build_profile_label('balanced'),
            self._build_profile_label('peak'),
        ]

    def _watch_peak_label(self, value: object) -> str:
        normalized = normalize_watch_resource_peak_percent(value, 15)
        return self._tr('watch_peak_option', value=normalized)

    def _watch_peak_choices(self) -> list[str]:
        return [self._watch_peak_label(value) for value in WATCH_RESOURCE_PEAK_OPTIONS]

    def _watch_peak_value(self, label: str) -> int:
        normalized = str(label or '').strip()
        for value in WATCH_RESOURCE_PEAK_OPTIONS:
            localized = self._watch_peak_label(value)
            if normalized == localized or normalized == str(value):
                return int(value)
        return normalize_watch_resource_peak_percent(normalized, 15)

    def _current_index_state(self, payload: dict[str, object] | None = None) -> str:
        source = payload if isinstance(payload, dict) else (self.status_snapshot if isinstance(self.status_snapshot, dict) else {})
        raw = str(source.get('index_state') or '').strip().lower()
        if raw in {'ready', 'missing', 'pending'}:
            return raw
        if isinstance(source.get('pending_rebuild'), dict):
            return 'pending'
        if bool(source.get('index_ready')):
            return 'ready'
        stats = source.get('stats') or {}
        return 'ready' if int(stats.get('chunks', 0) or 0) > 0 else 'missing'

    def _index_ready(self, payload: dict[str, object] | None = None) -> bool:
        return self._current_index_state(payload) == 'ready'

    def _watch_allowed(self, payload: dict[str, object] | None = None) -> bool:
        source = payload if isinstance(payload, dict) else self.status_snapshot
        if isinstance(source, dict) and 'watch_allowed' in source:
            return bool(source.get('watch_allowed'))
        return self._index_ready(payload)

    def _refresh_preflight_notice(self) -> None:
        show_notice = self.current_report is not None or self.latest_preflight_snapshot is not None
        self.preflight_notice_var.set(self._tr('preflight_success_notice_plain') if show_notice else '')
        label = getattr(self, 'preflight_notice_label', None)
        if label is not None:
            if show_notice:
                label.grid()
            else:
                label.grid_remove()

    def _open_preflight_log(self) -> None:
        if self.preflight_notice_var.get().strip():
            self._show_query_workspace(2)

    def _default_watch_summary(self) -> str:
        watch_text = self._tr("watch_ready") if WATCHDOG_AVAILABLE else self._tr("watch_fallback")
        return self._tr("vector_watch_summary", backend=self.backend_var.get().strip() or "disabled", watch_text=watch_text)

    def _watch_mode_label(self, mode: str | bool) -> str:
        if isinstance(mode, str):
            current_mode = mode.strip().lower()
            use_polling = current_mode == "polling"
        else:
            use_polling = bool(mode)
        return self._tr("watch_mode_polling") if use_polling else self._tr("watch_mode_watchdog")


    def _refresh_device_options(self) -> None:
        self.device_options = get_device_options()
        if self.device_var.get().strip() not in self.device_options:
            fallback = 'auto' if 'auto' in self.device_options else ('cpu' if 'cpu' in self.device_options else self.device_options[0])
            self.device_var.set(fallback)
        self.device_summary_var.set(self._device_capability_summary())
        device_combo = getattr(self, 'device_combo', None)
        if device_combo is not None:
            try:
                if int(device_combo.winfo_exists()):
                    device_combo.configure(values=self.device_options)
            except Exception:
                pass

    def _device_capability_summary(self) -> str:
        acceleration = detect_acceleration()
        requested = (self.device_var.get().strip() or 'cpu').lower()
        resolved = resolve_vector_device(requested)
        gpu_name = str(acceleration.get('gpu_name') or acceleration.get('cuda_name') or '').strip()
        nvcc_version = str(acceleration.get('nvcc_version') or '').strip()
        if acceleration.get('cuda_available'):
            return self._tr('device_summary_cuda_ready', gpu=gpu_name or 'NVIDIA GPU', resolved=resolved)
        if acceleration.get('gpu_present'):
            if not acceleration.get('torch_available'):
                if nvcc_version:
                    return self._tr('device_summary_gpu_runtime_missing_with_nvcc', gpu=gpu_name or 'NVIDIA GPU', cuda=nvcc_version)
                return self._tr('device_summary_gpu_runtime_missing', gpu=gpu_name or 'NVIDIA GPU')
            if not acceleration.get('sentence_transformers_available'):
                return self._tr('device_summary_gpu_runtime_incomplete', gpu=gpu_name or 'NVIDIA GPU')
            return self._tr('device_summary_gpu_detected_no_cuda', gpu=gpu_name or 'NVIDIA GPU')
        return self._tr('device_summary_cpu_only')

    def _current_task_elapsed_seconds(self) -> float:
        if not self.task_started_at:
            return 0.0
        paused_extra = 0.0
        if self.task_paused_started_at:
            paused_extra = max(time.time() - self.task_paused_started_at, 0.0)
        return max(time.time() - self.task_started_at - self.task_paused_total_seconds - paused_extra, 0.0)

    def _selected_hits(self) -> list:
        return [hit for hit in self.current_hits if hit.chunk_id in self.selected_chunk_ids]

    def _cancel_deferred_ui_callback(self, key: str) -> None:
        after_id = self.deferred_ui_after_ids.pop(key, None)
        if after_id is None:
            return
        try:
            self.root.after_cancel(after_id)
        except Exception:
            pass

    def _cancel_all_deferred_ui_callbacks(self) -> None:
        for key in list(self.deferred_ui_after_ids):
            self._cancel_deferred_ui_callback(key)

    def _cancel_window_geometry_capture(self) -> None:
        if self.capture_after_id is None:
            return
        try:
            self.root.after_cancel(self.capture_after_id)
        except Exception:
            pass
        self.capture_after_id = None

    def _cancel_ui_interaction_timer(self) -> None:
        if self.ui_interaction_after_id is None:
            return
        try:
            self.root.after_cancel(self.ui_interaction_after_id)
        except Exception:
            pass
        self.ui_interaction_after_id = None

    def _effective_ui_delay(self, key: str, delay_ms: int) -> int:
        delay = max(int(delay_ms), 0)
        if self.ui_interaction_active and (
            key.startswith('wrap-group:')
            or key.startswith('canvas-sync:')
            or key == 'window-layout:refresh'
        ):
            return max(delay, UI_WINDOW_INTERACTION_SETTLE_MS)
        return delay

    def _schedule_deferred_ui_callback(self, key: str, callback, *, delay_ms: int = UI_LAYOUT_DEFER_MS) -> None:
        self._cancel_deferred_ui_callback(key)
        if not self.root.winfo_exists():
            return

        def _run() -> None:
            self.deferred_ui_after_ids.pop(key, None)
            callback()

        self.deferred_ui_after_ids[key] = self.root.after(self._effective_ui_delay(key, delay_ms), _run)

    def _queue_window_geometry_capture(self, *, delay_ms: int = UI_WINDOW_INTERACTION_SETTLE_MS) -> None:
        self._cancel_window_geometry_capture()
        if not self.root.winfo_exists():
            return
        self.capture_after_id = self.root.after(max(int(delay_ms), 0), self._capture_window_geometry)

    def _begin_ui_interaction(self, *, settle_ms: int = UI_WINDOW_INTERACTION_SETTLE_MS) -> None:
        if not self.root.winfo_exists():
            return
        self.ui_interaction_active = True
        self._cancel_ui_interaction_timer()
        self.ui_interaction_after_id = self.root.after(max(int(settle_ms), 0), self._end_ui_interaction)

    def _end_ui_interaction(self) -> None:
        self.ui_interaction_after_id = None
        self.ui_interaction_active = False
        self._capture_window_geometry()
        self._schedule_deferred_ui_callback('window-layout:refresh', self._refresh_visible_ui_layout, delay_ms=0)

    def _widget_is_viewable(self, widget: tk.Widget | None) -> bool:
        if widget is None:
            return False
        try:
            return bool(int(widget.winfo_exists()) and int(widget.winfo_viewable()))
        except Exception:
            return False

    def _is_widget_descendant(self, widget: tk.Widget | None, ancestor: tk.Widget | None) -> bool:
        if widget is None or ancestor is None:
            return False
        current = widget
        while current is not None:
            if current is ancestor:
                return True
            try:
                parent_name = current.winfo_parent()
            except Exception:
                return False
            if not parent_name:
                return False
            try:
                current = current.nametowidget(parent_name)
            except Exception:
                return False
        return False

    def _schedule_wrap_group_refresh(self, group_key: str, *, delay_ms: int = UI_LAYOUT_DEFER_MS, force: bool = False) -> None:
        group = self.responsive_wrap_groups.get(group_key)
        if group is None:
            return
        group['force'] = bool(group.get('force')) or force
        self._schedule_deferred_ui_callback(
            str(group.get('callback_key') or f'wrap-group:{group_key}'),
            lambda key=group_key: self._apply_wrap_group(key),
            delay_ms=delay_ms,
        )

    def _apply_wrap_group(self, group_key: str) -> None:
        group = self.responsive_wrap_groups.get(group_key)
        if group is None:
            return
        parent = group.get('parent')
        try:
            if not int(parent.winfo_exists()):
                raise tk.TclError
            width = int(parent.winfo_width())
        except Exception:
            self.responsive_wrap_groups.pop(group_key, None)
            return

        force = bool(group.get('force'))
        group['force'] = False
        widgets = group.get('widgets')
        if not isinstance(widgets, dict):
            self.responsive_wrap_groups.pop(group_key, None)
            return

        for widget_key, spec in list(widgets.items()):
            widget = spec.get('widget')
            try:
                if not int(widget.winfo_exists()):
                    raise tk.TclError
            except Exception:
                widgets.pop(widget_key, None)
                continue
            if width <= 1 or (not force and not self._widget_is_viewable(widget)):
                continue

            padding = int(spec.get('padding', 24) or 24)
            min_wrap = int(spec.get('min_wrap', 220) or 220)
            max_wrap = int(spec.get('max_wrap', 980) or 980)
            available = max(0, width - self._scaled_px(padding, minimum=max(int(padding * 0.6), 8)))
            wraplength = 0 if available < self._scaled_px(min_wrap, minimum=min_wrap) else min(self._scaled_px(max_wrap, minimum=max_wrap), available)
            if spec.get('last_wraplength') != wraplength:
                try:
                    widget.configure(wraplength=wraplength)
                except Exception:
                    continue
                spec['last_wraplength'] = wraplength

        if not widgets:
            self.responsive_wrap_groups.pop(group_key, None)

    def _flush_canvas_sync(self, canvas_key: str) -> None:
        state = self.canvas_sync_states.get(canvas_key)
        if state is None:
            return
        canvas = state.get('canvas')
        inner = state.get('inner')
        try:
            if not int(canvas.winfo_exists()) or not int(inner.winfo_exists()):
                raise tk.TclError
        except Exception:
            self.canvas_sync_states.pop(canvas_key, None)
            return

        force = bool(state.get('force'))
        pending_width = bool(state.get('pending_width'))
        pending_scroll = bool(state.get('pending_scroll'))
        state['force'] = False
        state['pending_width'] = False
        state['pending_scroll'] = False
        viewable = self._widget_is_viewable(canvas) and self._widget_is_viewable(inner)

        if pending_width:
            if force or viewable:
                try:
                    width = int(canvas.winfo_width())
                except Exception:
                    width = 0
                if width > 1 and state.get('last_width') != width:
                    try:
                        canvas.itemconfigure(int(state.get('window_id')), width=width)
                    except Exception:
                        pass
                    else:
                        state['last_width'] = width
            else:
                state['pending_width'] = True

        if pending_scroll:
            if force or viewable:
                try:
                    bbox = canvas.bbox('all')
                except Exception:
                    bbox = None
                normalized_bbox = tuple(int(value) for value in bbox) if bbox else None
                if normalized_bbox and state.get('last_scrollregion') != normalized_bbox:
                    try:
                        canvas.configure(scrollregion=normalized_bbox)
                    except Exception:
                        pass
                    else:
                        state['last_scrollregion'] = normalized_bbox
            else:
                state['pending_scroll'] = True

    def _refresh_wrap_groups(self, *, root_widget: tk.Widget | None = None, force: bool = False) -> None:
        for group_key, group in list(self.responsive_wrap_groups.items()):
            parent = group.get('parent')
            if root_widget is not None and not self._is_widget_descendant(parent, root_widget):
                continue
            group['force'] = bool(group.get('force')) or force
            self._apply_wrap_group(group_key)

    def _refresh_canvas_syncs(self, *, root_widget: tk.Widget | None = None, force: bool = False) -> None:
        for canvas_key, state in list(self.canvas_sync_states.items()):
            canvas = state.get('canvas')
            if root_widget is not None and not self._is_widget_descendant(canvas, root_widget):
                continue
            state['pending_width'] = True
            state['pending_scroll'] = True
            state['force'] = bool(state.get('force')) or force
            self._flush_canvas_sync(canvas_key)

    def _refresh_visible_ui_layout(self) -> None:
        self._refresh_wrap_groups(force=False)
        self._refresh_canvas_syncs(force=False)

    def _bind_notebook_layout_refresh(self, notebook: ttk.Notebook) -> None:
        notebook_key = str(notebook)
        state = self.notebook_layout_states.get(notebook_key)
        if state is None or state.get('notebook') is not notebook:
            self.notebook_layout_states[notebook_key] = {'notebook': notebook, 'force': False}
        if not getattr(notebook, '_omniclip_layout_bound', False):
            notebook.bind('<<NotebookTabChanged>>', lambda _event, ref=notebook: self._on_notebook_tab_changed(ref), add='+')
            setattr(notebook, '_omniclip_layout_bound', True)
        self._schedule_notebook_layout_refresh(notebook, delay_ms=0, force=True)

    def _on_notebook_tab_changed(self, notebook: ttk.Notebook) -> None:
        self._begin_ui_interaction(settle_ms=max(UI_NOTEBOOK_LAYOUT_DEFER_MS * 3, 48))
        self._schedule_notebook_layout_refresh(notebook, delay_ms=0, force=True)

    def _schedule_notebook_layout_refresh(self, notebook: ttk.Notebook, *, delay_ms: int = UI_NOTEBOOK_LAYOUT_DEFER_MS, force: bool = False) -> None:
        notebook_key = str(notebook)
        state = self.notebook_layout_states.get(notebook_key)
        if state is None or state.get('notebook') is not notebook:
            state = {'notebook': notebook, 'force': False}
            self.notebook_layout_states[notebook_key] = state
        state['force'] = bool(state.get('force')) or force
        self._schedule_deferred_ui_callback(
            f'notebook-layout:{notebook_key}',
            lambda key=notebook_key: self._flush_notebook_layout(key),
            delay_ms=delay_ms,
        )

    def _flush_notebook_layout(self, notebook_key: str) -> None:
        state = self.notebook_layout_states.get(notebook_key)
        if state is None:
            return
        notebook = state.get('notebook')
        try:
            if not int(notebook.winfo_exists()):
                raise tk.TclError
            selected_tab = notebook.select()
        except Exception:
            self.notebook_layout_states.pop(notebook_key, None)
            return
        force = bool(state.get('force'))
        state['force'] = False
        if not selected_tab:
            return
        try:
            selected_widget = notebook.nametowidget(selected_tab)
        except Exception:
            return
        self._refresh_wrap_groups(root_widget=selected_widget, force=force)
        self._refresh_canvas_syncs(root_widget=selected_widget, force=force)

    def _cancel_scheduled_context_refresh(self) -> None:
        if self.context_after_id is None:
            return
        try:
            self.root.after_cancel(self.context_after_id)
        except Exception:
            pass
        self.context_after_id = None

    def _request_context_refresh(self, *, force_async: bool = False) -> None:
        if force_async or len(self.current_hits) > UI_CONTEXT_DEFER_HIT_THRESHOLD:
            self._cancel_scheduled_context_refresh()
            if self.root.winfo_exists():
                self.context_after_id = self.root.after(UI_CONTEXT_DEFER_MS, self._rebuild_context_view)
            return
        self._rebuild_context_view()

    def _restore_page_sort_order(self) -> None:
        if not self.result_page_sort_restore_order:
            return
        order = {chunk_id: index for index, chunk_id in enumerate(self.result_page_sort_restore_order)}
        fallback = len(order)
        self.current_hits.sort(
            key=lambda hit: (
                order.get(hit.chunk_id, fallback),
                self._sort_text_value(hit.title),
                self._sort_text_value(hit.anchor),
            )
        )

    def _update_page_sort_button(self) -> None:
        self.page_sort_var.set(self._tr('page_sort_restore_button') if self.result_page_sort_active else self._tr('page_sort_button'))
        if hasattr(self, 'page_sort_button'):
            if self.current_hits:
                self.page_sort_button.state(['!disabled'])
            else:
                self.page_sort_button.state(['disabled'])

    def _page_group_key(self, hit) -> tuple[str, str]:
        return (str(hit.title or ''), str(hit.source_path or ''))

    def _apply_page_sort(self) -> None:
        page_stats: dict[tuple[str, str], tuple[float, int, int]] = {}
        original_order = {hit.chunk_id: index for index, hit in enumerate(self.current_hits)}
        for index, hit in enumerate(self.current_hits):
            key = self._page_group_key(hit)
            total, count, first_index = page_stats.get(key, (0.0, 0, index))
            page_stats[key] = (total + float(hit.score), count + 1, min(first_index, index))
        ordered_pages = sorted(
            page_stats.items(),
            key=lambda item: (
                -(item[1][0] / max(item[1][1], 1)),
                item[1][2],
                self._sort_text_value(item[0][0]),
            ),
        )
        page_order = {page_key: index for index, (page_key, _stats) in enumerate(ordered_pages)}
        self.current_hits.sort(key=lambda hit: (page_order.get(self._page_group_key(hit), len(page_order)), original_order.get(hit.chunk_id, 0)))

    def _rebuild_context_view(self) -> None:
        self._cancel_scheduled_context_refresh()
        if not self.current_query_text and not self.current_hits:
            self.current_context = ''
            self.context_view_text = ''
            self._refresh_context_jump_controls()
            self._update_context_selection_summary()
            return
        self.current_context = OmniClipService.compose_context_pack_text(
            self.current_query_text,
            self._selected_hits(),
            export_mode='ai-collab' if self.context_export_ai_collab_var.get() else 'standard',
            language=self.language_code,
        )
        self.context_view_text = self.current_context
        self._refresh_context_jump_controls()
        if hasattr(self, 'context_text'):
            self._set_text(self.context_text, self.current_context or self._tr('context_empty'))
        self._update_context_selection_summary()

    def _update_context_selection_summary(self) -> None:
        selected = len(self._selected_hits())
        total = len(self.current_hits)
        if total <= 0:
            self.context_selection_var.set(self._tr('context_selection_empty'))
        else:
            self.context_selection_var.set(self._tr('context_selection_summary', selected=selected, total=total))
        self._update_context_toggle_button(selected=selected, total=total)
        self._update_page_sort_button()
        self._refresh_query_status_banner()

    def _update_context_toggle_button(self, *, selected: int | None = None, total: int | None = None) -> None:
        if selected is None or total is None:
            total = len(self.current_hits)
            selected = len(self._selected_hits())
        if total <= 0:
            self.context_toggle_var.set(self._tr('context_select_all'))
            if hasattr(self, 'context_toggle_button'):
                self.context_toggle_button.state(['disabled'])
            return
        self.context_toggle_var.set(self._tr('context_clear_all') if selected >= total else self._tr('context_select_all'))
        if hasattr(self, 'context_toggle_button'):
            self.context_toggle_button.state(['!disabled'])

    def _toggle_page_sort(self) -> None:
        if not self.current_hits:
            return
        preserve_chunk_id = self._selected_tree_chunk_id() or (self.current_hits[0].chunk_id if self.current_hits else None)
        if self.result_page_sort_active:
            self._restore_page_sort_order()
            self.result_sort_column = self.result_page_sort_restore_column
            self.result_sort_reverse = self.result_page_sort_restore_reverse
            self.result_page_sort_active = False
            self.result_page_sort_restore_order = []
            self.result_page_sort_restore_column = None
            self.result_page_sort_restore_reverse = False
        else:
            self.result_page_sort_restore_order = [hit.chunk_id for hit in self.current_hits]
            self.result_page_sort_restore_column = self.result_sort_column
            self.result_page_sort_restore_reverse = self.result_sort_reverse
            self.result_page_sort_active = True
            self.result_sort_column = None
            self.result_sort_reverse = False
            self._apply_page_sort()
        self._render_hits(selected_chunk_id=preserve_chunk_id)
        self._refresh_tree_headings()
        selected_index = self._find_hit_index(preserve_chunk_id)
        if selected_index is None and self.current_hits:
            selected_index = 0
        if selected_index is not None:
            self._show_hit(selected_index)

    def _show_query_workspace(self, detail_index: int | None = None) -> None:
        if hasattr(self, 'main_tabs'):
            try:
                self.main_tabs.select(0)
            except Exception:
                pass
        if detail_index is not None and hasattr(self, 'tabs'):
            try:
                self.tabs.select(detail_index)
            except Exception:
                pass

    def _selected_tree_chunk_id(self) -> str | None:
        if not hasattr(self, 'tree'):
            return None
        selection = self.tree.selection()
        if not selection:
            return None
        chunk_id = str(selection[0]).strip()
        if not chunk_id:
            return None
        return chunk_id if self._find_hit_index(chunk_id) is not None else None

    def _find_hit_index(self, chunk_id: str | None) -> int | None:
        if not chunk_id:
            return None
        for index, hit in enumerate(self.current_hits):
            if hit.chunk_id == chunk_id:
                return index
        return None

    def _sort_text_value(self, value: str) -> str:
        raw = str(value or '').strip().casefold()
        try:
            return locale.strxfrm(raw)
        except Exception:
            return raw

    def _hit_sort_value(self, hit, column: str):
        if column == 'include':
            return (1 if hit.chunk_id in self.selected_chunk_ids else 0, self._sort_text_value(hit.title))
        if column == 'score':
            return float(hit.score)
        if column == 'title':
            return self._sort_text_value(hit.title)
        if column == 'reason':
            return self._sort_text_value(hit.reason or self._tr('reason_fallback'))
        if column == 'anchor':
            return self._sort_text_value(hit.anchor)
        return self._sort_text_value(getattr(hit, column, ''))

    def _refresh_tree_headings(self) -> None:
        if not hasattr(self, 'tree'):
            return
        for key, title, _width in getattr(self, 'tree_columns', ()):
            heading_text = title
            if key == self.result_sort_column:
                heading_text = f"{title} {'↓' if self.result_sort_reverse else '↑'}"
            self.tree.heading(key, text=heading_text, command=lambda column=key: self._sort_hits_by(column))

    def _sort_hits_by(self, column: str) -> None:
        if not self.current_hits:
            return
        preserve_chunk_id = self._selected_tree_chunk_id() or (self.current_hits[0].chunk_id if self.current_hits else None)
        if self.result_page_sort_active:
            self._restore_page_sort_order()
            self.result_page_sort_active = False
            self.result_sort_column = None
            self.result_sort_reverse = False
            self.result_page_sort_restore_order = []
            self.result_page_sort_restore_column = None
            self.result_page_sort_restore_reverse = False
        if self.result_sort_column == column:
            self.result_sort_reverse = not self.result_sort_reverse
        else:
            self.result_sort_column = column
            self.result_sort_reverse = column in {'score', 'include'}
        self.current_hits.sort(key=lambda hit: self._hit_sort_value(hit, column), reverse=self.result_sort_reverse)
        self._render_hits(selected_chunk_id=preserve_chunk_id)
        self._refresh_tree_headings()
        selected_index = self._find_hit_index(preserve_chunk_id)
        if selected_index is None and self.current_hits:
            selected_index = 0
        if selected_index is not None:
            self._show_hit(selected_index)

    def _load_window_icons(self) -> None:
        png_path = _resource_path("app_icon.png")
        ico_path = _resource_path("app_icon.ico")
        if png_path.exists():
            self.icon_image = tk.PhotoImage(file=str(png_path))
            try:
                self.root.iconphoto(True, self.icon_image)
            except Exception:
                self.icon_image = None
            try:
                if self.icon_image is not None:
                    self.header_icon = self.icon_image.subsample(7, 7)
                else:
                    self.header_icon = None
            except Exception:
                self.header_icon = self.icon_image
        if sys.platform == "win32" and ico_path.exists():
            try:
                self.root.iconbitmap(default=str(ico_path))
            except Exception:
                pass

    def _render_ui(self) -> None:
        main_index = 0
        config_index = 0
        detail_index = 0
        if self.root.winfo_children():
            self._capture_layout_state()
        self._cancel_all_deferred_ui_callbacks()
        self._cancel_window_geometry_capture()
        self._cancel_ui_interaction_timer()
        self.ui_interaction_active = False
        self.notebook_layout_states.clear()
        self.canvas_sync_states.clear()
        self.responsive_wrap_groups.clear()

        if hasattr(self, "main_tabs"):
            try:
                main_index = self.main_tabs.index(self.main_tabs.select())
            except Exception:
                main_index = 0
        if hasattr(self, "left_tabs"):
            try:
                config_index = self.left_tabs.index(self.left_tabs.select())
            except Exception:
                config_index = 0
        if hasattr(self, "tabs"):
            try:
                detail_index = self.tabs.index(self.tabs.select())
            except Exception:
                detail_index = 0

        for child in self.root.winfo_children():
            child.destroy()
        self.tooltips.clear()

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        self._build_header()
        self._build_body()
        self._build_footer()
        self._bind_layout_tracking()
        self._apply_layout_state()

        if hasattr(self, "main_tabs"):
            try:
                self.main_tabs.select(min(main_index, max(self.main_tabs.index("end") - 1, 0)))
            except Exception:
                pass
        if hasattr(self, "left_tabs"):
            try:
                self.left_tabs.select(min(config_index, max(self.left_tabs.index("end") - 1, 0)))
            except Exception:
                pass
        if hasattr(self, "tabs"):
            try:
                self.tabs.select(min(detail_index, max(self.tabs.index("end") - 1, 0)))
            except Exception:
                pass

        self._refresh_dynamic_views()

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=self.colors["card"], highlightbackground=self.colors["border"], highlightthickness=1)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        header.grid_columnconfigure(1, weight=1)

        icon_label = tk.Label(header, image=self.header_icon, bg=self.colors["card"])
        icon_label.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(16, 10), pady=(16, 12))

        title_wrap = tk.Frame(header, bg=self.colors["card"])
        title_wrap.grid(row=0, column=1, sticky="nw", pady=(14, 0))
        title_line = tk.Frame(title_wrap, bg=self.colors["card"])
        title_line.grid(row=0, column=0, sticky="w")
        tk.Label(title_line, text=self._tr("title"), bg=self.colors["card"], fg=self.colors["ink"], font=self.fonts["header_title"]).grid(row=0, column=0, sticky="w")
        tk.Label(title_line, text=self._tr("tagline"), bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["header_subtitle"], anchor="w").grid(row=0, column=1, sticky="w", padx=(16, 0), pady=(3, 0))
        guide_label = tk.Label(title_wrap, text=self._tr("header_guide"), bg=self.colors["card"], fg=self.colors["accent_dark"], font=self.fonts["guide"], anchor="w", justify="left")
        guide_label.grid(row=1, column=0, sticky="w", pady=(12, 0))
        self._configure_responsive_wrap(guide_label, padding=12, min_wrap=240, max_wrap=880)

        controls = tk.Frame(header, bg=self.colors["card"])
        controls.grid(row=0, column=2, rowspan=2, sticky="ne", padx=16, pady=(14, 12))
        language_row = tk.Frame(controls, bg=self.colors["card"])
        language_row.grid(row=0, column=0, sticky="e")
        tk.Label(language_row, text=self._tr("language"), bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="e").grid(row=0, column=0, sticky="e", padx=(0, 8))
        language_box = ttk.Combobox(language_row, state="readonly", width=12, values=[language_label("zh-CN"), language_label("en")], textvariable=self.language_var, style="Field.TCombobox")
        language_box.grid(row=0, column=1, sticky="e")
        language_box.bind("<<ComboboxSelected>>", self._on_language_changed)
        self._attach_tooltip(language_box, "language_switch")

        version_badge = tk.Label(controls, text=self._tr("version", version=APP_VERSION), bg=self.colors["accent_soft"], fg=self.colors["accent_dark"], font=self.fonts["chip"], padx=10, pady=5)
        version_badge.grid(row=1, column=0, sticky="e", pady=(10, 0))

    def _build_body(self) -> None:
        body = tk.Frame(self.root, bg=self.colors["bg"])
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 10))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self.main_pane = None
        self.main_tabs = ttk.Notebook(body, style="App.TNotebook")
        self.main_tabs.grid(row=0, column=0, sticky="nsew")

        self.left = tk.Frame(self.main_tabs, bg=self.colors["bg"])
        self.left.grid_columnconfigure(0, weight=1)
        self.left.grid_rowconfigure(0, weight=1)

        self.right = tk.Frame(self.main_tabs, bg=self.colors["bg"])
        self.right.grid_columnconfigure(0, weight=1)
        self.right.grid_rowconfigure(0, weight=1)

        self.main_tabs.add(self.left, text=self._tr("main_tab_query"))
        self.main_tabs.add(self.right, text=self._tr("main_tab_config"))
        self._bind_notebook_layout_refresh(self.main_tabs)

        self.right_pane = ttk.Panedwindow(self.left, orient="vertical")
        self.right_pane.grid(row=0, column=0, sticky="nsew")

        self.search_host = tk.Frame(self.right_pane, bg=self.colors["bg"])
        self.search_host.grid_columnconfigure(0, weight=1)
        self.search_host.grid_rowconfigure(0, weight=1)

        self.results_host = tk.Frame(self.right_pane, bg=self.colors["bg"])
        self.results_host.grid_columnconfigure(0, weight=1)
        self.results_host.grid_rowconfigure(0, weight=1)

        self.right_pane.add(self.search_host, weight=0)
        self.right_pane.add(self.results_host, weight=1)

        self._build_right_cards()
        self._build_left_cards()

    def _build_footer(self) -> None:
        footer = tk.Frame(self.root, bg=self.colors["card"], highlightbackground=self.colors["border"], highlightthickness=1, height=34)
        footer.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))
        footer.grid_columnconfigure(0, weight=1)
        footer.grid_propagate(False)
        tk.Label(footer, textvariable=self.status_var, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w").grid(row=0, column=0, sticky="w", padx=12, pady=7)
        tk.Label(footer, textvariable=self.result_var, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="e").grid(row=0, column=1, sticky="e", padx=12, pady=7)

    def _bind_layout_tracking(self) -> None:
        self.last_root_size = (max(int(self.root.winfo_width()), 0), max(int(self.root.winfo_height()), 0))
        self.root.bind("<Configure>", self._on_root_configure)
        for pane_name in ("right_pane", "results_pane"):
            pane = getattr(self, pane_name, None)
            if pane is not None:
                pane.bind("<ButtonPress-1>", self._on_pane_interaction, add='+')
                pane.bind("<B1-Motion>", self._on_pane_interaction, add='+')
                pane.bind("<ButtonRelease-1>", self._on_pane_interaction_end, add='+')

    def _on_root_configure(self, event=None) -> None:
        if event is not None:
            try:
                if event.widget is not self.root:
                    return
            except Exception:
                return
            width = max(int(getattr(event, 'width', 0) or 0), 0)
            height = max(int(getattr(event, 'height', 0) or 0), 0)
            if width > 1 and height > 1:
                current_size = (width, height)
                if current_size != self.last_root_size:
                    self.last_root_size = current_size
                    self._begin_ui_interaction()
        self._queue_window_geometry_capture()

    def _on_pane_interaction(self, _event=None) -> None:
        self._begin_ui_interaction()

    def _on_pane_interaction_end(self, _event=None) -> None:
        self._capture_layout_state()
        self._schedule_deferred_ui_callback('layout:persist', self._persist_layout_state_snapshot, delay_ms=180)
        self._begin_ui_interaction(settle_ms=max(UI_NOTEBOOK_LAYOUT_DEFER_MS * 3, 48))

    def _capture_window_geometry(self, _event=None) -> None:
        self.capture_after_id = None
        try:
            if self.root.winfo_exists():
                self.ui_window_geometry = self.root.geometry()
        except Exception:
            pass

    def _capture_layout_state(self, _event=None) -> None:
        try:
            self.ui_window_geometry = self.root.geometry()
        except Exception:
            pass
        captured = False
        for attr_name, state_name in (("main_pane", "ui_main_sash"), ("right_pane", "ui_right_sash"), ("results_pane", "ui_results_sash")):
            pane = getattr(self, attr_name, None)
            if pane is None:
                continue
            try:
                position = int(pane.sashpos(0))
            except Exception:
                continue
            if position > 0:
                setattr(self, state_name, position)
                captured = True
        if captured:
            self.ui_layout_has_user_state = True

    def _legacy_layout_value(self, state_name: str) -> int | None:
        return {
            'ui_right_sash': 280,
            'ui_results_sash': 300,
        }.get(state_name)

    def _pane_default_position(self, pane, state_name: str, requested: int) -> int:
        total = pane.winfo_width() if str(pane.cget('orient')) == 'horizontal' else pane.winfo_height()
        if total <= 1:
            return max(int(requested or 1), 1)
        if state_name == 'ui_right_sash':
            preferred = max(int(getattr(self, 'search_host', pane).winfo_reqheight()), self._scaled_px(250, minimum=220))
            return min(preferred, max(total - self._scaled_px(180, minimum=160), self._scaled_px(220, minimum=180)))
        if state_name == 'ui_results_sash':
            preferred = int(round(total * 0.48))
            preferred = max(preferred, self._scaled_px(150, minimum=120))
            return preferred
        return int(requested or 1)

    def _resolve_layout_position(self, pane, state_name: str, requested: int, *, min_first: int, min_second: int) -> int:
        desired = int(requested or 0)
        if desired <= 0:
            desired = self._pane_default_position(pane, state_name, requested)
        elif desired == self._legacy_layout_value(state_name):
            desired = self._pane_default_position(pane, state_name, requested)
        return self._clamp_sash_position(pane, desired, min_first=min_first, min_second=min_second)

    def _persist_layout_state_snapshot(self) -> None:
        try:
            active_vault = normalize_vault_path(self.vault_var.get().strip())
            paths = ensure_data_paths(self.data_dir_var.get().strip() or str(default_data_root()), active_vault or None)
            config = load_config(paths)
            if config is None:
                config = AppConfig(
                    vault_path=active_vault,
                    vault_paths=self._collect_vault_paths(active_vault),
                    data_root=str(paths.global_root),
                )
            else:
                config.vault_path = active_vault
                config.vault_paths = self._collect_vault_paths(active_vault)
                config.data_root = str(paths.global_root)
            self._sync_runtime_layout_to_config(config)
            save_config(config, paths)
        except Exception:
            pass

    def _apply_layout_state(self) -> None:
        geometry = (self.ui_window_geometry or '').strip()
        if geometry:
            try:
                self.root.geometry(geometry)
            except Exception:
                pass

        pane_specs = (
            ("main_pane", "ui_main_sash", 760, 420),
            ("right_pane", "ui_right_sash", 220, 140),
            ("results_pane", "ui_results_sash", 96, 40),
        )

        def restore(attempt: int = 0) -> None:
            self.layout_after_id = None
            pending = False
            needs_followup = False
            for attr_name, state_name, min_first, min_second in pane_specs:
                pane = getattr(self, attr_name, None)
                position = getattr(self, state_name, 0)
                if pane is None:
                    continue
                if pane.winfo_width() <= 1 and pane.winfo_height() <= 1:
                    pending = True
                    continue
                try:
                    target = self._resolve_layout_position(pane, state_name, int(position), min_first=min_first, min_second=min_second)
                    pane.sashpos(0, target)
                    if attr_name in {'right_pane', 'results_pane'}:
                        try:
                            self.root.update_idletasks()
                        except Exception:
                            pass
                    try:
                        current = int(pane.sashpos(0))
                    except Exception:
                        current = target
                    if abs(current - target) > 2:
                        needs_followup = True
                except Exception:
                    pending = True
            if (pending or needs_followup) and attempt < 14 and self.root.winfo_exists():
                delay = 80 if pending else 120
                self.layout_after_id = self.root.after(delay, lambda: restore(attempt + 1))

        if self.layout_after_id is not None:
            try:
                self.root.after_cancel(self.layout_after_id)
            except Exception:
                pass
            self.layout_after_id = None
        self.layout_after_id = self.root.after(0, restore)
        self._schedule_deferred_ui_callback('layout:settle-restore', lambda: restore(0), delay_ms=320)
        self._schedule_deferred_ui_callback('layout:late-restore', lambda: restore(0), delay_ms=680)
        self._schedule_deferred_ui_callback('layout:final-restore', lambda: restore(0), delay_ms=1040)

    def _sync_runtime_layout_to_config(self, config: AppConfig) -> None:
        self._capture_layout_state()
        config.ui_window_geometry = self.ui_window_geometry
        config.ui_main_sash = int(self.ui_main_sash)
        config.ui_right_sash = int(self.ui_right_sash)
        config.ui_results_sash = int(self.ui_results_sash)

    def _coerce_layout_value(self, value, fallback: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return fallback
        return parsed if parsed > 0 else fallback

    def _clamp_sash_position(self, pane, requested: int, *, min_first: int, min_second: int) -> int:
        try:
            orient = str(pane.cget("orient"))
        except Exception:
            orient = "vertical"
        total = pane.winfo_width() if orient == "horizontal" else pane.winfo_height()
        if total <= 1:
            return max(1, requested)
        floor_first = self._scaled_px(64, minimum=56)
        floor_second = self._scaled_px(40, minimum=36)
        safe_first = max(floor_first, min(int(min_first), max(total - floor_second, floor_first)))
        # Let Tk decide the real upper bound. Its internal sash math can legitimately
        # exceed a naive total-minus-second-pane clamp during late layout settlement.
        return max(safe_first, int(requested))

    def _card(self, parent: tk.Widget, title: str, subtitle: str, row: int, *, pady: tuple[int, int] = (0, 0)) -> tk.Frame:
        card = tk.Frame(parent, bg=self.colors["card"], highlightbackground=self.colors["border"], highlightthickness=1)
        card.grid(row=row, column=0, sticky="ew", pady=pady)
        card.grid_columnconfigure(1, weight=1)
        header = tk.Frame(card, bg=self.colors["card"])
        header.grid(row=0, column=0, sticky="w", padx=16, pady=(14, 10))
        tk.Label(header, text=title, bg=self.colors["card"], fg=self.colors["ink"], font=self.fonts["card_title"], anchor="w").grid(row=0, column=0, sticky="w")
        tk.Label(header, text=subtitle, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w", justify="left", wraplength=760).grid(row=0, column=1, sticky="w", padx=(10, 0), pady=(1, 0))
        return card

    def _panel(self, parent: tk.Widget, row: int, *, column: int = 0, columnspan: int = 1, pady: tuple[int, int] = (0, 0)) -> tk.Frame:
        panel = tk.Frame(parent, bg=self.colors["soft_2"], highlightbackground=self.colors["border"], highlightthickness=1)
        panel.grid(row=row, column=column, columnspan=columnspan, sticky="nsew", pady=pady)
        panel.grid_columnconfigure(0, weight=1)
        return panel

    def _bind_mousewheel(self, canvas: tk.Canvas, target: tk.Widget) -> None:
        def _on_mousewheel(event):
            delta = event.delta
            if sys.platform == "darwin":
                delta = -1 * delta
            step = -1 if delta > 0 else 1
            canvas.yview_scroll(step, "units")

        def _bind(_event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind(_event):
            canvas.unbind_all("<MouseWheel>")

        target.bind("<Enter>", _bind)
        target.bind("<Leave>", _unbind)

    def _configure_canvas_window_sync(self, canvas: tk.Canvas, inner: tk.Widget, window_id: int, *, key_prefix: str) -> None:
        state = self.canvas_sync_states.get(key_prefix)
        if state is None or state.get('canvas') is not canvas:
            state = {
                'canvas': canvas,
                'inner': inner,
                'window_id': window_id,
                'pending_width': False,
                'pending_scroll': False,
                'force': False,
                'last_width': None,
                'last_scrollregion': None,
            }
            self.canvas_sync_states[key_prefix] = state

        def _request(*, sync_width: bool = False, sync_scroll: bool = False, delay_ms: int = UI_LAYOUT_DEFER_MS, force: bool = False) -> None:
            current = self.canvas_sync_states.get(key_prefix)
            if current is None:
                return
            if sync_width:
                current['pending_width'] = True
            if sync_scroll:
                current['pending_scroll'] = True
            current['force'] = bool(current.get('force')) or force
            self._schedule_deferred_ui_callback(
                f'canvas-sync:{key_prefix}',
                lambda key=key_prefix: self._flush_canvas_sync(key),
                delay_ms=delay_ms,
            )

        inner.bind('<Configure>', lambda _event: _request(sync_scroll=True), add='+')
        canvas.bind('<Configure>', lambda _event: _request(sync_width=True, sync_scroll=True), add='+')
        canvas.bind('<Map>', lambda _event: _request(sync_width=True, sync_scroll=True, delay_ms=0, force=True), add='+')
        _request(sync_width=True, sync_scroll=True, delay_ms=0, force=True)

    def _make_scrollable_tab(self, notebook: ttk.Notebook) -> tuple[tk.Frame, tk.Frame]:
        outer = tk.Frame(notebook, bg=self.colors["card"])
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(0, weight=1)

        canvas = tk.Canvas(outer, bg=self.colors["card"], highlightthickness=0, borderwidth=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)

        inner = tk.Frame(canvas, bg=self.colors["card"])
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        self._configure_canvas_window_sync(canvas, inner, window_id, key_prefix=f'scroll-tab:{str(canvas)}')
        self._bind_mousewheel(canvas, outer)
        return outer, inner

    def _build_left_cards(self) -> None:
        shell = tk.Frame(self.right, bg=self.colors["card"], highlightbackground=self.colors["border"], highlightthickness=1)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_rowconfigure(1, weight=1)

        shell_header = tk.Frame(shell, bg=self.colors["card"])
        shell_header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 10))
        shell_header.grid_columnconfigure(0, weight=1)
        title_row = tk.Frame(shell_header, bg=self.colors["card"])
        title_row.grid(row=0, column=0, sticky="ew")
        title_row.grid_columnconfigure(0, weight=1)
        tk.Label(title_row, text=self._tr("workspace_title"), bg=self.colors["card"], fg=self.colors["ink"], font=self.fonts["card_title"], anchor="w").grid(row=0, column=0, sticky="w")
        help_button = ttk.Button(title_row, text=self._tr("help_updates"), style="Secondary.TButton", command=self._open_help_and_updates)
        help_button.grid(row=0, column=1, sticky="e")
        self._attach_tooltip(help_button, "help_updates")
        subtitle_label = tk.Label(shell_header, text=self._tr("workspace_subtitle"), bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w", justify="left")
        subtitle_label.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self._configure_responsive_wrap(subtitle_label, padding=12, min_wrap=260, max_wrap=820)

        self.left_tabs = ttk.Notebook(shell, style="App.TNotebook")
        self.left_tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        start_tab, start_body = self._make_scrollable_tab(self.left_tabs)
        settings_tab, settings_body = self._make_scrollable_tab(self.left_tabs)
        ui_tab, ui_body = self._make_scrollable_tab(self.left_tabs)
        retrieval_tab, retrieval_body = self._make_scrollable_tab(self.left_tabs)
        data_tab, data_body = self._make_scrollable_tab(self.left_tabs)
        self.left_tabs.add(start_tab, text=self._tr("left_tab_start"))
        self.left_tabs.add(settings_tab, text=self._tr("left_tab_settings"))
        self.left_tabs.add(ui_tab, text=self._tr("left_tab_ui"))
        self.left_tabs.add(retrieval_tab, text=self._tr("left_tab_retrieval"))
        self.left_tabs.add(data_tab, text=self._tr("left_tab_data"))
        self._bind_notebook_layout_refresh(self.left_tabs)

        self._build_quick_start_card(start_body)
        self._build_settings_card(settings_body)
        self._build_ui_card(ui_body)
        self._build_retrieval_card(retrieval_body)
        self._build_data_card(data_body)

    def _build_quick_start_card(self, parent: tk.Widget) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_columnconfigure(1, weight=1)

        guide_panel = self._panel(parent, 0, columnspan=2)
        guide_panel.grid_columnconfigure(0, weight=1)
        quick_start_subtitle = tk.Label(
            guide_panel,
            text=self._tr("quick_start_subtitle"),
            bg=self.colors["soft_2"],
            fg=self.colors["muted"],
            font=self.fonts["small"],
            anchor="w",
            justify="left",
        )
        quick_start_subtitle.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        self._configure_responsive_wrap(quick_start_subtitle, padding=20, min_wrap=260, max_wrap=760)
        toggle_row = tk.Frame(guide_panel, bg=self.colors["soft_2"])
        toggle_row.grid(row=1, column=0, sticky="ew", padx=16)
        toggle_row.grid_columnconfigure(0, weight=1)
        self.quick_start_button = ttk.Button(
            toggle_row,
            textvariable=self.quick_start_button_var,
            style="Secondary.TButton",
            command=self._toggle_quick_start,
        )
        self.quick_start_button.grid(row=0, column=0, sticky="w")
        self._attach_tooltip(self.quick_start_button, "quick_start_toggle")

        self.quick_start_steps = tk.Frame(guide_panel, bg=self.colors["soft_2"])
        self.quick_start_steps.grid(row=2, column=0, sticky="ew", padx=16, pady=(12, 0))
        for index, key in enumerate(("step_1", "step_2", "step_3")):
            badge = tk.Label(self.quick_start_steps, text=str(index + 1), bg=self.colors["accent_soft"], fg=self.colors["accent_dark"], font=self.fonts["chip"], width=2, pady=3)
            badge.grid(row=index, column=0, sticky="nw")
            tk.Label(
                self.quick_start_steps,
                text=self._tr(key),
                bg=self.colors["soft_2"],
                fg=self.colors["ink"],
                font=self.fonts["body"],
                anchor="w",
                justify="left",
                wraplength=self._scaled_px(360, minimum=240),
            ).grid(row=index, column=1, sticky="w", padx=(8, 0), pady=(0 if index == 0 else 8, 0))

        chips = tk.Frame(guide_panel, bg=self.colors["soft_2"])
        chips.grid(row=3, column=0, sticky="ew", padx=16, pady=(14, 14))
        chips.grid_columnconfigure((0, 1, 2), weight=1)
        self.vault_chip = self._chip(chips, self.vault_state_var, 0)
        self.model_chip = self._chip(chips, self.model_state_var, 1)
        self.index_chip = self._chip(chips, self.index_state_var, 2)
        self._refresh_quick_start_visibility()

        paths_panel = self._panel(parent, 1, columnspan=2, pady=(12, 0))
        form = tk.Frame(paths_panel, bg=self.colors["soft_2"])
        form.grid(row=0, column=0, sticky="ew", padx=16, pady=14)
        form.grid_columnconfigure(1, weight=1)

        saved_caption = tk.Label(form, text=self._tr("saved_vaults_label"), bg=self.colors["soft_2"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        saved_caption.grid(row=0, column=0, sticky="w", pady=(0, 0))
        self._attach_tooltip(saved_caption, "saved_vaults")
        saved_row = tk.Frame(form, bg=self.colors["soft_2"])
        saved_row.grid(row=0, column=1, sticky="ew")
        saved_row.grid_columnconfigure(0, weight=1)
        self.vault_switch = ttk.Combobox(saved_row, textvariable=self.saved_vault_var, values=self.saved_vaults, state="readonly", style="Field.TCombobox")
        self.vault_switch.grid(row=0, column=0, sticky="ew")
        self.vault_switch.bind("<<ComboboxSelected>>", self._on_saved_vault_selected)
        self._attach_tooltip(self.vault_switch, "saved_vaults")
        remove_button = ttk.Button(saved_row, text=self._tr("remove_saved_vault"), style="Secondary.TButton", command=self._remove_selected_vault)
        remove_button.grid(row=0, column=1, padx=(8, 0))
        self._attach_tooltip(remove_button, "remove_saved_vault")

        self._path_row(form, 1, self._tr("vault_label"), self.vault_var, self._choose_vault, tooltip_key="vault", browse_tooltip_key="browse_vault")
        self._path_row(form, 2, self._tr("data_dir_label"), self.data_dir_var, self._choose_data_dir, tooltip_key="data_dir", browse_tooltip_key="browse_data")

        action_panel = self._panel(parent, 2, column=0, pady=(12, 0))
        actions = tk.Frame(action_panel, bg=self.colors["soft_2"])
        actions.grid(row=0, column=0, sticky="ew", padx=16, pady=14)
        for column in range(2):
            actions.grid_columnconfigure(column, weight=1)
        preflight_button = ttk.Button(actions, text=self._tr("preflight_button"), style="Secondary.TButton", command=self._estimate)
        preflight_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._attach_tooltip(preflight_button, "preflight")
        bootstrap_button = ttk.Button(actions, text=self._tr("bootstrap_button"), style="Secondary.TButton", command=self._bootstrap)
        bootstrap_button.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self._attach_tooltip(bootstrap_button, "bootstrap")
        rebuild_button = ttk.Button(actions, text=self._tr("rebuild_button"), style="Primary.TButton", command=self._rebuild)
        rebuild_button.grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(10, 0))
        self._attach_tooltip(rebuild_button, "rebuild")
        self.watch_button = ttk.Button(actions, text=self._tr("watch_start"), style="Primary.TButton", command=self._toggle_watch)
        self.watch_button.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(10, 0))
        self._attach_tooltip(self.watch_button, "watch")

        status_panel = self._panel(parent, 2, column=1, pady=(12, 0))
        stats = tk.Frame(status_panel, bg=self.colors["soft_2"])
        stats.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
        for index, (label, variable) in enumerate(((self._tr("stat_files"), self.files_var), (self._tr("stat_chunks"), self.chunks_var), (self._tr("stat_refs"), self.refs_var))):
            stats.grid_columnconfigure(index, weight=1)
            self._stat_box(stats, label, variable, index)
        preflight_label = tk.Label(status_panel, textvariable=self.preflight_var, bg=self.colors["soft_2"], fg=self.colors["muted"], font=self.fonts["small"], justify="left", anchor="w")
        preflight_label.grid(row=1, column=0, sticky="ew", padx=16, pady=(14, 4))
        self._configure_responsive_wrap(preflight_label, padding=20, min_wrap=220, max_wrap=520)
        self.preflight_notice_label = tk.Label(status_panel, textvariable=self.preflight_notice_var, bg=self.colors["soft_2"], fg=self.colors["accent_dark"], font=self.fonts["small_bold"], justify="left", anchor="w", cursor="hand2")
        self.preflight_notice_label.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 4))
        self.preflight_notice_label.bind("<Button-1>", lambda _event: self._open_preflight_log())
        self._configure_responsive_wrap(self.preflight_notice_label, padding=20, min_wrap=220, max_wrap=520)
        watch_label = tk.Label(status_panel, textvariable=self.watch_var, bg=self.colors["soft_2"], fg=self.colors["muted"], font=self.fonts["small"], justify="left", anchor="w")
        watch_label.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 8))
        self._configure_responsive_wrap(watch_label, padding=20, min_wrap=220, max_wrap=520)

        task_panel = tk.Frame(status_panel, bg=self.colors["soft_2"], highlightbackground=self.colors["border"], highlightthickness=1)
        task_panel.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 14))
        task_panel.grid_columnconfigure(0, weight=1)
        tk.Label(task_panel, text=self._tr("task_panel_title"), bg=self.colors["soft_2"], fg=self.colors["ink"], font=self.fonts["small"], anchor="w").grid(row=0, column=0, sticky="w", padx=12, pady=(10, 6))
        self.task_progress = ttk.Progressbar(task_panel, mode="indeterminate")
        self.task_progress.grid(row=1, column=0, sticky="ew", padx=12)
        button_row = tk.Frame(task_panel, bg=self.colors["soft_2"])
        button_row.grid(row=2, column=0, sticky="ew", padx=12, pady=(8, 0))
        button_row.grid_columnconfigure(0, weight=1)
        button_row.grid_columnconfigure(1, weight=1)
        self.rebuild_pause_button = ttk.Button(button_row, textvariable=self.rebuild_pause_var, style="Secondary.TButton", command=self._toggle_rebuild_pause)
        self.rebuild_pause_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.rebuild_pause_button.grid_remove()
        self._attach_tooltip(self.rebuild_pause_button, "pause_rebuild")
        self.rebuild_cancel_button = ttk.Button(button_row, text=self._tr("cancel_rebuild"), style="Danger.TButton", command=self._cancel_rebuild)
        self.rebuild_cancel_button.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.rebuild_cancel_button.grid_remove()
        self._attach_tooltip(self.rebuild_cancel_button, "cancel_rebuild")
        tk.Label(task_panel, textvariable=self.task_state_var, bg=self.colors["soft_2"], fg=self.colors["ink"], font=self.fonts["small"], anchor="w").grid(row=3, column=0, sticky="w", padx=12, pady=(8, 0))
        tk.Label(task_panel, textvariable=self.task_percent_var, bg=self.colors["soft_2"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w").grid(row=4, column=0, sticky="w", padx=12, pady=(4, 0))
        tk.Label(task_panel, textvariable=self.task_elapsed_var, bg=self.colors["soft_2"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w").grid(row=5, column=0, sticky="w", padx=12, pady=(4, 0))
        tk.Label(task_panel, textvariable=self.task_eta_var, bg=self.colors["soft_2"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w", justify="left", wraplength=220).grid(row=6, column=0, sticky="w", padx=12, pady=(4, 0))
        tk.Label(task_panel, textvariable=self.task_detail_var, bg=self.colors["soft_2"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w", justify="left", wraplength=220).grid(row=7, column=0, sticky="w", padx=12, pady=(4, 10))
    def _build_settings_card(self, parent: tk.Widget) -> None:
        parent.grid_columnconfigure(0, weight=1)
        self._refresh_device_options()

        form_panel = self._panel(parent, 0)
        settings_subtitle = tk.Label(form_panel, text=self._tr("settings_subtitle"), bg=self.colors["soft_2"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w", justify="left")
        settings_subtitle.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        self._configure_responsive_wrap(settings_subtitle, padding=20, min_wrap=260, max_wrap=760)
        device_summary_label = tk.Label(form_panel, textvariable=self.device_summary_var, bg=self.colors["soft_2"], fg=self.colors["accent_dark"], font=self.fonts["small"], anchor="w", justify="left")
        device_summary_label.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))
        self._configure_responsive_wrap(device_summary_label, padding=20, min_wrap=260, max_wrap=760)
        form = tk.Frame(form_panel, bg=self.colors["soft_2"])
        form.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 14))
        form.grid_columnconfigure(1, weight=1)
        form.grid_columnconfigure(3, weight=1)
        self._combo_row(form, 0, self._tr("backend_label"), self.backend_var, ["lancedb", "disabled"], tooltip_key="backend")
        self._entry_row(form, 1, self._tr("model_label"), self.model_var, tooltip_key="model")
        self.runtime_combo, self.device_combo = self._combo_pair_row(
            form,
            2,
            self._tr("runtime_label"),
            self.runtime_var,
            ["torch", "onnx"],
            self._tr("device_label"),
            self.device_var,
            self.device_options,
            left_tip="runtime",
            right_tip="device",
        )
        self.device_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_device_options())
        self._entry_row(form, 3, self._tr("interval_label"), self.interval_var, tooltip_key="interval")
        self.build_profile_combo = self._combo_row(form, 4, self._tr("build_resource_profile_label"), self.build_resource_profile_var, self._build_profile_choices(), tooltip_key="build_resource_profile")
        self.watch_peak_combo = self._combo_row(form, 5, self._tr("watch_resource_peak_label"), self.watch_resource_peak_var, self._watch_peak_choices(), tooltip_key="watch_resource_peak")

        action_panel = self._panel(parent, 1, pady=(12, 0))
        action_row = tk.Frame(action_panel, bg=self.colors["soft_2"])
        action_row.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
        for index in range(3):
            action_row.grid_columnconfigure(index, weight=1)
        recommended_button = ttk.Button(action_row, text=self._tr("apply_recommended"), style="Secondary.TButton", command=self._apply_recommended)
        recommended_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._attach_tooltip(recommended_button, "recommended")
        load_button = ttk.Button(action_row, text=self._tr("load_config"), style="Secondary.TButton", command=self._load_config_from_current_dir)
        load_button.grid(row=0, column=1, sticky="ew", padx=6)
        self._attach_tooltip(load_button, "load_config")
        save_button = ttk.Button(action_row, text=self._tr("save_config"), style="Primary.TButton", command=self._save_only)
        save_button.grid(row=0, column=2, sticky="ew", padx=(6, 0))
        self._attach_tooltip(save_button, "save_config")

        toggle_button = ttk.Button(action_panel, textvariable=self.advanced_button_var, style="Secondary.TButton", command=self._toggle_advanced)
        toggle_button.grid(row=1, column=0, sticky="ew", padx=16, pady=(12, 0))

        self.advanced_panel = tk.Frame(action_panel, bg=self.colors["soft_2"])
        self.advanced_panel.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 0))
        local_only = ttk.Checkbutton(self.advanced_panel, text=self._tr("local_only_label"), variable=self.local_only_var, style="Plain.TCheckbutton")
        local_only.grid(row=0, column=0, sticky="w")
        self._attach_tooltip(local_only, "local_only")
        force = ttk.Checkbutton(self.advanced_panel, text=self._tr("force_label"), variable=self.force_var, style="Plain.TCheckbutton")
        force.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._attach_tooltip(force, "force")
        polling = ttk.Checkbutton(self.advanced_panel, text=self._tr("polling_label"), variable=self.polling_var, style="Plain.TCheckbutton")
        polling.grid(row=2, column=0, sticky="w", pady=(6, 0))
        self._attach_tooltip(polling, "polling")
        self._refresh_advanced_visibility()

        refresh_button = ttk.Button(action_panel, text=self._tr("refresh_button"), style="Secondary.TButton", command=self._refresh)
        refresh_button.grid(row=3, column=0, sticky="ew", padx=16, pady=(14, 14))

    def _build_ui_card(self, parent: tk.Widget) -> None:
        parent.grid_columnconfigure(0, weight=1)

        ui_panel = self._panel(parent, 0)
        ui_subtitle = tk.Label(ui_panel, text=self._tr("ui_subtitle"), bg=self.colors["soft_2"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w", justify="left")
        ui_subtitle.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        self._configure_responsive_wrap(ui_subtitle, padding=20, min_wrap=260, max_wrap=760)

        form = tk.Frame(ui_panel, bg=self.colors["soft_2"])
        form.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        form.grid_columnconfigure(1, weight=1)

        scale_label = tk.Label(form, text=self._tr("ui_scale_label"), bg=self.colors["soft_2"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        scale_label.grid(row=0, column=0, sticky="w")
        self._attach_tooltip(scale_label, "ui_scale")
        self.ui_scale_entry = ttk.Entry(form, textvariable=self.ui_scale_var, style="Field.TEntry")
        self.ui_scale_entry.grid(row=0, column=1, sticky="ew")
        self._attach_tooltip(self.ui_scale_entry, "ui_scale")
        self.ui_scale_entry.bind("<Return>", lambda _event: self._apply_ui_preferences())

        theme_label = tk.Label(form, text=self._tr("ui_theme_label"), bg=self.colors["soft_2"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        theme_label.grid(row=1, column=0, sticky="w", pady=(10, 0))
        self._attach_tooltip(theme_label, "ui_theme")
        self.ui_theme_combo = ttk.Combobox(form, textvariable=self.ui_theme_var, values=self._ui_theme_choices(), state="readonly", style="Field.TCombobox")
        self.ui_theme_combo.grid(row=1, column=1, sticky="ew", pady=(10, 0))
        self._attach_tooltip(self.ui_theme_combo, "ui_theme")

        scale_hint = tk.Label(ui_panel, text=self._tr("ui_scale_hint"), bg=self.colors["soft_2"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w", justify="left")
        scale_hint.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        self._configure_responsive_wrap(scale_hint, padding=20, min_wrap=260, max_wrap=760)

        action_row = tk.Frame(ui_panel, bg=self.colors["soft_2"])
        action_row.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 14))
        action_row.grid_columnconfigure(0, weight=1)
        apply_button = ttk.Button(action_row, text=self._tr("apply_ui_button"), style="Primary.TButton", command=self._apply_ui_preferences)
        apply_button.grid(row=0, column=1, sticky="e")
        self._attach_tooltip(apply_button, "apply_ui")

    def _build_retrieval_card(self, parent: tk.Widget) -> None:
        parent.grid_columnconfigure(0, weight=1)

        summary_panel = self._panel(parent, 0)
        retrieval_subtitle = tk.Label(summary_panel, text=self._tr("retrieval_subtitle"), bg=self.colors["soft_2"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w", justify="left")
        retrieval_subtitle.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        self._configure_responsive_wrap(retrieval_subtitle, padding=20, min_wrap=260, max_wrap=760)
        self.reranker_state_label = tk.Label(summary_panel, textvariable=self.reranker_state_var, bg=self.colors["soft_2"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w", justify="left")
        self.reranker_state_label.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 14))
        self._attach_tooltip(self.reranker_state_label, "bootstrap_reranker")

        options_panel = self._panel(parent, 1, pady=(12, 0))
        options = tk.Frame(options_panel, bg=self.colors["soft_2"])
        options.grid(row=0, column=0, sticky="ew", padx=16, pady=14)
        options.grid_columnconfigure(1, weight=1)
        options.grid_columnconfigure(3, weight=1)

        reranker_check = ttk.Checkbutton(options, text=self._tr("reranker_enable_label"), variable=self.reranker_enabled_var, style="Plain.TCheckbutton")
        reranker_check.grid(row=0, column=0, columnspan=4, sticky="w")
        self._attach_tooltip(reranker_check, "reranker_enable")

        export_mode_check = ttk.Checkbutton(options, text=self._tr("export_ai_collab_label"), variable=self.context_export_ai_collab_var, style="Plain.TCheckbutton")
        export_mode_check.grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        self._attach_tooltip(export_mode_check, "export_ai_collab")

        self._entry_row(options, 2, self._tr("reranker_model_label"), self.reranker_model_var, tooltip_key="reranker_model")
        self._entry_pair_row(options, 3, self._tr("reranker_batch_cpu_label"), self.reranker_batch_cpu_var, self._tr("reranker_batch_cuda_label"), self.reranker_batch_cuda_var, left_tip="reranker_batch_cpu", right_tip="reranker_batch_cuda")

        action_panel = self._panel(parent, 2, pady=(12, 0))
        actions = tk.Frame(action_panel, bg=self.colors["soft_2"])
        actions.grid(row=0, column=0, sticky="ew", padx=16, pady=14)
        actions.grid_columnconfigure(0, weight=1)
        actions.grid_columnconfigure(1, weight=1)
        reranker_button = ttk.Button(actions, text=self._tr("bootstrap_reranker_button"), style="Primary.TButton", command=self._bootstrap_reranker)
        reranker_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._attach_tooltip(reranker_button, "bootstrap_reranker")
        refresh_button = ttk.Button(actions, text=self._tr("refresh_button"), style="Secondary.TButton", command=self._refresh)
        refresh_button.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self._attach_tooltip(refresh_button, "refresh")

        self._refresh_reranker_state_summary()
    def _build_data_card(self, parent: tk.Widget) -> None:
        parent.grid_columnconfigure(0, weight=1)

        button_panel = self._panel(parent, 0)
        data_subtitle = tk.Label(button_panel, text=self._tr("data_subtitle"), bg=self.colors["soft_2"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w", justify="left")
        data_subtitle.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        self._configure_responsive_wrap(data_subtitle, padding=20, min_wrap=260, max_wrap=760)
        workspace_label = tk.Label(button_panel, textvariable=self.current_workspace_var, bg=self.colors["soft_2"], fg=self.colors["accent_dark"], font=self.fonts["small"], anchor="w", justify="left")
        workspace_label.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))
        self._configure_responsive_wrap(workspace_label, padding=20, min_wrap=260, max_wrap=760)
        buttons = tk.Frame(button_panel, bg=self.colors["soft_2"])
        buttons.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 14))
        buttons.grid_columnconfigure((0, 1), weight=1)
        open_vault = ttk.Button(buttons, text=self._tr("open_vault"), style="Secondary.TButton", command=self._open_vault)
        open_vault.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._attach_tooltip(open_vault, "open_vault")
        open_data = ttk.Button(buttons, text=self._tr("open_data"), style="Secondary.TButton", command=self._open_data)
        open_data.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self._attach_tooltip(open_data, "open_data")
        open_exports = ttk.Button(buttons, text=self._tr("open_exports"), style="Secondary.TButton", command=self._open_exports)
        open_exports.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self._attach_tooltip(open_exports, "open_exports")

        cleanup_panel = self._panel(parent, 1, pady=(12, 0))
        checks = tk.Frame(cleanup_panel, bg=self.colors["soft_2"])
        checks.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
        ttk.Checkbutton(checks, text=self._tr("clear_index_label"), variable=self.clear_index_var, style="Plain.TCheckbutton").grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(checks, text=self._tr("clear_logs_label"), variable=self.clear_logs_var, style="Plain.TCheckbutton").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Checkbutton(checks, text=self._tr("clear_cache_label"), variable=self.clear_cache_var, style="Plain.TCheckbutton").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Checkbutton(checks, text=self._tr("clear_exports_label"), variable=self.clear_exports_var, style="Plain.TCheckbutton").grid(row=3, column=0, sticky="w", pady=(6, 0))
        clear_button = ttk.Button(cleanup_panel, text=self._tr("clear_button"), style="Danger.TButton", command=self._clear)
        clear_button.grid(row=1, column=0, sticky="ew", padx=16, pady=(14, 14))
        self._attach_tooltip(clear_button, "clear")

    def _build_right_cards(self) -> None:
        self.search_host.grid_columnconfigure(0, weight=1)
        self.results_host.grid_columnconfigure(0, weight=1)
        self.results_host.grid_rowconfigure(0, weight=1)

        search_card = self._card(self.search_host, self._tr("search_title"), self._tr("search_subtitle"), 0)
        self.query_status_shell = tk.Frame(search_card, bg=self.colors["query_idle_bg"], highlightbackground=self.colors["query_idle_border"], highlightthickness=1)
        self.query_status_shell.grid(row=0, column=1, sticky="ne", padx=(12, 16), pady=(12, 10))
        self.query_status_shell.grid_columnconfigure(0, weight=1)
        self.query_status_title_label = tk.Label(self.query_status_shell, textvariable=self.query_status_title_var, bg=self.colors["query_idle_bg"], fg=self.colors["query_idle_fg"], font=self.fonts["chip"], anchor="w")
        self.query_status_title_label.grid(row=0, column=0, sticky="w", padx=14, pady=(10, 3))
        self.query_status_detail_label = tk.Label(self.query_status_shell, textvariable=self.query_status_detail_var, bg=self.colors["query_idle_bg"], fg=self.colors["query_idle_fg"], font=self.fonts["small"], anchor="w", justify="left")
        self.query_status_detail_label.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))
        self._configure_responsive_wrap(self.query_status_detail_label, padding=28, min_wrap=220, max_wrap=320)
        self._apply_query_status_style()
        self._refresh_query_status_banner()

        tk.Label(search_card, text=self._tr("query_hint"), bg=self.colors["card"], fg=self.colors["accent_dark"], font=self.fonts["small"], anchor="w").grid(row=1, column=0, columnspan=2, sticky="w", padx=16)
        query_row = tk.Frame(search_card, bg=self.colors["card"])
        query_row.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(10, 6))
        query_row.grid_columnconfigure(0, weight=1)
        entry = ttk.Entry(query_row, textvariable=self.query_var, style="Query.TEntry")
        entry.grid(row=0, column=0, sticky="ew")
        entry.bind("<Return>", lambda _event: self._query(False))
        self._attach_tooltip(entry, "query")
        search_button = ttk.Button(query_row, text=self._tr("search_button"), style="Secondary.TButton", command=lambda: self._query(False))
        search_button.grid(row=0, column=1, padx=(10, 0))
        self._attach_tooltip(search_button, "search")
        search_copy_button = ttk.Button(query_row, text=self._tr("search_copy_button"), style="Primary.TButton", command=lambda: self._query(True))
        search_copy_button.grid(row=0, column=2, padx=(10, 0))
        self._attach_tooltip(search_copy_button, "search_copy")
        copy_context_button = ttk.Button(query_row, text=self._tr("copy_context_button"), style="Secondary.TButton", command=self._copy_context)
        copy_context_button.grid(row=0, column=3, padx=(10, 0))
        self._attach_tooltip(copy_context_button, "copy_context")

        query_meta = tk.Frame(search_card, bg=self.colors["card"])
        query_meta.grid(row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 14))
        query_meta.grid_columnconfigure(4, weight=1)
        threshold_label = tk.Label(query_meta, text=self._tr("score_threshold_label"), bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        threshold_label.grid(row=0, column=0, sticky="w")
        self._attach_tooltip(threshold_label, "score_threshold")
        threshold_entry = ttk.Entry(query_meta, textvariable=self.score_threshold_var, width=8, style="Field.TEntry")
        threshold_entry.grid(row=0, column=1, sticky="w", padx=(8, 14))
        self._attach_tooltip(threshold_entry, "score_threshold")
        limit_label = tk.Label(query_meta, text=self._tr("limit_label"), bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        limit_label.grid(row=0, column=2, sticky="w")
        self.limit_label_tooltip = self._attach_tooltip(limit_label, "limit")
        limit_entry = ttk.Entry(query_meta, textvariable=self.limit_var, width=8, style="Field.TEntry")
        limit_entry.grid(row=0, column=3, sticky="w", padx=(8, 14))
        self.limit_entry_tooltip = self._attach_tooltip(limit_entry, "limit")
        tk.Label(query_meta, textvariable=self.query_limit_hint_var, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w", justify="left").grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))

        result_card = self._card(self.results_host, self._tr("results_title"), self._tr("results_subtitle"), 0)
        result_card.grid_rowconfigure(2, weight=1)
        result_card.grid_columnconfigure(1, weight=1)

        filter_buttons = tk.Frame(result_card, bg=self.colors["card"])
        filter_buttons.grid(row=0, column=1, sticky="e", padx=(12, 16), pady=(10, 10))
        block_button = ttk.Button(filter_buttons, text=self._tr("page_blocklist_button"), style="Secondary.TButton", command=self._open_page_blocklist_window)
        block_button.grid(row=0, column=0, sticky="e")
        self._attach_tooltip(block_button, "page_blocklist")
        sensitive_button = ttk.Button(filter_buttons, text=self._tr("sensitive_filter_button"), style="Secondary.TButton", command=self._open_sensitive_filter_window)
        sensitive_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self._attach_tooltip(sensitive_button, "sensitive_filter")

        result_toolbar = tk.Frame(result_card, bg=self.colors["card"])
        result_toolbar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 10))
        result_toolbar.grid_columnconfigure(3, weight=1)
        self.context_toggle_button = ttk.Button(result_toolbar, textvariable=self.context_toggle_var, style="Secondary.TButton", command=self._toggle_all_hit_selection)
        self.context_toggle_button.grid(row=0, column=0, sticky="w")
        self._attach_tooltip(self.context_toggle_button, "context_select_toggle")
        self.page_sort_button = ttk.Button(result_toolbar, textvariable=self.page_sort_var, style="Secondary.TButton", command=self._toggle_page_sort)
        self.page_sort_button.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self._attach_tooltip(self.page_sort_button, "page_sort")
        tk.Label(result_toolbar, textvariable=self.page_blocklist_summary_var, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w").grid(row=0, column=2, sticky="w", padx=(12, 18))
        tk.Label(result_toolbar, textvariable=self.context_selection_var, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w").grid(row=0, column=3, sticky="w")

        self.results_pane = ttk.Panedwindow(result_card, orient="vertical")
        self.results_pane.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=16, pady=(0, 14))

        table_frame = tk.Frame(self.results_pane, bg=self.colors["card"])
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table_frame, columns=("include", "title", "reason", "anchor", "score"), show="headings", style="App.Treeview")
        self.tree_columns = (
            ("include", self._tr("col_include"), 100),
            ("title", self._tr("col_page"), 220),
            ("reason", self._tr("col_reason"), 220),
            ("anchor", self._tr("col_anchor"), 320),
            ("score", self._tr("col_score"), 90),
        )
        for key, title, width in self.tree_columns:
            self.tree.column(key, width=width, anchor="center" if key in {"include", "score"} else "w")
        self._refresh_tree_headings()
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self._select_hit)
        self.tree.bind("<Button-1>", self._on_result_tree_click)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        tabs_host = tk.Frame(self.results_pane, bg=self.colors["card"])
        tabs_host.grid_columnconfigure(0, weight=1)
        tabs_host.grid_rowconfigure(0, weight=1)
        self.tabs = ttk.Notebook(tabs_host, style="App.TNotebook")
        self.tabs.grid(row=0, column=0, sticky="nsew")

        preview_tab = tk.Frame(self.tabs, bg=self.colors["card"])
        context_tab = tk.Frame(self.tabs, bg=self.colors["card"])
        log_tab = tk.Frame(self.tabs, bg=self.colors["card"])
        self.tabs.add(preview_tab, text=self._tr("tab_preview"))
        self.tabs.add(context_tab, text=self._tr("tab_context"))
        self.tabs.add(log_tab, text=self._tr("tab_log"))
        self._bind_notebook_layout_refresh(self.tabs)
        self.preview_text = self._text(preview_tab, "preview")
        self.context_text = self._text(context_tab, "context", top_builder=self._build_context_jump_controls)
        self.log_text = self._text(log_tab, "log")

        self.results_pane.add(table_frame, weight=1)
        self.results_pane.add(tabs_host, weight=1)
    def _chip(self, parent: tk.Widget, variable: tk.StringVar, column: int) -> tk.Label:
        label = tk.Label(parent, textvariable=variable, bg=self.colors["chip_neutral_bg"], fg=self.colors["chip_neutral_fg"], font=self.fonts["chip"], padx=10, pady=6)
        label.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
        return label

    def _stat_box(self, parent: tk.Widget, label: str, variable: tk.StringVar, column: int) -> None:
        box = tk.Frame(parent, bg=self.colors["soft_2"], highlightbackground=self.colors["border"], highlightthickness=1)
        box.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
        tk.Label(box, text=label, bg=self.colors["soft_2"], fg=self.colors["muted"], font=self.fonts["small"]).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 2))
        tk.Label(box, textvariable=variable, bg=self.colors["soft_2"], fg=self.colors["ink"], font=self.fonts["value"]).grid(row=1, column=0, sticky="w", padx=12, pady=(0, 10))

    def _path_row(self, parent: tk.Widget, row: int, label: str, variable: tk.StringVar, browse_cmd, *, tooltip_key: str, browse_tooltip_key: str) -> None:
        background = str(parent.cget("bg"))
        caption = tk.Label(parent, text=label, bg=background, fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        caption.grid(row=row, column=0, sticky="w", pady=(8, 0))
        self._attach_tooltip(caption, tooltip_key)
        frame = tk.Frame(parent, bg=background)
        frame.grid(row=row, column=1, sticky="ew", pady=(8, 0))
        frame.grid_columnconfigure(0, weight=1)
        entry = ttk.Entry(frame, textvariable=variable, style="Field.TEntry")
        entry.grid(row=0, column=0, sticky="ew")
        self._attach_tooltip(entry, tooltip_key)
        button = ttk.Button(frame, text=self._tr("browse"), style="Secondary.TButton", command=browse_cmd)
        button.grid(row=0, column=1, padx=(8, 0))
        self._attach_tooltip(button, browse_tooltip_key)

    def _entry_row(self, parent: tk.Widget, row: int, label: str, variable: tk.StringVar, *, tooltip_key: str) -> None:
        background = str(parent.cget("bg"))
        caption = tk.Label(parent, text=label, bg=background, fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        caption.grid(row=row, column=0, sticky="w", pady=(8, 0))
        self._attach_tooltip(caption, tooltip_key)
        entry = ttk.Entry(parent, textvariable=variable, style="Field.TEntry")
        entry.grid(row=row, column=1, columnspan=3, sticky="ew", pady=(8, 0))
        self._attach_tooltip(entry, tooltip_key)

    def _combo_row(self, parent: tk.Widget, row: int, label: str, variable: tk.StringVar, values: list[str], *, tooltip_key: str):
        background = str(parent.cget("bg"))
        caption = tk.Label(parent, text=label, bg=background, fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        caption.grid(row=row, column=0, sticky="w", pady=(8, 0))
        self._attach_tooltip(caption, tooltip_key)
        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", style="Field.TCombobox")
        combo.grid(row=row, column=1, columnspan=3, sticky="ew", pady=(8, 0))
        self._attach_tooltip(combo, tooltip_key)
        return combo

    def _combo_pair_row(self, parent: tk.Widget, row: int, left_label: str, left_var: tk.StringVar, left_values: list[str], right_label: str, right_var: tk.StringVar, right_values: list[str], *, left_tip: str, right_tip: str):
        background = str(parent.cget("bg"))
        left_caption = tk.Label(parent, text=left_label, bg=background, fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        left_caption.grid(row=row, column=0, sticky="w", pady=(8, 0))
        self._attach_tooltip(left_caption, left_tip)
        left_combo = ttk.Combobox(parent, textvariable=left_var, values=left_values, state="readonly", style="Field.TCombobox")
        left_combo.grid(row=row, column=1, sticky="ew", pady=(8, 0))
        self._attach_tooltip(left_combo, left_tip)

        right_caption = tk.Label(parent, text=right_label, bg=background, fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        right_caption.grid(row=row, column=2, sticky="w", padx=(14, 0), pady=(8, 0))
        self._attach_tooltip(right_caption, right_tip)
        right_combo = ttk.Combobox(parent, textvariable=right_var, values=right_values, state="readonly", style="Field.TCombobox")
        right_combo.grid(row=row, column=3, sticky="ew", pady=(8, 0))
        self._attach_tooltip(right_combo, right_tip)
        return left_combo, right_combo

    def _entry_pair_row(self, parent: tk.Widget, row: int, left_label: str, left_var: tk.StringVar, right_label: str, right_var: tk.StringVar, *, left_tip: str, right_tip: str) -> None:
        background = str(parent.cget("bg"))
        left_caption = tk.Label(parent, text=left_label, bg=background, fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        left_caption.grid(row=row, column=0, sticky="w", pady=(8, 0))
        self._attach_tooltip(left_caption, left_tip)
        left_entry = ttk.Entry(parent, textvariable=left_var, style="Field.TEntry")
        left_entry.grid(row=row, column=1, sticky="ew", pady=(8, 0))
        self._attach_tooltip(left_entry, left_tip)

        right_caption = tk.Label(parent, text=right_label, bg=background, fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        right_caption.grid(row=row, column=2, sticky="w", padx=(14, 0), pady=(8, 0))
        self._attach_tooltip(right_caption, right_tip)
        right_entry = ttk.Entry(parent, textvariable=right_var, style="Field.TEntry")
        right_entry.grid(row=row, column=3, sticky="ew", pady=(8, 0))
        self._attach_tooltip(right_entry, right_tip)

    def _text(self, parent: tk.Widget, panel_key: str, *, top_builder=None) -> tk.Text:
        frame = tk.Frame(parent, bg=self.colors["card"])
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)

        text_row = 0
        if top_builder is not None:
            header = tk.Frame(frame, bg=self.colors["card"])
            header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(12, 0))
            header.grid_columnconfigure(0, weight=1)
            top_builder(header)
            text_row = 1

        frame.grid_rowconfigure(text_row, weight=1)
        text_widget = tk.Text(frame, wrap="word", relief="flat", borderwidth=0, highlightthickness=0, background=self.colors["input_bg"], foreground=self.colors["input_fg"], insertbackground=self.colors["input_fg"], font=self.fonts["body"], padx=self._scaled_px(14, minimum=10), pady=self._scaled_px(12, minimum=9))
        text_widget.grid(row=text_row, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text_widget.yview)
        scroll.grid(row=text_row, column=1, sticky="ns")
        text_widget.configure(yscrollcommand=scroll.set, state="disabled")

        search_var = tk.StringVar(value="")
        search_status_var = tk.StringVar(value=self._tr("text_search_empty"))
        footer = tk.Frame(frame, bg=self.colors["card"])
        footer.grid(row=text_row + 1, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 12))
        footer.grid_columnconfigure(0, weight=1)
        status_label = tk.Label(footer, textvariable=search_status_var, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="e")
        status_label.grid(row=0, column=1, sticky="e", padx=(0, 10))
        search_entry = ttk.Entry(footer, textvariable=search_var, width=26, style="Field.TEntry")
        search_entry.grid(row=0, column=2, sticky="e")
        search_entry.bind("<Return>", lambda _event, key=panel_key: self._find_in_text_panel(key))
        search_button = ttk.Button(footer, text=self._tr("text_search_button"), style="Secondary.TButton", command=lambda key=panel_key: self._find_in_text_panel(key))
        search_button.grid(row=0, column=3, sticky="e", padx=(8, 0))
        next_button = ttk.Button(footer, text=self._tr("text_search_next"), style="Secondary.TButton", command=lambda key=panel_key: self._find_in_text_panel(key, advance=True))
        next_button.grid(row=0, column=4, sticky="e", padx=(8, 0))

        self._attach_tooltip(search_entry, "text_search_entry")
        self._attach_tooltip(search_button, "text_search_button")
        self._attach_tooltip(next_button, "text_search_next")

        self.text_search_state[panel_key] = {
            "text": text_widget,
            "query_var": search_var,
            "status_var": search_status_var,
            "last_query": "",
            "matches": [],
            "index": -1,
        }
        return text_widget

    def _find_in_text_panel(self, panel_key: str, *, advance: bool = False) -> None:
        state = self.text_search_state.get(panel_key)
        if not state:
            return
        widget = state["text"]
        query_var = state["query_var"]
        status_var = state["status_var"]
        query = str(query_var.get()).strip()

        widget.configure(state="normal")
        widget.tag_configure("search_match", background=self.colors["accent_soft"], foreground=self.colors["ink"])
        widget.tag_configure("search_current", background=self.colors["search_current_bg"], foreground=self.colors["ink"])
        widget.tag_remove("search_match", "1.0", "end")
        widget.tag_remove("search_current", "1.0", "end")

        if not query:
            state["last_query"] = ""
            state["matches"] = []
            state["index"] = -1
            status_var.set(self._tr("text_search_empty"))
            widget.configure(state="disabled")
            return

        matches = state["matches"] if advance and state.get("last_query") == query else []
        if not matches:
            matches = []
            start = "1.0"
            while True:
                found = widget.search(query, start, stopindex="end", nocase=True)
                if not found:
                    break
                end = f"{found}+{len(query)}c"
                widget.tag_add("search_match", found, end)
                matches.append((found, end))
                start = end
            state["matches"] = matches
            state["last_query"] = query
            state["index"] = 0 if matches else -1
        elif matches:
            for found, end in matches:
                widget.tag_add("search_match", found, end)
            state["index"] = (int(state.get("index", -1)) + 1) % len(matches)

        if matches:
            current_index = int(state.get("index", 0))
            found, end = matches[current_index]
            widget.tag_add("search_current", found, end)
            widget.mark_set("insert", found)
            widget.see(found)
            status_var.set(self._tr("text_search_status", index=current_index + 1, total=len(matches)))
        else:
            status_var.set(self._tr("text_search_none"))

        widget.configure(state="disabled")

    def _refresh_text_search_state(self, widget: tk.Text) -> None:
        for panel_key, state in self.text_search_state.items():
            if state.get("text") is not widget:
                continue
            state["last_query"] = ""
            state["matches"] = []
            state["index"] = -1
            query = str(state["query_var"].get()).strip()
            if query:
                self._find_in_text_panel(panel_key)
            else:
                state["status_var"].set(self._tr("text_search_empty"))
            return

    def _build_context_jump_controls(self, parent: tk.Widget) -> None:
        jump_wrap = tk.Frame(parent, bg=self.colors["card"])
        jump_wrap.grid(row=0, column=1, sticky="ne")
        self.context_jump_combo = ttk.Combobox(jump_wrap, textvariable=self.context_jump_var, state="readonly", width=42, style="Field.TCombobox")
        self.context_jump_combo.grid(row=0, column=0, sticky="e")
        self.context_jump_combo.bind("<<ComboboxSelected>>", self._jump_to_context_page)
        self._attach_tooltip(self.context_jump_combo, "context_jump")
        tk.Label(jump_wrap, textvariable=self.context_jump_summary_var, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="e").grid(row=1, column=0, sticky="e", pady=(6, 0))
        self._refresh_context_jump_controls()

    def _collect_context_sections(self, context_text: str) -> list[dict[str, object]]:
        sections: list[dict[str, object]] = []
        current: dict[str, object] | None = None
        for line_no, line in enumerate((context_text or "").splitlines(), start=1):
            page_match = _CONTEXT_PAGE_RE.match(line.strip())
            if page_match:
                if current is not None:
                    sections.append(current)
                current = {"title": page_match.group(1).strip(), "line": line_no, "fragments": 0}
                continue
            if current is not None and _CONTEXT_FRAGMENT_RE.match(line.strip()):
                current["fragments"] = int(current["fragments"]) + 1
        if current is not None:
            sections.append(current)

        seen_counts: dict[str, int] = {}
        for section in sections:
            title = str(section.get("title") or self._tr("none_value"))
            fragments = int(section.get("fragments") or 0)
            base = self._tr("context_jump_item", title=title, count=fragments)
            seen_counts[base] = seen_counts.get(base, 0) + 1
            display = base if seen_counts[base] == 1 else f"{base} [{seen_counts[base]}]"
            section["display"] = display
        return sections

    def _refresh_context_jump_controls(self) -> None:
        self.context_jump_options = self._collect_context_sections(self.current_context)
        total_notes = len(self.context_jump_options)
        total_fragments = sum(int(section.get("fragments") or 0) for section in self.context_jump_options)
        self.context_jump_summary_var.set(self._tr("context_jump_summary", notes=total_notes, fragments=total_fragments) if total_notes else self._tr("context_jump_summary_empty"))
        values = [str(section.get("display") or "") for section in self.context_jump_options]
        if hasattr(self, "context_jump_combo"):
            self.context_jump_combo.configure(values=values)
        current_value = self.context_jump_var.get().strip()
        if current_value not in values:
            self.context_jump_var.set(values[0] if values else "")

    def _jump_to_context_page(self, _event=None) -> None:
        if not hasattr(self, "context_text"):
            return
        selected = self.context_jump_var.get().strip()
        if not selected:
            return
        for section in self.context_jump_options:
            if str(section.get("display")) != selected:
                continue
            line_no = int(section.get("line") or 1)
            index = f"{line_no}.0"
            self.context_text.see(index)
            self.context_text.mark_set("insert", index)
            return

    def _configure_responsive_wrap(self, widget: tk.Widget, *, padding: int = 24, min_wrap: int = 220, max_wrap: int = 980) -> None:
        try:
            parent = widget.nametowidget(widget.winfo_parent())
        except Exception:
            return

        group_key = str(parent)
        group = self.responsive_wrap_groups.get(group_key)
        if group is None or group.get('parent') is not parent:
            group = {
                'parent': parent,
                'callback_key': f'wrap-group:{group_key}',
                'widgets': {},
                'force': False,
                'bound': False,
            }
            self.responsive_wrap_groups[group_key] = group

        widgets = group.setdefault('widgets', {})
        widgets[str(widget)] = {
            'widget': widget,
            'padding': padding,
            'min_wrap': min_wrap,
            'max_wrap': max_wrap,
            'last_wraplength': None,
        }

        if not group.get('bound'):
            parent.bind("<Configure>", lambda _event, key=group_key: self._schedule_wrap_group_refresh(key), add="+")
            parent.bind("<Map>", lambda _event, key=group_key: self._schedule_wrap_group_refresh(key, delay_ms=0, force=True), add="+")
            group['bound'] = True
        self._schedule_wrap_group_refresh(group_key, delay_ms=0, force=True)

    def _attach_tooltip(self, widget: tk.Widget, key: str, **kwargs) -> ToolTip | None:
        tip_text = self._tip(key, **kwargs)
        if not tip_text:
            return None
        tip = ToolTip(widget, tip_text)
        self.tooltips.append(tip)
        return tip

    def _format_elapsed_ms(self, elapsed_ms: int) -> str:
        value = max(int(elapsed_ms or 0), 0)
        if value <= 0:
            return self._tr('query_limit_elapsed_unknown')
        if value < 1000:
            return self._tr('query_limit_elapsed_ms', value=value)
        return self._tr('query_limit_elapsed_s', value=f"{value / 1000:.1f}")

    def _query_limit_device_label(self, device: str) -> str:
        return self._tr('query_limit_device_cuda') if str(device or '').strip().lower() == 'cuda' else self._tr('query_limit_device_cpu')

    def _query_limit_reason_label(self, reason_code: str) -> str:
        normalized = str(reason_code or 'baseline').strip().lower()
        return self._tr(f'query_limit_reason_{normalized}')

    def _render_query_limit_hint(self, recommendation: dict[str, object] | None) -> str:
        if not recommendation:
            return self._tr('query_limit_hint_idle')
        minimum = int(recommendation.get('minimum', 0) or 0)
        maximum = int(recommendation.get('maximum', 0) or 0)
        preferred = int(recommendation.get('preferred', 0) or 0)
        if minimum <= 0 or maximum <= 0 or preferred <= 0:
            return self._tr('query_limit_hint_idle')
        return self._tr(
            'query_limit_hint_ready',
            current=self.limit_var.get().strip() or '0',
            minimum=minimum,
            maximum=maximum,
            preferred=preferred,
            device=self._query_limit_device_label(str(recommendation.get('device', 'cpu'))),
            elapsed=self._format_elapsed_ms(int(recommendation.get('elapsed_ms', 0) or 0)),
            samples=int(recommendation.get('samples', 0) or 0),
            reason=self._query_limit_reason_label(str(recommendation.get('reason_code', 'baseline'))),
        )

    def _refresh_query_limit_guidance(self) -> None:
        hint = self._render_query_limit_hint(self.query_limit_recommendation)
        self.query_limit_hint_var.set(hint)
        tooltip_text = self._tip('limit')
        if self.query_limit_recommendation:
            tooltip_text = f"{tooltip_text}\n\n{hint}".strip()
        for attr_name in ('limit_label_tooltip', 'limit_entry_tooltip'):
            tip = getattr(self, attr_name, None)
            if tip is not None:
                tip.text = tooltip_text

    def _update_query_limit_guidance(self, recommendation: dict[str, object] | None) -> None:
        self.query_limit_recommendation = recommendation or None
        self._refresh_query_limit_guidance()

    def _query_stage_label(self, payload: dict[str, object] | None) -> str:
        stage_code = str((payload or {}).get('stage_status') or 'prepare').strip().lower() or 'prepare'
        key = f'query_stage_{stage_code}'
        try:
            return self._tr(key)
        except KeyError:
            return stage_code

    def _query_progress_detail(self, payload: dict[str, object] | None) -> str:
        stage = self._query_stage_label(payload)
        data = payload or {}
        current = max(0, int(data.get('candidates') or data.get('hits') or 0))
        total = max(0, int(data.get('limit') or 0))
        if current > 0 and total > 0:
            return self._tr('query_status_running_detail_counts', stage=stage, current=current, total=total)
        return self._tr('query_status_running_detail', stage=stage)

    def _apply_query_status_style(self, mode: str | None = None) -> None:
        if not hasattr(self, 'query_status_shell'):
            return
        current_mode = mode or getattr(self, 'query_status_mode', 'idle')
        prefix = {
            'idle': 'query_idle',
            'blocked': 'query_blocked',
            'running': 'query_running',
            'done': 'query_done',
        }.get(current_mode, 'query_idle')
        bg = self.colors[f'{prefix}_bg']
        fg = self.colors[f'{prefix}_fg']
        border = self.colors[f'{prefix}_border']
        self.query_status_shell.configure(bg=bg, highlightbackground=border)
        self.query_status_title_label.configure(bg=bg, fg=fg)
        self.query_status_detail_label.configure(bg=bg, fg=fg)

    def _set_query_status(self, mode: str, title: str, detail: str) -> None:
        self.query_status_mode = mode
        self.query_status_title_var.set(title)
        self.query_status_detail_var.set(detail)
        self._apply_query_status_style(mode)

    def _refresh_query_status_banner(self) -> None:
        if self.busy and self.active_task_key == 'search_button':
            payload = self.latest_task_progress if isinstance(self.latest_task_progress, dict) else {}
            percent = float((payload or {}).get('overall_percent') or 0.0)
            self._set_query_status(
                'running',
                self._tr('query_status_running_title', percent=percent),
                self._query_progress_detail(payload),
            )
            return
        if self.watch_thread and self.watch_thread.is_alive():
            self._set_query_status(
                'blocked',
                self._tr('query_status_blocked_title'),
                self._tr('query_status_blocked_detail_watch'),
            )
            return
        if not self._index_ready():
            self._set_query_status(
                'blocked',
                self._tr('query_status_blocked_title'),
                self._tr('query_status_blocked_detail_index'),
            )
            return
        if self.busy and self.active_task_key:
            self._set_query_status(
                'blocked',
                self._tr('query_status_blocked_title'),
                self._tr('query_status_blocked_detail_task', task=self._tr(self.active_task_key)),
            )
            return
        if self.query_last_completed_at > 0:
            title_key = 'query_status_done_title_copied' if self.query_last_copied else 'query_status_done_title'
            completed_at = time.strftime('%H:%M', time.localtime(self.query_last_completed_at))
            self._set_query_status(
                'done',
                self._tr(title_key, time=completed_at),
                self._tr('query_status_done_detail', count=self.query_last_result_count),
            )
            return
        self._set_query_status(
            'idle',
            self._tr('query_status_idle_title'),
            self._tr('query_status_idle_detail'),
        )

    def _refresh_quick_start_visibility(self) -> None:
        expanded = self.quick_start_expanded_var.get()
        self.quick_start_button_var.set(self._tr('quick_start_hide') if expanded else self._tr('quick_start_show'))
        panel = getattr(self, 'quick_start_steps', None)
        if panel is None:
            return
        if expanded:
            panel.grid()
        else:
            panel.grid_remove()

    def _refresh_advanced_visibility(self) -> None:
        expanded = self.show_advanced_var.get()
        self.advanced_button_var.set(self._tr('advanced_hide') if expanded else self._tr('advanced_show'))
        panel = getattr(self, 'advanced_panel', None)
        if panel is None:
            return
        if expanded:
            panel.grid()
        else:
            panel.grid_remove()

    def _apply_ui_preferences_from_controls(self, *, rebuild_ui: bool, persist: bool = False) -> tuple[str, int]:
        theme_code = self._ui_theme_code(self.ui_theme_var.get())
        scale_percent = self._parse_ui_scale_percent(self.ui_scale_var.get())
        self.ui_theme_var.set(self._ui_theme_label(theme_code))
        self.ui_scale_var.set(str(scale_percent))
        self._apply_visual_preferences(theme_code=theme_code, scale_percent=scale_percent, rebuild_ui=rebuild_ui)
        if persist:
            active_vault = self.vault_var.get().strip() or None
            paths = ensure_data_paths(self.data_dir_var.get().strip() or str(default_data_root()), active_vault)
            config = load_config(paths)
            if config is None:
                config = AppConfig(vault_path=normalize_vault_path(active_vault or ''), data_root=str(paths.global_root))
            config.ui_language = self.language_code
            config.ui_theme = theme_code
            config.ui_scale_percent = scale_percent
            save_config(config, paths)
        return theme_code, scale_percent

    def _apply_ui_preferences(self) -> None:
        try:
            self._apply_ui_preferences_from_controls(rebuild_ui=True, persist=True)
        except Exception as exc:
            messagebox.showerror(self._tr('cannot_start_title'), str(exc), parent=self.root)
            return
        self.status_var.set(self._tr('status_ui_applied'))
        self._append_log(self._tr('status_ui_applied'))
        self._refresh_query_status_banner()

    def _collect_vault_paths(self, active_vault: str = "") -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for raw_value in [active_vault, *self.saved_vaults]:
            normalized = normalize_vault_path(raw_value)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
        return ordered

    def _set_saved_vaults(self, vaults: list[str], active_vault: str = "") -> None:
        ordered: list[str] = []
        seen: set[str] = set()
        for raw_value in ([active_vault] if active_vault else []) + list(vaults):
            normalized = normalize_vault_path(raw_value)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
        self.saved_vaults = ordered
        if hasattr(self, "vault_switch"):
            self.vault_switch.configure(values=self.saved_vaults)
        if active_vault:
            self.saved_vault_var.set(normalize_vault_path(active_vault))
        elif self.saved_vaults:
            if self.saved_vault_var.get().strip() not in self.saved_vaults:
                self.saved_vault_var.set(self.saved_vaults[0])
        else:
            self.saved_vault_var.set("")
        self._refresh_workspace_summary()

    def _reset_workspace_views(self) -> None:
        self.current_hits = []
        self.current_query_text = ""
        self.selected_chunk_ids.clear()
        self.result_sort_column = None
        self.result_sort_reverse = False
        self.result_page_sort_active = False
        self.result_page_sort_restore_order = []
        self.result_page_sort_restore_column = None
        self.result_page_sort_restore_reverse = False
        self._cancel_scheduled_context_refresh()
        self.current_context = ""
        self.current_report = None
        self.latest_preflight_snapshot = None
        self.context_view_text = ""
        self.context_jump_var.set("")
        self.context_jump_options = []
        self.context_jump_summary_var.set(self._tr("context_jump_summary_empty"))
        self.resume_prompt_workspace_id = None
        self.query_last_completed_at = 0.0
        self.query_last_result_count = 0
        self.query_last_copied = False
        self.files_var.set("0")
        self.chunks_var.set("0")
        self.refs_var.set("0")
        self.preflight_var.set(self._tr("preflight_empty"))
        self.result_var.set(self._tr("result_empty"))
        self._update_context_selection_summary()
        self._refresh_query_status_banner()

    def _refresh_workspace_summary(self) -> None:
        vault = normalize_vault_path(self.vault_var.get().strip())
        data_root = self.data_dir_var.get().strip() or str(default_data_root())
        if not vault:
            self.current_workspace_var.set(self._tr("workspace_empty"))
            return
        try:
            paths = ensure_data_paths(data_root, vault)
            self.current_workspace_var.set(
                self._tr(
                    "workspace_current",
                    vault=Path(vault).name or vault,
                    workspace=paths.root,
                    shared=paths.shared_root,
                )
            )
        except OSError:
            self.current_workspace_var.set(self._tr("workspace_pending", vault=Path(vault).name or vault))

    def _activate_vault(self, vault: str, *, refresh_status: bool = True) -> None:
        normalized = normalize_vault_path(vault)
        if not normalized:
            return
        self.vault_var.set(normalized)
        self._set_saved_vaults(self.saved_vaults + [normalized], active_vault=normalized)
        self._reset_workspace_views()
        self._refresh_state_chips()
        if refresh_status and not self.busy and not (self.watch_thread and self.watch_thread.is_alive()):
            self._load_initial_status()
            self._refresh_dynamic_views()

    def _on_saved_vault_selected(self, _event=None) -> None:
        selected = self.saved_vault_var.get().strip()
        if selected:
            self._activate_vault(selected)

    def _remove_selected_vault(self) -> None:
        selected = normalize_vault_path(self.saved_vault_var.get().strip() or self.vault_var.get().strip())
        if not selected:
            messagebox.showinfo(self._tr("not_ready_title"), self._tr("saved_vault_missing"), parent=self.root)
            return
        remaining = [vault for vault in self.saved_vaults if vault != selected]
        next_active = remaining[0] if remaining else ""
        self._set_saved_vaults(remaining, active_vault=next_active)
        self.vault_var.set(next_active)
        self._reset_workspace_views()
        self._refresh_state_chips()
        if next_active and not self.busy and not (self.watch_thread and self.watch_thread.is_alive()):
            self._load_initial_status()
        else:
            self.status_var.set(self._tr("status_ready"))
        self._append_log(self._tr("log_vault_removed", vault=Path(selected).name or selected))
        self._refresh_dynamic_views()

    def _format_elapsed(self, elapsed_seconds: float) -> str:
        total_seconds = max(0, int(elapsed_seconds))
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _task_profile(self, label_key: str, config: AppConfig, paths) -> tuple[str, str]:
        if label_key == "preflight_button":
            return self._tr("task_eta_preflight"), self._tr("task_detail_preflight")
        if label_key == "bootstrap_button":
            if is_local_model_ready(config, paths):
                return self._tr("task_eta_bootstrap_cached"), self._tr("task_detail_bootstrap_cached")
            return self._tr("task_eta_bootstrap_download"), self._tr("task_detail_bootstrap_download", model=config.vector_model)
        if label_key == "bootstrap_reranker_button":
            if is_local_reranker_ready(config, paths):
                return self._tr("task_eta_bootstrap_cached"), self._tr("task_detail_reranker_cached")
            return self._tr("task_eta_bootstrap_download"), self._tr("task_detail_reranker_download", model=config.reranker_model)
        if label_key == "search_button":
            return self._tr("task_eta_query"), self._tr("task_detail_query")
        if label_key in {"rebuild_button", "resume_rebuild_task"}:
            return self._tr("task_eta_rebuild"), self._tr("task_detail_rebuild")
        if label_key == "refresh_button":
            return self._tr("task_eta_refresh"), self._tr("task_detail_refresh")
        return self._tr("task_eta_unknown"), self._tr("task_detail_unknown")

    def _is_rebuild_task(self, label_key: str | None) -> bool:
        return label_key in {"rebuild_button", "resume_rebuild_task"}

    def _update_rebuild_pause_button(self) -> None:
        if not hasattr(self, "rebuild_pause_button"):
            return
        visible = self.busy and self._is_rebuild_task(self.active_task_key)
        if not visible:
            self.rebuild_pause_event.clear()
            self.rebuild_cancel_event.clear()
            self.rebuild_pause_var.set(self._tr("pause_rebuild"))
            self.rebuild_pause_button.grid_remove()
            if hasattr(self, "rebuild_cancel_button"):
                self.rebuild_cancel_button.grid_remove()
            return
        paused = self.rebuild_pause_event.is_set()
        self.rebuild_pause_var.set(self._tr("resume_rebuild_button") if paused else self._tr("pause_rebuild"))
        self.rebuild_pause_button.configure(style="Primary.TButton" if paused else "Secondary.TButton")
        self.rebuild_pause_button.grid()
        if hasattr(self, "rebuild_cancel_button"):
            self.rebuild_cancel_button.grid()

    def _toggle_rebuild_pause(self) -> None:
        if not self.busy or not self._is_rebuild_task(self.active_task_key):
            return
        if self.rebuild_pause_event.is_set():
            if self.task_paused_started_at:
                self.task_paused_total_seconds += max(time.time() - self.task_paused_started_at, 0.0)
            self.task_paused_started_at = 0.0
            self.rebuild_pause_event.clear()
            self.status_var.set(self._tr("status_rebuild_resumed"))
            self._append_log(self._tr("log_rebuild_resumed"))
            self.task_state_var.set(self._tr("task_running", task=self._tr(self.active_task_key or "rebuild_button")))
            if self.active_task_config is not None:
                current_device = (self.device_var.get().strip() or 'cpu').lower()
                if current_device != (self.active_task_config.vector_device or 'cpu').lower():
                    self._append_log(
                        self._tr(
                            "log_rebuild_resume_original_device",
                            original=self.active_task_config.vector_device,
                            current=current_device,
                        )
                    )
            if self.latest_task_progress is not None:
                self._update_task_progress(dict(self.latest_task_progress))
        else:
            self.rebuild_pause_event.set()
            self.task_paused_started_at = time.time()
            self.status_var.set(self._tr("status_rebuild_paused"))
            self._append_log(self._tr("log_rebuild_paused"))
            self.task_state_var.set(self._tr("task_paused", task=self._tr(self.active_task_key or "rebuild_button")))
            self.task_detail_var.set(self._tr("task_detail_rebuild_paused"))
            self.task_eta_var.set(self._tr("task_eta_paused", value=self.task_last_eta_text))
            self.task_elapsed_var.set(self._tr("task_elapsed", value=self._format_elapsed(self._current_task_elapsed_seconds())))
            self._set_task_progress_widget(mode="indeterminate", maximum=100, value=0)
        self._update_rebuild_pause_button()

    def _cancel_rebuild(self) -> None:
        if not self.busy or not self._is_rebuild_task(self.active_task_key):
            return
        if not messagebox.askyesno(self._tr("cancel_rebuild_confirm_title"), self._tr("cancel_rebuild_confirm_body"), parent=self.root):
            return
        if self.task_paused_started_at:
            self.task_paused_total_seconds += max(time.time() - self.task_paused_started_at, 0.0)
            self.task_paused_started_at = 0.0
        self.rebuild_pause_event.clear()
        self.rebuild_cancel_event.set()
        self.task_state_var.set(self._tr("task_state_cancelling"))
        self.task_detail_var.set(self._tr("task_detail_rebuild_cancelling"))
        self.status_var.set(self._tr("status_rebuild_cancel_requested"))
        self._append_log(self._tr("log_rebuild_cancel_requested"))
        self._update_rebuild_pause_button()

    def _task_progress_widget(self):
        widget = getattr(self, "task_progress", None)
        if widget is None:
            return None
        try:
            return widget if bool(widget.winfo_exists()) else None
        except Exception:
            return None

    def _set_task_progress_widget(self, *, mode: str, maximum: int, value: int, start_interval: int | None = None) -> None:
        widget = self._task_progress_widget()
        if widget is None:
            return
        try:
            widget.stop()
            widget.configure(mode=mode, maximum=maximum, value=value)
            if start_interval is not None and mode == "indeterminate":
                widget.start(start_interval)
        except Exception:
            return

    def _start_task_feedback(self, label_key: str, config: AppConfig, paths) -> None:
        eta_text, detail_text = self._task_profile(label_key, config, paths)
        self.active_task_key = label_key
        self.active_task_config = config
        self.latest_task_progress = None
        self.rebuild_pause_event.clear()
        self.rebuild_cancel_event.clear()
        self.task_started_at = time.time()
        self.task_paused_started_at = 0.0
        self.task_paused_total_seconds = 0.0
        self.task_last_eta_text = self._tr("task_eta_label", value=eta_text)
        self.task_state_var.set(self._tr("task_running", task=self._tr(label_key)))
        self.task_detail_var.set(detail_text)
        self.task_percent_var.set(self._tr("task_percent_idle"))
        self.task_elapsed_var.set(self._tr("task_elapsed", value="00:00"))
        self.task_eta_var.set(self.task_last_eta_text)
        self._update_rebuild_pause_button()
        self._set_task_progress_widget(mode="indeterminate", maximum=100, value=0, start_interval=12)
        self._tick_task_feedback()
        self._refresh_query_status_banner()

    def _tick_task_feedback(self) -> None:
        if self.task_after_id is not None:
            try:
                self.root.after_cancel(self.task_after_id)
            except Exception:
                pass
            self.task_after_id = None
        if not self.busy:
            return
        if not (self.rebuild_pause_event.is_set() and self._is_rebuild_task(self.active_task_key)):
            elapsed = self._format_elapsed(self._current_task_elapsed_seconds())
            self.task_elapsed_var.set(self._tr("task_elapsed", value=elapsed))
        self.task_after_id = self.root.after(500, self._tick_task_feedback)

    def _stop_task_feedback(self) -> None:
        if self.task_after_id is not None:
            try:
                self.root.after_cancel(self.task_after_id)
            except Exception:
                pass
            self.task_after_id = None
        self._set_task_progress_widget(mode="indeterminate", maximum=100, value=0)
        self.rebuild_pause_event.clear()
        self.rebuild_cancel_event.clear()
        self.latest_task_progress = None
        self.active_task_key = None
        self.active_task_config = None
        self.task_started_at = 0.0
        self.task_paused_started_at = 0.0
        self.task_paused_total_seconds = 0.0
        self.task_last_eta_text = self._tr("task_eta_idle")
        self.task_state_var.set(self._tr("task_idle"))
        self.task_detail_var.set(self._tr("task_idle_detail"))
        self.task_percent_var.set(self._tr("task_percent_idle"))
        self.task_elapsed_var.set(self._tr("task_elapsed", value="00:00"))
        self.task_eta_var.set(self._tr("task_eta_idle"))
        self._update_rebuild_pause_button()
        self._refresh_query_status_banner()

    def _update_task_progress(self, payload: dict[str, object]) -> None:
        if not hasattr(self, "task_progress"):
            return
        self.latest_task_progress = dict(payload)
        stage = str(payload.get("stage") or "")
        stage_status = str(payload.get('stage_status') or '')
        current = max(0, int(payload.get("current", 0) or 0))
        total = max(0, int(payload.get("total", 0) or 0))
        percent_value = payload.get('overall_percent')
        if percent_value is None and total > 0 and stage in {"indexing", "rendering", "vectorizing"}:
            percent_value = min((current / max(total, 1)) * 100.0, 100.0)
        if total > 0 and percent_value is not None:
            self.task_percent_var.set(self._tr("task_percent_label", percent=float(percent_value), current=current, total=total))
        elif stage == "rendering":
            self.task_percent_var.set(self._tr("task_percent_stage", stage=self._tr("resume_phase_rendering")))
        else:
            self.task_percent_var.set(self._tr("task_percent_idle"))

        eta_seconds = payload.get('eta_seconds')
        if eta_seconds is not None and not (self.rebuild_pause_event.is_set() and self._is_rebuild_task(self.active_task_key)):
            self.task_last_eta_text = self._tr("task_eta_label", value=format_duration(int(max(float(eta_seconds), 0.0))))
            self.task_eta_var.set(self.task_last_eta_text)

        if self.rebuild_pause_event.is_set() and self._is_rebuild_task(self.active_task_key):
            if stage in {"indexing", "rendering", "vectorizing"} and total > 0:
                self._set_task_progress_widget(mode="determinate", maximum=100, value=min(max(int(round(float(percent_value or 0.0))), 0), 100))
            else:
                self._set_task_progress_widget(mode="indeterminate", maximum=100, value=0)
            self._refresh_query_status_banner()
            return

        if stage == 'query':
            self._set_task_progress_widget(mode='determinate', maximum=100, value=min(current, 100))
            self.task_detail_var.set(self._query_progress_detail(payload))
        elif stage == "indexing" and total > 0:
            self._set_task_progress_widget(mode="determinate", maximum=100, value=min(max(int(round(float(percent_value or 0.0))), 0), 100))
            current_path = str(payload.get("current_path") or self._tr("none_value"))
            self.task_detail_var.set(self._tr("task_detail_rebuild_progress", current=current, total=total, path=current_path))
        elif stage == "rendering":
            if total > 0:
                self._set_task_progress_widget(mode="determinate", maximum=100, value=min(max(int(round(float(percent_value or 0.0))), 0), 100))
                self.task_detail_var.set(self._tr("task_detail_rebuild_rendering_progress", current=current, total=total))
            else:
                self._set_task_progress_widget(mode="indeterminate", maximum=100, value=0, start_interval=10)
                self.task_detail_var.set(self._tr("task_detail_rebuild_rendering"))
        elif stage == "vectorizing":
            if stage_status == 'loading_model' and current <= 0:
                self._set_task_progress_widget(mode="indeterminate", maximum=100, value=0, start_interval=10)
                self.task_detail_var.set(self._tr("task_detail_rebuild_vector_loading"))
            elif total > 0:
                self._set_task_progress_widget(mode="determinate", maximum=100, value=min(max(int(round(float(percent_value or 0.0))), 0), 100))
                detail = self._tr("task_detail_rebuild_vectorizing", total=total)
                tuning = self._render_vector_tuning(payload)
                self.task_detail_var.set(f"{detail}\n{tuning}" if tuning else detail)
            else:
                self._set_task_progress_widget(mode="indeterminate", maximum=100, value=0, start_interval=10)
                detail = self._tr("task_detail_rebuild_vectorizing", total=total)
                tuning = self._render_vector_tuning(payload)
                self.task_detail_var.set(f"{detail}\n{tuning}" if tuning else detail)
        self._refresh_query_status_banner()

    def _render_vector_tuning(self, payload: dict[str, object]) -> str:
        encode_batch = int(payload.get('encode_batch_size', 0) or 0)
        write_batch = int(payload.get('write_batch_size', 0) or 0)
        if encode_batch <= 0 and write_batch <= 0:
            return ''
        profile = self._build_profile_label(str(payload.get('build_profile', 'balanced')))
        action_key = f"vector_tuning_action_{str(payload.get('tuning_action', 'steady')).strip().lower() or 'steady'}"
        reason_key = f"vector_tuning_reason_{str(payload.get('tuning_reason', 'stable')).strip().lower() or 'stable'}"
        try:
            action = self._tr(action_key)
        except KeyError:
            action = str(payload.get('tuning_action', 'steady'))
        try:
            reason = self._tr(reason_key)
        except KeyError:
            reason = str(payload.get('tuning_reason', 'stable'))
        metrics = self._tr('none_value')
        sample_payload = payload.get('resource_sample')
        if isinstance(sample_payload, dict):
            try:
                metrics = format_resource_sample(ResourceSample(**sample_payload)) or self._tr('none_value')
            except Exception:
                metrics = self._tr('none_value')
        queue_depth = int(payload.get('write_queue_depth', 0) or 0)
        queue_capacity = int(payload.get('write_queue_capacity', 0) or 0)
        encoded_count = int(payload.get('encoded_count', 0) or 0)
        written_count = int(payload.get('written_count', 0) or 0)
        flush_count = int(payload.get('write_flush_count', 0) or 0)
        prepare_seconds = float(payload.get('prepare_elapsed_total_ms', 0.0) or 0.0) / 1000.0
        write_seconds = float(payload.get('write_elapsed_total_ms', 0.0) or 0.0) / 1000.0
        return self._tr(
            'task_detail_rebuild_vector_tuning',
            profile=profile,
            encode_batch=encode_batch,
            write_batch=write_batch,
            metrics=metrics,
            action=action,
            reason=reason,
            encoded_count=encoded_count,
            written_count=written_count,
            queue_depth=queue_depth,
            queue_capacity=queue_capacity,
            flush_count=flush_count,
            prepare_seconds=f'{prepare_seconds:.1f}',
            write_seconds=f'{write_seconds:.1f}',
        )

    def _refresh_dynamic_views(self) -> None:
        self._refresh_state_chips()
        self._render_hits()
        self._set_text(self.preview_text, self._current_preview_text())
        self._refresh_context_jump_controls()
        self._set_text(self.context_text, self.current_context or self._tr("context_empty"))
        self._set_text(self.log_text, "\n".join(self.log_lines) if self.log_lines else self._tr("log_empty"))
        self._update_watch_button_state()
        self._update_context_selection_summary()
        self._update_page_blocklist_summary()
        self._update_page_sort_button()
        self._refresh_query_status_banner()

    def _current_preview_text(self) -> str:
        if not self.current_hits:
            return self._tr("preview_empty")
        selected_chunk_id = self._selected_tree_chunk_id()
        index = self._find_hit_index(selected_chunk_id)
        if index is None:
            index = 0
        index = max(0, min(index, len(self.current_hits) - 1))
        hit = self.current_hits[index]
        return (
            f"{self._tr('col_page')}：{hit.title}\n"
            f"{self._tr('col_anchor')}：{hit.anchor}\n"
            f"{self._tr('col_source')}：{hit.source_path}\n"
            f"{self._tr('col_score')}：{hit.score:.1f}/100\n"
            f"{self._tr('col_reason')}：{hit.reason or self._tr('reason_fallback')}\n\n"
            f"{self._tr('preview_excerpt_label')}\n{hit.preview_text or self._tr('none_value')}\n\n"
            f"{self._tr('preview_full_label')}\n{hit.display_text or hit.rendered_text}"
        )

    def _hit_row_values(self, hit) -> tuple[str, str, str, str, str]:
        include_value = '[x]' if hit.chunk_id in self.selected_chunk_ids else '[ ]'
        return (include_value, hit.title, hit.reason or self._tr('reason_fallback'), hit.anchor, f"{hit.score:.1f}")

    def _render_hits(self, selected_chunk_id: str | None = None) -> None:
        if not hasattr(self, "tree"):
            return
        if selected_chunk_id is None:
            selected_chunk_id = self._selected_tree_chunk_id()
        desired_ids = [hit.chunk_id for hit in self.current_hits]
        desired_id_set = set(desired_ids)
        existing_ids = set(self.tree.get_children())
        for stale_id in existing_ids - desired_id_set:
            self.tree.delete(stale_id)
        for index, hit in enumerate(self.current_hits):
            values = self._hit_row_values(hit)
            if self.tree.exists(hit.chunk_id):
                if tuple(self.tree.item(hit.chunk_id, 'values')) != values:
                    self.tree.item(hit.chunk_id, values=values)
                self.tree.move(hit.chunk_id, '', index)
            else:
                self.tree.insert('', index, iid=hit.chunk_id, values=values)
        self._refresh_tree_headings()
        self._update_page_sort_button()
        if selected_chunk_id and self.tree.exists(selected_chunk_id):
            self.tree.selection_set(selected_chunk_id)
        elif self.current_hits:
            self.tree.selection_set(self.current_hits[0].chunk_id)

    def _on_result_tree_click(self, event) -> str | None:
        if not hasattr(self, 'tree'):
            return None
        row_id = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        if row_id and column_id == '#1':
            hit_index = self._find_hit_index(row_id)
            if hit_index is None:
                return 'break'
            self._toggle_hit_selection(hit_index)
            self.tree.selection_set(row_id)
            self._show_hit(hit_index)
            return 'break'
        return None

    def _toggle_hit_selection(self, index: int) -> None:
        if index < 0 or index >= len(self.current_hits):
            return
        hit = self.current_hits[index]
        if hit.chunk_id in self.selected_chunk_ids:
            self.selected_chunk_ids.remove(hit.chunk_id)
        else:
            self.selected_chunk_ids.add(hit.chunk_id)
        self._render_hits(selected_chunk_id=hit.chunk_id)
        self._update_context_selection_summary()
        self._request_context_refresh()

    def _toggle_all_hit_selection(self) -> None:
        if not self.current_hits:
            return
        selected_chunk_id = self._selected_tree_chunk_id()
        current_ids = {hit.chunk_id for hit in self.current_hits}
        selected_ids = current_ids & self.selected_chunk_ids
        if selected_ids and len(selected_ids) == len(current_ids):
            self.selected_chunk_ids.difference_update(current_ids)
        else:
            self.selected_chunk_ids.update(current_ids)
        self._render_hits(selected_chunk_id=selected_chunk_id)
        self._update_context_selection_summary()
        self._request_context_refresh()

    def _update_page_blocklist_summary(self) -> None:
        rules = _deserialize_page_filter_rules(self.page_blocklist_rules_var.get())
        enabled = sum(1 for is_enabled, _pattern in rules if is_enabled)
        total = len(rules)
        self.page_blocklist_summary_var.set(self._tr('page_blocklist_summary', enabled=enabled, total=total))

    def _open_page_blocklist_window(self) -> None:
        if self.page_blocklist_window is not None:
            try:
                if int(self.page_blocklist_window.winfo_exists()):
                    self.page_blocklist_window.deiconify()
                    self.page_blocklist_window.lift()
                    self.page_blocklist_window.focus_force()
                    return
            except Exception:
                self.page_blocklist_window = None

        window = tk.Toplevel(self.root)
        window.title(self._tr('page_blocklist_window_title'))
        window.geometry('860x560')
        window.minsize(760, 460)
        window.configure(bg=self.colors['card'])
        window.transient(self.root)
        self.page_blocklist_window = window

        def _on_close_window() -> None:
            self.page_blocklist_window = None
            window.destroy()

        def _refresh_rows() -> None:
            for index, row in enumerate(rule_rows, start=1):
                row['frame'].grid(row=index, column=0, sticky='ew', pady=(8 if index == 1 else 6, 0))

        def _add_row(enabled: bool = True, pattern: str = '') -> None:
            row = tk.Frame(list_body, bg=self.colors['soft_2'])
            row.grid_columnconfigure(1, weight=1)
            enabled_var = tk.BooleanVar(value=enabled)
            pattern_var = tk.StringVar(value=pattern)
            check = ttk.Checkbutton(row, variable=enabled_var, style='Plain.TCheckbutton')
            check.grid(row=0, column=0, sticky='w')
            entry = ttk.Entry(row, textvariable=pattern_var, style='Field.TEntry')
            entry.grid(row=0, column=1, sticky='ew', padx=(10, 10))
            remove_button = ttk.Button(row, text=self._tr('page_blocklist_remove'), style='Secondary.TButton')
            record = {'frame': row, 'enabled_var': enabled_var, 'pattern_var': pattern_var}
            remove_button.configure(command=lambda item=record: _remove_row(item))
            remove_button.grid(row=0, column=2, sticky='e')
            self._attach_tooltip(remove_button, 'page_blocklist_remove')
            rule_rows.append(record)
            _refresh_rows()

        def _remove_row(item) -> None:
            if item not in rule_rows:
                return
            rule_rows.remove(item)
            try:
                item['frame'].destroy()
            except Exception:
                pass
            _refresh_rows()

        def _reset_defaults() -> None:
            for row in list(rule_rows):
                _remove_row(row)
            for enabled, pattern in DEFAULT_PAGE_FILTER_RULES:
                _add_row(enabled, pattern)

        window.protocol('WM_DELETE_WINDOW', _on_close_window)
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(2, weight=1)

        tk.Label(window, text=self._tr('page_blocklist_window_title'), bg=self.colors['card'], fg=self.colors['ink'], font=self.fonts['card_title'], anchor='w').grid(row=0, column=0, sticky='w', padx=18, pady=(18, 6))
        tk.Label(window, text=self._tr('page_blocklist_window_body'), bg=self.colors['card'], fg=self.colors['muted'], font=self.fonts['small'], anchor='w', justify='left', wraplength=800).grid(row=1, column=0, sticky='new', padx=18)

        list_wrap = tk.Frame(window, bg=self.colors['card'])
        list_wrap.grid(row=2, column=0, sticky='nsew', padx=18, pady=(10, 14))
        list_wrap.grid_columnconfigure(0, weight=1)
        list_wrap.grid_rowconfigure(0, weight=1)

        canvas = tk.Canvas(list_wrap, bg=self.colors['soft_2'], highlightthickness=0, borderwidth=0)
        canvas.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(list_wrap, orient='vertical', command=canvas.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        canvas.configure(yscrollcommand=scroll.set)

        list_body = tk.Frame(canvas, bg=self.colors['soft_2'])
        list_body.grid_columnconfigure(0, weight=1)
        window_id = canvas.create_window((0, 0), window=list_body, anchor='nw')
        self._configure_canvas_window_sync(canvas, list_body, window_id, key_prefix=f'page-blocklist:{str(canvas)}')

        header = tk.Frame(list_body, bg=self.colors['soft_2'])
        header.grid(row=0, column=0, sticky='ew')
        header.grid_columnconfigure(1, weight=1)
        tk.Label(header, text=self._tr('page_blocklist_enabled'), bg=self.colors['soft_2'], fg=self.colors['muted'], font=self.fonts['small'], anchor='w').grid(row=0, column=0, sticky='w')
        tk.Label(header, text=self._tr('page_blocklist_regex'), bg=self.colors['soft_2'], fg=self.colors['muted'], font=self.fonts['small'], anchor='w').grid(row=0, column=1, sticky='w', padx=(10, 10))

        rule_rows: list[dict[str, object]] = []
        for enabled, pattern in _deserialize_page_filter_rules(self.page_blocklist_rules_var.get()):
            _add_row(enabled, pattern)

        action_row = tk.Frame(window, bg=self.colors['card'])
        action_row.grid(row=3, column=0, sticky='ew', padx=18, pady=(0, 18))
        action_row.grid_columnconfigure(2, weight=1)
        add_button = ttk.Button(action_row, text=self._tr('page_blocklist_add'), style='Secondary.TButton', command=lambda: _add_row())
        add_button.grid(row=0, column=0, sticky='w')
        self._attach_tooltip(add_button, 'page_blocklist_add')
        reset_button = ttk.Button(action_row, text=self._tr('page_blocklist_reset_defaults'), style='Secondary.TButton', command=_reset_defaults)
        reset_button.grid(row=0, column=1, sticky='w', padx=(8, 0))
        self._attach_tooltip(reset_button, 'page_blocklist_reset_defaults')
        save_button = ttk.Button(action_row, text=self._tr('save_config'), style='Primary.TButton', command=lambda: self._save_page_blocklist_rules(rule_rows, _on_close_window))
        save_button.grid(row=0, column=3, sticky='e')
        self._attach_tooltip(save_button, 'save_filters')

    def _save_page_blocklist_rules(self, rows, close_window) -> None:
        serialized = _serialize_page_filter_rules([
            (bool(row['enabled_var'].get()), str(row['pattern_var'].get()).strip())
            for row in rows
            if str(row['pattern_var'].get()).strip()
        ])
        self.page_blocklist_rules_var.set(serialized)
        self._update_page_blocklist_summary()
        rules = _deserialize_page_filter_rules(serialized)
        enabled = sum(1 for is_enabled, _pattern in rules if is_enabled)
        self.status_var.set(self._tr('status_page_blocklist_saved'))
        self._append_log(self._tr('log_page_blocklist_saved', enabled=enabled, total=len(rules)))
        close_window()
        if self.query_var.get().strip() and not self.busy:
            self._query(False)

    def _open_sensitive_filter_window(self) -> None:
        if self.sensitive_filter_window is not None:
            try:
                if int(self.sensitive_filter_window.winfo_exists()):
                    self.sensitive_filter_window.deiconify()
                    self.sensitive_filter_window.lift()
                    self.sensitive_filter_window.focus_force()
                    return
            except Exception:
                self.sensitive_filter_window = None

        window = tk.Toplevel(self.root)
        window.title(self._tr('sensitive_filter_window_title'))
        window.geometry('780x460')
        window.minsize(700, 420)
        window.configure(bg=self.colors['card'])
        window.transient(self.root)
        self.sensitive_filter_window = window

        local_core_var = tk.BooleanVar(value=self.rag_filter_core_var.get())
        local_extended_var = tk.BooleanVar(value=self.rag_filter_extended_var.get())

        def _on_close_window() -> None:
            self.sensitive_filter_window = None
            window.destroy()

        def _save_sensitive_filters() -> None:
            custom_rules = custom_text.get('1.0', 'end-1c').strip()
            self.rag_filter_core_var.set(local_core_var.get())
            self.rag_filter_extended_var.set(local_extended_var.get())
            self.rag_filter_custom_rules_var.set(custom_rules)
            self.status_var.set(self._tr('status_sensitive_filters_saved'))
            self._append_log(self._tr('log_sensitive_filters_saved'))
            _on_close_window()
            if self.query_var.get().strip() and not self.busy:
                self._query(False)

        window.protocol('WM_DELETE_WINDOW', _on_close_window)
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(2, weight=1)

        tk.Label(window, text=self._tr('sensitive_filter_window_title'), bg=self.colors['card'], fg=self.colors['ink'], font=self.fonts['card_title'], anchor='w').grid(row=0, column=0, sticky='w', padx=18, pady=(18, 6))
        tk.Label(window, text=self._tr('sensitive_filter_window_body'), bg=self.colors['card'], fg=self.colors['muted'], font=self.fonts['small'], anchor='w', justify='left', wraplength=720).grid(row=1, column=0, sticky='new', padx=18)

        body = tk.Frame(window, bg=self.colors['soft_2'], highlightbackground=self.colors['border'], highlightthickness=1)
        body.grid(row=2, column=0, sticky='nsew', padx=18, pady=(10, 14))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(3, weight=1)

        core_check = ttk.Checkbutton(body, text=self._tr('rag_filter_core_label'), variable=local_core_var, style='Plain.TCheckbutton')
        core_check.grid(row=0, column=0, sticky='w', padx=16, pady=(16, 0))
        self._attach_tooltip(core_check, 'rag_filter_core')
        extended_check = ttk.Checkbutton(body, text=self._tr('rag_filter_extended_label'), variable=local_extended_var, style='Plain.TCheckbutton')
        extended_check.grid(row=1, column=0, sticky='w', padx=16, pady=(8, 0))
        self._attach_tooltip(extended_check, 'rag_filter_extended')
        custom_label = tk.Label(body, text=self._tr('rag_filter_custom_label'), bg=self.colors['soft_2'], fg=self.colors['muted'], font=self.fonts['small'], anchor='w', justify='left', wraplength=640)
        custom_label.grid(row=2, column=0, sticky='w', padx=16, pady=(12, 0))
        custom_text = tk.Text(body, wrap='word', relief='flat', borderwidth=0, highlightbackground=self.colors['input_border'], highlightthickness=1, background=self.colors['input_bg'], foreground=self.colors['input_fg'], insertbackground=self.colors['input_fg'], font=self.fonts['body'], height=8)
        custom_text.grid(row=3, column=0, sticky='nsew', padx=16, pady=(6, 0))
        custom_text.insert('1.0', self.rag_filter_custom_rules_var.get())
        self._attach_tooltip(custom_text, 'rag_filter_custom_rules')

        action_row = tk.Frame(window, bg=self.colors['card'])
        action_row.grid(row=3, column=0, sticky='ew', padx=18, pady=(0, 18))
        action_row.grid_columnconfigure(0, weight=1)
        save_button = ttk.Button(action_row, text=self._tr('save_config'), style='Primary.TButton', command=_save_sensitive_filters)
        save_button.grid(row=0, column=1, sticky='e')
        self._attach_tooltip(save_button, 'save_filters')

    def _refresh_reranker_state_summary(self, payload: dict[str, object] | None = None) -> None:
        ready: bool | None = None
        model_name = self.reranker_model_var.get().strip() or 'BAAI/bge-reranker-v2-m3'
        if isinstance(payload, dict):
            if 'reranker_ready' in payload:
                ready = bool(payload.get('reranker_ready'))
            if payload.get('reranker_model'):
                model_name = str(payload.get('reranker_model'))
        if ready is None:
            try:
                config, paths = self._config(False)
            except Exception:
                ready = False
            else:
                model_name = config.reranker_model or model_name
                ready = is_local_reranker_ready(config, paths)
        self.reranker_state_var.set(self._tr('reranker_ready') if ready else self._tr('reranker_missing'))
        if self.reranker_state_label is not None:
            self.reranker_state_label.configure(fg=self.colors['accent_dark'] if ready else self.colors['muted'])
    def _refresh_state_chips(self) -> None:
        self._refresh_workspace_summary()
        try:
            vault_path = Path(self.vault_var.get().strip()).expanduser()
            vault_ready = bool(self.vault_var.get().strip()) and vault_path.exists() and vault_path.is_dir()
        except OSError:
            vault_ready = False
        self.vault_state_var.set(self._tr("vault_ready") if vault_ready else self._tr("vault_missing"))
        self._set_chip_style(self.vault_chip, ok=vault_ready)

        model_ready = self._is_model_ready()
        self.model_state_var.set(self._tr("model_ready") if model_ready else self._tr("model_missing"))
        self._set_chip_style(self.model_chip, ok=model_ready, warn=not model_ready)

        index_state = self._current_index_state()
        self.index_state_var.set(self._tr(f"index_{index_state}"))
        self._set_chip_style(self.index_chip, ok=index_state == "ready", warn=index_state == "pending")

    def _set_chip_style(self, widget: tk.Label, *, ok: bool = False, warn: bool = False) -> None:
        if ok:
            widget.configure(bg=self.colors["chip_ok_bg"], fg=self.colors["chip_ok_fg"])
        elif warn:
            widget.configure(bg=self.colors["chip_warn_bg"], fg=self.colors["chip_warn_fg"])
        else:
            widget.configure(bg=self.colors["chip_neutral_bg"], fg=self.colors["chip_neutral_fg"])

    def _load_initial_config(self) -> None:
        self._append_log(self._tr("log_started"))
        paths = ensure_data_paths()
        self.data_dir_var.set(str(paths.global_root))
        self._load_config(paths)
        self._load_initial_status()

    def _on_language_changed(self, _event=None) -> None:
        self.language_code = language_code_from_label(self.language_var.get())
        self.language_var.set(language_label(self.language_code))
        self._refresh_localized_runtime_state()
        self._render_ui()

    def _refresh_localized_runtime_state(self) -> None:
        self._refresh_workspace_summary()
        self._refresh_device_options()
        self.ui_theme_var.set(self._ui_theme_label(self.ui_theme))
        self.ui_scale_var.set(str(self.ui_scale_percent))
        ui_theme_combo = getattr(self, 'ui_theme_combo', None)
        if ui_theme_combo is not None:
            try:
                if int(ui_theme_combo.winfo_exists()):
                    ui_theme_combo.configure(values=self._ui_theme_choices())
            except tk.TclError:
                pass
        if self.current_report is not None:
            self.preflight_var.set(summarize_preflight(self.current_report, self.language_code))
        elif self.latest_preflight_snapshot is not None:
            self.preflight_var.set(
                self._tr(
                    "recent_preflight",
                    risk=self.latest_preflight_snapshot.get("risk_level"),
                    required=format_bytes(int(self.latest_preflight_snapshot.get("required_free_bytes", 0))),
                    available=format_bytes(int(self.latest_preflight_snapshot.get("available_free_bytes", 0))),
                )
            )
        else:
            self.preflight_var.set(self._tr("preflight_empty"))

        if self.watch_thread and self.watch_thread.is_alive():
            mode = self._watch_mode_label(self.polling_var.get() or not WATCHDOG_AVAILABLE)
            try:
                seconds = float(self.interval_var.get().strip() or "2.0")
            except ValueError:
                seconds = 2.0
            self.watch_var.set(self._tr("watch_running", mode=mode, seconds=seconds))
            if not self.busy:
                self.status_var.set(self._tr("status_watch_running"))
        else:
            self.watch_var.set(self._default_watch_summary())
            if not self.busy:
                if self.current_hits or self.current_context:
                    self.status_var.set(self._tr("status_query_done"))
                elif self.log_lines:
                    self.status_var.set(self._tr("status_refresh_done"))
                else:
                    self.status_var.set(self._tr("status_ready"))

        if self.busy and self.active_task_key:
            if self.rebuild_pause_event.is_set() and self._is_rebuild_task(self.active_task_key):
                self.task_state_var.set(self._tr("task_paused", task=self._tr(self.active_task_key)))
            else:
                self.task_state_var.set(self._tr("task_running", task=self._tr(self.active_task_key)))
            try:
                config, paths = self._config(False)
                eta_text, detail_text = self._task_profile(self.active_task_key, config, paths)
            except Exception:
                eta_text, detail_text = self._tr("task_eta_unknown"), self._tr("task_detail_unknown")
            if not (self.rebuild_pause_event.is_set() and self._is_rebuild_task(self.active_task_key)):
                self.task_detail_var.set(detail_text)
                self.task_eta_var.set(self._tr("task_eta_label", value=eta_text))
        else:
            self.task_state_var.set(self._tr("task_idle"))
            self.task_detail_var.set(self._tr("task_idle_detail"))
            self.task_percent_var.set(self._tr("task_percent_idle"))
            self.task_elapsed_var.set(self._tr("task_elapsed", value="00:00"))
            self.task_eta_var.set(self._tr("task_eta_idle"))

        if self.current_hits or self.current_context:
            self.result_var.set(self._tr("query_hits", count=len(self.current_hits)))
        else:
            self.result_var.set(self._tr("result_empty"))
        self._update_context_selection_summary()
        self._refresh_quick_start_visibility()
        self._refresh_advanced_visibility()
        self._refresh_query_status_banner()

    def _apply_recommended(self) -> None:
        self.backend_var.set("lancedb")
        self.model_var.set("BAAI/bge-m3")
        self.runtime_var.set("torch")
        self.device_var.set("auto")
        self.limit_var.set("15")
        self.score_threshold_var.set("35")
        self.interval_var.set("2.0")
        self.build_resource_profile_var.set(self._build_profile_label('balanced'))
        self.watch_resource_peak_var.set(self._watch_peak_label(15))
        self.local_only_var.set(False)
        self.rag_filter_core_var.set(True)
        self.rag_filter_extended_var.set(False)
        self.rag_filter_custom_rules_var.set("")
        self.reranker_enabled_var.set(False)
        self.reranker_model_var.set('BAAI/bge-reranker-v2-m3')
        self.reranker_batch_cpu_var.set('4')
        self.reranker_batch_cuda_var.set('8')
        self.context_export_ai_collab_var.set(False)
        self.page_blocklist_rules_var.set(_merge_page_filter_defaults(""))
        self.force_var.set(False)
        self._update_page_blocklist_summary()
        self.polling_var.set(False)
        self._refresh_device_options()
        profile_code = self._build_profile_code(self.build_resource_profile_var.get())
        self.build_resource_profile_var.set(self._build_profile_label(profile_code))
        self.watch_resource_peak_var.set(self._watch_peak_label(self._watch_peak_value(self.watch_resource_peak_var.get())))
        build_profile_combo = getattr(self, 'build_profile_combo', None)
        if build_profile_combo is not None:
            try:
                if int(build_profile_combo.winfo_exists()):
                    build_profile_combo.configure(values=self._build_profile_choices())
            except tk.TclError:
                pass
        self._refresh_query_limit_guidance()
        self.status_var.set(self._tr("status_recommended"))
        self._refresh_state_chips()

    def _toggle_quick_start(self) -> None:
        self.quick_start_expanded_var.set(not self.quick_start_expanded_var.get())
        self._refresh_quick_start_visibility()

    def _toggle_advanced(self) -> None:
        self.show_advanced_var.set(not self.show_advanced_var.get())
        self._refresh_advanced_visibility()

    def _open_help_and_updates(self) -> None:
        if not messagebox.askyesno(self._tr("help_updates_confirm_title"), self._tr("help_updates_confirm_body"), parent=self.root):
            return
        self._open_url(REPO_URL)

    def _open_url(self, url: str) -> None:
        if not webbrowser.open(url):
            messagebox.showwarning(self._tr("help_updates"), self._tr("help_failed"), parent=self.root)

    def _choose_vault(self) -> None:
        selected = filedialog.askdirectory(title=self._tr("vault_label"), initialdir=self.vault_var.get().strip() or str(Path.home()))
        if selected:
            self._activate_vault(selected)
            self._append_log(self._tr("log_vault_selected", vault=Path(selected).name or selected))

    def _choose_data_dir(self) -> None:
        selected = filedialog.askdirectory(title=self._tr("data_dir_label"), initialdir=self.data_dir_var.get().strip() or str(default_data_root()))
        if selected:
            self.data_dir_var.set(str(Path(selected).expanduser().resolve()))
            self._load_config_from_current_dir()
            self._refresh_state_chips()

    def _load_config_from_current_dir(self) -> None:
        active_vault = self.vault_var.get().strip() or None
        self._load_config(ensure_data_paths(self.data_dir_var.get().strip() or str(default_data_root()), active_vault))

    def _load_config(self, paths) -> None:
        config = load_config(paths)
        if config is None:
            self._set_saved_vaults([], active_vault=self.vault_var.get().strip())
            self.latest_preflight_snapshot = None
            self.status_snapshot = None
            self.preflight_var.set(self._tr("preflight_empty"))
            self._refresh_preflight_notice()
            return
        new_language = normalize_language(config.ui_language)
        language_changed = new_language != self.language_code
        self.language_code = new_language
        self.language_var.set(language_label(self.language_code))
        self.vault_var.set(config.vault_path)
        self._set_saved_vaults(config.vault_paths, active_vault=config.vault_path)
        self.data_dir_var.set(config.data_root)
        self.backend_var.set(config.vector_backend or "disabled")
        self.model_var.set(config.vector_model)
        self.runtime_var.set(config.vector_runtime)
        self.device_var.set(config.vector_device or 'auto')
        acceleration = detect_acceleration()
        if (config.vector_device or '').strip().lower() in {'', 'cpu'} and acceleration.get('cuda_available'):
            self.device_var.set('auto')
        self.limit_var.set(str(config.query_limit or 15))
        threshold_value = float(config.query_score_threshold)
        self.score_threshold_var.set(str(int(threshold_value)) if threshold_value.is_integer() else str(threshold_value))
        self.interval_var.set(str(config.poll_interval_seconds))
        self.build_resource_profile_var.set(self._build_profile_label(getattr(config, 'build_resource_profile', 'balanced')))
        self.watch_resource_peak_var.set(self._watch_peak_label(getattr(config, 'watch_resource_peak_percent', 15)))
        self.local_only_var.set(config.vector_local_files_only)
        self.reranker_enabled_var.set(getattr(config, 'reranker_enabled', False))
        self.reranker_model_var.set(getattr(config, 'reranker_model', 'BAAI/bge-reranker-v2-m3'))
        self.reranker_batch_cpu_var.set(str(getattr(config, 'reranker_batch_size_cpu', 4)))
        self.reranker_batch_cuda_var.set(str(getattr(config, 'reranker_batch_size_cuda', 8)))
        self.context_export_ai_collab_var.set(getattr(config, 'context_export_mode', 'standard') == 'ai-collab')
        self.rag_filter_core_var.set(config.rag_filter_core_enabled)
        self.rag_filter_extended_var.set(config.rag_filter_extended_enabled)
        self.rag_filter_custom_rules_var.set(config.rag_filter_custom_rules)
        self.page_blocklist_rules_var.set(_merge_page_filter_defaults(config.page_blocklist_rules))
        self._update_page_blocklist_summary()
        self.quick_start_expanded_var.set(config.ui_quick_start_expanded)
        self.ui_theme_var.set(self._ui_theme_label(getattr(config, 'ui_theme', self.ui_theme)))
        self.ui_scale_var.set(str(getattr(config, 'ui_scale_percent', self.ui_scale_percent)))
        visual_changed = self._apply_visual_preferences(
            theme_code=getattr(config, 'ui_theme', self.ui_theme),
            scale_percent=getattr(config, 'ui_scale_percent', self.ui_scale_percent),
            rebuild_ui=False,
        )
        self.ui_window_geometry = config.ui_window_geometry or self.ui_window_geometry
        self.ui_main_sash = self._coerce_layout_value(config.ui_main_sash, self.ui_main_sash)
        self.ui_right_sash = self._coerce_layout_value(config.ui_right_sash, self.ui_right_sash)
        self.ui_results_sash = self._coerce_layout_value(config.ui_results_sash, self.ui_results_sash)
        self.ui_layout_has_user_state = any(
            value != self._legacy_layout_value(name)
            for name, value in (("ui_right_sash", self.ui_right_sash), ("ui_results_sash", self.ui_results_sash))
        )
        self._refresh_device_options()
        profile_code = self._build_profile_code(self.build_resource_profile_var.get())
        self.build_resource_profile_var.set(self._build_profile_label(profile_code))
        build_profile_combo = getattr(self, 'build_profile_combo', None)
        if build_profile_combo is not None:
            try:
                if int(build_profile_combo.winfo_exists()):
                    build_profile_combo.configure(values=self._build_profile_choices())
            except tk.TclError:
                pass
        self._refresh_query_limit_guidance()
        self._append_log(self._tr("log_loaded_config", path=paths.config_file))
        if language_changed or visual_changed:
            self._render_ui()
        self._refresh_state_chips()
        self._refresh_localized_runtime_state()
        self._apply_layout_state()

    def _config(self, require_vault: bool):
        vault = self.vault_var.get().strip()
        if require_vault:
            if not vault:
                raise ValueError(self._tr("choose_vault_first"))
            vault_path = Path(vault).expanduser().resolve()
            if not vault_path.exists() or not vault_path.is_dir():
                raise ValueError(self._tr("vault_invalid"))
            vault = str(vault_path)
        else:
            vault = normalize_vault_path(vault)
        paths = ensure_data_paths(self.data_dir_var.get().strip() or str(default_data_root()), vault or None)
        limit = int(self.limit_var.get().strip() or "15")
        interval = float(self.interval_var.get().strip() or "2.0")
        score_threshold = float(self.score_threshold_var.get().strip() or "35")
        reranker_batch_cpu = int(self.reranker_batch_cpu_var.get().strip() or "4")
        reranker_batch_cuda = int(self.reranker_batch_cuda_var.get().strip() or "8")
        if limit <= 0 or interval <= 0 or score_threshold < 0 or reranker_batch_cpu <= 0 or reranker_batch_cuda <= 0:
            raise ValueError(self._tr("number_invalid"))
        config = AppConfig(
            vault_path=vault,
            vault_paths=self._collect_vault_paths(vault),
            data_root=str(paths.global_root),
            query_limit=limit,
            query_score_threshold=score_threshold,
            poll_interval_seconds=interval,
            build_resource_profile=self._build_profile_code(self.build_resource_profile_var.get()),
            watch_resource_peak_percent=self._watch_peak_value(self.watch_resource_peak_var.get()),
            vector_backend=self.backend_var.get().strip() or "disabled",
            vector_model=self.model_var.get().strip() or "BAAI/bge-m3",
            vector_runtime=self.runtime_var.get().strip() or "torch",
            vector_device=self.device_var.get().strip() or "auto",
            vector_local_files_only=self.local_only_var.get(),
            reranker_enabled=self.reranker_enabled_var.get(),
            reranker_model=self.reranker_model_var.get().strip() or 'BAAI/bge-reranker-v2-m3',
            reranker_batch_size_cpu=reranker_batch_cpu,
            reranker_batch_size_cuda=reranker_batch_cuda,
            context_export_mode='ai-collab' if self.context_export_ai_collab_var.get() else 'standard',
            rag_filter_core_enabled=self.rag_filter_core_var.get(),
            rag_filter_extended_enabled=self.rag_filter_extended_var.get(),
            rag_filter_custom_rules=self.rag_filter_custom_rules_var.get().strip(),
            page_blocklist_rules=self.page_blocklist_rules_var.get().strip(),
            ui_language=self.language_code,
            ui_theme=self.ui_theme,
            ui_scale_percent=self.ui_scale_percent,
            ui_quick_start_expanded=self.quick_start_expanded_var.get(),
            ui_window_geometry=self.ui_window_geometry,
            ui_main_sash=int(self.ui_main_sash),
            ui_right_sash=int(self.ui_right_sash),
            ui_results_sash=int(self.ui_results_sash),
        )
        return config, paths

    def _save_only(self) -> None:
        try:
            self._apply_ui_preferences_from_controls(rebuild_ui=True, persist=False)
            config, paths = self._config(False)
            self._sync_runtime_layout_to_config(config)
            save_config(config, paths)
            self._set_saved_vaults(config.vault_paths, active_vault=config.vault_path)
            self.status_var.set(self._tr("status_saved", path=paths.config_file))
            self._append_log(self._tr("log_saved_config", path=paths.config_file))
        except Exception as exc:
            messagebox.showerror(self._tr("save_failed_title"), str(exc), parent=self.root)

    def _run_task(self, label_key: str, action, callback, *, config: AppConfig, paths) -> None:
        if self.busy:
            messagebox.showinfo(self._tr("busy_title"), self._tr("busy_body"), parent=self.root)
            return
        if self.watch_thread and self.watch_thread.is_alive():
            messagebox.showinfo(self._tr("stop_watch_first_title"), self._tr("stop_watch_first_body"), parent=self.root)
            return
        try:
            self._sync_runtime_layout_to_config(config)
            save_config(config, paths)
            self._set_saved_vaults(config.vault_paths, active_vault=config.vault_path)
        except Exception as exc:
            messagebox.showerror(self._tr("cannot_start_title"), str(exc), parent=self.root)
            return
        label = self._tr(label_key)
        self.busy = True
        self.status_var.set(f"{label}…")
        self._start_task_feedback(label_key, config, paths)

        def worker() -> None:
            service = OmniClipService(config, paths)
            try:
                payload = action(service)
            except BuildCancelledError:
                self.queue.put(("cancelled", label_key, label, service.status_snapshot(), callback))
            except RuntimeDependencyError as exc:
                self.queue.put(("runtime-error", label_key, label, str(exc).strip()))
            except Exception:
                self.queue.put(("error", label_key, label, traceback.format_exc()))
            else:
                self.queue.put(("success", label_key, label, payload, callback))
            finally:
                service.close()

        threading.Thread(target=worker, daemon=True).start()

    def _start_task(self, label_key: str, action, callback, *, require_vault: bool = True, ensure_model: bool = False) -> None:
        try:
            config, paths = self._config(require_vault)
        except Exception as exc:
            messagebox.showerror(self._tr("cannot_start_title"), str(exc), parent=self.root)
            return
        if (
            ensure_model
            and self._backend_enabled(config)
            and not is_local_model_ready(config, paths)
            and not self._prepare_model_for_followup(
                label_key,
                config,
                paths,
                require_vault,
                lambda: self._start_task(
                    label_key,
                    action,
                    callback,
                    require_vault=require_vault,
                    ensure_model=False,
                ),
            )
        ):
            return
        self._run_task(label_key, action, callback, config=config, paths=paths)

    def _backend_enabled(self, config: AppConfig) -> bool:
        return (config.vector_backend or "disabled").strip().lower() not in {"", "disabled", "none", "off"}

    def _is_model_ready(self) -> bool:
        try:
            config, paths = self._config(False)
        except Exception:
            return False
        if not self._backend_enabled(config):
            return True
        return is_local_model_ready(config, paths)


    def _load_initial_status(self) -> None:
        try:
            config, paths = self._config(False)
        except Exception:
            self.status_var.set(self._tr("status_ready"))
            return
        service = OmniClipService(config, paths)
        try:
            payload = service.status_snapshot()
        finally:
            service.close()
        self._apply_status(payload)
        self.status_var.set(self._tr("status_refresh_done"))
        self._append_log(self._tr("log_status_done"))
        self._offer_resume_rebuild(config, paths, payload)

    def _manual_model_hint(self, config: AppConfig, paths) -> str:
        return self._tr(
            "manual_model_hint",
            mirror_url="https://hf-mirror.com/",
            hf_url=f"https://huggingface.co/{config.vector_model}",
            model=config.vector_model,
            size=format_bytes(estimate_model_cache_bytes(config.vector_model, config.vector_runtime)),
            model_dir=get_local_model_dir(config, paths),
        )

    def _manual_reranker_hint(self, config: AppConfig, paths) -> str:
        model_dir = get_local_reranker_dir(config, paths)
        model_dir.mkdir(parents=True, exist_ok=True)
        return self._tr(
            "manual_reranker_hint",
            mirror_url="https://hf-mirror.com/",
            hf_url=f"https://huggingface.co/{config.reranker_model}",
            model=config.reranker_model,
            size=format_bytes(estimate_model_cache_bytes(config.reranker_model, config.vector_runtime)),
            model_dir=model_dir,
        )

    def _choose_model_download_mode(self, task_label: str, config: AppConfig, paths) -> str | None:
        model_dir = get_local_model_dir(config, paths)
        model_dir.mkdir(parents=True, exist_ok=True)
        size_text = format_bytes(estimate_model_cache_bytes(config.vector_model, config.vector_runtime))
        choice = messagebox.askyesnocancel(
            self._tr("model_prompt_title"),
            self._tr(
                "model_download_choice_body",
                task=task_label,
                model=config.vector_model,
                size=size_text,
                model_dir=model_dir,
            ),
            parent=self.root,
        )
        if choice is True:
            self._append_log(self._tr("log_model_download_prompt"))
            return "auto"
        if choice is False:
            messagebox.showinfo(
                self._tr("model_manual_title"),
                self._manual_model_hint(config, paths),
                parent=self.root,
            )
            self.status_var.set(self._tr("status_manual_download_waiting"))
            self._append_log(self._tr("log_manual_download_hint", model=config.vector_model))
            return "manual"
        self.status_var.set(self._tr("model_prompt_declined"))
        self._append_log(self._tr("log_model_download_declined"))
        return None

    def _choose_reranker_download_mode(self, task_label: str, config: AppConfig, paths) -> str | None:
        model_dir = get_local_reranker_dir(config, paths)
        model_dir.mkdir(parents=True, exist_ok=True)
        size_text = format_bytes(estimate_model_cache_bytes(config.reranker_model, config.vector_runtime))
        choice = messagebox.askyesnocancel(
            self._tr("model_prompt_title"),
            self._tr(
                "reranker_download_choice_body",
                task=task_label,
                model=config.reranker_model,
                size=size_text,
                model_dir=model_dir,
            ),
            parent=self.root,
        )
        if choice is True:
            self._append_log(self._tr("log_reranker_download_prompt"))
            return "auto"
        if choice is False:
            messagebox.showinfo(
                self._tr("reranker_manual_title"),
                self._manual_reranker_hint(config, paths),
                parent=self.root,
            )
            self.status_var.set(self._tr("status_manual_download_waiting"))
            self._append_log(self._tr("log_manual_reranker_hint", model=config.reranker_model))
            return "manual"
        self.status_var.set(self._tr("status_reranker_download_declined"))
        self._append_log(self._tr("log_reranker_download_declined"))
        return None

    def _traceback_summary(self, tb: str) -> str:
        lines = [line.strip() for line in tb.splitlines() if line.strip()]
        for line in reversed(lines):
            if not line.startswith("File "):
                return line
        return lines[-1] if lines else "Unknown error"

    def _friendly_task_error(self, label_key: str, label: str, tb: str) -> str:
        summary = self._traceback_summary(tb)
        if label_key == "bootstrap_button":
            try:
                config, paths = self._config(False)
                manual_hint = self._manual_model_hint(config, paths)
            except Exception:
                manual_hint = ""
            return self._tr("bootstrap_failed_body", error=summary, manual_hint=manual_hint)
        if label_key == "bootstrap_reranker_button":
            try:
                config, paths = self._config(False)
                manual_hint = self._manual_reranker_hint(config, paths)
            except Exception:
                manual_hint = ""
            return self._tr("reranker_failed_body", error=summary, manual_hint=manual_hint)
        return summary

    def _describe_resume_phase(self, phase: str | None) -> str:
        normalized = (phase or "indexing").strip().lower()
        return self._tr(f"resume_phase_{normalized}")

    def _discard_pending_rebuild(self, config: AppConfig, paths) -> None:
        service = OmniClipService(config, paths)
        try:
            service.discard_pending_rebuild()
            payload = service.status_snapshot()
        finally:
            service.close()
        self._apply_status(payload)
        self.status_var.set(self._tr("status_resume_discarded"))
        self._append_log(self._tr("log_resume_discarded"))

    def _offer_resume_rebuild(self, config: AppConfig, paths, payload) -> None:
        pending = payload.get("pending_rebuild") if isinstance(payload, dict) else None
        if not isinstance(pending, dict):
            return
        workspace_id = str(payload.get("workspace_id") or paths.root.name)
        if self.resume_prompt_workspace_id == workspace_id:
            return
        self.resume_prompt_workspace_id = workspace_id
        phase_label = self._describe_resume_phase(str(pending.get("phase") or "indexing"))
        self._append_log(
            self._tr(
                "log_resume_found",
                completed=int(pending.get("completed", 0) or 0),
                total=int(pending.get("total", 0) or 0),
                phase=phase_label,
            )
        )
        should_resume = messagebox.askyesno(
            self._tr("resume_rebuild_title"),
            self._tr(
                "resume_rebuild_body",
                phase=phase_label,
                completed=int(pending.get("completed", 0) or 0),
                total=int(pending.get("total", 0) or 0),
            ),
            parent=self.root,
        )
        if should_resume:
            self._append_log(self._tr("log_resume_continue"))
            self._start_rebuild(resume=True)
            return
        self._discard_pending_rebuild(config, paths)

    def _start_rebuild(self, *, resume: bool) -> None:
        try:
            config, paths = self._config(True)
        except Exception as exc:
            messagebox.showerror(self._tr("cannot_start_title"), str(exc), parent=self.root)
            return
        label_key = "resume_rebuild_task" if resume else "rebuild_button"
        if self._backend_enabled(config) and not is_local_model_ready(config, paths):
            if not self._prepare_model_for_followup(label_key, config, paths, True, lambda: self._start_rebuild(resume=resume)):
                return
            return
        force = self.force_var.get()

        def action(service):
            report = None
            if not resume:
                report = service.estimate_space()
                if not report.can_proceed and not force:
                    return {"blocked": True, "report": report}
            stats = service.rebuild_index(
                resume=resume,
                on_progress=lambda progress: self.queue.put(("progress", progress)),
                pause_event=self.rebuild_pause_event,
                cancel_event=self.rebuild_cancel_event,
            )
            return {"blocked": False, "report": report, "stats": stats, "status": service.status_snapshot(), "resumed": resume}

        self.status_var.set(self._tr("status_resume_building") if resume else self._tr("status_ready"))
        self._run_task(label_key, action, self._after_rebuild, config=config, paths=paths)

    # Why: 先在 UI 层把“自动下载 / 手动下载 / 取消”说清楚，避免第一次查询或建库时像卡死。
    def _prepare_model_for_followup(self, label_key: str, config: AppConfig, paths, require_vault: bool, followup) -> bool:
        choice = self._choose_model_download_mode(self._tr(label_key), config, paths)
        if choice != "auto":
            return False

        if config.vector_local_files_only and not is_local_model_ready(config, paths):
            allow_remote = messagebox.askyesno(
                self._tr("model_prompt_title"),
                self._tr("model_prompt_local_only", manual_hint=self._manual_model_hint(config, paths)),
                parent=self.root,
            )
            if not allow_remote:
                self.status_var.set(self._tr("model_prompt_declined"))
                self._append_log(self._tr("log_model_download_declined"))
                return False
            self.local_only_var.set(False)
            try:
                config, paths = self._config(require_vault)
            except Exception as exc:
                messagebox.showerror(self._tr("cannot_start_title"), str(exc), parent=self.root)
                return False

        def action(service):
            report = service.estimate_space()
            if not report.can_proceed and not self.force_var.get():
                return {"blocked": True, "report": report}
            return {"blocked": False, "report": report, "result": service.bootstrap_model(), "status": service.status_snapshot()}

        def after(payload):
            self._after_bootstrap(payload)
            if payload.get("blocked"):
                return
            followup()

        self._run_task("bootstrap_button", action, after, config=config, paths=paths)
        return False

    def _estimate(self) -> None:
        self._start_task("preflight_button", lambda service: {"report": service.estimate_space(), "status": service.status_snapshot()}, self._after_preflight)

    def _bootstrap(self) -> None:
        try:
            config, paths = self._config(True)
        except Exception as exc:
            messagebox.showerror(self._tr("cannot_start_title"), str(exc), parent=self.root)
            return
        if self._backend_enabled(config) and is_local_model_ready(config, paths):
            self.status_var.set(self._tr("status_model_already_ready"))
            self._append_log(self._tr("log_model_already_ready", model=config.vector_model))
            self._refresh_state_chips()
            messagebox.showinfo(self._tr("model_ready_title"), self._tr("model_ready_body", model=config.vector_model), parent=self.root)
            return

        choice = self._choose_model_download_mode(self._tr("bootstrap_button"), config, paths)
        if choice != "auto":
            return

        if config.vector_local_files_only and not is_local_model_ready(config, paths):
            allow_remote = messagebox.askyesno(
                self._tr("model_prompt_title"),
                self._tr("model_prompt_local_only", manual_hint=self._manual_model_hint(config, paths)),
                parent=self.root,
            )
            if not allow_remote:
                self.status_var.set(self._tr("model_prompt_declined"))
                self._append_log(self._tr("log_model_download_declined"))
                return
            self.local_only_var.set(False)
            try:
                config, paths = self._config(True)
            except Exception as exc:
                messagebox.showerror(self._tr("cannot_start_title"), str(exc), parent=self.root)
                return

        force = self.force_var.get()

        def action(service):
            report = service.estimate_space()
            if not report.can_proceed and not force:
                return {"blocked": True, "report": report}
            return {"blocked": False, "report": report, "result": service.bootstrap_model(), "status": service.status_snapshot()}

        self._run_task("bootstrap_button", action, self._after_bootstrap, config=config, paths=paths)

    def _bootstrap_reranker(self) -> None:
        try:
            config, paths = self._config(False)
        except Exception as exc:
            messagebox.showerror(self._tr("cannot_start_title"), str(exc), parent=self.root)
            return
        if is_local_reranker_ready(config, paths):
            self.status_var.set(self._tr("status_reranker_already_ready"))
            self._append_log(self._tr("log_reranker_already_ready", model=config.reranker_model))
            self._refresh_reranker_state_summary()
            messagebox.showinfo(self._tr("reranker_ready_title"), self._tr("reranker_ready_body", model=config.reranker_model), parent=self.root)
            return
        choice = self._choose_reranker_download_mode(self._tr("bootstrap_reranker_button"), config, paths)
        if choice != "auto":
            return

        def action(service):
            return {"result": service.bootstrap_reranker(), "status": service.status_snapshot()}

        self._run_task("bootstrap_reranker_button", action, self._after_bootstrap_reranker, config=config, paths=paths)

    def _rebuild(self) -> None:
        try:
            config, paths = self._config(True)
        except Exception as exc:
            messagebox.showerror(self._tr("cannot_start_title"), str(exc), parent=self.root)
            return
        service = OmniClipService(config, paths)
        try:
            pending = service.pending_rebuild()
        finally:
            service.close()
        if isinstance(pending, dict):
            phase_label = self._describe_resume_phase(str(pending.get("phase") or "indexing"))
            should_resume = messagebox.askyesno(
                self._tr("resume_rebuild_title"),
                self._tr(
                    "resume_rebuild_body",
                    phase=phase_label,
                    completed=int(pending.get("completed", 0) or 0),
                    total=int(pending.get("total", 0) or 0),
                ),
                parent=self.root,
            )
            if should_resume:
                self._append_log(self._tr("log_resume_continue"))
                self._start_rebuild(resume=True)
                return
            self._discard_pending_rebuild(config, paths)
        if self._index_ready():
            should_rebuild = messagebox.askyesno(
                self._tr("rebuild_confirm_existing_title"),
                self._tr("rebuild_confirm_existing_body"),
                parent=self.root,
            )
            if not should_rebuild:
                return
        self._start_rebuild(resume=False)

    def _refresh(self) -> None:
        self._start_task("refresh_button", lambda service: service.status_snapshot(), self._after_status, require_vault=False)

    def _query(self, copy_result: bool) -> None:
        query = self.query_var.get().strip()
        if not query:
            messagebox.showinfo(self._tr("empty_query_title"), self._tr("empty_query_body"), parent=self.root)
            return
        if not self._index_ready():
            messagebox.showinfo(self._tr("cannot_start_title"), self._tr("query_status_blocked_detail_index"), parent=self.root)
            return
        try:
            score_threshold = float(self.score_threshold_var.get().strip() or "0")
        except ValueError:
            messagebox.showerror(self._tr("cannot_start_title"), self._tr("number_invalid"), parent=self.root)
            return
        self._start_task(
            "search_button",
            lambda service: {
                "query": query,
                "copied": copy_result,
                "score_threshold": score_threshold,
                "payload": service.query(
                    query,
                    copy_result=copy_result,
                    score_threshold=score_threshold,
                    on_progress=lambda progress: self.queue.put(("progress", progress)),
                ),
            },
            self._after_query,
            ensure_model=True,
        )

    def _clear(self) -> None:
        if not any((self.clear_index_var.get(), self.clear_logs_var.get(), self.clear_cache_var.get(), self.clear_exports_var.get())):
            messagebox.showinfo(self._tr("clear_pick_title"), self._tr("clear_pick_body"), parent=self.root)
            return
        if not messagebox.askyesno(self._tr("clear_confirm_title"), self._tr("clear_confirm_body"), parent=self.root):
            return

        def action(service):
            service.clear_data(
                clear_index=self.clear_index_var.get(),
                clear_logs=self.clear_logs_var.get(),
                clear_cache=self.clear_cache_var.get(),
                clear_exports=self.clear_exports_var.get(),
            )
            return service.status_snapshot()

        self._start_task("clear_button", action, self._after_clear, require_vault=True)

    def _toggle_watch(self) -> None:
        if self.watch_thread and self.watch_thread.is_alive():
            if self.watch_stop is not None:
                self.watch_stop.set()
            self.watch_stop_requested = True
            self.status_var.set(self._tr("status_watch_stopping"))
            self._append_log(self._tr("log_watch_requested_stop"))
            self._update_watch_button_state()
            self._refresh_query_status_banner()
            return
        if self.busy:
            messagebox.showinfo(self._tr("busy_title"), self._tr("busy_body"), parent=self.root)
            return
        try:
            config, paths = self._config(True)
        except Exception as exc:
            messagebox.showerror(self._tr("watch_start_failed_title"), str(exc), parent=self.root)
            return
        service = OmniClipService(config, paths)
        try:
            snapshot = service.status_snapshot()
        finally:
            service.close()
        self._apply_status(snapshot)
        index_state = self._current_index_state(snapshot)
        if index_state != "ready":
            body_key = "watch_start_blocked_pending_body" if index_state == "pending" else "watch_start_blocked_missing_body"
            message = self._tr(body_key)
            self.status_var.set(message)
            messagebox.showinfo(self._tr("watch_start_blocked_title"), message, parent=self.root)
            return
        if self._backend_enabled(config) and not is_local_model_ready(config, paths):
            self._prepare_model_for_followup("watch_start", config, paths, True, self._toggle_watch)
            return
        try:
            save_config(config, paths)
        except Exception as exc:
            messagebox.showerror(self._tr("watch_start_failed_title"), str(exc), parent=self.root)
            return

        self.watch_stop = threading.Event()
        self.watch_stop_requested = False
        mode = self._watch_mode_label(self.polling_var.get() or not WATCHDOG_AVAILABLE)
        self.status_var.set(self._tr("status_watch_running"))
        self.watch_var.set(self._tr("watch_running", mode=mode, seconds=config.poll_interval_seconds))
        self._append_log(self._tr("log_watch_started", mode=mode))
        self._update_watch_button_state()

        def worker() -> None:
            service = OmniClipService(config, paths)
            try:
                service.watch_until_stopped(
                    self.watch_stop,
                    interval=config.poll_interval_seconds,
                    force_polling=self.polling_var.get(),
                    on_update=lambda payload: self.queue.put(("watch-update", payload)),
                )
            except Exception:
                self.queue.put(("watch-error", traceback.format_exc()))
            finally:
                service.close()
                raw_mode = "polling" if self.polling_var.get() or not WATCHDOG_AVAILABLE else "watchdog"
                self.queue.put(("watch-stopped", raw_mode))

        self.watch_thread = threading.Thread(target=worker, daemon=True)
        self.watch_thread.start()
        self._refresh_query_status_banner()

    def _after_preflight(self, payload) -> None:
        report = payload["report"]
        self.current_report = report
        self.context_view_text = ""
        self.preflight_var.set(summarize_preflight(report, self.language_code))
        self._apply_status(payload.get("status"))
        self.status_var.set(self._tr("status_preflight_done"))
        self._append_log(self._tr("log_preflight_done"))
        self._append_log(format_space_report(report, self.language_code))

    def _after_bootstrap(self, payload) -> None:
        report = payload.get("report")
        if report is not None:
            self.current_report = report
            self.preflight_var.set(summarize_preflight(report, self.language_code))
            self._append_log(format_space_report(report, self.language_code))
        if payload.get("blocked"):
            self.status_var.set(self._tr("bootstrap_blocked_title"))
            self._append_log(self._tr("log_bootstrap_blocked"))
            messagebox.showwarning(self._tr("bootstrap_blocked_title"), self._tr("bootstrap_blocked_body"), parent=self.root)
            return
        result = payload["result"]
        self._apply_status(payload.get("status"))
        self.status_var.set(self._tr("status_bootstrap_done"))
        self._append_log(
            self._tr(
                "log_bootstrap_done",
                model=result.get("model"),
                dimension=result.get("dimension"),
                cache=format_bytes(int(result.get("cache_bytes", 0))),
            )
        )
        self._refresh_state_chips()

    def _after_bootstrap_reranker(self, payload) -> None:
        result = payload['result']
        self._apply_status(payload.get('status'))
        self.status_var.set(self._tr('status_reranker_ready'))
        self._append_log(self._tr('log_reranker_ready', model=result.get('model')))
        self._refresh_state_chips()

    def _after_rebuild(self, payload) -> None:
        report = payload.get("report")
        if report is not None:
            self.current_report = report
            self.preflight_var.set(summarize_preflight(report, self.language_code))
        if payload.get("blocked"):
            self.status_var.set(self._tr("rebuild_blocked_title"))
            self._append_log(self._tr("log_rebuild_blocked"))
            messagebox.showwarning(self._tr("rebuild_blocked_title"), self._tr("rebuild_blocked_body"), parent=self.root)
            return
        stats = payload["stats"]
        self._apply_status(payload.get("status"))
        duplicate_count = int(stats.get("duplicate_block_ids", 0))
        if duplicate_count:
            self.status_var.set(self._tr("status_rebuild_done_duplicates", count=duplicate_count))
            self._append_log(self._tr("log_duplicate_block_ids", count=duplicate_count))
        else:
            self.status_var.set(self._tr("status_rebuild_done"))
        self._append_log(self._tr("log_rebuild_done", files=stats["files"], chunks=stats["chunks"], refs=stats["refs"]))
        self._refresh_state_chips()

    def _after_status(self, payload) -> None:
        self._apply_status(payload)
        self.status_var.set(self._tr("status_refresh_done"))
        self._append_log(self._tr("log_status_done"))

    def _after_query(self, payload) -> None:
        query = payload["query"]
        copied = payload["copied"]
        result = payload["payload"]
        hits = list(result.hits)
        self.current_query_text = query
        self.current_hits = hits
        self.selected_chunk_ids = {hit.chunk_id for hit in hits}
        self.result_sort_column = None
        self.result_sort_reverse = False
        self.result_page_sort_active = False
        self.result_page_sort_restore_order = []
        self.result_page_sort_restore_column = None
        self.result_page_sort_restore_reverse = False
        self.query_last_completed_at = time.time()
        self.query_last_result_count = len(hits)
        self.query_last_copied = copied
        selected_chunk_id = hits[0].chunk_id if hits else None
        self._render_hits(selected_chunk_id=selected_chunk_id)
        self._rebuild_context_view()
        self._update_query_limit_guidance(asdict(result.insights.recommendation) if result.insights.recommendation is not None else None)
        if hits:
            self._show_hit(0)
        else:
            self._set_text(self.preview_text, self._tr("no_results"))
        self._show_query_workspace(1)
        self.result_var.set(self._tr("query_hits", count=len(hits)))
        self.status_var.set(self._tr("status_query_copied") if copied else self._tr("status_query_done"))
        self._append_log(self._tr("log_query_done", query=query, count=len(hits)))
        self._refresh_query_status_banner()
        if result.insights.reranker is not None and result.insights.reranker.enabled:
            if result.insights.reranker.applied:
                self._append_log(self._tr('log_reranker_applied', device=result.insights.reranker.resolved_device, count=result.insights.reranker.reranked_count))
            else:
                self._append_log(self._tr('log_reranker_skipped', reason=result.insights.reranker.skipped_reason or self._tr('none_value')))

    def _after_clear(self, payload) -> None:
        self.clear_index_var.set(False)
        self.clear_logs_var.set(False)
        self.clear_cache_var.set(False)
        self.clear_exports_var.set(False)
        self._apply_status(payload)
        self.status_var.set(self._tr("status_clear_done"))
        self._append_log(self._tr("log_clear_done"))
        self._refresh_state_chips()

    def _apply_status(self, payload) -> None:
        if not isinstance(payload, dict):
            self.status_snapshot = None
            self._refresh_preflight_notice()
            return
        self.status_snapshot = dict(payload)
        recommendation = payload.get("query_limit_recommendation")
        if isinstance(recommendation, dict):
            self._update_query_limit_guidance(recommendation)
        stats = payload.get("stats") or {}
        self.files_var.set(str(stats.get("files", 0)))
        self.chunks_var.set(str(stats.get("chunks", 0)))
        self.refs_var.set(str(stats.get("refs", 0)))
        latest = payload.get("latest_preflight")
        if isinstance(latest, dict):
            self.latest_preflight_snapshot = latest
            self.preflight_var.set(
                self._tr(
                    "recent_preflight",
                    risk=latest.get("risk_level"),
                    required=format_bytes(int(latest.get("required_free_bytes", 0))),
                    available=format_bytes(int(latest.get("available_free_bytes", 0))),
                )
            )
        elif self.current_report is not None:
            self.preflight_var.set(summarize_preflight(self.current_report, self.language_code))
        else:
            self.preflight_var.set(self._tr("preflight_empty"))
        self._refresh_preflight_notice()
        backend = payload.get("vector_backend") or self.backend_var.get().strip() or "disabled"
        watch_text = self._tr("watch_ready") if payload.get("watchdog_available", WATCHDOG_AVAILABLE) else self._tr("watch_fallback")
        if not (self.watch_thread and self.watch_thread.is_alive()):
            self.watch_var.set(self._tr("vector_watch_summary", backend=backend, watch_text=watch_text))
        self._refresh_state_chips()
        self._update_watch_button_state()
        self._refresh_reranker_state_summary(payload)

    def _select_hit(self, _event=None) -> None:
        selection = self.tree.selection()
        if selection:
            hit_index = self._find_hit_index(selection[0])
            if hit_index is not None:
                self._show_hit(hit_index)

    def _show_hit(self, index: int) -> None:
        if index < 0 or index >= len(self.current_hits):
            return
        hit = self.current_hits[index]
        self._set_text(
            self.preview_text,
            (
                f"{self._tr('col_page')}：{hit.title}\n"
                f"{self._tr('col_anchor')}：{hit.anchor}\n"
                f"{self._tr('col_source')}：{hit.source_path}\n"
                f"{self._tr('col_score')}：{hit.score:.1f}/100\n"
                f"{self._tr('col_reason')}：{hit.reason or self._tr('reason_fallback')}\n\n"
                f"{self._tr('preview_excerpt_label')}\n{hit.preview_text or self._tr('none_value')}\n\n"
                f"{self._tr('preview_full_label')}\n{hit.display_text or hit.rendered_text}"
            ),
        )

    def _copy_context(self) -> None:
        self._rebuild_context_view()
        if not self.current_context.strip():
            messagebox.showinfo(self._tr("copy_empty_title"), self._tr("copy_empty_body"), parent=self.root)
            return
        copy_text(self.current_context)
        self.status_var.set(self._tr("status_context_copied"))
        self._append_log(self._tr("log_context_copied"))

    def _open_vault(self) -> None:
        try:
            config, paths = self._config(True)
        except Exception as exc:
            messagebox.showinfo(self._tr("not_ready_title"), str(exc), parent=self.root)
            return
        service = OmniClipService(config, paths)
        try:
            service.open_vault_dir()
        finally:
            service.close()

    def _open_data(self) -> None:
        try:
            config, paths = self._config(True)
        except Exception as exc:
            messagebox.showinfo(self._tr("not_ready_title"), str(exc), parent=self.root)
            return
        service = OmniClipService(config, paths)
        try:
            service.open_data_dir()
        finally:
            service.close()

    def _open_exports(self) -> None:
        try:
            config, paths = self._config(True)
        except Exception as exc:
            messagebox.showinfo(self._tr("not_ready_title"), str(exc), parent=self.root)
            return
        service = OmniClipService(config, paths)
        try:
            service.open_exports_dir()
        finally:
            service.close()

    def _append_log(self, message: str) -> None:
        message = message.strip()
        if not message:
            return
        self.log_lines.append(message)
        if hasattr(self, "log_text"):
            if len(self.log_lines) == 1:
                self._set_text(self.log_text, message)
            else:
                self._append_text(self.log_text, message)
            self.log_text.see("end")

    def _append_watch_events(self, events: list[dict[str, object]]) -> None:
        for event in events:
            kind = str(event.get("kind") or "").strip().lower()
            if kind == "vault_offline":
                self._append_log(self._tr("log_watch_vault_offline", reason=str(event.get("reason") or self._tr("none_value"))))
            elif kind == "vault_recovered":
                self._append_log(self._tr("log_watch_vault_recovered"))
            elif kind == "repair":
                self._append_log(
                    self._tr(
                        "log_watch_repaired",
                        paths=int(event.get("paths", 0) or 0),
                        vector_paths=int(event.get("vector_paths", 0) or 0),
                        vector_chunk_ids=int(event.get("vector_chunk_ids", 0) or 0),
                    )
                )
            elif kind == "batch_retry":
                changed = ", ".join(event.get("changed", [])[:3]) or self._tr("none_value")
                deleted = ", ".join(event.get("deleted", [])[:3]) or self._tr("none_value")
                self._append_log(
                    self._tr(
                        "log_watch_batch_retry",
                        changed=changed,
                        deleted=deleted,
                        error=str(event.get("error") or self._tr("none_value")),
                    )
                )

    def _set_text(self, widget: tk.Text, text_value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text_value)
        widget.configure(state="disabled")
        self._refresh_text_search_state(widget)

    def _append_text(self, widget: tk.Text, text_value: str) -> None:
        widget.configure(state="normal")
        widget.insert("end", f"\n{text_value}")
        widget.configure(state="disabled")
        self._refresh_text_search_state(widget)

    def _update_watch_button_state(self) -> None:
        if not hasattr(self, "watch_button"):
            return
        if self.watch_thread and self.watch_thread.is_alive():
            state = "disabled" if self.watch_stop_requested else "normal"
            self.watch_button.configure(text=self._tr("watch_stop"), style="Danger.TButton", state=state)
        else:
            state = "normal" if not self.busy else "disabled"
            self.watch_button.configure(text=self._tr("watch_start"), style="Primary.TButton", state=state)

    def _drain_queue(self) -> None:
        processed = 0
        latest_progress_payload = None
        while processed < UI_QUEUE_BATCH_SIZE:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            processed += 1
            kind = item[0]
            if kind == "success":
                _, label_key, _label, payload, callback = item
                self.busy = False
                self._stop_task_feedback()
                if callback:
                    callback(payload)
            elif kind == "cancelled":
                _, label_key, _label, payload, _callback = item
                self.busy = False
                self._stop_task_feedback()
                self._apply_status(payload)
                if self._is_rebuild_task(label_key):
                    self.status_var.set(self._tr("status_rebuild_cancelled"))
                    self._append_log(self._tr("log_rebuild_cancelled"))
                else:
                    self.status_var.set(self._tr("status_failed", label=self._tr(label_key)))
            elif kind == "runtime-error":
                _, _label_key, label, message = item
                self.busy = False
                self._stop_task_feedback()
                self.status_var.set(self._tr("status_failed", label=label))
                self._append_log(message)
                self._show_query_workspace(2)
                messagebox.showerror(label, message, parent=self.root)
            elif kind == "error":
                _, label_key, label, tb = item
                self.busy = False
                self._stop_task_feedback()
                self.status_var.set(self._tr("status_failed", label=label))
                self._append_log(tb.strip())
                self._show_query_workspace(2)
                messagebox.showerror(label, self._friendly_task_error(label_key, label, tb), parent=self.root)
            elif kind == "progress":
                _, payload = item
                latest_progress_payload = payload
            elif kind == "watch-update":
                _, payload = item
                stats = payload.get("stats", {})
                if self.status_snapshot is None:
                    self.status_snapshot = {}
                self.status_snapshot = dict(self.status_snapshot)
                self.status_snapshot["stats"] = stats
                self.files_var.set(str(stats.get("files", 0)))
                self.chunks_var.set(str(stats.get("chunks", 0)))
                self.refs_var.set(str(stats.get("refs", 0)))
                events = payload.get("events", [])
                if events:
                    self._append_watch_events(events)
                if not payload.get("note_only"):
                    self.status_var.set(self._tr("status_watch_update"))
                    changed = ", ".join(payload.get("changed", [])[:3]) or self._tr("none_value")
                    deleted = ", ".join(payload.get("deleted", [])[:3]) or self._tr("none_value")
                    self._append_log(self._tr("log_watch_update", changed=changed, deleted=deleted))
                    duplicate_count = int(stats.get("duplicate_block_ids", 0) or 0)
                    if duplicate_count:
                        self._append_log(self._tr("log_duplicate_block_ids", count=duplicate_count))
                self._refresh_state_chips()
            elif kind == "watch-error":
                _, tb = item
                self.status_var.set(self._tr("status_watch_error"))
                self._append_log(self._tr("log_watch_error"))
                self._append_log(tb.strip())
                self._show_query_workspace(2)
                messagebox.showerror(self._tr("watch_start_failed_title"), "\n".join([line.strip() for line in tb.splitlines() if line.strip()][-6:]), parent=self.root)
            elif kind == "watch-stopped":
                _, mode = item
                localized_mode = self._watch_mode_label(mode)
                self.watch_var.set(self._tr("watch_stopped", mode=localized_mode))
                self.status_var.set(self._tr("status_watch_stopped"))
                self._append_log(self._tr("log_watch_stopped"))
                self.watch_thread = None
                self.watch_stop = None
                self.watch_stop_requested = False
                self._update_watch_button_state()
        if latest_progress_payload is not None:
            self._update_task_progress(latest_progress_payload)
        self._refresh_query_status_banner()
        if self.root.winfo_exists():
            has_backlog = processed >= UI_QUEUE_BATCH_SIZE or not self.queue.empty()
            delay_ms = UI_QUEUE_FAST_POLL_MS if has_backlog else UI_QUEUE_IDLE_POLL_MS
            self.queue_after_id = self.root.after(delay_ms, self._drain_queue)

    def _on_close(self) -> None:
        if self.watch_stop is not None:
            self.watch_stop.set()
        self._stop_task_feedback()
        self._cancel_scheduled_context_refresh()
        self._cancel_all_deferred_ui_callbacks()
        if self.queue_after_id is not None:
            try:
                self.root.after_cancel(self.queue_after_id)
            except Exception:
                pass
            self.queue_after_id = None
        if self.layout_after_id is not None:
            try:
                self.root.after_cancel(self.layout_after_id)
            except Exception:
                pass
            self.layout_after_id = None
        self._cancel_window_geometry_capture()
        self._cancel_ui_interaction_timer()
        self.ui_interaction_active = False
        self._capture_layout_state()
        try:
            self._apply_ui_preferences_from_controls(rebuild_ui=False, persist=False)
            config, paths = self._config(False)
            self._sync_runtime_layout_to_config(config)
            save_config(config, paths)
        except Exception:
            pass
        self.root.destroy()


def main() -> int:
    return OmniClipDesktopApp().run()


if __name__ == "__main__":
    raise SystemExit(main())












