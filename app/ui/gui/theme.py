"""The desktop window's look (ADR 0057): an instrument-panel palette, fonts and ttk styles.

Dark navy surfaces, one cyan accent used sparingly, and three type roles: Bahnschrift
(an engineered DIN-style face shipped with Windows) for the wordmark and headings, Segoe
UI for text, Cascadia Mono for command output. Each role falls back to a face every
Windows 11 has.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from tkinter import ttk

# Surfaces, darkest first.
BG = "#0a0f1c"
OUTPUT_BG = "#070b15"
SURFACE = "#0e1527"
RAISED = "#131c33"
HOVER = "#18233f"
# Lines.
LINE = "#1d2944"
LINE_BRIGHT = "#2a3b62"
GRID_DOT = "#18233c"
# Text.
TEXT = "#e6edf7"
MUTED = "#8a9ab8"
FAINT = "#5a6890"
# The accent and the states.
ACCENT = "#38bdf8"
ACCENT_SOFT = "#7dd3fc"
ACCENT_DEEP = "#0c4a6e"
OK = "#34d399"
WARN = "#fbbf24"
ERROR = "#f87171"

HEADING_FACES = ("Bahnschrift SemiBold", "Bahnschrift", "Segoe UI Semibold", "Segoe UI")
TEXT_FACES = ("Segoe UI Variable Text", "Segoe UI", "Tahoma")
MONO_FACES = ("Cascadia Mono", "Consolas", "Courier New")


@dataclass(frozen=True)
class Fonts:
    """The type roles, as Tk font descriptions."""

    wordmark: tuple
    title: tuple
    heading: tuple
    label: tuple
    body: tuple
    small: tuple
    code: tuple
    mono: tuple


def first_available(wanted: tuple[str, ...], available: set[str]) -> str:
    """The first face in `wanted` that Tk can use, else the last one (Tk substitutes)."""
    return next((face for face in wanted if face in available), wanted[-1])


def spaced(text: str) -> str:
    """Capitals set apart with thin spaces: the technical label style (Tk has no tracking)."""
    return " ".join(text.upper())


_FACES: dict[str, str] = {}


def fonts(root: tk.Misc) -> Fonts:
    """The type roles, choosing each face once per process (listing the fonts is slow)."""
    if not _FACES:
        available = set(tkfont.families(root))
        _FACES.update(heading=first_available(HEADING_FACES, available),
                      text=first_available(TEXT_FACES, available), mono=first_available(MONO_FACES, available))
    heading, text, mono = _FACES["heading"], _FACES["text"], _FACES["mono"]
    return Fonts(
        wordmark=(heading, 20),
        title=(heading, 18),
        heading=(heading, 11),
        label=(text, 9),
        body=(text, 10),
        small=(text, 8),
        code=(heading, 8),
        mono=(mono, 10),
    )


def apply(root: tk.Tk) -> Fonts:
    """Style the ttk widgets the window uses and return the fonts."""
    chosen = fonts(root)
    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, font=chosen.body, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, troughcolor=SURFACE, focuscolor=ACCENT)
    style.configure("TFrame", background=BG)
    style.configure("Surface.TFrame", background=SURFACE)
    style.configure("TLabel", background=BG, foreground=TEXT)

    style.configure("TEntry", fieldbackground=RAISED, foreground=TEXT, insertcolor=ACCENT_SOFT,
                    bordercolor=LINE_BRIGHT, lightcolor=RAISED, darkcolor=RAISED, padding=6)
    style.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT_DEEP)])

    button = dict(padding=(14, 6), relief="flat", borderwidth=1, font=chosen.label)
    style.configure("TButton", background=RAISED, foreground=TEXT, bordercolor=LINE_BRIGHT,
                    lightcolor=RAISED, darkcolor=RAISED, **button)
    style.map("TButton", background=[("disabled", SURFACE), ("pressed", HOVER), ("active", HOVER)],
              foreground=[("disabled", FAINT)], bordercolor=[("active", ACCENT_DEEP)])
    style.configure("Accent.TButton", background=ACCENT_DEEP, foreground="#f0f9ff", bordercolor=ACCENT,
                    lightcolor=ACCENT_DEEP, darkcolor=ACCENT_DEEP, **button)
    style.map("Accent.TButton", background=[("disabled", SURFACE), ("pressed", "#075985"), ("active", "#075985")],
              foreground=[("disabled", FAINT)], bordercolor=[("disabled", LINE)])

    for widget in ("TCheckbutton", "TRadiobutton"):
        style.configure(widget, background=BG, foreground=MUTED, font=chosen.label,
                        indicatorbackground=RAISED, indicatorforeground=ACCENT_SOFT,
                        upperbordercolor=LINE_BRIGHT, lowerbordercolor=LINE_BRIGHT)
        style.map(widget, background=[("active", BG)], foreground=[("selected", TEXT), ("active", TEXT)],
                  indicatorbackground=[("selected", ACCENT_DEEP)])
    style.configure("Surface.TCheckbutton", background=SURFACE)
    style.map("Surface.TCheckbutton", background=[("active", SURFACE)])

    style.configure("Vertical.TScrollbar", background=RAISED, troughcolor=OUTPUT_BG, bordercolor=OUTPUT_BG,
                    lightcolor=RAISED, darkcolor=RAISED, arrowcolor=MUTED, gripcount=0)
    style.map("Vertical.TScrollbar", background=[("active", HOVER)])
    return chosen
