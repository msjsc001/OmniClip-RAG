from __future__ import annotations

import queue
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .clipboard import copy_text
from .config import AppConfig, default_data_root, ensure_data_paths, load_config, save_config
from .formatting import format_bytes, format_space_report, summarize_preflight
from .service import WATCHDOG_AVAILABLE, OmniClipService

APP_TITLE = "OmniClip RAG · 无界 RAG"
APP_TAGLINE = "跨越任何笔记软件的边界，无缝对接任何 AI。"


class OmniClipDesktopApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("1480x920")
        self.root.minsize(1240, 760)
        self.root.configure(bg="#f3ecdf")

        self.queue = queue.Queue()
        self.busy = False
        self.watch_thread: threading.Thread | None = None
        self.watch_stop: threading.Event | None = None
        self.current_hits = []
        self.current_context = ""
        self.current_report = None

        self._init_style()
        self._init_vars()
        self._build_ui()
        self._load_initial_config()
        self.root.after(120, self._drain_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def run(self) -> int:
        self.root.mainloop()
        return 0

    def _init_style(self) -> None:
        self.colors = {
            "bg": "#f3ecdf",
            "card": "#fff9f0",
            "soft": "#f8f1e5",
            "ink": "#1f2428",
            "muted": "#6c665f",
            "accent": "#1d7467",
            "accent2": "#15584f",
            "danger": "#9d4028",
            "border": "#d8c8b1",
            "select": "#e6f1ec",
        }
        style = ttk.Style()
        style.theme_use("clam")
        self.root.option_add("*Font", "{Segoe UI} 10")
        style.configure("App.TFrame", background=self.colors["bg"])
        style.configure("Card.TFrame", background=self.colors["card"])
        style.configure("Soft.TFrame", background=self.colors["soft"])
        style.configure("HeroTitle.TLabel", background=self.colors["accent"], foreground="#ffffff", font=("Segoe UI Semibold", 22))
        style.configure("HeroText.TLabel", background=self.colors["accent"], foreground="#dff3ee", font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background=self.colors["card"], foreground=self.colors["ink"], font=("Segoe UI Semibold", 12))
        style.configure("CardText.TLabel", background=self.colors["card"], foreground=self.colors["muted"], font=("Segoe UI", 9))
        style.configure("Value.TLabel", background=self.colors["soft"], foreground=self.colors["ink"], font=("Segoe UI Semibold", 11))
        style.configure("Accent.TButton", background=self.colors["accent"], foreground="#ffffff", borderwidth=0, padding=(12, 8))
        style.map("Accent.TButton", background=[("active", self.colors["accent2"])])
        style.configure("Ghost.TButton", background=self.colors["soft"], foreground=self.colors["ink"], borderwidth=1, padding=(12, 8))
        style.map("Ghost.TButton", background=[("active", "#efe4d2")])
        style.configure("Danger.TButton", background=self.colors["danger"], foreground="#ffffff", borderwidth=0, padding=(12, 8))
        style.map("Danger.TButton", background=[("active", "#833421")])
        style.configure("App.TCheckbutton", background=self.colors["card"], foreground=self.colors["ink"])
        style.configure("App.TEntry", fieldbackground="#fffefb", bordercolor=self.colors["border"], lightcolor=self.colors["border"], darkcolor=self.colors["border"], padding=6)
        style.configure("App.TCombobox", fieldbackground="#fffefb", bordercolor=self.colors["border"], lightcolor=self.colors["border"], darkcolor=self.colors["border"], padding=4)
        style.configure("App.Treeview", background="#fffefb", fieldbackground="#fffefb", foreground=self.colors["ink"], rowheight=28)
        style.map("App.Treeview", background=[("selected", self.colors["select"])], foreground=[("selected", self.colors["ink"])])
        style.configure("App.Treeview.Heading", background=self.colors["soft"], foreground=self.colors["ink"], font=("Segoe UI Semibold", 10))
        style.configure("App.TNotebook", background=self.colors["bg"], borderwidth=0)
        style.configure("App.TNotebook.Tab", background=self.colors["soft"], foreground=self.colors["ink"], padding=(12, 8))

    def _init_vars(self) -> None:
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
        self.clear_index_var = tk.BooleanVar(value=False)
        self.clear_logs_var = tk.BooleanVar(value=False)
        self.clear_cache_var = tk.BooleanVar(value=False)
        self.clear_exports_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="准备就绪")
        self.preflight_var = tk.StringVar(value="最近还没有空间预检查记录。")
        self.watch_var = tk.StringVar(value="watchdog 可用")
        self.files_var = tk.StringVar(value="0")
        self.chunks_var = tk.StringVar(value="0")
        self.refs_var = tk.StringVar(value="0")
        self.result_var = tk.StringVar(value="结果区还没有内容")

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        hero = tk.Frame(self.root, bg=self.colors["accent"], height=106)
        hero.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10))
        hero.grid_propagate(False)
        hero.columnconfigure(0, weight=1)
        ttk.Label(hero, text=APP_TITLE, style="HeroTitle.TLabel").grid(row=0, column=0, sticky="w", padx=24, pady=(18, 4))
        ttk.Label(hero, text=APP_TAGLINE, style="HeroText.TLabel").grid(row=1, column=0, sticky="w", padx=24)
        ttk.Label(hero, text="V0.1.0", style="HeroText.TLabel").grid(row=0, column=1, sticky="e", padx=24)

        content = ttk.Frame(self.root, style="App.TFrame")
        content.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 12))
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        self.left = ttk.Frame(content, style="App.TFrame")
        self.left.grid(row=0, column=0, sticky="nsw")
        self.right = ttk.Frame(content, style="App.TFrame")
        self.right.grid(row=0, column=1, sticky="nsew", padx=(14, 0))
        self.right.columnconfigure(0, weight=1)
        self.right.rowconfigure(1, weight=1)
        self.right.rowconfigure(2, weight=1)
        self._build_left_cards()
        self._build_right_cards()

        status = ttk.Frame(self.root, style="Card.TFrame")
        status.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 18))
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.status_var, style="CardText.TLabel").grid(row=0, column=0, sticky="w", padx=14, pady=10)
        ttk.Label(status, textvariable=self.result_var, style="CardText.TLabel").grid(row=0, column=1, sticky="e", padx=14)

    def _card(self, parent, title, subtitle, row, pady=(0, 0)):
        card = ttk.Frame(parent, style="Card.TFrame")
        card.grid(row=row, column=0, sticky="ew", pady=pady)
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text=title, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", padx=18, pady=(16, 2))
        ttk.Label(card, text=subtitle, style="CardText.TLabel", wraplength=360, justify="left").grid(row=1, column=0, sticky="w", padx=18)
        return card

    def _build_left_cards(self) -> None:
        config = self._card(self.left, "工作区配置", "配置笔记库、数据目录和向量选项。", 0)
        form = ttk.Frame(config, style="Card.TFrame")
        form.grid(row=2, column=0, sticky="ew", padx=18, pady=(6, 16))
        form.columnconfigure(1, weight=1)
        self._path_row(form, 0, "笔记库", self.vault_var, self._choose_vault)
        self._path_row(form, 1, "数据目录", self.data_dir_var, self._choose_data_dir)
        ttk.Label(form, text="向量后端", style="CardText.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(form, textvariable=self.backend_var, style="App.TCombobox", state="readonly", values=["lancedb", "disabled"]).grid(row=2, column=1, sticky="ew", pady=(8, 0))
        ttk.Label(form, text="模型", style="CardText.TLabel").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(form, textvariable=self.model_var, style="App.TEntry").grid(row=3, column=1, sticky="ew", pady=(8, 0))
        duo = ttk.Frame(form, style="Card.TFrame")
        duo.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        duo.columnconfigure(1, weight=1)
        duo.columnconfigure(3, weight=1)
        ttk.Label(duo, text="运行时", style="CardText.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Combobox(duo, textvariable=self.runtime_var, style="App.TCombobox", state="readonly", values=["torch", "onnx"]).grid(row=0, column=1, sticky="ew")
        ttk.Label(duo, text="设备", style="CardText.TLabel").grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Combobox(duo, textvariable=self.device_var, style="App.TCombobox", state="readonly", values=["cpu", "cuda"]).grid(row=0, column=3, sticky="ew")
        duo2 = ttk.Frame(form, style="Card.TFrame")
        duo2.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        duo2.columnconfigure(1, weight=1)
        duo2.columnconfigure(3, weight=1)
        ttk.Label(duo2, text="查询条数", style="CardText.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(duo2, textvariable=self.limit_var, style="App.TEntry", width=8).grid(row=0, column=1, sticky="ew")
        ttk.Label(duo2, text="监听间隔", style="CardText.TLabel").grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Entry(duo2, textvariable=self.interval_var, style="App.TEntry", width=8).grid(row=0, column=3, sticky="ew")
        ttk.Checkbutton(form, text="只使用本地模型缓存", variable=self.local_only_var, style="App.TCheckbutton").grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Checkbutton(form, text="预检失败仍继续", variable=self.force_var, style="App.TCheckbutton").grid(row=7, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Checkbutton(form, text="监听时强制轮询", variable=self.polling_var, style="App.TCheckbutton").grid(row=8, column=0, columnspan=2, sticky="w", pady=(4, 0))
        btns = ttk.Frame(config, style="Card.TFrame")
        btns.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 18))
        for i in range(2):
            btns.columnconfigure(i, weight=1)
        ttk.Button(btns, text="载入配置", style="Ghost.TButton", command=self._load_config_from_current_dir).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(btns, text="保存配置", style="Accent.TButton", command=self._save_only).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Button(btns, text="打开笔记库", style="Ghost.TButton", command=self._open_vault).grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(10, 0))
        ttk.Button(btns, text="打开数据目录", style="Ghost.TButton", command=self._open_data).grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(10, 0))
        ttk.Button(btns, text="打开导出目录", style="Ghost.TButton", command=self._open_exports).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        ops = self._card(self.left, "索引与监听", "预检、预热、建库和热更新。", 1, pady=(14, 0))
        stats = ttk.Frame(ops, style="Card.TFrame")
        stats.grid(row=2, column=0, sticky="ew", padx=18, pady=(6, 12))
        for i, (title, var) in enumerate((("文件", self.files_var), ("片段", self.chunks_var), ("引用", self.refs_var))):
            box = ttk.Frame(stats, style="Soft.TFrame")
            box.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0))
            stats.columnconfigure(i, weight=1)
            ttk.Label(box, text=title, style="CardText.TLabel").grid(row=0, column=0, sticky="w", padx=12, pady=(10, 2))
            ttk.Label(box, textvariable=var, style="Value.TLabel").grid(row=1, column=0, sticky="w", padx=12, pady=(0, 10))
        ttk.Label(ops, textvariable=self.preflight_var, style="CardText.TLabel", wraplength=360, justify="left").grid(row=3, column=0, sticky="ew", padx=18)
        ttk.Label(ops, textvariable=self.watch_var, style="CardText.TLabel").grid(row=4, column=0, sticky="w", padx=18, pady=(8, 0))
        obtns = ttk.Frame(ops, style="Card.TFrame")
        obtns.grid(row=5, column=0, sticky="ew", padx=18, pady=(12, 18))
        for i in range(2):
            obtns.columnconfigure(i, weight=1)
        ttk.Button(obtns, text="空间预检", style="Ghost.TButton", command=self._estimate).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(obtns, text="模型预热", style="Ghost.TButton", command=self._bootstrap).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Button(obtns, text="全量建库", style="Accent.TButton", command=self._rebuild).grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(10, 0))
        ttk.Button(obtns, text="刷新状态", style="Ghost.TButton", command=self._refresh).grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(10, 0))
        self.watch_button = ttk.Button(obtns, text="启动热监听", style="Accent.TButton", command=self._toggle_watch)
        self.watch_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        clean = self._card(self.left, "分类清理", "只清理无界 RAG 自己的数据。", 2, pady=(14, 0))
        inner = ttk.Frame(clean, style="Card.TFrame")
        inner.grid(row=2, column=0, sticky="ew", padx=18, pady=(6, 18))
        ttk.Checkbutton(inner, text="索引数据库与向量库", variable=self.clear_index_var, style="App.TCheckbutton").grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(inner, text="日志", variable=self.clear_logs_var, style="App.TCheckbutton").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Checkbutton(inner, text="模型缓存", variable=self.clear_cache_var, style="App.TCheckbutton").grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Checkbutton(inner, text="导出的上下文包", variable=self.clear_exports_var, style="App.TCheckbutton").grid(row=3, column=0, sticky="w", pady=(4, 0))
        ttk.Button(inner, text="执行清理", style="Danger.TButton", command=self._clear).grid(row=4, column=0, sticky="ew", pady=(12, 0))

    def _build_right_cards(self) -> None:
        search = self._card(self.right, "查询台", "拿到高质量相关页面和片段，直接复制给 AI。", 0)
        bar = ttk.Frame(search, style="Card.TFrame")
        bar.grid(row=2, column=0, sticky="ew", padx=18, pady=(8, 18))
        bar.columnconfigure(0, weight=1)
        entry = ttk.Entry(bar, textvariable=self.query_var, style="App.TEntry")
        entry.grid(row=0, column=0, sticky="ew")
        entry.bind("<Return>", lambda _event: self._query(False))
        ttk.Button(bar, text="查询", style="Ghost.TButton", command=lambda: self._query(False)).grid(row=0, column=1, padx=(10, 0))
        ttk.Button(bar, text="查询并复制", style="Accent.TButton", command=lambda: self._query(True)).grid(row=0, column=2, padx=(10, 0))
        ttk.Button(bar, text="复制当前上下文", style="Ghost.TButton", command=self._copy_context).grid(row=0, column=3, padx=(10, 0))

        results = self._card(self.right, "命中结果", "双击或选择查看详情。", 1, pady=(14, 0))
        results.rowconfigure(2, weight=1)
        results.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(results, columns=("title", "anchor", "path", "score"), show="headings", style="App.Treeview")
        for key, title, width in (("title", "页面", 190), ("anchor", "语义路径", 360), ("path", "来源", 220), ("score", "分数", 80)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w" if key != "score" else "e")
        self.tree.grid(row=2, column=0, sticky="nsew", padx=(18, 0), pady=(8, 18))
        self.tree.bind("<<TreeviewSelect>>", self._select_hit)
        scroll = ttk.Scrollbar(results, orient="vertical", command=self.tree.yview)
        scroll.grid(row=2, column=1, sticky="ns", padx=(0, 18), pady=(8, 18))
        self.tree.configure(yscrollcommand=scroll.set)

        self.tabs = ttk.Notebook(self.right, style="App.TNotebook")
        self.tabs.grid(row=2, column=0, sticky="nsew", pady=(14, 0))
        preview_tab = ttk.Frame(self.tabs, style="Card.TFrame")
        context_tab = ttk.Frame(self.tabs, style="Card.TFrame")
        log_tab = ttk.Frame(self.tabs, style="Card.TFrame")
        self.tabs.add(preview_tab, text="片段预览")
        self.tabs.add(context_tab, text="上下文包")
        self.tabs.add(log_tab, text="活动日志")
        self.preview_text = self._text(preview_tab)
        self.context_text = self._text(context_tab)
        self.log_text = self._text(log_tab)
        self._set_text(self.preview_text, "查询后选择一条结果，这里会展示完整片段。")
        self._set_text(self.context_text, "最终会复制给 AI 的上下文包会显示在这里。")
        self._set_text(self.log_text, "启动后，这里会记录关键动作。")

    def _path_row(self, parent, row, label, variable, browse_cmd) -> None:
        ttk.Label(parent, text=label, style="CardText.TLabel").grid(row=row, column=0, sticky="w", pady=(8, 0))
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.grid(row=row, column=1, sticky="ew", pady=(8, 0))
        frame.columnconfigure(0, weight=1)
        ttk.Entry(frame, textvariable=variable, style="App.TEntry").grid(row=0, column=0, sticky="ew")
        ttk.Button(frame, text="浏览", style="Ghost.TButton", command=browse_cmd).grid(row=0, column=1, padx=(8, 0))

    def _text(self, parent) -> tk.Text:
        text = tk.Text(parent, wrap="word", relief="flat", borderwidth=0, background="#fffefb", foreground=self.colors["ink"], insertbackground=self.colors["ink"], font=("Consolas", 10), padx=16, pady=14)
        text.pack(fill="both", expand=True)
        return text
    def _load_initial_config(self) -> None:
        paths = ensure_data_paths()
        self.data_dir_var.set(str(paths.root))
        self._load_config(paths)
        self._append_log("已启动桌面界面。")
        self._refresh()

    def _choose_vault(self) -> None:
        selected = filedialog.askdirectory(title="选择笔记库根目录", initialdir=self.vault_var.get().strip() or str(Path.home()))
        if selected:
            self.vault_var.set(selected)

    def _choose_data_dir(self) -> None:
        selected = filedialog.askdirectory(title="选择数据目录", initialdir=self.data_dir_var.get().strip() or str(default_data_root()))
        if selected:
            self.data_dir_var.set(selected)
            self._load_config_from_current_dir()

    def _load_config_from_current_dir(self) -> None:
        self._load_config(ensure_data_paths(self.data_dir_var.get().strip() or str(default_data_root())))

    def _load_config(self, paths) -> None:
        config = load_config(paths)
        if config is None:
            self.preflight_var.set("当前数据目录还没有保存过配置。")
            return
        self.vault_var.set(config.vault_path)
        self.data_dir_var.set(config.data_root)
        self.backend_var.set(config.vector_backend or "disabled")
        self.model_var.set(config.vector_model)
        self.runtime_var.set(config.vector_runtime)
        self.device_var.set(config.vector_device)
        self.limit_var.set(str(config.query_limit))
        self.interval_var.set(str(config.poll_interval_seconds))
        self.local_only_var.set(config.vector_local_files_only)
        self._append_log(f"已载入配置：{paths.config_file}")

    def _config(self, require_vault: bool):
        paths = ensure_data_paths(self.data_dir_var.get().strip() or str(default_data_root()))
        vault = self.vault_var.get().strip()
        if require_vault:
            if not vault:
                raise ValueError("请先选择笔记库根目录。")
            vault_path = Path(vault).expanduser().resolve()
            if not vault_path.exists() or not vault_path.is_dir():
                raise ValueError("笔记库根目录不存在，或不是文件夹。")
            vault = str(vault_path)
        else:
            vault = str(Path(vault).expanduser().resolve()) if vault else str(Path.home())
        limit = int(self.limit_var.get().strip() or "8")
        interval = float(self.interval_var.get().strip() or "2.0")
        if limit <= 0 or interval <= 0:
            raise ValueError("查询条数和监听间隔都必须大于 0。")
        config = AppConfig(vault_path=vault, data_root=str(paths.root), query_limit=limit, poll_interval_seconds=interval, vector_backend=self.backend_var.get().strip() or "disabled", vector_model=self.model_var.get().strip() or "BAAI/bge-m3", vector_runtime=self.runtime_var.get().strip() or "torch", vector_device=self.device_var.get().strip() or "cpu", vector_local_files_only=self.local_only_var.get())
        return config, paths

    def _save_only(self) -> None:
        try:
            config, paths = self._config(False)
            save_config(config, paths)
            self.status_var.set(f"配置已保存到 {paths.config_file}")
            self._append_log(f"已保存配置：{paths.config_file}")
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)

    def _task(self, label, action, callback, require_vault=True) -> None:
        if self.busy:
            messagebox.showinfo("稍等", "后台还有任务在跑，请等这一轮完成。", parent=self.root)
            return
        if self.watch_thread and self.watch_thread.is_alive():
            messagebox.showinfo("先停一下", "当前正在热监听。为避免资源冲突，请先停止监听再执行这个操作。", parent=self.root)
            return
        try:
            config, paths = self._config(require_vault)
            save_config(config, paths)
        except Exception as exc:
            messagebox.showerror("无法开始", str(exc), parent=self.root)
            return
        self.busy = True
        self.status_var.set(f"{label}进行中，请稍候")

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

    def _estimate(self) -> None:
        self._task("空间预检", lambda service: {"report": service.estimate_space(), "status": service.status_snapshot()}, self._after_preflight)

    def _bootstrap(self) -> None:
        force = self.force_var.get()
        def action(service):
            report = service.estimate_space()
            if not report.can_proceed and not force:
                return {"blocked": True, "report": report}
            return {"blocked": False, "report": report, "result": service.bootstrap_model(), "status": service.status_snapshot()}
        self._task("模型预热", action, self._after_bootstrap)

    def _rebuild(self) -> None:
        force = self.force_var.get()
        def action(service):
            report = service.estimate_space()
            if not report.can_proceed and not force:
                return {"blocked": True, "report": report}
            return {"blocked": False, "report": report, "stats": service.rebuild_index(), "status": service.status_snapshot()}
        self._task("全量建库", action, self._after_rebuild)

    def _refresh(self) -> None:
        self._task("刷新状态", lambda service: service.status_snapshot(), self._after_status, require_vault=False)

    def _query(self, copy_result: bool) -> None:
        query = self.query_var.get().strip()
        if not query:
            messagebox.showinfo("还没输入", "先输入你要检索的问题。", parent=self.root)
            return
        self._task("查询", lambda service: {"query": query, "copied": copy_result, "payload": service.query(query, copy_result=copy_result)}, self._after_query)

    def _clear(self) -> None:
        if not any((self.clear_index_var.get(), self.clear_logs_var.get(), self.clear_cache_var.get(), self.clear_exports_var.get())):
            messagebox.showinfo("还没选择", "至少勾选一类数据再清理。", parent=self.root)
            return
        if not messagebox.askyesno("确认清理", "这只会清理无界 RAG 的数据目录，不会删除你的原始笔记。继续吗？", parent=self.root):
            return
        def action(service):
            service.clear_data(clear_index=self.clear_index_var.get(), clear_logs=self.clear_logs_var.get(), clear_cache=self.clear_cache_var.get(), clear_exports=self.clear_exports_var.get())
            return service.status_snapshot()
        self._task("清理数据", action, self._after_clear, require_vault=False)

    def _toggle_watch(self) -> None:
        if self.watch_thread and self.watch_thread.is_alive():
            if self.watch_stop is not None:
                self.watch_stop.set()
            self.status_var.set("正在停止热监听")
            self._append_log("收到停止热监听请求。")
            return
        try:
            config, paths = self._config(True)
            save_config(config, paths)
        except Exception as exc:
            messagebox.showerror("无法启动监听", str(exc), parent=self.root)
            return
        self.watch_stop = threading.Event()
        mode = "polling" if self.polling_var.get() or not WATCHDOG_AVAILABLE else "watchdog"
        self.watch_button.configure(text="停止热监听", style="Danger.TButton")
        self.watch_var.set(f"监听中：{mode} / {config.poll_interval_seconds:.1f}s")
        self.status_var.set("热监听运行中")
        self._append_log(f"已启动热监听，模式：{mode}。")

        def worker() -> None:
            service = OmniClipService(config, paths)
            try:
                service.watch_until_stopped(self.watch_stop, interval=config.poll_interval_seconds, force_polling=self.polling_var.get(), on_update=lambda payload: self.queue.put(("watch-update", payload)))
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
        self.status_var.set("空间预检完成")
        self._append_log("空间预检完成。")

    def _after_bootstrap(self, payload) -> None:
        report = payload.get("report")
        if report is not None:
            self.current_report = report
            self.preflight_var.set(summarize_preflight(report))
            self._set_text(self.context_text, format_space_report(report))
        if payload.get("blocked"):
            self.status_var.set("模型预热已停止")
            self._append_log("模型预热被预检查拦下。")
            messagebox.showwarning("预热已停止", "空间或本地模型前置条件未通过。你可以先看右侧预检结果，或勾选“预检失败仍继续”。", parent=self.root)
            return
        result = payload["result"]
        self._apply_status(payload.get("status"))
        self.status_var.set("模型预热完成")
        self._append_log(f"模型预热完成：{result.get('model')} / 维度 {result.get('dimension')} / 缓存 {format_bytes(int(result.get('cache_bytes', 0)))}")

    def _after_rebuild(self, payload) -> None:
        report = payload.get("report")
        if report is not None:
            self.current_report = report
            self.preflight_var.set(summarize_preflight(report))
        if payload.get("blocked"):
            self.status_var.set("全量建库已停止")
            self._append_log("全量建库被预检查拦下。")
            messagebox.showwarning("建库已停止", "空间或本地模型前置条件未通过。你可以先做预检，或勾选“预检失败仍继续”。", parent=self.root)
            return
        stats = payload["stats"]
        self._apply_status(payload.get("status"))
        self.status_var.set("全量建库完成")
        self._append_log(f"全量建库完成：{stats['files']} 文件 / {stats['chunks']} 片段 / {stats['refs']} 引用。")
    def _after_status(self, payload) -> None:
        self._apply_status(payload)
        self.status_var.set("状态已刷新")
        self._append_log("已刷新状态。")

    def _after_query(self, payload) -> None:
        query = payload["query"]
        copied = payload["copied"]
        hits, context = payload["payload"]
        self.current_hits = hits
        self.current_context = context
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, hit in enumerate(hits):
            self.tree.insert("", "end", iid=str(index), values=(hit.title, hit.anchor, hit.source_path, f"{hit.score:.1f}"))
        if hits:
            self.tree.selection_set("0")
            self._show_hit(0)
        else:
            self._set_text(self.preview_text, "没有命中高置信内容。")
        self._set_text(self.context_text, context)
        self.tabs.select(1)
        self.result_var.set(f"本次命中 {len(hits)} 条结果")
        self.status_var.set("查询完成" + ("，已复制上下文" if copied else ""))
        self._append_log(f"查询完成：{query} / 命中 {len(hits)} 条。")

    def _after_clear(self, payload) -> None:
        self.clear_index_var.set(False)
        self.clear_logs_var.set(False)
        self.clear_cache_var.set(False)
        self.clear_exports_var.set(False)
        self._apply_status(payload)
        self.status_var.set("清理完成")
        self._append_log("已完成分类清理。")

    def _apply_status(self, payload) -> None:
        if not isinstance(payload, dict):
            return
        stats = payload.get("stats") or {}
        self.files_var.set(str(stats.get("files", 0)))
        self.chunks_var.set(str(stats.get("chunks", 0)))
        self.refs_var.set(str(stats.get("refs", 0)))
        latest = payload.get("latest_preflight")
        if isinstance(latest, dict):
            self.preflight_var.set(f"最近预检：风险 {latest.get('risk_level')} / 需要 {format_bytes(int(latest.get('required_free_bytes', 0)))} / 可用 {format_bytes(int(latest.get('available_free_bytes', 0)))}")
        elif self.current_report is not None:
            self.preflight_var.set(summarize_preflight(self.current_report))
        backend = payload.get("vector_backend") or self.backend_var.get().strip() or "disabled"
        watchdog_text = "watchdog 可用" if payload.get("watchdog_available", WATCHDOG_AVAILABLE) else "watchdog 不可用，监听会回退到轮询"
        self.watch_var.set(f"向量后端：{backend} | {watchdog_text}")

    def _select_hit(self, _event=None) -> None:
        selection = self.tree.selection()
        if selection:
            self._show_hit(int(selection[0]))

    def _show_hit(self, index: int) -> None:
        if index < 0 or index >= len(self.current_hits):
            return
        hit = self.current_hits[index]
        self._set_text(self.preview_text, f"页面：{hit.title}\n语义路径：{hit.anchor}\n来源：{hit.source_path}\n分数：{hit.score:.2f}\n\n{hit.rendered_text}")

    def _copy_context(self) -> None:
        if not self.current_context.strip():
            messagebox.showinfo("还没有内容", "先查一次，再复制当前上下文。", parent=self.root)
            return
        copy_text(self.current_context)
        self.status_var.set("已复制当前上下文")
        self._append_log("已复制当前上下文包。")

    def _open_vault(self) -> None:
        vault = self.vault_var.get().strip()
        if not vault:
            messagebox.showinfo("还没设置", "先选择笔记库路径。", parent=self.root)
            return
        service = OmniClipService(AppConfig(vault_path=str(Path(vault).expanduser().resolve()), data_root=str(ensure_data_paths(self.data_dir_var.get().strip() or str(default_data_root())).root)), ensure_data_paths(self.data_dir_var.get().strip() or str(default_data_root())))
        try:
            service.open_vault_dir()
        finally:
            service.close()

    def _open_data(self) -> None:
        paths = ensure_data_paths(self.data_dir_var.get().strip() or str(default_data_root()))
        service = OmniClipService(AppConfig(vault_path=self.vault_var.get().strip() or str(Path.home()), data_root=str(paths.root)), paths)
        try:
            service.open_data_dir()
        finally:
            service.close()

    def _open_exports(self) -> None:
        paths = ensure_data_paths(self.data_dir_var.get().strip() or str(default_data_root()))
        service = OmniClipService(AppConfig(vault_path=self.vault_var.get().strip() or str(Path.home()), data_root=str(paths.root)), paths)
        try:
            service.open_exports_dir()
        finally:
            service.close()

    def _append_log(self, message: str) -> None:
        self.log_text.insert("end", message.strip() + "\n")
        self.log_text.see("end")

    def _set_text(self, widget: tk.Text, text: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", text)

    def _drain_queue(self) -> None:
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            kind = item[0]
            if kind == "success":
                _, label, payload, callback = item
                self.busy = False
                if callback:
                    callback(payload)
            elif kind == "error":
                _, label, tb = item
                self.busy = False
                self.status_var.set(f"{label}失败")
                self._append_log(f"{label}失败。")
                self._append_log(tb.strip())
                self.tabs.select(2)
                messagebox.showerror(f"{label}失败", "\n".join([line.strip() for line in tb.splitlines() if line.strip()][-6:]), parent=self.root)
            elif kind == "watch-update":
                _, payload = item
                stats = payload.get("stats", {})
                self.files_var.set(str(stats.get("files", 0)))
                self.chunks_var.set(str(stats.get("chunks", 0)))
                self.refs_var.set(str(stats.get("refs", 0)))
                self.status_var.set("热监听已完成一次增量更新")
                self._append_log(f"监听更新：改动 {', '.join(payload.get('changed', [])[:3]) or '无'} / 删除 {', '.join(payload.get('deleted', [])[:3]) or '无'}。")
            elif kind == "watch-error":
                _, tb = item
                self.status_var.set("热监听异常停止")
                self._append_log("热监听异常停止。")
                self._append_log(tb.strip())
                messagebox.showerror("热监听异常", "\n".join([line.strip() for line in tb.splitlines() if line.strip()][-6:]), parent=self.root)
            elif kind == "watch-stopped":
                _, mode = item
                self.watch_button.configure(text="启动热监听", style="Accent.TButton")
                self.watch_var.set(f"热监听已停止，上一轮模式：{mode}")
                self.status_var.set("热监听已停止")
                self._append_log("热监听已停止。")
                self.watch_thread = None
                self.watch_stop = None
        self.root.after(120, self._drain_queue)

    def _on_close(self) -> None:
        if self.watch_stop is not None:
            self.watch_stop.set()
        self.root.destroy()


def main() -> int:
    return OmniClipDesktopApp().run()


if __name__ == "__main__":
    raise SystemExit(main())


