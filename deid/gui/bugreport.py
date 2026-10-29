"""Minimal bug-report dialog: builds a PHI-scrubbed diagnostics bundle and
opens a pre-filled email. The recipient is resolved at runtime (Settings / env /
CI-stamped) and is never hard-coded in the public source (see support.py)."""

from __future__ import annotations

import webbrowser
from tkinter import messagebox

import customtkinter as ctk

from .. import support
from .widgets import PAD


class BugReportDialog(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Report a Bug")
        self.geometry("560x440")
        self.transient(app)
        self.grab_set()

        ctk.CTkLabel(self, text="Report a Bug",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(
            anchor="w", padx=PAD, pady=(PAD, 2))
        ctk.CTkLabel(self, text="A PHI-scrubbed diagnostics bundle is attached "
                     "(no images). Review before sending.",
                     justify="left").pack(anchor="w", padx=PAD)

        ctk.CTkLabel(self, text="What happened?").pack(anchor="w", padx=PAD,
                                                       pady=(PAD, 0))
        self.desc = ctk.CTkTextbox(self, height=120, wrap="word")
        self.desc.pack(fill="both", expand=True, padx=PAD, pady=4)
        ctk.CTkLabel(self, text="Steps to reproduce").pack(anchor="w", padx=PAD)
        self.steps = ctk.CTkTextbox(self, height=90, wrap="word")
        self.steps.pack(fill="both", expand=True, padx=PAD, pady=4)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=PAD, pady=PAD)
        ctk.CTkButton(bar, text="Create report & open email",
                      command=self._send).pack(side="left")
        ctk.CTkButton(bar, text="Close", fg_color="gray40",
                      command=self.destroy).pack(side="left", padx=PAD)

    def _send(self) -> None:
        desc = self.desc.get("1.0", "end").strip()
        steps = self.steps.get("1.0", "end").strip()
        try:
            zpath = support.build_report(desc, steps, include_log=True,
                                         settings=self.app.settings)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Report", f"Could not build report: {exc}",
                                 parent=self)
            return
        to = support.resolve_bug_email(self.app.settings)
        body = support.report_body(zpath, self.app.settings)
        webbrowser.open(support.mailto_url(to, support.default_subject(), body))
        messagebox.showinfo(
            "Report created",
            f"Diagnostics saved to:\n{zpath}\n\nAttach it to the email that "
            "opened" + (f" to {to}." if to else ". Set a recipient in Settings."),
            parent=self)
        self.destroy()
