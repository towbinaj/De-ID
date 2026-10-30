"""Quick de-identify: a guided, front-door workflow.

Most de-id work is "take this folder of studies, clean it, give me the output".
This view walks that path in three visible steps - drop a folder, read the
plain-English preview (including how many distinct patients it holds), press the
button - without visiting Projects / Rules / Settings first. The full control
panel still lives in the Run and Rules views for power users.

The preview is the guardrail: it scans headers before anything is written and
states, in words, that each patient keeps a separate identity - or warns, with a
one-click fix, when the active project's settings would merge every patient into
one blank identifier.
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
from .widgets import LogBox, PAD, build_drop_zone, run_threaded, section

_OK = "#5cb85c"
_WARN = "#d9a441"


class QuickView(View):
    key = "quick"
    title = "Quick de-identify"
    description = ("Drop a folder, check the preview, press the button. "
                   "Each patient keeps a separate identity.")

    def build(self) -> None:
        self.files: list[Path] = []
        self.out_dir: Path | None = None
        self._scan = None
        self._scanning = False

        self.body.grid_rowconfigure(5, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

        # -- Step 1: pick files ---------------------------------------------
        section(self.body, "1.  Choose a folder of DICOM files").grid(
            row=0, column=0, sticky="w", padx=PAD, pady=(PAD, 2))
        zone, self.count_lbl = build_drop_zone(
            self.app, self.body, self._on_drop, "De-identify",
            [("Choose folder...", self._add_folder, 140),
             ("Add files...", self._add_files, 120),
             ("Clear", self._clear, 70)])
        zone.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(0, PAD))

        # -- Step 2: preview ------------------------------------------------
        section(self.body, "2.  Preview — what's in it").grid(
            row=2, column=0, sticky="w", padx=PAD, pady=(0, 2))
        card = ctk.CTkFrame(self.body)
        card.grid(row=3, column=0, sticky="ew", padx=PAD, pady=(0, PAD))
        card.grid_columnconfigure(0, weight=1)
        self.overview_lbl = ctk.CTkLabel(
            card, text="Add a folder to see a preview.", anchor="w",
            justify="left", wraplength=760, text_color=MUTED)
        self.overview_lbl.grid(row=0, column=0, sticky="ew", padx=PAD,
                               pady=(PAD, 2))
        self.sep_lbl = ctk.CTkLabel(card, text="", anchor="w", justify="left",
                                    wraplength=760,
                                    font=ctk.CTkFont(size=13, weight="bold"))
        self.sep_lbl.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(0, 2))
        self.drop_lbl = ctk.CTkLabel(card, text="", anchor="w", justify="left",
                                     wraplength=760, text_color=MUTED)
        self.drop_lbl.grid(row=2, column=0, sticky="ew", padx=PAD)
        self.fix_btn = ctk.CTkButton(
            card, text="Keep patients separate", width=200, fg_color=_WARN,
            hover_color="#c8922f", text_color="black",
            command=self._fix_separation)
        self.fix_btn.grid(row=3, column=0, sticky="w", padx=PAD, pady=(6, PAD))
        self.fix_btn.grid_remove()
        # Naming scheme: trial-style IDs like TRIAL_0001 (optional).
        name_fr = ctk.CTkFrame(card, fg_color="transparent")
        name_fr.grid(row=4, column=0, sticky="ew", padx=PAD, pady=(2, 2))
        ctk.CTkLabel(name_fr, text="Naming:").pack(side="left")
        self.trial_entry = ctk.CTkEntry(
            name_fr, width=170, placeholder_text="Trial name (e.g. ACRIN6698)")
        self.trial_entry.pack(side="left", padx=(6, 10))
        self.trial_entry.bind("<Return>", lambda _e: self._apply_naming())
        ctk.CTkLabel(name_fr, text="digits").pack(side="left")
        self.digits_menu = ctk.CTkOptionMenu(
            name_fr, width=70, values=["2", "3", "4", "5", "6"],
            command=lambda _v: self._apply_naming())
        self.digits_menu.set("4")
        self.digits_menu.pack(side="left", padx=(4, 10))
        ctk.CTkButton(name_fr, text="Apply", width=70,
                      command=self._apply_naming).pack(side="left")
        self.example_lbl = ctk.CTkLabel(card, text="", anchor="w",
                                        justify="left", wraplength=760,
                                        text_color=MUTED,
                                        font=ctk.CTkFont(size=11))
        self.example_lbl.grid(row=5, column=0, sticky="ew", padx=PAD)
        self.proj_lbl = ctk.CTkLabel(
            card, text="", anchor="w", justify="left", wraplength=760,
            text_color=MUTED, font=ctk.CTkFont(size=11))
        self.proj_lbl.grid(row=6, column=0, sticky="ew", padx=PAD,
                           pady=(0, PAD))

        # -- Step 3: output + run -------------------------------------------
        section(self.body, "3.  De-identify").grid(
            row=4, column=0, sticky="w", padx=PAD, pady=(0, 2))
        runbar = ctk.CTkFrame(self.body, fg_color="transparent")
        runbar.grid(row=5, column=0, sticky="new", padx=PAD)
        runbar.grid_columnconfigure(0, weight=1)

        outrow = ctk.CTkFrame(runbar, fg_color="transparent")
        outrow.grid(row=0, column=0, sticky="ew")
        outrow.grid_columnconfigure(0, weight=1)
        self.out_lbl = ctk.CTkLabel(outrow, text="", anchor="w",
                                    text_color=MUTED, wraplength=640)
        self.out_lbl.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(outrow, text="Change output...", width=140,
                      fg_color="gray40", command=self._pick_out).grid(
            row=0, column=1, padx=(PAD, 0))

        btnbar = ctk.CTkFrame(runbar, fg_color="transparent")
        btnbar.grid(row=1, column=0, sticky="w", pady=(PAD, 2))
        self.btn = ctk.CTkButton(btnbar, text="De-identify", height=40,
                                 font=ctk.CTkFont(size=15, weight="bold"),
                                 command=self._run)
        self.btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(
            btnbar, text="Cancel", command=self._cancel, width=80,
            state="disabled", fg_color="#a33", hover_color="#c44")
        self.cancel_btn.pack(side="left", padx=PAD)

        self.log = LogBox(runbar, logger_name="quick", height=70)
        self.log.grid(row=2, column=0, sticky="ew", pady=(2, 2))

        self.runner = BatchRunner(self, verb="written")
        self.runner.build(runbar).grid(row=3, column=0, sticky="nsew",
                                       pady=(0, PAD))
        runbar.grid_rowconfigure(3, weight=1)

        self._refresh()

    # -- active-project / output defaults -------------------------------
    def _refresh(self) -> None:
        p = self.app.active_project
        self.proj_lbl.configure(
            text=f"Using project “{p.name}” — {p.summary() or '(no actions)'}. "
                 f"Fine-tune it in the Rules tab.")
        if not self.out_dir:
            self.out_dir = (Path(p.output_dir) if p.output_dir
                            else paths.data_dir() / "output")
        self.out_lbl.configure(text=f"Output folder: {self.out_dir}")
        self._sync_naming_widgets()
        self._update_example()
        self._update_button()

    def on_show(self) -> None:
        self._refresh()
        self._render_preview()

    def on_project_changed(self) -> None:
        self._refresh()
        self._rescan()

    # -- naming scheme ---------------------------------------------------
    def _sync_naming_widgets(self) -> None:
        """Reflect the active project's scheme into the trial / digits widgets."""
        p = self.app.active_project
        cur = self.trial_entry.get()
        want = p.pseudonym_prefix if (p.pseudonym_mode or "") == "custom" else ""
        if cur != want and self.focus_get() is not self.trial_entry:
            self.trial_entry.delete(0, "end")
            if want:
                self.trial_entry.insert(0, want)
        pad = str(int(p.pseudonym_pad or 4))
        if pad in ("2", "3", "4", "5", "6"):
            self.digits_menu.set(pad)

    def _apply_naming(self) -> None:
        """Turn the trial name + digits into the project's pseudonym scheme."""
        p = self.app.active_project
        trial = self.trial_entry.get().strip()
        try:
            pad = int(self.digits_menu.get())
        except ValueError:
            pad = 4
        p.pseudonymize = True
        tags = list(p.pseudonym_tags or [])
        for kw in ("PatientID", "AccessionNumber"):
            if kw not in tags:
                tags.append(kw)
        p.pseudonym_tags = tags
        p.pseudonym_pad = pad
        if trial:
            p.pseudonym_mode = "custom"
            p.pseudonym_prefix = trial
            self.log.write(f"Naming scheme: {p.pseudonym_example(1)}, "
                           f"{p.pseudonym_example(2)}, … (per patient & "
                           f"accession).")
        else:
            p.pseudonym_mode = "sequential"
            p.pseudonym_prefix = ""
        self.app.save_active_project()
        self.app.notify_project_changed()   # rescans + refreshes

    def _update_example(self) -> None:
        p = self.app.active_project
        ex = (f"Patients → {p.pseudonym_example(1)}, {p.pseudonym_example(2)}"
              f"   ·   Accessions → {p.pseudonym_example(1)}, "
              f"{p.pseudonym_example(2)}   (separate counters)")
        warn = False
        if self._scan is not None and self._scan.patient_count:
            n = self._scan.patient_count
            pad = int(p.pseudonym_pad or 0)
            cap = 10 ** pad - 1 if pad > 0 else None
            if cap is not None:
                ex += f"\n{n} patient(s) in this batch; {pad} digits → up to " \
                      f"{cap:,}."
                if n > cap:
                    ex += "  ⚠ add more digits."
                    warn = True
        self.example_lbl.configure(text=ex,
                                   text_color=(_WARN if warn else MUTED))

    # -- source management ----------------------------------------------
    def _add_files(self) -> None:
        chosen = filedialog.askopenfilenames(
            filetypes=[("DICOM", "*.dcm *.dic *.ima"), ("All files", "*.*")])
        if chosen:
            self.files.extend(Path(p) for p in chosen)
            self._after_change()

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
        self._after_change()

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
            self._after_change()

    def _pick_out(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self.out_dir = Path(folder)
            self.out_lbl.configure(text=f"Output folder: {self.out_dir}")

    def _clear(self) -> None:
        self.files = []
        self._scan = None
        self._after_change()

    def _after_change(self) -> None:
        self.files = list(dict.fromkeys(self.files))
        self.count_lbl.configure(text=f"{len(self.files):,} file(s).")
        self._rescan()
        self._update_button()

    def _update_button(self) -> None:
        n = len(self.files)
        self.btn.configure(
            text=f"De-identify {n:,} file(s)" if n else "De-identify",
            state="normal" if n else "disabled")

    # -- preview ---------------------------------------------------------
    def _rescan(self) -> None:
        if self._scanning or not self.files:
            self._render_preview()
            return
        self._scanning = True
        self.overview_lbl.configure(text="Reading headers…")
        files = list(self.files)
        drop_rules = list(self.app.active_project.drop_rules)

        def work():
            return scan.scan_files(files, drop_rules=drop_rules)

        run_threaded(self, work, self._scan_done)

    def _scan_done(self, result) -> None:
        self._scanning = False
        self._scan = result
        self._render_preview()
        self._update_example()

    def _render_preview(self) -> None:
        s = self._scan
        if not self.files:
            self.overview_lbl.configure(text="Add a folder to see a preview.",
                                        text_color=MUTED)
            self.sep_lbl.configure(text="")
            self.drop_lbl.configure(text="")
            self.fix_btn.grid_remove()
            return
        if s is None:
            self.overview_lbl.configure(text="Reading headers…",
                                        text_color=MUTED)
            return
        self.overview_lbl.configure(text=scan.overview(s), text_color=MUTED)
        level, msg = scan.patient_separation(self.app.active_project, s)
        color = {"ok": _OK, "warn": _WARN}.get(level, MUTED)
        self.sep_lbl.configure(text=msg, text_color=color)
        if level == "warn":
            self.fix_btn.grid()
        else:
            self.fix_btn.grid_remove()
        drops = []
        if s.would_drop:
            drops.append(f"{s.would_drop} object(s) would be dropped")
        if s.would_quarantine:
            drops.append(f"{s.would_quarantine} quarantined for review")
        if s.blank_id:
            drops.append(f"{s.blank_id} file(s) have no PatientID")
        self.drop_lbl.configure(text="  ·  ".join(drops))

    def _fix_separation(self) -> None:
        """One-click: turn on per-patient pseudonyms for the active project."""
        p = self.app.active_project
        p.pseudonymize = True
        tags = [str(t).replace(" ", "").lower() for t in (p.pseudonym_tags or [])]
        if "patientid" not in tags:
            p.pseudonym_tags = list(p.pseudonym_tags or []) + ["PatientID"]
        self.app.save_active_project()
        self.log.write("Turned on per-patient pseudonyms (PatientID) for "
                       f"“{p.name}”.")
        self.app.notify_project_changed()  # rescans + refreshes this view

    # -- run -------------------------------------------------------------
    def _cancel(self) -> None:
        self.runner.cancel()

    def _run(self) -> None:
        if not self.files:
            return
        p = self.app.active_project
        if not p.basic_profile and not p.rules and not p.morph_enabled:
            if not messagebox.askyesno(
                    "De-identification is OFF",
                    "This project would NOT de-identify (Basic Profile off, no "
                    "tag rules). Write the files through unchanged anyway?",
                    parent=self):
                return
        # Warn once if patients would be merged into one blank identity.
        if self._scan is not None:
            level, msg = scan.patient_separation(p, self._scan)
            if level == "warn":
                if not messagebox.askyesno(
                        "Patients would be merged",
                        msg + "\n\nRun anyway? (Use “Keep patients separate” "
                        "to give each patient a stable ID instead.)",
                        parent=self):
                    return
        if not self.out_dir:
            self._pick_out()
            if not self.out_dir:
                return

        cfg = p.to_receiver_config()
        pseudonymizer = p.build_pseudonymizer(self.app.pseudonyms)
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
        self.cancel_btn.configure(state="disabled")
        self._update_button()
        res = getattr(self, "_last_result", None)
        if res:
            self.log.write(
                f"Done — de-identified {res['ok']:,}, dropped "
                f"{res['dropped']:,}, quarantined {res['quarantined']:,}, "
                f"failed {res['failed']:,}.")
            if res["quarantined"]:
                self.log.write(f"Quarantined originals set aside in "
                               f"{self._qroot}")
        pv = self.app.views.get("pseudonyms")
        if pv is not None:
            pv.on_project_changed()
