"""Shared light desktop controls for EverAI Studio."""
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

BACKGROUND = "#e6edf7"
ACCENT = "#285fe8"
TEXT = "#17243b"
MUTED = "#52647f"
BORDER = "#bac9df"


class StudioButton(ctk.CTkButton):
    def __init__(self, master, *, style="", width=100, **kwargs):
        primary = style == "Primary.TButton"
        balance = style == "Balance.TButton"
        if isinstance(master, ttk.Widget):
            kwargs.setdefault("bg_color", ttk.Style(master).lookup(master.cget("style") or "TFrame", "background") or "white")
        super().__init__(
            master, width=width if width > 10 else width * 10 + 16, height=34 if primary else 32,
            corner_radius=8, border_width=1,
            fg_color=ACCENT if primary else "#eef8f2" if balance else "white",
            hover_color="#2456d9" if primary else "#eaf0fe",
            border_color=ACCENT if primary else "#dcefe4" if balance else BORDER,
            text_color="white" if primary else "#21805b" if balance else TEXT,
            text_color_disabled="#a0abc0", font=("Segoe UI", 13), **kwargs,
        )
        self._resting_border = self.cget("border_color")
        tk.Frame.configure(self, takefocus=1)
        tk.Frame.bind(self, "<Return>", self._keyboard_invoke)
        tk.Frame.bind(self, "<space>", self._keyboard_invoke)
        tk.Frame.bind(self, "<FocusIn>", lambda _: self.configure(border_color=ACCENT))
        tk.Frame.bind(self, "<FocusOut>", lambda _: self.configure(border_color=self._resting_border))

    def _keyboard_invoke(self, _):
        self.invoke()
        return "break"


class Card(ctk.CTkFrame):
    def __init__(self, master, title, *, tint="#e4edff", accent="#234eaa"):
        super().__init__(master, fg_color="white", bg_color=BACKGROUND, border_color=BORDER,
                         border_width=1, corner_radius=14)
        tk.Label(self, text=title, bg=tint, fg=accent, anchor="w", padx=10, pady=2,
                 font=("Segoe UI", 11, "bold")).pack(fill="x", padx=8, pady=(8, 4))
        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True, padx=16, pady=(0, 10))
