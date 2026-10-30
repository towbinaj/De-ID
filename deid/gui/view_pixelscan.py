"""Pixel PHI Scan view: OCR a folder and report burned-in PHI, read-only.

A verification pass that answers "did de-identification miss any burned-in text?"
It never modifies files. Findings are classified against each file's metadata
(name / id / date) or flagged as pattern-based "suspects" for a human to review.
Findings contain PHI, so they are shown here but never written to the log; the
exported report goes to the local, git-ignored pixelscan folder.
"""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .. import pixelscan
from ..engine import fileops, ocr
from .base import View
from .theme import MUTED
from .widgets import PAD, build_drop_zone, run_threaded, section

_PHI_COLOR = "#e05a5a"
_SUSPECT_COLOR = "#d9a441"
_CAT_LABEL = {"name": "NAME", "id": "ID", "date": "DATE", "suspect": "review"}


class PixelScanView(View):
    key = "pixelscan"
    title = "Pixel PHI Scan"
    description = ("Check files for burned-in PHI in the pixels — nothing is "
                   "modified. Scan originals to see what's burned in, or the "
                   "output to confirm it's clean.")

    def build(self) -> None:
        self.files: list[Path] = []
        self._results: list = []
        self._cancel = False
        self._scanning = False

        self.body.grid_rowconfigure(4, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

        section(self.body, "1.  Choose files or a folder").grid(
            row=0, column=0, sticky="w", padx=PAD, pady=(PAD, 2))
        zone, self.count_lbl = build_drop_zone(
            self.app, self.body, self._on_drop, "Scan",
            [("Add files...", self._add_files, 120),
             ("Add folder...", self._add_folder, 120),
             ("Clear", self._clear, 70)])
        zone.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(0, PAD))

        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=PAD)
        self.scan_btn = ctk.CTkButton(bar, text="Scan for burned-in PHI",
                                      height=36, command=self._scan)
        self.scan_btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(
            bar, text="Cancel", width=80, state="disabled", fg_color="#a33",
            hover_color="#c44", command=self._do_cancel)
        self.cancel_btn.pack(side="left", padx=PAD)
        ctk.CTkLabel(bar, text="min confidence").pack(side="left", padx=(PAD, 4))
        self.conf = ctk.CTkEntry(bar, width=56)
        self.conf.insert(0, "0.4")
        self.conf.pack(side="left")
        self.save_btn = ctk.CTkButton(bar, text="Save report…", width=120,
                                      fg_color="gray40",
                                      command=self._save_report)
        self.save_btn.pack(side="right")
        self.save_btn.pack_forget()

        self.summary = ctk.CTkLabel(self.body, text="", anchor="w",
                                    justify="left", wraplength=820)
        self.summary.grid(row=3, column=0, sticky="ew", padx=PAD, pady=(6, 2))

        self.results = ctk.CTkScrollableFrame(self.body, label_text="Findings")
        self.results.grid(row=4, column=0, sticky="nsew", padx=PAD,
                          pady=(0, PAD))
        self.results.grid_columnconfigure(0, weight=1)
        self._update_count()

    def on_show(self) -> None:
        if not ocr.available():
            self.summary.configure(
                text="⚠ No Tesseract OCR engine found — pixel scanning is "
                "unavailable. (The bundled copy ships with the built exe.)",
                text_color=_SUSPECT_COLOR)

    # -- sources ---------------------------------------------------------
    def _add_files(self) -> None:
        chosen = filedialog.askopenfilenames(
            filetypes=[("DICOM", "*.dcm *.dic *.ima"), ("All files", "*.*")])
        if chosen:
            self.files.extend(Path(p) for p in chosen)
            self._update_count()

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory()
        if not folder:
            return
        self.count_lbl.configure(text="Scanning folder…")
        run_threaded(self, lambda: fileops.find_dicom_files(Path(folder)),
                     self._folder_done)

    def _folder_done(self, found) -> None:
        self.files.extend(found)
        self._update_count()

    def _on_drop(self, dropped: list) -> None:
        chosen = [Path(p) for p in dropped]
        self.files.extend(p for p in chosen if p.is_file())
        folders = [p for p in chosen if p.is_dir()]
        if folders:
            def work():
                out = []
                for d in folders:
                    out.extend(fileops.find_dicom_files(d))
                return out
            run_threaded(self, work, self._folder_done)
        else:
            self._update_count()

    def _clear(self) -> None:
        self.files = []
        self._update_count()

    def _update_count(self) -> None:
        self.files = list(dict.fromkeys(self.files))
        self.count_lbl.configure(text=f"{len(self.files):,} file(s).")

    # -- scan ------------------------------------------------------------
    def _do_cancel(self) -> None:
        self._cancel = True

    def _scan(self) -> None:
        if self._scanning:
            return
        if not self.files:
            messagebox.showinfo("Pixel scan", "Add files or a folder first.",
                                parent=self)
            return
        if not ocr.available():
            messagebox.showwarning(
                "Pixel scan", "No Tesseract OCR engine is available.",
                parent=self)
            return
        try:
            conf = max(0.0, min(1.0, float(self.conf.get() or "0.4")))
        except ValueError:
            conf = 0.4
        files = list(self.files)
        self._cancel = False
        self._scanning = True
        self.scan_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.summary.configure(
            text=f"Scanning {len(files):,} file(s)…", text_color=MUTED)

        def progress(i, total, path, n):
            self.after(0, lambda: self.summary.configure(
                text=f"Scanning {i:,}/{total:,}…", text_color=MUTED))

        def work():
            return pixelscan.scan_folder(
                files, min_confidence=conf,
                should_cancel=lambda: self._cancel, on_item=progress)

        run_threaded(self, work, self._scan_done)

    def _scan_done(self, results) -> None:
        self._scanning = False
        self._results = results or []
        self.scan_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self._render_results()
        if self._results:
            path = pixelscan.default_report_path(
                self.app.active_project.name)
            try:
                pixelscan.write_report_csv(self._results, path)
                self._report_path = path
                self.save_btn.pack(side="right")
            except OSError:
                self._report_path = None

    def _render_results(self) -> None:
        for w in self.results.winfo_children():
            w.destroy()
        files_scanned = len(self.files)
        n_phi = sum(len(r.phi) for r in self._results)
        n_susp = sum(len(r.suspects) for r in self._results)
        if not self._results:
            self.summary.configure(
                text=f"No burned-in text found in {files_scanned:,} file(s). "
                "(Text below the confidence floor may still exist.)",
                text_color="#5cb85c")
            return
        color = _PHI_COLOR if n_phi else _SUSPECT_COLOR
        self.summary.configure(
            text=f"{n_phi} metadata-matched PHI hit(s) and {n_susp} suspect(s) "
            f"across {len(self._results):,} of {files_scanned:,} file(s). "
            f"Report saved locally (PHI — keep secure).", text_color=color)
        shown = 0
        for r in self._results:
            if shown > 400:
                ctk.CTkLabel(self.results, text="… more in the saved report.",
                             text_color=MUTED, anchor="w").grid(sticky="ew")
                break
            head = ctk.CTkLabel(
                self.results, anchor="w", justify="left",
                text=f"{Path(r.path).name}   [{r.modality or '?'}]   "
                f"{len(r.phi)} PHI · {len(r.suspects)} review",
                font=ctk.CTkFont(size=12, weight="bold"))
            head.grid(sticky="ew", padx=4, pady=(8, 0))
            shown += 1
            for f in r.findings:
                if shown > 400:
                    break
                c = _PHI_COLOR if f["category"] in pixelscan.PHI_CATEGORIES \
                    else _SUSPECT_COLOR
                row = ctk.CTkLabel(
                    self.results, anchor="w", justify="left", text_color=c,
                    text=f"    [{_CAT_LABEL.get(f['category'], f['category'])}]"
                    f"  “{f['text']}”  ({f['conf']:.0f}%)")
                row.grid(sticky="ew", padx=4)
                shown += 1

    def _save_report(self) -> None:
        if not self._results:
            return
        if not messagebox.askyesno(
                "Save pixel-scan report",
                "This report lists the burned-in text found — it is PHI.\n\n"
                "Save it somewhere secure, separate from any data you share. "
                "Continue?", parent=self):
            return
        dest = filedialog.asksaveasfilename(
            title="Save pixel-scan report (PHI)", defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=pixelscan.default_report_path(
                self.app.active_project.name).name)
        if not dest:
            return
        try:
            pixelscan.write_report_csv(self._results, dest)
        except OSError as exc:
            messagebox.showerror("Save report", f"Could not write: {exc}",
                                 parent=self)
            return
        messagebox.showinfo("Save report", f"Saved to:\n{dest}", parent=self)
