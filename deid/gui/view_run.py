"""Run view: point the active project at files/folders and de-identify them.

Drop files or folders (or use the buttons), confirm the output folder, and press
De-identify. Uses the active project's recipe and the shared BatchRunner for
progress / ETA / failures / retry. Export to a PACS/VNA is added in a later phase.
"""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .. import paths
from ..engine import fileops, fileproc, scan
from ..project import _slug
from .base import View
from .batch import BatchRunner
from .theme import MUTED
from .widgets import LogBox, PAD, build_drop_zone, run_threaded

_OK = "#5cb85c"
_WARN = "#d9a441"


class RunView(View):
    key = "run"
    title = "Run"
    description = "De-identify local files/folders with the active project."

    def build(self) -> None:
        self.files: list[Path] = []
        self.out_dir: Path | None = None
        self._scan = None
        self._scanning = False

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
        self.preview_lbl = ctk.CTkLabel(info, text="", anchor="w",
                                        text_color=MUTED, wraplength=720)
        self.preview_lbl.grid(row=2, column=0, sticky="ew", pady=(2, 0))
        self.sep_lbl = ctk.CTkLabel(info, text="", anchor="w", justify="left",
                                    wraplength=720,
                                    font=ctk.CTkFont(size=13, weight="bold"))
        self.sep_lbl.grid(row=3, column=0, sticky="ew", pady=(2, 0))
        self.warn_lbl = ctk.CTkLabel(info, text="", anchor="w",
                                     text_color=_WARN, wraplength=720)
        self.warn_lbl.grid(row=4, column=0, sticky="ew", pady=(2, 0))

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
        self._render_preview()

    def _render_preview(self) -> None:
        """Header-only preview: counts + whether patients stay separate."""
        s = self._scan
        if not self.files or s is None:
            self.preview_lbl.configure(text="")
            self.sep_lbl.configure(text="")
            return
        self.preview_lbl.configure(text=scan.overview(s))
        level, msg = scan.patient_separation(self.app.active_project, s)
        color = {"ok": _OK, "warn": _WARN}.get(level, MUTED)
        self.sep_lbl.configure(text=msg, text_color=color)

    def _rescan(self) -> None:
        if self._scanning or not self.files:
            self._render_preview()
            return
        self._scanning = True
        files = list(self.files)
        drop_rules = list(self.app.active_project.drop_rules)
        run_threaded(self, lambda: scan.scan_files(files, drop_rules=drop_rules),
                     self._scan_done)

    def _scan_done(self, result) -> None:
        self._scanning = False
        self._scan = result
        self._render_preview()

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
        self._scan = None
        self._update_count()

    def _update_count(self) -> None:
        self.files = list(dict.fromkeys(self.files))
        self.count_lbl.configure(text=f"{len(self.files):,} file(s).")
        self._rescan()

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
        if self._scan is not None:
            level, msg = scan.patient_separation(p, self._scan)
            if level == "warn" and not messagebox.askyesno(
                    "Patients would be merged",
                    msg + "\n\nRun anyway? (Turn on pseudonyms in Rules to give "
                    "each patient a stable ID instead.)", parent=self):
                return
        cfg = p.to_receiver_config()
        pseudonymizer = p.build_pseudonymizer(self.app.pseudonyms)
        self._run_project = p
        files = list(self.files)
        out = str(self.out_dir)
        drop_rules = list(p.drop_rules)
        self._qroot = paths.quarantine_dir() / _slug(p.name)
        self._last_result = None
        total = len(files)
        self.log.write(f"--- De-identifying {total:,} file(s) → {out} "
                       f"[{p.name}] ---")
        self.btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")

        def worker():
            res = fileproc.deidentify_files(
                files, out, cfg, self.runner.on_item,
                should_cancel=lambda: self.runner.cancelled,
                pseudonymizer=pseudonymizer, drop_rules=drop_rules,
                quarantine_root=self._qroot)
            self._last_result = res
            return res

        self.runner.run(total, "deid", worker, on_done=self._done)

    def _done(self) -> None:
        self.btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        res = getattr(self, "_last_result", None)
        if res:
            self.log.write(
                f"Summary — de-identified {res['ok']:,}, dropped "
                f"{res['dropped']:,}, quarantined {res['quarantined']:,}, "
                f"failed {res['failed']:,}.")
            if res["quarantined"]:
                self.log.write(f"Quarantined originals set aside in "
                               f"{self._qroot}")
            self._write_crosswalk(res.get("crosswalk") or [])
        # Refresh the Pseudonyms view (new mappings may have been assigned).
        pv = self.app.views.get("pseudonyms")
        if pv is not None:
            pv.on_project_changed()

    def _write_crosswalk(self, rows: list) -> None:
        """Auto-save the re-id key to the local crosswalks folder (PHI)."""
        if not rows:
            return
        from .. import crosswalk
        p = getattr(self, "_run_project", self.app.active_project)
        path = crosswalk.default_path(p.name)
        try:
            crosswalk.write_crosswalk_csv(rows, path)
        except OSError as exc:
            self.log.write(f"Could not write crosswalk: {exc}")
            return
        # Never log PHI values — only the count and the path.
        self.log.write(f"Re-ID crosswalk ({len(rows)} study rows) saved — PHI, "
                       f"keep secure, do NOT share with the output:")
        self.log.write(f"  {path}")
