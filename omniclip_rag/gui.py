from __future__ import annotations

import ctypes
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
from .config import AppConfig, default_data_root, ensure_data_paths, load_config, normalize_vault_path, save_config
from .errors import BuildCancelledError, RuntimeDependencyError
from .formatting import format_bytes, format_duration, format_space_report, summarize_preflight
from .preflight import estimate_model_cache_bytes
from .service import WATCHDOG_AVAILABLE, OmniClipService
from .ui_i18n import language_code_from_label, language_label, normalize_language, text, tooltip
from .ui_tooltip import ToolTip
from .vector_index import detect_acceleration, get_device_options, get_local_model_dir, is_local_model_ready, resolve_vector_device

APP_TITLE = "OmniClip RAG · 方寸引"
APP_VERSION = "V0.1.8"
REPO_URL = "https://github.com/msjsc001/OmniClip-RAG"
_CONTEXT_PAGE_RE = re.compile(r'^# 笔记名：(.*)$')
_CONTEXT_FRAGMENT_RE = re.compile(r'^笔记片段\d+：$')
DEFAULT_PAGE_FILTER_RULES: tuple[tuple[bool, str], ...] = (
    (True, r"^2026-.*\.android$"),
    (True, r"^.*\.sync-conflict-\d{8}-\d{6}-[A-Z0-9]+$"),
    (True, r"^\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2}\.\d{3}Z\.(?:Desktop|android)$"),
    (True, r"^hls__.*?_\d+_\d+_\d+_\d+$"),
)


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

    def _init_style(self) -> None:
        self.colors = {
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
        }
        self.root.configure(bg=self.colors["bg"])
        self.root.option_add("*Font", "{Segoe UI} 10")
        self.root.option_add("*TCombobox*Listbox.font", "{Segoe UI} 10")
        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(family="Segoe UI", size=10)
        text_font = tkfont.nametofont("TkTextFont")
        text_font.configure(family="Segoe UI", size=10)
        fixed_font = tkfont.nametofont("TkFixedFont")
        fixed_font.configure(family="Consolas", size=10)

        self.fonts = {
            "header_title": ("Segoe UI Semibold", 17),
            "header_subtitle": ("Segoe UI", 10),
            "guide": ("Segoe UI", 10),
            "card_title": ("Segoe UI Semibold", 12),
            "body": ("Segoe UI", 10),
            "small": ("Segoe UI", 9),
            "chip": ("Segoe UI Semibold", 9),
            "value": ("Segoe UI Semibold", 15),
        }

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Primary.TButton", background=self.colors["accent"], foreground="#FFFFFF", borderwidth=0, padding=(14, 10), font=("Segoe UI Semibold", 10))
        style.map("Primary.TButton", background=[("active", self.colors["accent_dark"])])
        style.configure("Secondary.TButton", background=self.colors["soft"], foreground=self.colors["ink"], bordercolor=self.colors["border"], padding=(14, 10), font=("Segoe UI", 10))
        style.map("Secondary.TButton", background=[("active", "#E5ECF3")])
        style.configure("Danger.TButton", background=self.colors["danger"], foreground="#FFFFFF", borderwidth=0, padding=(14, 10), font=("Segoe UI Semibold", 10))
        style.map("Danger.TButton", background=[("active", self.colors["danger_dark"])])
        style.configure("Field.TEntry", fieldbackground="#FFFFFF", foreground=self.colors["ink"], bordercolor=self.colors["border"], lightcolor=self.colors["border"], darkcolor=self.colors["border"], padding=7)
        style.configure("Query.TEntry", fieldbackground="#FFFFFF", foreground=self.colors["ink"], bordercolor=self.colors["border"], lightcolor=self.colors["border"], darkcolor=self.colors["border"], padding=10)
        style.configure("Field.TCombobox", fieldbackground="#FFFFFF", foreground=self.colors["ink"], bordercolor=self.colors["border"], lightcolor=self.colors["border"], darkcolor=self.colors["border"], padding=5)
        style.configure("Plain.TCheckbutton", background=self.colors["card"], foreground=self.colors["ink"], font=("Segoe UI", 10))
        style.configure("App.Treeview", background="#FFFFFF", fieldbackground="#FFFFFF", foreground=self.colors["ink"], rowheight=30, bordercolor=self.colors["border"], relief="flat")
        style.map("App.Treeview", background=[("selected", self.colors["select"])], foreground=[("selected", self.colors["ink"])])
        style.configure("App.Treeview.Heading", background=self.colors["soft"], foreground=self.colors["ink"], font=("Segoe UI Semibold", 10), relief="flat")
        style.configure("App.TNotebook", background=self.colors["card"], borderwidth=0)
        style.configure("App.TNotebook.Tab", background=self.colors["soft"], foreground=self.colors["ink"], padding=(14, 8), font=("Segoe UI", 10))
        style.map("App.TNotebook.Tab", background=[("selected", self.colors["card"])])

    def _init_vars(self) -> None:
        self.language_code = normalize_language(None)
        self.language_var = tk.StringVar(value=language_label(self.language_code))
        self.vault_var = tk.StringVar()
        self.saved_vault_var = tk.StringVar()
        self.saved_vaults: list[str] = []
        self.data_dir_var = tk.StringVar(value=str(default_data_root()))
        self.backend_var = tk.StringVar(value="lancedb")
        self.model_var = tk.StringVar(value="BAAI/bge-m3")
        self.runtime_var = tk.StringVar(value="torch")
        self.device_var = tk.StringVar(value="auto")
        self.device_summary_var = tk.StringVar(value="")
        self.limit_var = tk.StringVar(value="15")
        self.score_threshold_var = tk.StringVar(value="0")
        self.interval_var = tk.StringVar(value="2.0")
        self.query_var = tk.StringVar()
        self.context_selection_var = tk.StringVar(value="")
        self.context_toggle_var = tk.StringVar(value=self._tr("context_select_all"))
        self.result_sort_column: str | None = None
        self.result_sort_reverse = False
        self.local_only_var = tk.BooleanVar(value=False)
        self.rag_filter_core_var = tk.BooleanVar(value=True)
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
        self.quick_start_expanded_var = tk.BooleanVar(value=True)
        self.clear_index_var = tk.BooleanVar(value=False)
        self.clear_logs_var = tk.BooleanVar(value=False)
        self.clear_cache_var = tk.BooleanVar(value=False)
        self.clear_exports_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value=self._tr("status_ready"))
        self.preflight_var = tk.StringVar(value=self._tr("preflight_empty"))
        self.watch_var = tk.StringVar(value=self._default_watch_summary())
        self.files_var = tk.StringVar(value="0")
        self.chunks_var = tk.StringVar(value="0")
        self.refs_var = tk.StringVar(value="0")
        self.result_var = tk.StringVar(value=self._tr("result_empty"))
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
        self.layout_after_id: str | None = None
        self.capture_after_id: str | None = None
        self.active_task_key: str | None = None
        self.active_task_config: AppConfig | None = None
        self.resume_prompt_workspace_id: str | None = None
        self.current_query_text = ""
        self.selected_chunk_ids: set[str] = set()
        self.device_options = get_device_options()
        self.ui_window_geometry = "1560x1000"
        self.ui_main_sash = 900
        self.ui_right_sash = 280
        self.ui_results_sash = 300

    def _tr(self, key: str, **kwargs) -> str:
        return text(self.language_code, key, **kwargs)

    def _tip(self, key: str, **kwargs) -> str:
        return tooltip(self.language_code, key, **kwargs)

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

    def _rebuild_context_view(self) -> None:
        if not self.current_query_text and not self.current_hits:
            self.current_context = ''
            self.context_view_text = ''
            self._refresh_context_jump_controls()
            self._update_context_selection_summary()
            return
        self.current_context = OmniClipService.compose_context_pack_text(self.current_query_text, self._selected_hits())
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
        try:
            index = int(selection[0])
        except (TypeError, ValueError):
            return None
        if index < 0 or index >= len(self.current_hits):
            return None
        return self.current_hits[index].chunk_id

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
        self.root.bind("<Configure>", self._capture_window_geometry, add="+")
        for pane_name in ("right_pane", "results_pane"):
            pane = getattr(self, pane_name, None)
            if pane is not None:
                pane.bind("<ButtonRelease-1>", self._capture_layout_state, add="+")

    def _capture_window_geometry(self, _event=None) -> None:
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

    def _apply_layout_state(self) -> None:
        geometry = (self.ui_window_geometry or '').strip()
        if geometry:
            try:
                self.root.geometry(geometry)
            except Exception:
                pass

        pane_specs = (
            ("main_pane", "ui_main_sash", 760, 420),
            ("right_pane", "ui_right_sash", 260, 420),
            ("results_pane", "ui_results_sash", 260, 280),
        )

        def restore(attempt: int = 0) -> None:
            self.layout_after_id = None
            pending = False
            for attr_name, state_name, min_first, min_second in pane_specs:
                pane = getattr(self, attr_name, None)
                position = getattr(self, state_name, 0)
                if pane is None or not position:
                    continue
                if pane.winfo_width() <= 1 and pane.winfo_height() <= 1:
                    pending = True
                    continue
                try:
                    pane.sashpos(0, self._clamp_sash_position(pane, int(position), min_first=min_first, min_second=min_second))
                except Exception:
                    pending = True
            if pending and attempt < 8 and self.root.winfo_exists():
                self.layout_after_id = self.root.after(80, lambda: restore(attempt + 1))

        if self.layout_after_id is not None:
            try:
                self.root.after_cancel(self.layout_after_id)
            except Exception:
                pass
            self.layout_after_id = None
        self.layout_after_id = self.root.after(0, restore)

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
        safe_first = max(80, min(min_first, max(total - 140, 80)))
        safe_second = max(140, min(min_second, max(total - 80, 140)))
        upper = max(safe_first, total - safe_second)
        return max(safe_first, min(int(requested), upper))

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

        inner.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window_id, width=event.width))
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
        data_tab, data_body = self._make_scrollable_tab(self.left_tabs)
        self.left_tabs.add(start_tab, text=self._tr("left_tab_start"))
        self.left_tabs.add(settings_tab, text=self._tr("left_tab_settings"))
        self.left_tabs.add(data_tab, text=self._tr("left_tab_data"))

        self._build_quick_start_card(start_body)
        self._build_settings_card(settings_body)
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
        quick_start_button = ttk.Button(
            toggle_row,
            text=self._tr("quick_start_show") if not self.quick_start_expanded_var.get() else self._tr("quick_start_hide"),
            style="Secondary.TButton",
            command=self._toggle_quick_start,
        )
        quick_start_button.grid(row=0, column=0, sticky="w")
        self._attach_tooltip(quick_start_button, "quick_start_toggle")

        if self.quick_start_expanded_var.get():
            steps = tk.Frame(guide_panel, bg=self.colors["soft_2"])
            steps.grid(row=2, column=0, sticky="ew", padx=16, pady=(12, 0))
            for index, key in enumerate(("step_1", "step_2", "step_3")):
                badge = tk.Label(steps, text=str(index + 1), bg=self.colors["accent_soft"], fg=self.colors["accent_dark"], font=self.fonts["chip"], width=2, pady=3)
                badge.grid(row=index, column=0, sticky="nw")
                tk.Label(
                    steps,
                    text=self._tr(key),
                    bg=self.colors["soft_2"],
                    fg=self.colors["ink"],
                    font=self.fonts["body"],
                    anchor="w",
                    justify="left",
                    wraplength=360,
                ).grid(row=index, column=1, sticky="w", padx=(8, 0), pady=(0 if index == 0 else 8, 0))
            chip_row = 3
        else:
            chip_row = 2

        chips = tk.Frame(guide_panel, bg=self.colors["soft_2"])
        chips.grid(row=chip_row, column=0, sticky="ew", padx=16, pady=(14, 14))
        chips.grid_columnconfigure((0, 1, 2), weight=1)
        self.vault_chip = self._chip(chips, self.vault_state_var, 0)
        self.model_chip = self._chip(chips, self.model_state_var, 1)
        self.index_chip = self._chip(chips, self.index_state_var, 2)

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
        watch_label = tk.Label(status_panel, textvariable=self.watch_var, bg=self.colors["soft_2"], fg=self.colors["muted"], font=self.fonts["small"], justify="left", anchor="w")
        watch_label.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        self._configure_responsive_wrap(watch_label, padding=20, min_wrap=220, max_wrap=520)

        task_panel = tk.Frame(status_panel, bg=self.colors["soft_2"], highlightbackground=self.colors["border"], highlightthickness=1)
        task_panel.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 14))
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

        toggle_button = ttk.Button(action_panel, text=self._tr("advanced_hide") if self.show_advanced_var.get() else self._tr("advanced_show"), style="Secondary.TButton", command=self._toggle_advanced)
        toggle_button.grid(row=1, column=0, sticky="ew", padx=16, pady=(12, 0))

        if self.show_advanced_var.get():
            advanced = tk.Frame(action_panel, bg=self.colors["soft_2"])
            advanced.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 0))
            local_only = ttk.Checkbutton(advanced, text=self._tr("local_only_label"), variable=self.local_only_var, style="Plain.TCheckbutton")
            local_only.grid(row=0, column=0, sticky="w")
            self._attach_tooltip(local_only, "local_only")
            force = ttk.Checkbutton(advanced, text=self._tr("force_label"), variable=self.force_var, style="Plain.TCheckbutton")
            force.grid(row=1, column=0, sticky="w", pady=(6, 0))
            self._attach_tooltip(force, "force")
            polling = ttk.Checkbutton(advanced, text=self._tr("polling_label"), variable=self.polling_var, style="Plain.TCheckbutton")
            polling.grid(row=2, column=0, sticky="w", pady=(6, 0))
            self._attach_tooltip(polling, "polling")

        refresh_button = ttk.Button(action_panel, text=self._tr("refresh_button"), style="Secondary.TButton", command=self._refresh)
        refresh_button.grid(row=3, column=0, sticky="ew", padx=16, pady=(14, 14))
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
        self._attach_tooltip(limit_label, "limit")
        limit_entry = ttk.Entry(query_meta, textvariable=self.limit_var, width=8, style="Field.TEntry")
        limit_entry.grid(row=0, column=3, sticky="w", padx=(8, 14))
        self._attach_tooltip(limit_entry, "limit")

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
        result_toolbar.grid_columnconfigure(2, weight=1)
        self.context_toggle_button = ttk.Button(result_toolbar, textvariable=self.context_toggle_var, style="Secondary.TButton", command=self._toggle_all_hit_selection)
        self.context_toggle_button.grid(row=0, column=0, sticky="w")
        self._attach_tooltip(self.context_toggle_button, "context_select_toggle")
        tk.Label(result_toolbar, textvariable=self.page_blocklist_summary_var, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w").grid(row=0, column=1, sticky="w", padx=(12, 18))
        tk.Label(result_toolbar, textvariable=self.context_selection_var, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w").grid(row=0, column=2, sticky="w")

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

    def _combo_row(self, parent: tk.Widget, row: int, label: str, variable: tk.StringVar, values: list[str], *, tooltip_key: str) -> None:
        background = str(parent.cget("bg"))
        caption = tk.Label(parent, text=label, bg=background, fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        caption.grid(row=row, column=0, sticky="w", pady=(8, 0))
        self._attach_tooltip(caption, tooltip_key)
        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", style="Field.TCombobox")
        combo.grid(row=row, column=1, columnspan=3, sticky="ew", pady=(8, 0))
        self._attach_tooltip(combo, tooltip_key)

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
        text_widget = tk.Text(frame, wrap="word", relief="flat", borderwidth=0, highlightthickness=0, background="#FFFFFF", foreground=self.colors["ink"], insertbackground=self.colors["ink"], font=("Segoe UI", 10), padx=14, pady=12)
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
        widget.tag_configure("search_current", background="#FDE68A", foreground=self.colors["ink"])
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

        def _update(_event=None) -> None:
            try:
                if not int(widget.winfo_exists()) or not int(parent.winfo_exists()):
                    return
                width = parent.winfo_width()
            except Exception:
                return
            available = max(0, int(width) - padding)
            wraplength = 0 if available < min_wrap else min(max_wrap, available)
            try:
                current = int(widget.cget("wraplength"))
            except Exception:
                current = -1
            if current != wraplength:
                widget.configure(wraplength=wraplength)

        parent.bind("<Configure>", _update, add="+")
        _update()

    def _attach_tooltip(self, widget: tk.Widget, key: str, **kwargs) -> None:
        tip_text = self._tip(key, **kwargs)
        if tip_text:
            self.tooltips.append(ToolTip(widget, tip_text))

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
        self.current_context = ""
        self.current_report = None
        self.latest_preflight_snapshot = None
        self.context_view_text = ""
        self.context_jump_var.set("")
        self.context_jump_options = []
        self.context_jump_summary_var.set(self._tr("context_jump_summary_empty"))
        self.resume_prompt_workspace_id = None
        self.files_var.set("0")
        self.chunks_var.set("0")
        self.refs_var.set("0")
        self.preflight_var.set(self._tr("preflight_empty"))
        self.result_var.set(self._tr("result_empty"))
        self._update_context_selection_summary()

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
            if hasattr(self, "task_progress"):
                self.task_progress.stop()
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
        if hasattr(self, "task_progress"):
            self.task_progress.stop()
            self.task_progress.configure(mode="indeterminate", maximum=100, value=0)
            self.task_progress.start(12)
        self._tick_task_feedback()

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
        if hasattr(self, "task_progress"):
            self.task_progress.stop()
            self.task_progress.configure(mode="indeterminate", maximum=100, value=0)
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
                self.task_progress.stop()
                self.task_progress.configure(mode="determinate", maximum=max(total, 1), value=min(current, total))
            else:
                self.task_progress.stop()
                self.task_progress.configure(mode="indeterminate", maximum=100, value=0)
            return

        if stage == "indexing" and total > 0:
            self.task_progress.stop()
            self.task_progress.configure(mode="determinate", maximum=max(total, 1), value=min(current, total))
            current_path = str(payload.get("current_path") or self._tr("none_value"))
            self.task_detail_var.set(self._tr("task_detail_rebuild_progress", current=current, total=total, path=current_path))
        elif stage == "rendering":
            if total > 0:
                self.task_progress.stop()
                self.task_progress.configure(mode="determinate", maximum=max(total, 1), value=min(current, total))
                self.task_detail_var.set(self._tr("task_detail_rebuild_rendering_progress", current=current, total=total))
            else:
                self.task_progress.stop()
                self.task_progress.configure(mode="indeterminate", maximum=100, value=0)
                self.task_progress.start(10)
                self.task_detail_var.set(self._tr("task_detail_rebuild_rendering"))
        elif stage == "vectorizing":
            if stage_status == 'loading_model' and current <= 0:
                self.task_progress.stop()
                self.task_progress.configure(mode="indeterminate", maximum=100, value=0)
                self.task_progress.start(10)
                self.task_detail_var.set(self._tr("task_detail_rebuild_vector_loading"))
            elif total > 0:
                self.task_progress.stop()
                self.task_progress.configure(mode="determinate", maximum=max(total, 1), value=min(current, total))
                self.task_detail_var.set(self._tr("task_detail_rebuild_vectorizing", total=total))
            else:
                self.task_progress.stop()
                self.task_progress.configure(mode="indeterminate", maximum=100, value=0)
                self.task_progress.start(10)
                self.task_detail_var.set(self._tr("task_detail_rebuild_vectorizing", total=total))

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

    def _current_preview_text(self) -> str:
        if not self.current_hits:
            return self._tr("preview_empty")
        selection = self.tree.selection() if hasattr(self, "tree") else ()
        if selection:
            try:
                index = int(selection[0])
            except ValueError:
                index = 0
        else:
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

    def _render_hits(self, selected_chunk_id: str | None = None) -> None:
        if not hasattr(self, "tree"):
            return
        if selected_chunk_id is None:
            selected_chunk_id = self._selected_tree_chunk_id()
        for item in self.tree.get_children():
            self.tree.delete(item)
        selected_index = 0
        for index, hit in enumerate(self.current_hits):
            include_value = '[x]' if hit.chunk_id in self.selected_chunk_ids else '[ ]'
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(include_value, hit.title, hit.reason or self._tr('reason_fallback'), hit.anchor, f"{hit.score:.1f}"),
            )
            if selected_chunk_id and hit.chunk_id == selected_chunk_id:
                selected_index = index
        self._refresh_tree_headings()
        if self.current_hits:
            self.tree.selection_set(str(selected_index))

    def _on_result_tree_click(self, event) -> str | None:
        if not hasattr(self, 'tree'):
            return None
        row_id = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        if row_id and column_id == '#1':
            self._toggle_hit_selection(int(row_id))
            self.tree.selection_set(row_id)
            self._show_hit(int(row_id))
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
        self._rebuild_context_view()

    def _toggle_all_hit_selection(self) -> None:
        if not self.current_hits:
            return
        current_ids = {hit.chunk_id for hit in self.current_hits}
        selected_ids = current_ids & self.selected_chunk_ids
        if selected_ids and len(selected_ids) == len(current_ids):
            self.selected_chunk_ids.difference_update(current_ids)
        else:
            self.selected_chunk_ids.update(current_ids)
        self._render_hits(selected_chunk_id=self._selected_tree_chunk_id())
        self._rebuild_context_view()

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
        list_body.bind('<Configure>', lambda _event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(window_id, width=event.width))

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
        custom_text = tk.Text(body, wrap='word', relief='flat', borderwidth=0, highlightbackground=self.colors['border'], highlightthickness=1, background='#FFFFFF', foreground=self.colors['ink'], insertbackground=self.colors['ink'], font=('Segoe UI', 10), height=8)
        custom_text.grid(row=3, column=0, sticky='nsew', padx=16, pady=(6, 0))
        custom_text.insert('1.0', self.rag_filter_custom_rules_var.get())
        self._attach_tooltip(custom_text, 'rag_filter_custom_rules')

        action_row = tk.Frame(window, bg=self.colors['card'])
        action_row.grid(row=3, column=0, sticky='ew', padx=18, pady=(0, 18))
        action_row.grid_columnconfigure(0, weight=1)
        save_button = ttk.Button(action_row, text=self._tr('save_config'), style='Primary.TButton', command=_save_sensitive_filters)
        save_button.grid(row=0, column=1, sticky='e')
        self._attach_tooltip(save_button, 'save_filters')

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

        index_ready = int(self.chunks_var.get() or "0") > 0
        self.index_state_var.set(self._tr("index_ready") if index_ready else self._tr("index_missing"))
        self._set_chip_style(self.index_chip, ok=index_ready, warn=not index_ready)

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

    def _apply_recommended(self) -> None:
        self.backend_var.set("lancedb")
        self.model_var.set("BAAI/bge-m3")
        self.runtime_var.set("torch")
        self.device_var.set("auto")
        self.limit_var.set("15")
        self.score_threshold_var.set("0")
        self.interval_var.set("2.0")
        self.local_only_var.set(False)
        self.rag_filter_core_var.set(True)
        self.rag_filter_extended_var.set(False)
        self.rag_filter_custom_rules_var.set("")
        self.page_blocklist_rules_var.set(_merge_page_filter_defaults(""))
        self.force_var.set(False)
        self._update_page_blocklist_summary()
        self.polling_var.set(False)
        self._refresh_device_options()
        self.status_var.set(self._tr("status_recommended"))
        self._refresh_state_chips()

    def _toggle_quick_start(self) -> None:
        self.quick_start_expanded_var.set(not self.quick_start_expanded_var.get())
        self._render_ui()

    def _toggle_advanced(self) -> None:
        self.show_advanced_var.set(not self.show_advanced_var.get())
        self._render_ui()

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
            self.preflight_var.set(self._tr("preflight_empty"))
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
        self.score_threshold_var.set(str(config.query_score_threshold))
        self.interval_var.set(str(config.poll_interval_seconds))
        self.local_only_var.set(config.vector_local_files_only)
        self.rag_filter_core_var.set(config.rag_filter_core_enabled)
        self.rag_filter_extended_var.set(config.rag_filter_extended_enabled)
        self.rag_filter_custom_rules_var.set(config.rag_filter_custom_rules)
        self.page_blocklist_rules_var.set(_merge_page_filter_defaults(config.page_blocklist_rules))
        self._update_page_blocklist_summary()
        self.quick_start_expanded_var.set(config.ui_quick_start_expanded)
        self.ui_window_geometry = config.ui_window_geometry or self.ui_window_geometry
        self.ui_main_sash = self._coerce_layout_value(config.ui_main_sash, self.ui_main_sash)
        self.ui_right_sash = self._coerce_layout_value(config.ui_right_sash, self.ui_right_sash)
        self.ui_results_sash = self._coerce_layout_value(config.ui_results_sash, self.ui_results_sash)
        self._refresh_device_options()
        self._append_log(self._tr("log_loaded_config", path=paths.config_file))
        if language_changed:
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
        score_threshold = float(self.score_threshold_var.get().strip() or "0")
        if limit <= 0 or interval <= 0 or score_threshold < 0:
            raise ValueError(self._tr("number_invalid"))
        config = AppConfig(
            vault_path=vault,
            vault_paths=self._collect_vault_paths(vault),
            data_root=str(paths.global_root),
            query_limit=limit,
            query_score_threshold=score_threshold,
            poll_interval_seconds=interval,
            vector_backend=self.backend_var.get().strip() or "disabled",
            vector_model=self.model_var.get().strip() or "BAAI/bge-m3",
            vector_runtime=self.runtime_var.get().strip() or "torch",
            vector_device=self.device_var.get().strip() or "auto",
            vector_local_files_only=self.local_only_var.get(),
            rag_filter_core_enabled=self.rag_filter_core_var.get(),
            rag_filter_extended_enabled=self.rag_filter_extended_var.get(),
            rag_filter_custom_rules=self.rag_filter_custom_rules_var.get().strip(),
            page_blocklist_rules=self.page_blocklist_rules_var.get().strip(),
            ui_language=self.language_code,
            ui_quick_start_expanded=self.quick_start_expanded_var.get(),
            ui_window_geometry=self.ui_window_geometry,
            ui_main_sash=int(self.ui_main_sash),
            ui_right_sash=int(self.ui_right_sash),
            ui_results_sash=int(self.ui_results_sash),
        )
        return config, paths

    def _save_only(self) -> None:
        try:
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
                self._tr(
                    "manual_model_hint",
                    mirror_url="https://hf-mirror.com/",
                    hf_url=f"https://huggingface.co/{config.vector_model}",
                    model=config.vector_model,
                    size=size_text,
                    model_dir=model_dir,
                ),
                parent=self.root,
            )
            self.status_var.set(self._tr("status_manual_download_waiting"))
            self._append_log(self._tr("log_manual_download_hint", model=config.vector_model))
            return "manual"
        self.status_var.set(self._tr("model_prompt_declined"))
        self._append_log(self._tr("log_model_download_declined"))
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
        if int(self.chunks_var.get() or "0") > 0:
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
                "payload": service.query(query, copy_result=copy_result, score_threshold=score_threshold),
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
            self.status_var.set(self._tr("status_watch_stopping"))
            self._append_log(self._tr("log_watch_requested_stop"))
            return
        if self.busy:
            messagebox.showinfo(self._tr("busy_title"), self._tr("busy_body"), parent=self.root)
            return
        try:
            config, paths = self._config(True)
        except Exception as exc:
            messagebox.showerror(self._tr("watch_start_failed_title"), str(exc), parent=self.root)
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

    def _after_preflight(self, payload) -> None:
        report = payload["report"]
        self.current_report = report
        self.context_view_text = ""
        self.preflight_var.set(summarize_preflight(report, self.language_code))
        self._apply_status(payload.get("status"))
        self.status_var.set(self._tr("status_preflight_done"))
        self._append_log(self._tr("log_preflight_done"))
        self._append_log(format_space_report(report, self.language_code))
        self._show_query_workspace(2)

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
        hits, _context = payload["payload"]
        self.current_query_text = query
        self.current_hits = hits
        self.selected_chunk_ids = {hit.chunk_id for hit in hits}
        self.result_sort_column = None
        self.result_sort_reverse = False
        selected_chunk_id = hits[0].chunk_id if hits else None
        self._render_hits(selected_chunk_id=selected_chunk_id)
        self._rebuild_context_view()
        if hits:
            self._show_hit(0)
        else:
            self._set_text(self.preview_text, self._tr("no_results"))
        self._show_query_workspace(1)
        self.result_var.set(self._tr("query_hits", count=len(hits)))
        self.status_var.set(self._tr("status_query_copied") if copied else self._tr("status_query_done"))
        self._append_log(self._tr("log_query_done", query=query, count=len(hits)))

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
            return
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
        backend = payload.get("vector_backend") or self.backend_var.get().strip() or "disabled"
        watch_text = self._tr("watch_ready") if payload.get("watchdog_available", WATCHDOG_AVAILABLE) else self._tr("watch_fallback")
        if not (self.watch_thread and self.watch_thread.is_alive()):
            self.watch_var.set(self._tr("vector_watch_summary", backend=backend, watch_text=watch_text))
        self._refresh_state_chips()

    def _select_hit(self, _event=None) -> None:
        selection = self.tree.selection()
        if selection:
            self._show_hit(int(selection[0]))

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
            self._set_text(self.log_text, "\n".join(self.log_lines))
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

    def _update_watch_button_state(self) -> None:
        if not hasattr(self, "watch_button"):
            return
        if self.watch_thread and self.watch_thread.is_alive():
            self.watch_button.configure(text=self._tr("watch_stop"), style="Danger.TButton")
        else:
            self.watch_button.configure(text=self._tr("watch_start"), style="Primary.TButton")

    def _drain_queue(self) -> None:
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
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
                self._update_task_progress(payload)
            elif kind == "watch-update":
                _, payload = item
                stats = payload.get("stats", {})
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
                self._update_watch_button_state()
        if self.root.winfo_exists():
            self.queue_after_id = self.root.after(120, self._drain_queue)

    def _on_close(self) -> None:
        if self.watch_stop is not None:
            self.watch_stop.set()
        self._stop_task_feedback()
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
        if self.capture_after_id is not None:
            try:
                self.root.after_cancel(self.capture_after_id)
            except Exception:
                pass
            self.capture_after_id = None
        self._capture_layout_state()
        try:
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
