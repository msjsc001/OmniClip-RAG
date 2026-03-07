from __future__ import annotations

import ctypes
import queue
import sys
import threading
import traceback
import webbrowser
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .clipboard import copy_text
from .config import AppConfig, default_data_root, ensure_data_paths, load_config, save_config
from .formatting import format_bytes, format_space_report, summarize_preflight
from .service import WATCHDOG_AVAILABLE, OmniClipService
from .ui_i18n import language_code_from_label, language_label, normalize_language, text, tooltip
from .ui_tooltip import ToolTip
from .vector_index import is_local_model_ready

APP_TITLE = "OmniClip RAG · 无界 RAG"
APP_VERSION = "V0.1.0"
REPO_URL = "https://github.com/msjsc001/OmniClip-RAG"
RELEASES_URL = f"{REPO_URL}/releases"


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
        self.root.geometry("1460x900")
        self.root.minsize(1220, 760)

        self.queue: queue.Queue[tuple] = queue.Queue()
        self.busy = False
        self.watch_thread: threading.Thread | None = None
        self.watch_stop: threading.Event | None = None
        self.current_hits = []
        self.current_context = ""
        self.current_report = None
        self.log_lines: list[str] = []
        self.tooltips: list[ToolTip] = []
        self.icon_image: tk.PhotoImage | None = None
        self.header_icon: tk.PhotoImage | None = None

        self._init_style()
        self._init_vars()
        self._load_window_icons()
        self._render_ui()
        self._load_initial_config()
        self.root.after(120, self._drain_queue)
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
        self.data_dir_var = tk.StringVar(value=str(default_data_root()))
        self.backend_var = tk.StringVar(value="lancedb")
        self.model_var = tk.StringVar(value="BAAI/bge-m3")
        self.runtime_var = tk.StringVar(value="torch")
        self.device_var = tk.StringVar(value="cpu")
        self.limit_var = tk.StringVar(value="8")
        self.interval_var = tk.StringVar(value="2.0")
        self.query_var = tk.StringVar()
        self.local_only_var = tk.BooleanVar(value=False)
        self.force_var = tk.BooleanVar(value=False)
        self.polling_var = tk.BooleanVar(value=False)
        self.show_advanced_var = tk.BooleanVar(value=False)
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

    def _tr(self, key: str, **kwargs) -> str:
        return text(self.language_code, key, **kwargs)

    def _tip(self, key: str, **kwargs) -> str:
        return tooltip(self.language_code, key, **kwargs)

    def _default_watch_summary(self) -> str:
        watch_text = self._tr("watch_ready") if WATCHDOG_AVAILABLE else self._tr("watch_fallback")
        return self._tr("vector_watch_summary", backend=self.backend_var.get().strip() or "disabled", watch_text=watch_text)

    def _load_window_icons(self) -> None:
        png_path = _resource_path("app_icon.png")
        ico_path = _resource_path("app_icon.ico")
        if png_path.exists():
            self.icon_image = tk.PhotoImage(file=str(png_path))
            self.root.iconphoto(True, self.icon_image)
            try:
                self.header_icon = self.icon_image.subsample(7, 7)
            except Exception:
                self.header_icon = self.icon_image
        if sys.platform == "win32" and ico_path.exists():
            try:
                self.root.iconbitmap(default=str(ico_path))
            except Exception:
                pass

    def _render_ui(self) -> None:
        for child in self.root.winfo_children():
            child.destroy()
        self.tooltips.clear()

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        self._build_header()
        self._build_body()
        self._build_footer()
        self._refresh_dynamic_views()

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=self.colors["card"], highlightbackground=self.colors["border"], highlightthickness=1)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        header.grid_columnconfigure(1, weight=1)

        icon_label = tk.Label(header, image=self.header_icon, bg=self.colors["card"])
        icon_label.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(16, 12), pady=14)

        title_wrap = tk.Frame(header, bg=self.colors["card"])
        title_wrap.grid(row=0, column=1, sticky="w", pady=(14, 6))
        tk.Label(title_wrap, text=self._tr("title"), bg=self.colors["card"], fg=self.colors["ink"], font=self.fonts["header_title"]).grid(row=0, column=0, sticky="w")
        tk.Label(title_wrap, text=self._tr("tagline"), bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["header_subtitle"]).grid(row=1, column=0, sticky="w", pady=(3, 0))
        tk.Label(header, text=self._tr("header_guide"), bg=self.colors["card"], fg=self.colors["accent_dark"], font=self.fonts["guide"], anchor="w").grid(row=1, column=1, sticky="ew", pady=(0, 14))

        controls = tk.Frame(header, bg=self.colors["card"])
        controls.grid(row=0, column=2, rowspan=2, sticky="ne", padx=16, pady=14)
        tk.Label(controls, text=self._tr("language"), bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"]).grid(row=0, column=0, sticky="e")
        language_box = ttk.Combobox(controls, state="readonly", width=12, values=[language_label("zh-CN"), language_label("en")], textvariable=self.language_var, style="Field.TCombobox")
        language_box.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        language_box.bind("<<ComboboxSelected>>", self._on_language_changed)
        self._attach_tooltip(language_box, "language_switch")

        help_button = ttk.Button(controls, text=self._tr("help"), style="Secondary.TButton", command=self._open_help)
        help_button.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self._attach_tooltip(help_button, "help")
        updates_button = ttk.Button(controls, text=self._tr("updates"), style="Secondary.TButton", command=self._open_updates)
        updates_button.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(10, 0))
        self._attach_tooltip(updates_button, "updates")

        version_badge = tk.Label(controls, text=self._tr("version", version=APP_VERSION), bg=self.colors["accent_soft"], fg=self.colors["accent_dark"], font=self.fonts["chip"], padx=10, pady=5)
        version_badge.grid(row=2, column=0, columnspan=2, sticky="e", pady=(10, 0))

    def _build_body(self) -> None:
        body = tk.Frame(self.root, bg=self.colors["bg"])
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 10))
        body.grid_columnconfigure(0, minsize=430)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self.left = tk.Frame(body, bg=self.colors["bg"])
        self.left.grid(row=0, column=0, sticky="nsw")
        self.right = tk.Frame(body, bg=self.colors["bg"])
        self.right.grid(row=0, column=1, sticky="nsew", padx=(14, 0))
        self.right.grid_columnconfigure(0, weight=1)
        self.right.grid_rowconfigure(1, weight=1)

        self._build_left_cards()
        self._build_right_cards()

    def _build_footer(self) -> None:
        footer = tk.Frame(self.root, bg=self.colors["card"], highlightbackground=self.colors["border"], highlightthickness=1, height=34)
        footer.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))
        footer.grid_columnconfigure(0, weight=1)
        footer.grid_propagate(False)
        tk.Label(footer, textvariable=self.status_var, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w").grid(row=0, column=0, sticky="w", padx=12, pady=7)
        tk.Label(footer, textvariable=self.result_var, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="e").grid(row=0, column=1, sticky="e", padx=12, pady=7)

    def _card(self, parent: tk.Widget, title: str, subtitle: str, row: int, *, pady: tuple[int, int] = (0, 0)) -> tk.Frame:
        card = tk.Frame(parent, bg=self.colors["card"], highlightbackground=self.colors["border"], highlightthickness=1)
        card.grid(row=row, column=0, sticky="ew", pady=pady)
        card.grid_columnconfigure(0, weight=1)
        tk.Label(card, text=title, bg=self.colors["card"], fg=self.colors["ink"], font=self.fonts["card_title"], anchor="w").grid(row=0, column=0, sticky="w", padx=16, pady=(14, 2))
        tk.Label(card, text=subtitle, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w", justify="left", wraplength=390).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 10))
        return card

    def _build_left_cards(self) -> None:
        self.left.grid_columnconfigure(0, weight=1)
        self._build_quick_start_card()
        self._build_settings_card()
        self._build_data_card()

    def _build_quick_start_card(self) -> None:
        card = self._card(self.left, self._tr("quick_start_title"), self._tr("quick_start_subtitle"), 0)

        steps = tk.Frame(card, bg=self.colors["card"])
        steps.grid(row=2, column=0, sticky="ew", padx=16)
        for index, key in enumerate(("step_1", "step_2", "step_3")):
            badge = tk.Label(steps, text=str(index + 1), bg=self.colors["accent_soft"], fg=self.colors["accent_dark"], font=self.fonts["chip"], width=2, pady=3)
            badge.grid(row=index, column=0, sticky="w")
            tk.Label(steps, text=self._tr(key), bg=self.colors["card"], fg=self.colors["ink"], font=self.fonts["body"], anchor="w").grid(row=index, column=1, sticky="w", padx=(8, 0), pady=(0 if index == 0 else 6, 0))

        chips = tk.Frame(card, bg=self.colors["card"])
        chips.grid(row=3, column=0, sticky="ew", padx=16, pady=(14, 8))
        chips.grid_columnconfigure((0, 1, 2), weight=1)
        self.vault_chip = self._chip(chips, self.vault_state_var, 0)
        self.model_chip = self._chip(chips, self.model_state_var, 1)
        self.index_chip = self._chip(chips, self.index_state_var, 2)

        form = tk.Frame(card, bg=self.colors["card"])
        form.grid(row=4, column=0, sticky="ew", padx=16)
        form.grid_columnconfigure(1, weight=1)
        self._path_row(form, 0, self._tr("vault_label"), self.vault_var, self._choose_vault, tooltip_key="vault", browse_tooltip_key="browse_vault")
        self._path_row(form, 1, self._tr("data_dir_label"), self.data_dir_var, self._choose_data_dir, tooltip_key="data_dir", browse_tooltip_key="browse_data")

        actions = tk.Frame(card, bg=self.colors["card"])
        actions.grid(row=5, column=0, sticky="ew", padx=16, pady=(14, 0))
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

        stats = tk.Frame(card, bg=self.colors["card"])
        stats.grid(row=6, column=0, sticky="ew", padx=16, pady=(14, 0))
        for index, (label, variable) in enumerate((("Files", self.files_var), ("Chunks", self.chunks_var), ("Refs", self.refs_var))):
            stats.grid_columnconfigure(index, weight=1)
            self._stat_box(stats, label, variable, index)
        tk.Label(card, textvariable=self.preflight_var, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], justify="left", wraplength=390, anchor="w").grid(row=7, column=0, sticky="ew", padx=16, pady=(14, 4))
        tk.Label(card, textvariable=self.watch_var, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], justify="left", wraplength=390, anchor="w").grid(row=8, column=0, sticky="ew", padx=16, pady=(0, 14))
    def _build_settings_card(self) -> None:
        card = self._card(self.left, self._tr("settings_title"), self._tr("settings_subtitle"), 1, pady=(14, 0))
        form = tk.Frame(card, bg=self.colors["card"])
        form.grid(row=2, column=0, sticky="ew", padx=16)
        form.grid_columnconfigure(1, weight=1)
        form.grid_columnconfigure(3, weight=1)

        self._combo_row(form, 0, self._tr("backend_label"), self.backend_var, ["lancedb", "disabled"], tooltip_key="backend")
        self._entry_row(form, 1, self._tr("model_label"), self.model_var, tooltip_key="model")
        self._combo_pair_row(form, 2, self._tr("runtime_label"), self.runtime_var, ["torch", "onnx"], self._tr("device_label"), self.device_var, ["cpu", "cuda"], left_tip="runtime", right_tip="device")
        self._entry_pair_row(form, 3, self._tr("limit_label"), self.limit_var, self._tr("interval_label"), self.interval_var, left_tip="limit", right_tip="interval")

        action_row = tk.Frame(card, bg=self.colors["card"])
        action_row.grid(row=3, column=0, sticky="ew", padx=16, pady=(14, 0))
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

        toggle_button = ttk.Button(card, text=self._tr("advanced_hide") if self.show_advanced_var.get() else self._tr("advanced_show"), style="Secondary.TButton", command=self._toggle_advanced)
        toggle_button.grid(row=4, column=0, sticky="ew", padx=16, pady=(12, 0))

        if self.show_advanced_var.get():
            advanced = tk.Frame(card, bg=self.colors["card"])
            advanced.grid(row=5, column=0, sticky="ew", padx=16, pady=(10, 0))
            local_only = ttk.Checkbutton(advanced, text=self._tr("local_only_label"), variable=self.local_only_var, style="Plain.TCheckbutton")
            local_only.grid(row=0, column=0, sticky="w")
            self._attach_tooltip(local_only, "local_only")
            force = ttk.Checkbutton(advanced, text=self._tr("force_label"), variable=self.force_var, style="Plain.TCheckbutton")
            force.grid(row=1, column=0, sticky="w", pady=(6, 0))
            self._attach_tooltip(force, "force")
            polling = ttk.Checkbutton(advanced, text=self._tr("polling_label"), variable=self.polling_var, style="Plain.TCheckbutton")
            polling.grid(row=2, column=0, sticky="w", pady=(6, 0))
            self._attach_tooltip(polling, "polling")

        refresh_button = ttk.Button(card, text=self._tr("refresh_button"), style="Secondary.TButton", command=self._refresh)
        refresh_button.grid(row=6, column=0, sticky="ew", padx=16, pady=(14, 14))

    def _build_data_card(self) -> None:
        card = self._card(self.left, self._tr("data_title"), self._tr("data_subtitle"), 2, pady=(14, 0))
        buttons = tk.Frame(card, bg=self.colors["card"])
        buttons.grid(row=2, column=0, sticky="ew", padx=16)
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

        checks = tk.Frame(card, bg=self.colors["card"])
        checks.grid(row=3, column=0, sticky="ew", padx=16, pady=(14, 0))
        ttk.Checkbutton(checks, text=self._tr("clear_index_label"), variable=self.clear_index_var, style="Plain.TCheckbutton").grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(checks, text=self._tr("clear_logs_label"), variable=self.clear_logs_var, style="Plain.TCheckbutton").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Checkbutton(checks, text=self._tr("clear_cache_label"), variable=self.clear_cache_var, style="Plain.TCheckbutton").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Checkbutton(checks, text=self._tr("clear_exports_label"), variable=self.clear_exports_var, style="Plain.TCheckbutton").grid(row=3, column=0, sticky="w", pady=(6, 0))
        clear_button = ttk.Button(card, text=self._tr("clear_button"), style="Danger.TButton", command=self._clear)
        clear_button.grid(row=4, column=0, sticky="ew", padx=16, pady=(14, 14))
        self._attach_tooltip(clear_button, "clear")

    def _build_right_cards(self) -> None:
        search_card = self._card(self.right, self._tr("search_title"), self._tr("search_subtitle"), 0)
        tk.Label(search_card, text=self._tr("query_hint"), bg=self.colors["card"], fg=self.colors["accent_dark"], font=self.fonts["small"], anchor="w").grid(row=2, column=0, sticky="w", padx=16)
        query_row = tk.Frame(search_card, bg=self.colors["card"])
        query_row.grid(row=3, column=0, sticky="ew", padx=16, pady=(10, 14))
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

        result_card = self._card(self.right, self._tr("results_title"), self._tr("results_subtitle"), 1, pady=(14, 0))
        result_card.grid_rowconfigure(2, weight=3)
        result_card.grid_rowconfigure(3, weight=2)
        result_card.grid_columnconfigure(0, weight=1)

        table_frame = tk.Frame(result_card, bg=self.colors["card"])
        table_frame.grid(row=2, column=0, sticky="nsew", padx=16)
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table_frame, columns=("title", "anchor", "source", "score"), show="headings", style="App.Treeview")
        for key, title, width in (
            ("title", self._tr("col_page"), 220),
            ("anchor", self._tr("col_anchor"), 370),
            ("source", self._tr("col_source"), 240),
            ("score", self._tr("col_score"), 90),
        ):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w" if key != "score" else "e")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self._select_hit)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        tabs_host = tk.Frame(result_card, bg=self.colors["card"])
        tabs_host.grid(row=3, column=0, sticky="nsew", padx=16, pady=(14, 14))
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
        self.preview_text = self._text(preview_tab)
        self.context_text = self._text(context_tab)
        self.log_text = self._text(log_tab)

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
        caption = tk.Label(parent, text=label, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        caption.grid(row=row, column=0, sticky="w", pady=(8, 0))
        self._attach_tooltip(caption, tooltip_key)
        frame = tk.Frame(parent, bg=self.colors["card"])
        frame.grid(row=row, column=1, sticky="ew", pady=(8, 0))
        frame.grid_columnconfigure(0, weight=1)
        entry = ttk.Entry(frame, textvariable=variable, style="Field.TEntry")
        entry.grid(row=0, column=0, sticky="ew")
        self._attach_tooltip(entry, tooltip_key)
        button = ttk.Button(frame, text=self._tr("browse"), style="Secondary.TButton", command=browse_cmd)
        button.grid(row=0, column=1, padx=(8, 0))
        self._attach_tooltip(button, browse_tooltip_key)

    def _entry_row(self, parent: tk.Widget, row: int, label: str, variable: tk.StringVar, *, tooltip_key: str) -> None:
        caption = tk.Label(parent, text=label, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        caption.grid(row=row, column=0, sticky="w", pady=(8, 0))
        self._attach_tooltip(caption, tooltip_key)
        entry = ttk.Entry(parent, textvariable=variable, style="Field.TEntry")
        entry.grid(row=row, column=1, columnspan=3, sticky="ew", pady=(8, 0))
        self._attach_tooltip(entry, tooltip_key)

    def _combo_row(self, parent: tk.Widget, row: int, label: str, variable: tk.StringVar, values: list[str], *, tooltip_key: str) -> None:
        caption = tk.Label(parent, text=label, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        caption.grid(row=row, column=0, sticky="w", pady=(8, 0))
        self._attach_tooltip(caption, tooltip_key)
        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", style="Field.TCombobox")
        combo.grid(row=row, column=1, columnspan=3, sticky="ew", pady=(8, 0))
        self._attach_tooltip(combo, tooltip_key)

    def _combo_pair_row(self, parent: tk.Widget, row: int, left_label: str, left_var: tk.StringVar, left_values: list[str], right_label: str, right_var: tk.StringVar, right_values: list[str], *, left_tip: str, right_tip: str) -> None:
        left_caption = tk.Label(parent, text=left_label, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        left_caption.grid(row=row, column=0, sticky="w", pady=(8, 0))
        self._attach_tooltip(left_caption, left_tip)
        left_combo = ttk.Combobox(parent, textvariable=left_var, values=left_values, state="readonly", style="Field.TCombobox")
        left_combo.grid(row=row, column=1, sticky="ew", pady=(8, 0))
        self._attach_tooltip(left_combo, left_tip)

        right_caption = tk.Label(parent, text=right_label, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        right_caption.grid(row=row, column=2, sticky="w", padx=(14, 0), pady=(8, 0))
        self._attach_tooltip(right_caption, right_tip)
        right_combo = ttk.Combobox(parent, textvariable=right_var, values=right_values, state="readonly", style="Field.TCombobox")
        right_combo.grid(row=row, column=3, sticky="ew", pady=(8, 0))
        self._attach_tooltip(right_combo, right_tip)

    def _entry_pair_row(self, parent: tk.Widget, row: int, left_label: str, left_var: tk.StringVar, right_label: str, right_var: tk.StringVar, *, left_tip: str, right_tip: str) -> None:
        left_caption = tk.Label(parent, text=left_label, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        left_caption.grid(row=row, column=0, sticky="w", pady=(8, 0))
        self._attach_tooltip(left_caption, left_tip)
        left_entry = ttk.Entry(parent, textvariable=left_var, style="Field.TEntry")
        left_entry.grid(row=row, column=1, sticky="ew", pady=(8, 0))
        self._attach_tooltip(left_entry, left_tip)

        right_caption = tk.Label(parent, text=right_label, bg=self.colors["card"], fg=self.colors["muted"], font=self.fonts["small"], anchor="w")
        right_caption.grid(row=row, column=2, sticky="w", padx=(14, 0), pady=(8, 0))
        self._attach_tooltip(right_caption, right_tip)
        right_entry = ttk.Entry(parent, textvariable=right_var, style="Field.TEntry")
        right_entry.grid(row=row, column=3, sticky="ew", pady=(8, 0))
        self._attach_tooltip(right_entry, right_tip)

    def _text(self, parent: tk.Widget) -> tk.Text:
        frame = tk.Frame(parent, bg=self.colors["card"])
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        text_widget = tk.Text(frame, wrap="word", relief="flat", borderwidth=0, highlightthickness=0, background="#FFFFFF", foreground=self.colors["ink"], insertbackground=self.colors["ink"], font=("Segoe UI", 10), padx=14, pady=12)
        text_widget.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text_widget.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        text_widget.configure(yscrollcommand=scroll.set, state="disabled")
        return text_widget

    def _attach_tooltip(self, widget: tk.Widget, key: str, **kwargs) -> None:
        tip_text = self._tip(key, **kwargs)
        if tip_text:
            self.tooltips.append(ToolTip(widget, tip_text))

    def _refresh_dynamic_views(self) -> None:
        self._refresh_state_chips()
        self._render_hits()
        self._set_text(self.preview_text, self._current_preview_text())
        self._set_text(self.context_text, self.current_context or self._tr("context_empty"))
        self._set_text(self.log_text, "\n".join(self.log_lines) if self.log_lines else self._tr("log_empty"))
        self._update_watch_button_state()

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
            f"{self._tr('col_score')}：{hit.score:.2f}\n\n"
            f"{hit.rendered_text}"
        )

    def _render_hits(self) -> None:
        if not hasattr(self, "tree"):
            return
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, hit in enumerate(self.current_hits):
            self.tree.insert("", "end", iid=str(index), values=(hit.title, hit.anchor, hit.source_path, f"{hit.score:.1f}"))
        if self.current_hits:
            self.tree.selection_set("0")

    def _refresh_state_chips(self) -> None:
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
        self.data_dir_var.set(str(paths.root))
        self._load_config(paths)
        self._refresh()

    def _on_language_changed(self, _event=None) -> None:
        self.language_code = language_code_from_label(self.language_var.get())
        self.language_var.set(language_label(self.language_code))
        if not self.log_lines:
            self.status_var.set(self._tr("status_ready"))
            self.result_var.set(self._tr("result_empty"))
        self._render_ui()

    def _apply_recommended(self) -> None:
        self.backend_var.set("lancedb")
        self.model_var.set("BAAI/bge-m3")
        self.runtime_var.set("torch")
        self.device_var.set("cpu")
        self.limit_var.set("8")
        self.interval_var.set("2.0")
        self.local_only_var.set(False)
        self.force_var.set(False)
        self.polling_var.set(False)
        self.status_var.set(self._tr("status_recommended"))
        self._refresh_state_chips()

    def _toggle_advanced(self) -> None:
        self.show_advanced_var.set(not self.show_advanced_var.get())
        self._render_ui()

    def _open_help(self) -> None:
        self._open_url(REPO_URL)

    def _open_updates(self) -> None:
        self._open_url(RELEASES_URL)

    def _open_url(self, url: str) -> None:
        if not webbrowser.open(url):
            messagebox.showwarning(self._tr("help"), self._tr("help_failed"), parent=self.root)

    def _choose_vault(self) -> None:
        selected = filedialog.askdirectory(title=self._tr("vault_label"), initialdir=self.vault_var.get().strip() or str(Path.home()))
        if selected:
            self.vault_var.set(selected)
            self._refresh_state_chips()

    def _choose_data_dir(self) -> None:
        selected = filedialog.askdirectory(title=self._tr("data_dir_label"), initialdir=self.data_dir_var.get().strip() or str(default_data_root()))
        if selected:
            self.data_dir_var.set(selected)
            self._load_config_from_current_dir()
            self._refresh_state_chips()

    def _load_config_from_current_dir(self) -> None:
        self._load_config(ensure_data_paths(self.data_dir_var.get().strip() or str(default_data_root())))

    def _load_config(self, paths) -> None:
        config = load_config(paths)
        if config is None:
            self.preflight_var.set(self._tr("preflight_empty"))
            return
        new_language = normalize_language(config.ui_language)
        language_changed = new_language != self.language_code
        self.language_code = new_language
        self.language_var.set(language_label(self.language_code))
        self.vault_var.set(config.vault_path)
        self.data_dir_var.set(config.data_root)
        self.backend_var.set(config.vector_backend or "disabled")
        self.model_var.set(config.vector_model)
        self.runtime_var.set(config.vector_runtime)
        self.device_var.set(config.vector_device)
        self.limit_var.set(str(config.query_limit))
        self.interval_var.set(str(config.poll_interval_seconds))
        self.local_only_var.set(config.vector_local_files_only)
        self._append_log(self._tr("log_loaded_config", path=paths.config_file))
        if language_changed:
            self._render_ui()
        self._refresh_state_chips()

    def _config(self, require_vault: bool):
        paths = ensure_data_paths(self.data_dir_var.get().strip() or str(default_data_root()))
        vault = self.vault_var.get().strip()
        if require_vault:
            if not vault:
                raise ValueError(self._tr("choose_vault_first"))
            vault_path = Path(vault).expanduser().resolve()
            if not vault_path.exists() or not vault_path.is_dir():
                raise ValueError(self._tr("vault_invalid"))
            vault = str(vault_path)
        else:
            vault = str(Path(vault).expanduser().resolve()) if vault else str(Path.home())
        limit = int(self.limit_var.get().strip() or "8")
        interval = float(self.interval_var.get().strip() or "2.0")
        if limit <= 0 or interval <= 0:
            raise ValueError(self._tr("number_invalid"))
        config = AppConfig(
            vault_path=vault,
            data_root=str(paths.root),
            query_limit=limit,
            poll_interval_seconds=interval,
            vector_backend=self.backend_var.get().strip() or "disabled",
            vector_model=self.model_var.get().strip() or "BAAI/bge-m3",
            vector_runtime=self.runtime_var.get().strip() or "torch",
            vector_device=self.device_var.get().strip() or "cpu",
            vector_local_files_only=self.local_only_var.get(),
            ui_language=self.language_code,
        )
        return config, paths

    def _save_only(self) -> None:
        try:
            config, paths = self._config(False)
            save_config(config, paths)
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
            save_config(config, paths)
        except Exception as exc:
            messagebox.showerror(self._tr("cannot_start_title"), str(exc), parent=self.root)
            return
        label = self._tr(label_key)
        self.busy = True
        self.status_var.set(f"{label}…")

        def worker() -> None:
            service = OmniClipService(config, paths)
            try:
                payload = action(service)
            except Exception:
                self.queue.put(("error", label, traceback.format_exc()))
            else:
                self.queue.put(("success", label, payload, callback))
            finally:
                service.close()

        threading.Thread(target=worker, daemon=True).start()

    def _start_task(self, label_key: str, action, callback, *, require_vault: bool = True, ensure_model: bool = False) -> None:
        try:
            config, paths = self._config(require_vault)
        except Exception as exc:
            messagebox.showerror(self._tr("cannot_start_title"), str(exc), parent=self.root)
            return
        if ensure_model and self._backend_enabled(config) and not self._prepare_model_for_followup(label_key, config, paths, require_vault, lambda: self._start_task(label_key, action, callback, require_vault=require_vault, ensure_model=False)):
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

    # Why: 先在 UI 层征求是否自动下载模型，避免第一次查询或建库时静默卡住，让新手知道当前缺了什么。
    def _prepare_model_for_followup(self, label_key: str, config: AppConfig, paths, require_vault: bool, followup) -> bool:
        if config.vector_local_files_only and not is_local_model_ready(config, paths):
            allow_remote = messagebox.askyesno(self._tr("model_prompt_title"), self._tr("model_prompt_local_only"), parent=self.root)
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

        prompt = messagebox.askyesno(
            self._tr("model_prompt_title"),
            self._tr("model_prompt_body", task=self._tr(label_key), model=config.vector_model),
            parent=self.root,
        )
        self._append_log(self._tr("log_model_download_prompt"))
        if not prompt:
            self.status_var.set(self._tr("model_prompt_declined"))
            self._append_log(self._tr("log_model_download_declined"))
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
        force = self.force_var.get()

        def action(service):
            report = service.estimate_space()
            if not report.can_proceed and not force:
                return {"blocked": True, "report": report}
            return {"blocked": False, "report": report, "result": service.bootstrap_model(), "status": service.status_snapshot()}

        self._start_task("bootstrap_button", action, self._after_bootstrap)

    def _rebuild(self) -> None:
        force = self.force_var.get()

        def action(service):
            report = service.estimate_space()
            if not report.can_proceed and not force:
                return {"blocked": True, "report": report}
            return {"blocked": False, "report": report, "stats": service.rebuild_index(), "status": service.status_snapshot()}

        self._start_task("rebuild_button", action, self._after_rebuild, ensure_model=True)

    def _refresh(self) -> None:
        self._start_task("refresh_button", lambda service: service.status_snapshot(), self._after_status, require_vault=False)

    def _query(self, copy_result: bool) -> None:
        query = self.query_var.get().strip()
        if not query:
            messagebox.showinfo(self._tr("empty_query_title"), self._tr("empty_query_body"), parent=self.root)
            return
        self._start_task(
            "search_button",
            lambda service: {"query": query, "copied": copy_result, "payload": service.query(query, copy_result=copy_result)},
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

        self._start_task("clear_button", action, self._after_clear, require_vault=False)

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
        mode = "polling" if self.polling_var.get() or not WATCHDOG_AVAILABLE else "watchdog"
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
                self.queue.put(("watch-stopped", mode))

        self.watch_thread = threading.Thread(target=worker, daemon=True)
        self.watch_thread.start()
    def _after_preflight(self, payload) -> None:
        report = payload["report"]
        self.current_report = report
        self.preflight_var.set(summarize_preflight(report))
        self._set_text(self.context_text, format_space_report(report))
        self.tabs.select(1)
        self._apply_status(payload.get("status"))
        self.status_var.set(self._tr("status_preflight_done"))
        self._append_log(self._tr("log_preflight_done"))

    def _after_bootstrap(self, payload) -> None:
        report = payload.get("report")
        if report is not None:
            self.current_report = report
            self.preflight_var.set(summarize_preflight(report))
            self._set_text(self.context_text, format_space_report(report))
            self.tabs.select(1)
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
            self.preflight_var.set(summarize_preflight(report))
        if payload.get("blocked"):
            self.status_var.set(self._tr("rebuild_blocked_title"))
            self._append_log(self._tr("log_rebuild_blocked"))
            messagebox.showwarning(self._tr("rebuild_blocked_title"), self._tr("rebuild_blocked_body"), parent=self.root)
            return
        stats = payload["stats"]
        self._apply_status(payload.get("status"))
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
        hits, context = payload["payload"]
        self.current_hits = hits
        self.current_context = context
        self._render_hits()
        if hits:
            self.tree.selection_set("0")
            self._show_hit(0)
        else:
            self._set_text(self.preview_text, self._tr("no_results"))
        self._set_text(self.context_text, context)
        self.tabs.select(1)
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
            self.preflight_var.set(
                self._tr(
                    "recent_preflight",
                    risk=latest.get("risk_level"),
                    required=format_bytes(int(latest.get("required_free_bytes", 0))),
                    available=format_bytes(int(latest.get("available_free_bytes", 0))),
                )
            )
        elif self.current_report is not None:
            self.preflight_var.set(summarize_preflight(self.current_report))
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
                f"{self._tr('col_score')}：{hit.score:.2f}\n\n"
                f"{hit.rendered_text}"
            ),
        )

    def _copy_context(self) -> None:
        if not self.current_context.strip():
            messagebox.showinfo(self._tr("copy_empty_title"), self._tr("copy_empty_body"), parent=self.root)
            return
        copy_text(self.current_context)
        self.status_var.set(self._tr("status_context_copied"))
        self._append_log(self._tr("log_context_copied"))

    def _open_vault(self) -> None:
        vault = self.vault_var.get().strip()
        if not vault:
            messagebox.showinfo(self._tr("not_ready_title"), self._tr("choose_vault_first"), parent=self.root)
            return
        paths = ensure_data_paths(self.data_dir_var.get().strip() or str(default_data_root()))
        service = OmniClipService(AppConfig(vault_path=str(Path(vault).expanduser().resolve()), data_root=str(paths.root), ui_language=self.language_code), paths)
        try:
            service.open_vault_dir()
        finally:
            service.close()

    def _open_data(self) -> None:
        paths = ensure_data_paths(self.data_dir_var.get().strip() or str(default_data_root()))
        service = OmniClipService(AppConfig(vault_path=self.vault_var.get().strip() or str(Path.home()), data_root=str(paths.root), ui_language=self.language_code), paths)
        try:
            service.open_data_dir()
        finally:
            service.close()

    def _open_exports(self) -> None:
        paths = ensure_data_paths(self.data_dir_var.get().strip() or str(default_data_root()))
        service = OmniClipService(AppConfig(vault_path=self.vault_var.get().strip() or str(Path.home()), data_root=str(paths.root), ui_language=self.language_code), paths)
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

    def _set_text(self, widget: tk.Text, text_value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text_value)
        widget.configure(state="disabled")

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
                _, _label, payload, callback = item
                self.busy = False
                if callback:
                    callback(payload)
            elif kind == "error":
                _, label, tb = item
                self.busy = False
                self.status_var.set(f"{label} failed")
                self._append_log(tb.strip())
                self.tabs.select(2)
                messagebox.showerror(label, "\n".join([line.strip() for line in tb.splitlines() if line.strip()][-6:]), parent=self.root)
            elif kind == "watch-update":
                _, payload = item
                stats = payload.get("stats", {})
                self.files_var.set(str(stats.get("files", 0)))
                self.chunks_var.set(str(stats.get("chunks", 0)))
                self.refs_var.set(str(stats.get("refs", 0)))
                self.status_var.set(self._tr("status_watch_update"))
                changed = ", ".join(payload.get("changed", [])[:3]) or "none"
                deleted = ", ".join(payload.get("deleted", [])[:3]) or "none"
                self._append_log(self._tr("log_watch_update", changed=changed, deleted=deleted))
                self._refresh_state_chips()
            elif kind == "watch-error":
                _, tb = item
                self.status_var.set(self._tr("status_watch_error"))
                self._append_log(self._tr("log_watch_error"))
                self._append_log(tb.strip())
                self.tabs.select(2)
                messagebox.showerror(self._tr("watch_start_failed_title"), "\n".join([line.strip() for line in tb.splitlines() if line.strip()][-6:]), parent=self.root)
            elif kind == "watch-stopped":
                _, mode = item
                self.watch_var.set(self._tr("watch_stopped", mode=mode))
                self.status_var.set(self._tr("status_watch_stopped"))
                self._append_log(self._tr("log_watch_stopped"))
                self.watch_thread = None
                self.watch_stop = None
                self._update_watch_button_state()
        self.root.after(120, self._drain_queue)

    def _on_close(self) -> None:
        if self.watch_stop is not None:
            self.watch_stop.set()
        self.root.destroy()


def main() -> int:
    return OmniClipDesktopApp().run()


if __name__ == "__main__":
    raise SystemExit(main())
