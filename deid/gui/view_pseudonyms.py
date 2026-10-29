"""Pseudonyms view: browse / search / re-identify / export the crosswalk.

The index maps original identifiers to the stable pseudonyms assigned during
de-identification, scoped to the active project. It is local-only and contains
PHI (the original values), so it is shown here on screen but never written to
the activity log and never leaves the machine except via an explicit CSV export
you choose.
"""

from __future__ import annotations

from tkinter import filedialog, messagebox

import customtkinter as ctk

from .base import View
from .theme import MUTED, mono
from .widgets import PAD

_SHOWN_LIMIT = 500


class PseudonymsView(View):
    key = "pseudonyms"
    title = "Pseudonyms"
    description = ("The original → pseudonym crosswalk for the active project "
                  "(local only; this is how you re-identify).")

    def build(self) -> None:
        self.body.grid_rowconfigure(2, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=PAD, pady=PAD)
        bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(bar, text="Search").grid(row=0, column=0, padx=(0, 6))
        self.search = ctk.CTkEntry(bar, placeholder_text="original or pseudonym…")
        self.search.grid(row=0, column=1, sticky="ew")
        self.search.bind("<KeyRelease>", lambda _e: self._refresh())
        ctk.CTkButton(bar, text="Export crosswalk CSV…", width=170,
                      command=self._export).grid(row=0, column=2, padx=6)
        ctk.CTkButton(bar, text="Clear index", width=100, fg_color="#a33",
                      hover_color="#c44", command=self._clear).grid(
            row=0, column=3)

        self.count_lbl = ctk.CTkLabel(self.body, text="", anchor="w",
                                      text_color=MUTED)
        self.count_lbl.grid(row=1, column=0, sticky="w", padx=PAD)

        self.table = ctk.CTkScrollableFrame(self.body)
        self.table.grid(row=2, column=0, sticky="nsew", padx=PAD, pady=(2, PAD))
        for c, w in enumerate((160, 1, 1, 150)):
            self.table.grid_columnconfigure(c, weight=w)

    # -- rendering -------------------------------------------------------
    def _refresh(self) -> None:
        for w in self.table.winfo_children():
            w.destroy()
        project = self.app.active_project.name
        idx = self.app.pseudonyms
        query = self.search.get().strip()
        total = idx.count(project)
        rows = idx.entries(project, query=query, limit=_SHOWN_LIMIT)

        hdr = ("Kind", "Original (PHI)", "Pseudonym", "First seen")
        for c, text in enumerate(hdr):
            ctk.CTkLabel(self.table, text=text, anchor="w",
                         font=ctk.CTkFont(size=12, weight="bold")).grid(
                row=0, column=c, sticky="w", padx=6, pady=(0, 4))
        for r, (kind, original, pseudonym, created) in enumerate(rows, start=1):
            ctk.CTkLabel(self.table, text=kind, anchor="w", font=mono(12)).grid(
                row=r, column=0, sticky="w", padx=6, pady=1)
            ctk.CTkLabel(self.table, text=original, anchor="w",
                         font=mono(12)).grid(row=r, column=1, sticky="w",
                                             padx=6, pady=1)
            ctk.CTkLabel(self.table, text=pseudonym, anchor="w",
                         font=mono(12)).grid(row=r, column=2, sticky="w",
                                             padx=6, pady=1)
            ctk.CTkLabel(self.table, text=(created or "")[:10], anchor="w",
                         text_color=MUTED, font=mono(11)).grid(
                row=r, column=3, sticky="w", padx=6, pady=1)

        if total == 0:
            self.count_lbl.configure(
                text="No pseudonyms yet — run a project with pseudonymization "
                "enabled (set it in Rules).")
        else:
            shown = len(rows)
            extra = (f"  (showing {shown} of {total}" +
                     (" matching)" if query else ")")) if (
                         shown < total or query) else ""
            self.count_lbl.configure(
                text=f"{total:,} mapping(s) for “{project}”.{extra}")

    def on_show(self) -> None:
        self._refresh()

    def on_project_changed(self) -> None:
        self._refresh()

    # -- actions ---------------------------------------------------------
    def _export(self) -> None:
        project = self.app.active_project.name
        if self.app.pseudonyms.count(project) == 0:
            messagebox.showinfo("Export", "Nothing to export yet.", parent=self)
            return
        if not messagebox.askyesno(
                "Export crosswalk",
                "The CSV will contain the ORIGINAL identifiers (PHI) next to "
                "their pseudonyms. Save it only to a secure location. Continue?",
                parent=self):
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile=f"{project}_crosswalk.csv")
        if not path:
            return
        n = self.app.pseudonyms.export_csv(project, path)
        messagebox.showinfo("Export", f"Wrote {n:,} mapping(s).", parent=self)

    def _clear(self) -> None:
        project = self.app.active_project.name
        if not messagebox.askyesno(
                "Clear index",
                f"Delete ALL pseudonym mappings for “{project}”? This cannot be "
                "undone — you will lose the ability to re-identify, and a later "
                "run will assign NEW pseudonyms.", parent=self):
            return
        self.app.pseudonyms.clear(project)
        self._refresh()
