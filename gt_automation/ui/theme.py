"""Cross-platform ttk theme for the desktop application."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Dict


PALETTES = {
    "dark": {
        "bg": "#0b1020",
        "sidebar": "#080d19",
        "sidebar_card": "#0e1728",
        "card": "#121a2b",
        "card_alt": "#172135",
        "border": "#26344d",
        "text": "#e8eefc",
        "muted": "#91a0bb",
        "accent": "#42d3a5",
        "accent_hover": "#66e6bc",
        "blue": "#5aa7ff",
        "warning": "#f6c86a",
        "warning_bg": "#3a2c17",
        "danger": "#ff7185",
        "success": "#55d68b",
        "success_bg": "#123326",
        "log_bg": "#070b13",
        "input": "#0d1524",
        "primary_text": "#07120f",
        "primary_disabled": "#34423f",
        "primary_disabled_text": "#71817d",
        "secondary_hover": "#22304a",
        "secondary_disabled": "#141b29",
        "disabled_text": "#66738b",
        "danger_bg": "#3a1d27",
        "danger_hover": "#512433",
        "danger_disabled": "#21151a",
        "danger_disabled_text": "#76515b",
    },
    "light": {
        "bg": "#f4f7fb",
        "sidebar": "#e9eef6",
        "sidebar_card": "#ffffff",
        "card": "#ffffff",
        "card_alt": "#f2f5fa",
        "border": "#d8e0eb",
        "text": "#182235",
        "muted": "#68758a",
        "accent": "#159b73",
        "accent_hover": "#118562",
        "blue": "#2e74c9",
        "warning": "#b7791f",
        "warning_bg": "#fff3d9",
        "danger": "#d64c61",
        "success": "#218a59",
        "success_bg": "#e3f6ec",
        "log_bg": "#101825",
        "input": "#f8fafc",
        "primary_text": "#07120f",
        "primary_disabled": "#b7cec7",
        "primary_disabled_text": "#7f918b",
        "secondary_hover": "#e3eaf4",
        "secondary_disabled": "#edf1f6",
        "disabled_text": "#a1aab8",
        "danger_bg": "#fdecef",
        "danger_hover": "#fbdbe1",
        "danger_disabled": "#f7f0f1",
        "danger_disabled_text": "#c7a7ad",
    },
}

COLORS = dict(PALETTES["dark"])


def set_theme(name: str) -> str:
    selected = name if name in PALETTES else "light"
    COLORS.clear()
    COLORS.update(PALETTES[selected])
    return selected


def choose_font_family(root: tk.Misc) -> str:
    available = set(tkfont.families(root))
    for family in (
        "SF Pro Display",
        "Segoe UI Variable",
        "Segoe UI",
        "Helvetica Neue",
        "Arial",
    ):
        if family in available:
            return family
    return "TkDefaultFont"


def configure_theme(root: tk.Misc) -> Dict[str, tkfont.Font]:
    family = choose_font_family(root)
    fonts = {
        "body": tkfont.Font(root=root, family=family, size=11),
        "small": tkfont.Font(root=root, family=family, size=9),
        "bold": tkfont.Font(root=root, family=family, size=11, weight="bold"),
        "title": tkfont.Font(root=root, family=family, size=24, weight="bold"),
        "section": tkfont.Font(root=root, family=family, size=14, weight="bold"),
        "brand": tkfont.Font(root=root, family=family, size=19, weight="bold"),
        "log": tkfont.Font(
            root=root,
            family="Menlo" if "Menlo" in set(tkfont.families(root)) else "Consolas",
            size=10,
        ),
    }

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=fonts["body"], background=COLORS["bg"])
    style.configure("TFrame", background=COLORS["bg"])
    style.configure("Card.TFrame", background=COLORS["card"])
    style.configure("CardAlt.TFrame", background=COLORS["card_alt"])
    style.configure(
        "TLabel",
        background=COLORS["bg"],
        foreground=COLORS["text"],
    )
    style.configure(
        "Card.TLabel",
        background=COLORS["card"],
        foreground=COLORS["text"],
    )
    style.configure(
        "Muted.Card.TLabel",
        background=COLORS["card"],
        foreground=COLORS["muted"],
        font=fonts["small"],
    )
    style.configure(
        "Section.Card.TLabel",
        background=COLORS["card"],
        foreground=COLORS["text"],
        font=fonts["section"],
    )
    style.configure(
        "Value.Card.TLabel",
        background=COLORS["card"],
        foreground=COLORS["text"],
        font=fonts["bold"],
    )
    style.configure(
        "Accent.Card.TLabel",
        background=COLORS["card"],
        foreground=COLORS["accent"],
        font=fonts["bold"],
    )
    style.configure(
        "Warning.Card.TLabel",
        background=COLORS["card"],
        foreground=COLORS["warning"],
        font=fonts["small"],
    )
    style.configure(
        "Danger.Card.TLabel",
        background=COLORS["card"],
        foreground=COLORS["danger"],
        font=fonts["small"],
    )
    style.configure(
        "TButton",
        padding=(14, 8),
        borderwidth=0,
        focusthickness=0,
        font=fonts["bold"],
    )
    style.configure(
        "Primary.TButton",
        background=COLORS["accent"],
        foreground=COLORS["primary_text"],
        padding=(16, 9),
    )
    style.map(
        "Primary.TButton",
        background=[
            ("active", COLORS["accent_hover"]),
            ("disabled", COLORS["primary_disabled"]),
        ],
        foreground=[("disabled", COLORS["primary_disabled_text"])],
    )
    style.configure(
        "Secondary.TButton",
        background=COLORS["card_alt"],
        foreground=COLORS["text"],
        padding=(14, 9),
    )
    style.map(
        "Secondary.TButton",
        background=[
            ("active", COLORS["secondary_hover"]),
            ("disabled", COLORS["secondary_disabled"]),
        ],
        foreground=[("disabled", COLORS["disabled_text"])],
    )
    style.configure(
        "Danger.TButton",
        background=COLORS["danger_bg"],
        foreground=COLORS["danger"],
        padding=(14, 9),
    )
    style.map(
        "Danger.TButton",
        background=[
            ("active", COLORS["danger_hover"]),
            ("disabled", COLORS["danger_disabled"]),
        ],
        foreground=[("disabled", COLORS["danger_disabled_text"])],
    )
    style.configure(
        "TCheckbutton",
        background=COLORS["card"],
        foreground=COLORS["text"],
        font=fonts["body"],
    )
    style.map(
        "TCheckbutton",
        background=[("active", COLORS["card"])],
        foreground=[("disabled", COLORS["muted"])],
        indicatorcolor=[
            ("selected", COLORS["accent"]),
            ("!selected", COLORS["input"]),
        ],
    )
    style.configure(
        "TEntry",
        fieldbackground=COLORS["input"],
        foreground=COLORS["text"],
        insertcolor=COLORS["text"],
        bordercolor=COLORS["border"],
        lightcolor=COLORS["border"],
        darkcolor=COLORS["border"],
        padding=7,
    )
    style.configure(
        "Horizontal.TProgressbar",
        background=COLORS["accent"],
        troughcolor=COLORS["input"],
        borderwidth=0,
        thickness=4,
    )
    return fonts
