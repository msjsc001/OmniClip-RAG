from __future__ import annotations

import tkinter as tk


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str, *, delay_ms: int = 350) -> None:
        self.widget = widget
        self.text = text.strip()
        self.delay_ms = delay_ms
        self._after_id: str | None = None
        self._tip: tk.Toplevel | None = None
        if self.text:
            widget.bind("<Enter>", self._schedule, add=True)
            widget.bind("<Leave>", self._hide, add=True)
            widget.bind("<ButtonPress>", self._hide, add=True)

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self) -> None:
        if self._tip is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 10
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.attributes("-topmost", True)
        self._tip.geometry(f"+{x}+{y}")
        label = tk.Label(
            self._tip,
            text=self.text,
            justify="left",
            wraplength=300,
            background="#152232",
            foreground="#ffffff",
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 9),
            padx=10,
            pady=7,
        )
        label.pack()

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None
