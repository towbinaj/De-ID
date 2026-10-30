"""Shared visual theme: readable colors, fonts, and UI scaling."""

from __future__ import annotations

import customtkinter as ctk

# Secondary/hint text. Tuple = (light-mode, dark-mode).
MUTED = ("gray30", "gray74")

DEFAULT_SCALE = 1.15

SCALE_OPTIONS: dict[str, float] = {
    "90%": 0.9,
    "100%": 1.0,
    "115%": 1.15,
    "130%": 1.3,
    "150%": 1.5,
    "175%": 1.75,
}


def scale_name(value: float) -> str:
    for name, v in SCALE_OPTIONS.items():
        if abs(v - value) < 0.001:
            return name
    return "115%"


def apply_scale(value: float) -> None:
    """Scale every widget + font uniformly (CustomTkinter global setting)."""
    ctk.set_widget_scaling(value)


def mono(size: int = 13) -> ctk.CTkFont:
    return ctk.CTkFont(family="Consolas", size=size)


# Per-view accent colors (nav-rail accent bar + view title). Keyed by view key.
VIEW_COLORS: dict[str, str] = {
    "quick": "#4f9dde",         # blue (the guided front door)
    "projects": "#cf6f9b",      # pink (the de-id identity color)
    "rules": "#d4a017",         # gold
    "pseudonyms": "#5aa9c9",    # cyan
    "run": "#5cb85c",           # green
    "pixelscan": "#c96f5a",     # terracotta (pixel inspection)
    "queue": "#7fa650",         # olive
    "settings": "#8a8f94",      # gray
}

DEFAULT_ACCENT = "#cf6f9b"


def view_color(key: str) -> str:
    return VIEW_COLORS.get(key, DEFAULT_ACCENT)
