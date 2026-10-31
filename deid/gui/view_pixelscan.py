"""Pixel PHI Scan view: study-level QA + correction for burned-in PHI.

Scans a folder, rolls findings up into a study → series → instance report with a
pass / corrected / needs-review status per level, and (optionally) redacts the
metadata-matched PHI into corrected copies. Nothing in the source folder is
changed. Findings are PHI: shown here, written to a local report, never logged.
"""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .. import paths, pixelscan
from ..engine import fileops, ocr
from .base import View
from .theme import MUTED
from .widgets import PAD, build_drop_zone, run_threaded, section

_STATUS_COLOR = {
    pixelscan.STATUS_PASS: "#5cb85c",
    pixelscan.STATUS_CORRECTED: "#4f9dde",
    pixelscan.STATUS_REVIEW: "#e05a5a",
}
_CAT_LABEL = {"name": "NAME", "id": "ID", "date": "DATE", "suspect": "review"}
# Adjudication row colors: undecided (amber), OK/not-PHI (green), confirmed PHI.
_ADJ_UNDECIDED = "#e0a05a"
_ADJ_OK = "#5cb85c"
_ADJ_PHI = "#e05a5a"
_FILTERS = {"All studies": None, "Needs review": pixelscan.STATUS_REVIEW,
            "Corrected": pixelscan.STATUS_CORRECTED, "Pass": pixelscan.STATUS_PASS}


