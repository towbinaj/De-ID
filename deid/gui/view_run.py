"""Run view: point the active project at files/folders and de-identify them.

Drop files or folders (or use the buttons), confirm the output folder, and press
De-identify. Uses the active project's recipe and the shared BatchRunner for
progress / ETA / failures / retry. Export to a PACS/VNA is added in a later phase.
"""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from ..engine import fileops, fileproc
from .base import View
from .batch import BatchRunner
from .theme import MUTED
from .widgets import LogBox, PAD, build_drop_zone, run_threaded


class RunView(View):
    key = "run"
    title = "Run"
    description = "De-identify local files/folders with the active project."

    def build(self) -> None:
        self.files: list[Path] = []
        self.out_dir: Path | None = None

        self.body.grid_rowconfigure(4, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

        zone, self.count_lbl = build_drop_zone(
            self.app, self.body, self._on_drop, "De-identify",
            [("Add Files...", self._add_files, 110),
             ("Add Folder...", self._add_folder, 120),
             ("Output folder...", self._pick_out, 140),
             ("Clear", self._clear, 70)])
        zone.grid(row=0, column=0, sticky="ew", padx=PAD, pady=PAD)

        info = ctk.CTkFrame(self.body, fg_color="transparent")
        info.grid(row=1, column=0, sticky="ew", padx=PAD)
        info.grid_columnconfigure(0, weight=1)
        self.summary_lbl = ctk.CTkLabel(info, text="", anchor="w",
                                        justify="left", wraplength=720)
        self.summary_lbl.grid(row=0, column=0, sticky="ew")
        self.out_lbl = ctk.CTkLabel(info, text="", anchor="w",
                                    text_color=MUTED, wraplength=720)
        self.out_lbl.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.warn_lbl = ctk.CTkLabel(info, text="", anchor="w",
                                     text_color="#d9a441", wraplength=720)
        self.warn_lbl.grid(row=2, column=0, sticky="ew", pady=(2, 0))

        btnbar = ctk.CTkFrame(self.body, fg_color="transparent")
        btnbar.grid(row=2, column=0, sticky="w", padx=PAD, pady=(PAD, 2))
        self.btn = ctk.CTkButton(btnbar, text="De-identify", command=self._run)
        self.btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(
            btnbar, text="Cancel", command=self._cancel, width=80,
            state="disabled", fg_color="#a33", hover_color="#c44")
        self.cancel_btn.pack(side="left", padx=PAD)

        self.log = LogBox(self.body, logger_name="run", height=90)
        self.log.grid(row=3, column=0, sticky="ew", padx=PAD, pady=(2, 2))

        self.runner = BatchRunner(self, verb="written")
        self.runner.build(self.body).grid(row=4, column=0, sticky="nsew",
                                          padx=PAD, pady=(0, PAD))
        self._refresh()

    # -- active-project display -----------------------------------------
    def _refresh(self) -> None:
        p = self.app.active_project
        self.summary_lbl.configure(
            text=f"Project “{p.name}”:  {p.summary() or '(no actions)'}")
        if not self.out_dir and p.output_dir:
            self.out_dir = Path(p.output_dir)
        self.out_lbl.configure(
            text=f"Output: {self.out_dir or '(choose a folder)'}")
        # Safety warning: nothing would actually be de-identified.
        if not p.basic_profile and not p.rules and not p.morph_enabled:
            self.warn_lbl.configure(
                text="⚠ This project has the Basic Profile OFF and no tag rules "
                "— output would NOT be de-identified. Edit it in Rules.")
        else:
            self.warn_lbl.configure(text="")

    def on_show(self) -> None:
        self._refresh()

    def on_project_changed(self) -> None:
        self._refresh()

    # -- source management ----------------------------------------------
    def _add_files(self) -> None:
        chosen = filedialog.askopenfilenames(
            filetypes=[("DICOM", "*.dcm *.dic *.ima"), ("All files", "*.*")])
        self.files.extend(Path(p) for p in chosen)
        self._update_count()

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory()
        if not folder:
            return
        self.count_lbl.configure(text="Scanning folder...")
        run_threaded(self, lambda: fileops.find_dicom_files(Path(folder)),
                     self._folder_done)

    def _folder_done(self, found) -> None:
        self.files.extend(found)
        self.log.write(f"Added {len(found):,} file(s).")
        self._update_count()

    def _on_drop(self, dropped: list) -> None:
        chosen = [Path(p) for p in dropped]
        self.files.extend(p for p in chosen if p.is_file())
        folders = [p for p in chosen if p.is_dir()]
        if folders:
            self.count_lbl.configure(text="Scanning dropped folder(s)...")

            def work():
                out = []
                for d in folders:
                    out.extend(fileops.find_dicom_files(d))
                return out

            run_threaded(self, work, self._folder_done)
        else:
            self._update_count()

    def _pick_out(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self.out_dir = Path(folder)
            self.out_lbl.configure(text=f"Output: {self.out_dir}")

    def _clear(self) -> None:
        self.files = []
        self._update_count()

    def _update_count(self) -> None:
        self.files = list(dict.fromkeys(self.files))
        self.count_lbl.configure(text=f"{len(self.files):,} file(s).")

    def requeue(self, requeue_paths: list) -> None:
        self.files = list(dict.fromkeys(Path(p) for p in requeue_paths))
        self._update_count()

    # -- run -------------------------------------------------------------
    def _cancel(self) -> None:
        self.runner.cancel()

    def _run(self) -> None:
        if not self.files:
            messagebox.showinfo("De-identify", "Add some files or a folder "
                                "first.", parent=self)
            return
        if not self.out_dir:
            self._pick_out()
            if not self.out_dir:
                return
        p = self.app.active_project
        if not p.basic_profile and not p.rules and not p.morph_enabled:
            if not messagebox.askyesno(
                    "De-identification is OFF",
                    "This project would NOT de-identify (Basic Profile off, no "
                    "tag rules). Write the files through unchanged anyway?",
                    parent=self):
                return
        cfg = p.to_receiver_config()
        files = list(self.files)
        out = str(self.out_dir)
        total = len(files)
        self.log.write(f"--- De-identifying {total:,} file(s) → {out} "
                       f"[{p.name}] ---")
        self.btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")

        def worker():
            return fileproc.deidentify_files(
                files, out, cfg, self.runner.on_item,
                should_cancel=lambda: self.runner.cancelled)

        self.runner.run(total, "deid", worker, on_done=self._done)

    def _done(self) -> None:
        self.btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
