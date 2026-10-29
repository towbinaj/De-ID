"""Settings view: appearance, text size, and the bug-report recipient.

Destinations and the receiver AE/port are added in the receive/export phase.
"""

from __future__ import annotations

import customtkinter as ctk

from .. import paths
from .base import View
from .theme import SCALE_OPTIONS, apply_scale, scale_name, MUTED
from .widgets import PAD


class SettingsView(View):
    key = "settings"
    title = "Settings"
    description = "Appearance and local preferences. Everything stays on this machine."

    def build(self) -> None:
        s = self.app.settings
        form = ctk.CTkFrame(self.body, fg_color="transparent")
        form.grid(row=0, column=0, sticky="new", padx=PAD, pady=PAD)
        form.grid_columnconfigure(1, weight=1)
        r = 0

        def row(label, widget):
            nonlocal r
            ctk.CTkLabel(form, text=label, anchor="w", width=160).grid(
                row=r, column=0, sticky="w", padx=PAD, pady=6)
            widget.grid(row=r, column=1, sticky="w", padx=PAD, pady=6)
            r += 1
            return widget

        self.appearance = ctk.CTkOptionMenu(
            form, values=["System", "Light", "Dark"],
            command=self._set_appearance)
        self.appearance.set(s.appearance)
        row("Appearance", self.appearance)

        self.scale = ctk.CTkOptionMenu(
            form, values=list(SCALE_OPTIONS), command=self._set_scale)
        self.scale.set(scale_name(s.ui_scale))
        row("Text size", self.scale)

        self.bug_email = ctk.CTkEntry(form, width=320,
                                      placeholder_text="you@example.org")
        if s.bug_report_email:
            self.bug_email.insert(0, s.bug_report_email)
        self.bug_email.bind("<FocusOut>", lambda _e: self._save_email())
        row("Bug-report email", self.bug_email)

        ctk.CTkLabel(form, text=f"Data folder:  {paths.data_dir()}",
                     text_color=MUTED, anchor="w").grid(
            row=r, column=0, columnspan=2, sticky="w", padx=PAD, pady=(PAD, 2))
        r += 1
        ctk.CTkLabel(
            form, text="Projects, the pseudonym index, quarantined originals, "
            "and settings all live there — never in the app's repository.",
            text_color=MUTED, anchor="w", justify="left",
            font=ctk.CTkFont(size=11)).grid(
            row=r, column=0, columnspan=2, sticky="w", padx=PAD)

    def _set_appearance(self, mode: str) -> None:
        ctk.set_appearance_mode(mode)
        self.app.settings.appearance = mode
        self.app.settings.save()

    def _set_scale(self, name: str) -> None:
        value = SCALE_OPTIONS.get(name, 1.15)
        apply_scale(value)
        self.app.settings.ui_scale = value
        self.app.settings.save()

    def _save_email(self) -> None:
        self.app.settings.bug_report_email = self.bug_email.get().strip()
        self.app.settings.save()
