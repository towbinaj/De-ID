"""Projects view: create / duplicate / rename / delete de-id projects and pick
the active one. A project is a saved recipe; edit its rules in the Rules view and
run it in the Run view."""

from __future__ import annotations

import json
from tkinter import filedialog, messagebox, simpledialog

import customtkinter as ctk

from ..project import Project
from .base import View
from .theme import MUTED, view_color
from .widgets import PAD


class ProjectsView(View):
    key = "projects"
    title = "Projects"
    description = ("A project is a saved de-id recipe — pick one, then edit its "
                  "Rules and Run it. The active project is used everywhere.")

    def build(self) -> None:
        self.body.grid_rowconfigure(1, weight=1)
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_columnconfigure(1, weight=1)

        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=PAD, pady=PAD)
        ctk.CTkButton(bar, text="New", width=70, command=self._new).pack(
            side="left")
        ctk.CTkButton(bar, text="Duplicate", width=90,
                      command=self._duplicate).pack(side="left", padx=6)
        ctk.CTkButton(bar, text="Rename", width=80, command=self._rename).pack(
            side="left")
        ctk.CTkButton(bar, text="Delete", width=70, fg_color="#a33",
                      hover_color="#c44", command=self._delete).pack(
            side="left", padx=6)
        ctk.CTkButton(bar, text="Import…", width=80, fg_color="gray40",
                      command=self._import).pack(side="right")
        ctk.CTkButton(bar, text="Export…", width=80, fg_color="gray40",
                      command=self._export).pack(side="right", padx=6)

        self.listframe = ctk.CTkScrollableFrame(self.body,
                                                label_text="Projects")
        self.listframe.grid(row=1, column=0, sticky="nsew", padx=PAD,
                            pady=(0, PAD))
        self.listframe.grid_columnconfigure(0, weight=1)

        self.details = ctk.CTkFrame(self.body)
        self.details.grid(row=1, column=1, sticky="nsew", padx=PAD,
                          pady=(0, PAD))
        self.details.grid_columnconfigure(0, weight=1)
        self.detail_name = ctk.CTkLabel(
            self.details, text="", anchor="w",
            font=ctk.CTkFont(size=18, weight="bold"))
        self.detail_name.grid(row=0, column=0, sticky="ew", padx=PAD,
                              pady=(PAD, 2))
        self.detail_summary = ctk.CTkLabel(
            self.details, text="", anchor="w", justify="left",
            wraplength=360, text_color=MUTED)
        self.detail_summary.grid(row=1, column=0, sticky="ew", padx=PAD)
        self.detail_out = ctk.CTkLabel(
            self.details, text="", anchor="w", justify="left",
            wraplength=360, text_color=MUTED, font=ctk.CTkFont(size=11))
        self.detail_out.grid(row=2, column=0, sticky="ew", padx=PAD,
                             pady=(8, 0))
        ctk.CTkLabel(self.details, text="Edit this recipe in the Rules view; "
                     "process files in the Run view.", anchor="w",
                     justify="left", wraplength=360, text_color=MUTED,
                     font=ctk.CTkFont(size=11)).grid(
            row=3, column=0, sticky="ew", padx=PAD, pady=(12, PAD))

        self._rows: dict = {}
        self._render_list()
        self._render_details()

    # -- rendering -------------------------------------------------------
    def _render_list(self) -> None:
        for w in self.listframe.winfo_children():
            w.destroy()
        self._rows = {}
        active = self.app.active_project.name
        for name in self.app.projects.names():
            is_active = name == active
            row = ctk.CTkButton(
                self.listframe, text=name, anchor="w", height=34,
                fg_color=(view_color(self.key) if is_active else "transparent"),
                text_color=("white" if is_active else ("gray10", "gray90")),
                hover_color=("gray75", "gray25"),
                command=lambda n=name: self._select(n))
            row.grid(sticky="ew", padx=4, pady=2)
            self._rows[name] = row

    def _render_details(self) -> None:
        p = self.app.active_project
        self.detail_name.configure(text=p.name)
        self.detail_summary.configure(text=p.summary() or "(no actions)")
        self.detail_out.configure(
            text=f"Output: {p.output_dir or '(ask at run time)'}")

    def on_project_changed(self) -> None:
        self._render_list()
        self._render_details()

    def on_show(self) -> None:
        self._render_list()
        self._render_details()

    # -- actions ---------------------------------------------------------
    def _select(self, name: str) -> None:
        self.app.set_active_project(name)

    def _unique_name(self, base: str) -> str:
        names = set(self.app.projects.names())
        if base not in names:
            return base
        i = 2
        while f"{base} {i}" in names:
            i += 1
        return f"{base} {i}"

    def _new(self) -> None:
        proj = Project(name=self._unique_name("New project"))
        self.app.projects.save(proj)
        self.app.set_active_project(proj.name)

    def _duplicate(self) -> None:
        cur = self.app.active_project
        copy = Project.from_dict(cur.to_dict())
        copy.name = self._unique_name(cur.name + " copy")
        self.app.projects.save(copy)
        self.app.set_active_project(copy.name)

    def _rename(self) -> None:
        cur = self.app.active_project
        new = simpledialog.askstring("Rename project", "New name:",
                                     initialvalue=cur.name, parent=self)
        if not new or new.strip() == cur.name:
            return
        new = new.strip()
        if new in self.app.projects.names():
            messagebox.showerror("Rename", "A project with that name exists.",
                                 parent=self)
            return
        old = cur.name
        cur.name = new
        self.app.projects.save(cur)
        self.app.projects.delete(old)
        self.app.set_active_project(new)

    def _export(self) -> None:
        cur = self.app.active_project
        path = filedialog.asksaveasfilename(
            title="Export project", defaultextension=".json",
            filetypes=[("De-ID project", "*.json")],
            initialfile=f"{cur.name}.json")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(cur.to_dict(), fh, indent=2)
        except OSError as exc:
            messagebox.showerror("Export", f"Could not write: {exc}",
                                 parent=self)
            return
        messagebox.showinfo("Export", f"Exported '{cur.name}' to:\n{path}",
                            parent=self)

    def _import(self) -> None:
        path = filedialog.askopenfilename(
            title="Import project",
            filetypes=[("De-ID project", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                proj = Project.from_dict(json.load(fh))
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Import", f"Not a valid project file: {exc}",
                                 parent=self)
            return
        proj.name = self._unique_name(proj.name or "Imported project")
        self.app.projects.save(proj)
        self.app.set_active_project(proj.name)
        messagebox.showinfo("Import", f"Imported as '{proj.name}'.",
                            parent=self)

    def _delete(self) -> None:
        cur = self.app.active_project
        if len(self.app.projects.names()) <= 1:
            messagebox.showinfo("Projects", "Keep at least one project.",
                                parent=self)
            return
        if not messagebox.askyesno("Delete project",
                                   f"Delete '{cur.name}'?", parent=self):
            return
        self.app.projects.delete(cur.name)
        remaining = self.app.projects.names()
        self.app.set_active_project(remaining[0])