class PixelScanView(View):
    key = "pixelscan"
    title = "Pixel PHI Scan"
    description = ("Check studies for burned-in PHI and optionally redact it. "
                   "Source files are never changed; corrected copies are "
                   "written separately.")

    def build(self) -> None:
        self.files: list[Path] = []
        self.out_dir: Path | None = None
        self._studies: list = []
        self._adjudications: dict = {}   # adjudication_key -> "ok" | "phi"
        self._cancel = False
        self._scanning = False
        self._viewer_started = False     # the persistent OHIF window is open
        try:
            from .. import ohif
            self._ohif_ok = ohif.available()
        except Exception:  # noqa: BLE001
            self._ohif_ok = False

        self.body.grid_rowconfigure(5, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

        section(self.body, "1.  Choose files or a folder").grid(
            row=0, column=0, sticky="w", padx=PAD, pady=(PAD, 2))
        zone, self.count_lbl = build_drop_zone(
            self.app, self.body, self._on_drop, "Scan",
            [("Add files...", self._add_files, 120),
             ("Add folder...", self._add_folder, 120),
             ("Clear", self._clear, 70)])
        zone.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(0, PAD))

        opt = ctk.CTkFrame(self.body, fg_color="transparent")
        opt.grid(row=2, column=0, sticky="ew", padx=PAD)
        self.redact_cb = ctk.CTkCheckBox(
            opt, text="Redact matched PHI → corrected copies",
            command=self._toggle_redact)
        self.redact_cb.pack(side="left")
        ctk.CTkLabel(opt, text="min conf").pack(side="left", padx=(PAD, 4))
        self.conf = ctk.CTkEntry(opt, width=52)
        self.conf.insert(0, "0.4")
        self.conf.pack(side="left")
        self.out_btn = ctk.CTkButton(opt, text="Output folder…", width=130,
                                     fg_color="gray40", command=self._pick_out)
        self.filter = ctk.CTkOptionMenu(opt, width=150, values=list(_FILTERS),
                                        command=lambda _v: self._render())
        self.filter.set("All studies")
        self.filter.pack(side="right")

        self.out_lbl = ctk.CTkLabel(self.body, text="", anchor="w",
                                    text_color=MUTED, wraplength=820)
        self.out_lbl.grid(row=3, column=0, sticky="ew", padx=PAD, pady=(4, 0))

        barrow = ctk.CTkFrame(self.body, fg_color="transparent")
        barrow.grid(row=4, column=0, sticky="ew", padx=PAD, pady=(6, 2))
        self.scan_btn = ctk.CTkButton(barrow, text="Scan for burned-in PHI",
                                      height=36, command=self._scan)
        self.scan_btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(
            barrow, text="Cancel", width=80, state="disabled", fg_color="#a33",
            hover_color="#c44", command=self._do_cancel)
        self.cancel_btn.pack(side="left", padx=PAD)
        self.save_btn = ctk.CTkButton(barrow, text="Save report…", width=120,
                                      fg_color="gray40",
                                      command=self._save_report)
        self.save_btn.pack(side="right")
        self.save_btn.pack_forget()
        # Shown by _update_clean_btn once there are confirmed-PHI items.
        self.clean_btn = ctk.CTkButton(
            barrow, text="Clean confirmed PHI", width=180,
            fg_color="#b5651d", hover_color="#c8761f",
            command=self._clean_confirmed)
        self.adj_lbl = ctk.CTkLabel(barrow, text="", anchor="e",
                                    text_color=MUTED,
                                    font=ctk.CTkFont(size=12))
        self.adj_lbl.pack(side="right", padx=PAD)
        self.summary = ctk.CTkLabel(barrow, text="", anchor="w", wraplength=520,
                                    justify="left")
        self.summary.pack(side="left", padx=PAD)

        self.results = ctk.CTkScrollableFrame(self.body, label_text="Studies")
        self.results.grid(row=5, column=0, sticky="nsew", padx=PAD,
                          pady=(0, PAD))
        self.results.grid_columnconfigure(0, weight=1)
        self._update_count()

    def on_show(self) -> None:
        if not ocr.available():
            self.summary.configure(
                text="⚠ No Tesseract OCR engine found — scanning unavailable.",
                text_color="#d9a441")

    def _toggle_redact(self) -> None:
        if self.redact_cb.get():
            self.out_btn.pack(side="left", padx=(PAD, 0))
            if not self.out_dir:
                self.out_dir = paths.data_dir() / "pixel-corrected"
            self._refresh_out()
        else:
            self.out_btn.pack_forget()
            self.out_lbl.configure(text="")

    def _refresh_out(self) -> None:
        if self.redact_cb.get():
            self.out_lbl.configure(
                text=f"Corrected copies → {self.out_dir}   (pixels only — run "
                "metadata de-id too; source files are left unchanged)")

    def _pick_out(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self.out_dir = Path(folder)
            self._refresh_out()

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
            messagebox.showwarning("Pixel scan", "No OCR engine available.",
                                   parent=self)
            return
        redact = bool(self.redact_cb.get())
        if redact and not self.out_dir:
            self._pick_out()
            if not self.out_dir:
                return
        try:
            conf = max(0.0, min(1.0, float(self.conf.get() or "0.4")))
        except ValueError:
            conf = 0.4
        files = list(self.files)
        out_dir = str(self.out_dir) if redact else None
        self._cancel = False
        self._scanning = True
        self.scan_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.summary.configure(text=f"Scanning {len(files):,} file(s)…",
                               text_color=MUTED)

        def progress(i, total, path, n):
            self.after(0, lambda: self.summary.configure(
                text=f"Scanning {i:,}/{total:,}…", text_color=MUTED))

        def work():
            return pixelscan.scan_studies(
                files, redact=redact, out_dir=out_dir, min_confidence=conf,
                should_cancel=lambda: self._cancel, on_item=progress)

        run_threaded(self, work, self._scan_done)

    def _scan_done(self, studies) -> None:
        self._scanning = False
        self._studies = studies or []
        self._adjudications = {}          # fresh results clear prior decisions
        self._viewer_started = False      # new results -> fresh viewer manifest
        self.scan_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self._render()
        if self._studies:
            ov, fn = pixelscan.default_report_paths(self.app.active_project.name)
            try:
                pixelscan.write_overview_csv(self._studies, ov)
                pixelscan.write_findings_csv(self._studies, fn,
                                             self._adjudications)
                self._report_paths = (ov, fn)
                self.save_btn.pack(side="right")
            except OSError:
                self._report_paths = None

    # -- rendering -------------------------------------------------------
    def _render(self) -> None:
        for w in self.results.winfo_children():
            w.destroy()
        studies = self._studies
        if not studies:
            self.summary.configure(
                text=f"No burned-in text found in {len(self.files):,} file(s).",
                text_color="#5cb85c")
            self.adj_lbl.configure(text="")
            return
        n_rev = sum(1 for s in studies if s.status == pixelscan.STATUS_REVIEW)
        n_cor = sum(1 for s in studies if s.status == pixelscan.STATUS_CORRECTED)
        n_pass = sum(1 for s in studies if s.status == pixelscan.STATUS_PASS)
        self.summary.configure(
            text=f"{len(studies)} studies — {n_rev} need review · {n_cor} "
            f"corrected · {n_pass} pass. Report saved locally (PHI).",
            text_color=(_STATUS_COLOR[pixelscan.STATUS_REVIEW] if n_rev
                        else _STATUS_COLOR[pixelscan.STATUS_CORRECTED] if n_cor
                        else "#5cb85c"))
        want = _FILTERS.get(self.filter.get())
        shown = 0
        for st in studies:
            if want is not None and st.status != want:
                continue
            if shown > 500:
                ctk.CTkLabel(self.results, text="… more in the saved report.",
                             text_color=MUTED, anchor="w").grid(sticky="ew")
                break
            shown += self._render_study(st)
        self._update_adj_counter()

    def _render_study(self, st) -> int:
        color = _STATUS_COLOR.get(st.status, MUTED)
        # A divider before each study so the long list breaks into clear blocks.
        ctk.CTkFrame(self.results, height=2, fg_color=("gray80", "gray30")).grid(
            sticky="ew", padx=4, pady=(14, 4))
        label = (f"[{st.status.upper()}]  Acc {st.accession or '—'}  ·  "
                 f"{st.modalities or '?'}  ·  {st.n_series} series, "
                 f"{st.n_instances} img  ·  {st.n_phi} PHI / {st.n_suspects} "
                 f"review" + (f" · {st.n_redacted} redacted"
                              if st.n_redacted else ""))
        ctk.CTkLabel(self.results, text=label, anchor="w", justify="left",
                     text_color=color, wraplength=820,
                     font=ctk.CTkFont(size=13, weight="bold")).grid(
            sticky="ew", padx=4, pady=(0, 2))
        rows = 2
        # Keep the actionable (suspect) findings visually apart from the ones
        # already matched/handled, so handled items don't read as "to review".
        review_series, handled_series = [], []
        for se in st.series:
            if not se.instances:
                continue
            suspects, matched = self._series_findings(se)
            if suspects:
                review_series.append((se, suspects))
            if matched:
                handled_series.append((se, matched))
        rows += self._render_group(
            st, "Needs review  —  click OK (not PHI) or PHI (confirmed) on "
            "each item below", _STATUS_COLOR[pixelscan.STATUS_REVIEW],
            review_series, review=True)
        rows += self._render_group(
            st, "Already matched — handled",
            _STATUS_COLOR[pixelscan.STATUS_CORRECTED], handled_series,
            review=False)
        return rows

    @staticmethod
    def _series_findings(se):
        """De-duplicated (per series) findings split into (suspects, matched),
        preserving first-seen order."""
        suspects, matched = [], []
        seen = set()
        for inst in se.instances:
            for f in inst.findings:
                key = (f["category"], f["text"])
                if key in seen:
                    continue
                seen.add(key)
                # A suspect the user cleaned (redacted) moves to the handled side.
                is_review = f["category"] == "suspect" and not f.get("cleaned")
                (suspects if is_review else matched).append(f)
        return suspects, matched

    def _viewer_files(self) -> list:
        """Files to show in the viewer: the corrected/output copy of each
        instance where one was written, else the original. This way review
        shows what the shared output looks like, not the input with PHI."""
        files = []
        for st in self._studies:
            for se in st.series:
                for inst in se.instances:
                    p = inst.out_path or inst.path
                    if p:
                        files.append(Path(p))
        return files

    def _view_in_ohif(self, study_uid, series_uid="") -> None:
        """Open (or re-point) the persistent OHIF window on this study/series."""
        from .. import ohif
        try:
            if not self._viewer_started:
                shell, _m = ohif.prepare_unified(self._viewer_files())
                ohif.open_window(shell)
                self._viewer_started = True
            ohif.goto(study_uid, series_uid)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Viewer", f"Could not open the image viewer:\n{exc}",
                parent=self)

    def _view_button(self, parent, st, se, **grid):
        if not self._ohif_ok:
            return
        ctk.CTkButton(
            parent, text="View", width=52, height=24, fg_color="gray40",
            command=lambda s=st.study_uid, u=se.series_uid:
            self._view_in_ohif(s, u)).grid(**grid)

    def _render_group(self, st, title, color, series_items, review) -> int:
        """Render one labeled group (needs-review or handled) within a study."""
        if not series_items:
            return 0
        ctk.CTkLabel(self.results, text=f"   {title}", anchor="w",
                     text_color=color,
                     font=ctk.CTkFont(size=12, weight="bold")).grid(
            sticky="ew", padx=4, pady=(4, 0))
        rows = 1
        for se, findings in series_items:
            srow = ctk.CTkFrame(self.results, fg_color="transparent")
            srow.grid(sticky="ew", padx=4)
            srow.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                srow, anchor="w", justify="left", text_color=MUTED,
                text=f"      Series {se.series_number or '?'} "
                f"{se.series_desc or ''}").grid(row=0, column=0, sticky="ew")
            self._view_button(srow, st, se, row=0, column=1, sticky="e",
                              padx=(8, 0))
            rows += 1
            for f in findings:
                cat_label = ("redacted" if f.get("cleaned")
                             else _CAT_LABEL.get(f["category"]))
                text = (f"         [{cat_label}] "
                        f"“{f['text']}” ({f['conf']:.0f}%)")
                if review:
                    self._render_review_row(st, se, f, text)
                else:
                    ctk.CTkLabel(
                        self.results, anchor="w", justify="left", text=text,
                        text_color=_STATUS_COLOR[pixelscan.STATUS_CORRECTED]
                    ).grid(sticky="ew", padx=4)
                rows += 1
        return rows

    @staticmethod
    def _adj_color(decision):
        return {"ok": _ADJ_OK, "phi": _ADJ_PHI}.get(decision, _ADJ_UNDECIDED)

    def _render_review_row(self, st, se, finding, text) -> None:
        akey = pixelscan.adjudication_key(
            st.study_uid, se.series_uid, "suspect", finding["text"])
        decision = self._adjudications.get(akey)
        # Each adjudicatable item is its own card, so it stands out from the
        # header/handled lines and is easy to find in a long list.
        row = ctk.CTkFrame(self.results, fg_color=("gray92", "gray17"),
                           corner_radius=6)
        row.grid(sticky="ew", padx=10, pady=3)
        row.grid_columnconfigure(0, weight=1)
        lbl = ctk.CTkLabel(row, anchor="w", justify="left", text=text.strip(),
                           wraplength=480,
                           text_color=self._adj_color(decision))
        lbl.grid(row=0, column=0, sticky="ew", padx=(12, 4), pady=7)
        col = 1
        if self._ohif_ok:
            self._view_button(row, st, se, row=0, column=col, sticky="e",
                              padx=4, pady=7)
            col += 1
        seg = ctk.CTkSegmentedButton(
            row, values=["—", "OK", "PHI"], width=140, height=28,
            font=ctk.CTkFont(size=11),
            command=lambda v, k=akey, w=lbl: self._set_adjudication(k, v, w))
        seg.set({"ok": "OK", "phi": "PHI"}.get(decision, "—"))
        seg.grid(row=0, column=col, sticky="e", padx=(4, 10), pady=7)

    def _set_adjudication(self, akey, ui_value, label) -> None:
        if ui_value == "OK":
            self._adjudications[akey] = "ok"
        elif ui_value == "PHI":
            self._adjudications[akey] = "phi"
        else:
            self._adjudications.pop(akey, None)
        label.configure(
            text_color=self._adj_color(self._adjudications.get(akey)))
        self._update_adj_counter()

    def _review_keys(self) -> set:
        """Every distinct suspect row the user can adjudicate (deduped per
        series, matching how the report renders them)."""
        keys = set()
        for st in self._studies:
            for se in st.series:
                seen = set()
                for inst in se.instances:
                    for f in inst.findings:
                        if f["category"] != "suspect" or f["text"] in seen:
                            continue
                        seen.add(f["text"])
                        if f.get("cleaned"):      # already redacted -> not pending
                            continue
                        keys.add(pixelscan.adjudication_key(
                            st.study_uid, se.series_uid, "suspect", f["text"]))
        return keys

    def _update_adj_counter(self) -> None:
        keys = self._review_keys()
        total = len(keys)
        ok = sum(1 for k in keys if self._adjudications.get(k) == "ok")
        phi = sum(1 for k in keys if self._adjudications.get(k) == "phi")
        self._update_clean_btn(phi)
        if not total:
            self.adj_lbl.configure(text="")
            return
        left = total - ok - phi
        self.adj_lbl.configure(
            text=f"Adjudicated {ok + phi}/{total}  ·  {ok} OK · {phi} PHI · "
                 f"{left} left",
            text_color=(_ADJ_OK if left == 0 else MUTED))

    def _update_clean_btn(self, phi_count: int) -> None:
        if phi_count > 0:
            self.clean_btn.configure(text=f"Clean confirmed PHI ({phi_count})")
            if not self.clean_btn.winfo_ismapped():
                self.clean_btn.pack(side="right", padx=PAD)
        else:
            self.clean_btn.pack_forget()

    def _clean_confirmed(self) -> None:
        """Redact the suspects the user marked PHI into corrected output copies."""
        phi_keys = [k for k in self._review_keys()
                    if self._adjudications.get(k) == "phi"]
        if not phi_keys:
            return
        if not self.out_dir:
            self._pick_out()
            if not self.out_dir:
                return
        if not messagebox.askyesno(
                "Clean confirmed PHI",
                f"Black out {len(phi_keys)} confirmed-PHI finding(s) and write "
                f"corrected copies to:\n{self.out_dir}\n\nContinue?",
                parent=self):
            return
        base = pixelscan.mirror_base(self.files)
        try:
            summary = pixelscan.redact_adjudicated(
                self._studies, self._adjudications, str(self.out_dir), base)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Clean confirmed PHI",
                                 f"Could not clean: {exc}", parent=self)
            return
        self._viewer_started = False      # output changed -> re-prepare viewer
        self._render()
        msg = (f"Cleaned {summary['instances']} image(s), "
               f"{summary['boxes']} region(s).\nCorrected copies in "
               f"{self.out_dir}.")
        if summary["failed"]:
            msg += f"\n{summary['failed']} could not be written."
        messagebox.showinfo("Clean confirmed PHI", msg, parent=self)

    def _save_report(self) -> None:
        if not self._studies:
            return
        if not messagebox.askyesno(
                "Save pixel-scan report",
                "This report lists burned-in text — it is PHI.\n\nSave it "
                "somewhere secure, separate from any data you share. Continue?",
                parent=self):
            return
        folder = filedialog.askdirectory(title="Save report (overview + "
                                         "findings) to folder")
        if not folder:
            return
        ov, fn = pixelscan.default_report_paths(self.app.active_project.name)
        try:
            pixelscan.write_overview_csv(self._studies, Path(folder) / ov.name)
            pixelscan.write_findings_csv(self._studies, Path(folder) / fn.name,
                                         self._adjudications)
        except OSError as exc:
            messagebox.showerror("Save report", f"Could not write: {exc}",
                                 parent=self)
            return
        messagebox.showinfo("Save report", f"Saved overview + findings to:\n"
                            f"{folder}", parent=self)
