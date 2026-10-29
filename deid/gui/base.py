"""Base class for nav-rail views."""

from __future__ import annotations

import customtkinter as ctk

from .theme import MUTED, view_color
from .widgets import PAD


class View(ctk.CTkFrame):
    """A single focused view: a titled header plus a body for its controls."""

    key = "view"
    title = "View"
    description = ""

    def __init__(self, master, app, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        accent = view_color(self.key)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 0))
        titlerow = ctk.CTkFrame(header, fg_color="transparent")
        titlerow.pack(anchor="w", fill="x")
        ctk.CTkFrame(titlerow, width=6, height=28, corner_radius=3,
                     fg_color=accent).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(titlerow, text=self.title, text_color=accent,
                     font=ctk.CTkFont(size=22, weight="bold"),
                     anchor="w").pack(side="left")
        if self.description:
            ctk.CTkLabel(header, text=self.description, text_color=MUTED,
                         anchor="w", justify="left").pack(anchor="w")

        self.body = ctk.CTkFrame(self)
        self.body.grid(row=1, column=0, sticky="nsew", padx=PAD, pady=PAD)
        self.body.grid_columnconfigure(0, weight=1)

        self.build()

    # Subclasses override ------------------------------------------------
    def build(self) -> None:
        """Populate ``self.body`` with controls."""

    def on_show(self) -> None:
        """Called each time the view becomes visible."""

    def on_project_changed(self) -> None:
        """Called when the active project changes or is edited elsewhere."""
