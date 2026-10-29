"""Rules view: edit the active project's de-identification recipe.

Standards-based flags (PS3.15 Basic Profile, retain/shift dates, remove private),
coarse remove-groups/tags, pixel blanking, and a table of per-tag rules
(Remove / Blank / Replace / Keep). Object-drop rules, pseudonyms, and OCR get
their own sub-sections in later phases. Press Save to persist to the project.
"""

from __future__ import annotations

from tkinter import messagebox

import customtkinter as ctk

from ..engine import anon_profiles as ap
from ..engine import objectrules as orl
from ..project import Project
from .base import View
from .theme import MUTED
from .widgets import PAD, section


def _fmt_tag(hex8: str) -> str:
    h = (hex8 or "").strip().replace(",", "")
    return f"{h[:4]},{h[4:8]}" if len(h) >= 8 else hex8


class _RuleRow:
    def __init__(self, master, rule: ap.AnonRule, on_delete):
        self.frame = ctk.CTkFrame(master, fg_color="transparent")
        self.frame.grid_columnconfigure(1, weight=1)
        self.frame.grid_columnconfigure(3, weight=1)
        self.tag = ctk.CTkEntry(self.frame, width=90,
                                placeholder_text="gggg,eeee")
        self.tag.insert(0, _fmt_tag(rule.tag))
        self.tag.grid(row=0, column=0, padx=2, pady=2)
        self.kw = ctk.CTkLabel(self.frame, text="", width=170, anchor="w",
                               text_color=MUTED)
        self.kw.grid(row=0, column=1, padx=2, sticky="w")
        self.action = ctk.CTkOptionMenu(self.frame, values=ap.ACTIONS, width=104,
                                        command=lambda _v: self._sync())
        self.action.set(rule.action if rule.action in ap.ACTIONS else "REMOVE")
        self.action.grid(row=0, column=2, padx=2)
        self.value = ctk.CTkEntry(self.frame,
                                  placeholder_text="replacement value")
        self.value.insert(0, rule.value)
        self.value.grid(row=0, column=3, padx=2, sticky="ew")
        ctk.CTkButton(self.frame, text="X", width=28, fg_color="#a33",
                      hover_color="#c44",
                      command=lambda: on_delete(self)).grid(row=0, column=4,
                                                            padx=2)
        self.tag.bind("<KeyRelease>", lambda _e: self._sync())
        self._sync()

    def _sync(self) -> None:
        self.kw.configure(text=ap.keyword_for(self.tag.get()) or "(unknown tag)")
        self.value.configure(state="normal"
                             if self.action.get() == "REPLACE" else "disabled")

    def to_rule(self) -> ap.AnonRule | None:
        tag = self.tag.get().strip().replace(",", "").upper()
        if len(tag) != 8:
            return None
        return ap.AnonRule(tag=tag, action=self.action.get(),
                           value=self.value.get())

    def destroy(self) -> None:
        self.frame.destroy()


class _DropRuleRow:
    """One object/series drop rule: tag · op · value · action · scope."""

    def __init__(self, master, rule: orl.DropRule, on_delete):
        self.frame = ctk.CTkFrame(master, fg_color="transparent")
        self.frame.grid_columnconfigure(2, weight=1)
        self.tag = ctk.CTkEntry(self.frame, width=130,
                                placeholder_text="tag/keyword")
        self.tag.insert(0, rule.tag)
        self.tag.grid(row=0, column=0, padx=2, pady=2)
        self.op = ctk.CTkOptionMenu(self.frame, values=orl.OPS, width=100)
        self.op.set(rule.op if rule.op in orl.OPS else "equals")
        self.op.grid(row=0, column=1, padx=2)
        self.value = ctk.CTkEntry(self.frame, placeholder_text="value / regex")
        self.value.insert(0, rule.value)
        self.value.grid(row=0, column=2, padx=2, sticky="ew")
        self.action = ctk.CTkOptionMenu(self.frame, values=orl.ACTIONS,
                                        width=104)
        self.action.set(rule.action if rule.action in orl.ACTIONS else "drop")
        self.action.grid(row=0, column=3, padx=2)
        self.scope = ctk.CTkOptionMenu(self.frame, values=orl.SCOPES, width=92)
        self.scope.set(rule.scope if rule.scope in orl.SCOPES else "instance")
        self.scope.grid(row=0, column=4, padx=2)
        ctk.CTkButton(self.frame, text="X", width=28, fg_color="#a33",
                      hover_color="#c44",
                      command=lambda: on_delete(self)).grid(row=0, column=5,
                                                            padx=2)

    def to_rule(self) -> orl.DropRule | None:
        tag = self.tag.get().strip()
        if not tag:
            return None
        return orl.DropRule(tag=tag, op=self.op.get(),
                            value=self.value.get(), action=self.action.get(),
                            scope=self.scope.get())

    def destroy(self) -> None:
        self.frame.destroy()


class RulesView(View):
    key = "rules"
    title = "Rules"
    description = "What the active project does to each object. Press Save to keep changes."

    def build(self) -> None:
        self.body.grid_rowconfigure(0, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

        self.form = ctk.CTkScrollableFrame(self.body)
        self.form.grid(row=0, column=0, sticky="nsew", padx=PAD, pady=PAD)
        self.form.grid_columnconfigure(1, weight=1)

        bottom = ctk.CTkFrame(self.body, fg_color="transparent")
        bottom.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(0, PAD))
        ctk.CTkButton(bottom, text="Save", width=120, command=self._save).pack(
            side="left")
        self.saved_lbl = ctk.CTkLabel(bottom, text="", text_color=MUTED)
        self.saved_lbl.pack(side="left", padx=PAD)

        self._rows: list[_RuleRow] = []
        self._build_form()
        self._load(self.app.active_project)

    # -- form scaffolding ------------------------------------------------
    def _build_form(self) -> None:
        f = self.form
        r = 0

        def place(widget, label="", span_full=False):
            nonlocal r
            if span_full:
                widget.grid(row=r, column=0, columnspan=2, sticky="ew",
                            padx=PAD, pady=3)
            else:
                ctk.CTkLabel(f, text=label, anchor="w", width=170).grid(
                    row=r, column=0, sticky="w", padx=PAD, pady=3)
                widget.grid(row=r, column=1, sticky="ew", padx=PAD, pady=3)
            r += 1
            return widget

        section(f, "Standard de-identification").grid(
            row=r, column=0, columnspan=2, sticky="w", padx=PAD, pady=(4, 2))
        r += 1

        basicbar = ctk.CTkFrame(f, fg_color="transparent")
        self.basic = ctk.CTkCheckBox(
            basicbar, text="DICOM PS3.15 Basic Confidentiality Profile "
            "(recommended)")
        self.basic.pack(side="left")
        ctk.CTkButton(basicbar, text="What's in Basic?", width=120,
                      command=self._explain_basic).pack(side="left", padx=PAD)
        place(basicbar, span_full=True)

        self.retain = ctk.CTkCheckBox(f, text="   └ retain study dates "
                                      "(longitudinal)")
        place(self.retain, span_full=True)
        self.shift = ctk.CTkCheckBox(
            f, text="Shift dates (DOB → 1900-01-01, keep age; study/series "
            "dates shifted to match)")
        place(self.shift, span_full=True)
        self.rm_private = ctk.CTkCheckBox(f, text="Remove private tags")
        place(self.rm_private, span_full=True)

        self.rm_groups = place(ctk.CTkEntry(f), "Remove Groups (gggg|..)")
        self.rm_tags = place(ctk.CTkEntry(f), "Remove Tags (ggggeeee|..)")

        section(f, "Pixel blanking (fixed rectangles)").grid(
            row=r, column=0, columnspan=2, sticky="w", padx=PAD, pady=(10, 2))
        r += 1
        self.img_top = place(ctk.CTkEntry(f, width=80), "Remove Image Top %")
        self.img_top_mod = place(ctk.CTkEntry(f), "  …top modalities")
        self.img_left = place(ctk.CTkEntry(f, width=80), "Remove Image Left %")
        self.img_left_mod = place(ctk.CTkEntry(f), "  …left modalities")
        section(f, "Burned-in text (OCR)").grid(
            row=r, column=0, columnspan=2, sticky="w", padx=PAD, pady=(10, 2))
        r += 1
        self.ocr_enable = ctk.CTkCheckBox(
            f, text="Redact burned-in pixel text with OCR")
        place(self.ocr_enable, span_full=True)
        self.ocr_conf = place(ctk.CTkEntry(f, width=80),
                              "Min confidence (0-1)")
        self.ocr_mod = place(ctk.CTkEntry(f), "  …limit to modalities")
        from ..engine import ocr as _ocr
        _ok = _ocr.available()
        ctk.CTkLabel(
            f, text=("OCR engine available." if _ok else
                     "OCR engine NOT installed — this option is skipped at run "
                     "time. Install the 'rapidocr-onnxruntime' extra to enable it."),
            anchor="w", justify="left",
            text_color=(("#2e8b57", "#43c59e") if _ok else "#d9a441"),
            font=ctk.CTkFont(size=11)).grid(
            row=r, column=0, columnspan=2, sticky="w", padx=PAD)
        r += 1

        section(f, "Consistent pseudonyms (persistent crosswalk)").grid(
            row=r, column=0, columnspan=2, sticky="w", padx=PAD, pady=(10, 2))
        r += 1
        pbar = ctk.CTkFrame(f, fg_color="transparent")
        self.pseudo_enable = ctk.CTkCheckBox(
            pbar, text="Assign stable pseudonyms to identifier tags")
        self.pseudo_enable.pack(side="left")
        ctk.CTkLabel(pbar, text="mode").pack(side="left", padx=(PAD, 4))
        self.pseudo_mode = ctk.CTkOptionMenu(
            pbar, width=130, values=["sequential", "hash"])
        self.pseudo_mode.set("sequential")
        self.pseudo_mode.pack(side="left")
        place(pbar, span_full=True)
        tagbar = ctk.CTkFrame(f, fg_color="transparent")
        self.pseudo_tag_vars: dict = {}
        for kw, label in (("PatientID", "Patient ID"),
                          ("PatientName", "Patient Name"),
                          ("AccessionNumber", "Accession"),
                          ("StudyID", "Study ID")):
            cb = ctk.CTkCheckBox(tagbar, text=label)
            cb.pack(side="left", padx=(0, PAD))
            self.pseudo_tag_vars[kw] = cb
        place(tagbar, span_full=True)
        ctk.CTkLabel(
            f, text="The same original value always gets the same pseudonym, "
            "across runs and days. Re-identify from the Pseudonyms view "
            "(local only).", anchor="w", justify="left", text_color=MUTED,
            font=ctk.CTkFont(size=11)).grid(
            row=r, column=0, columnspan=2, sticky="w", padx=PAD)
        r += 1

        hdr = ctk.CTkFrame(f, fg_color="transparent")
        hdr.grid(row=r, column=0, columnspan=2, sticky="ew", padx=PAD,
                 pady=(12, 0))
        ctk.CTkLabel(hdr, text="Per-tag rules  (Keep = do NOT anonymize)",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        ctk.CTkButton(hdr, text="Import CTP…", width=100, fg_color="gray40",
                      command=self._import_ctp).pack(side="left", padx=(PAD, 2))
        ctk.CTkButton(hdr, text="Export CTP…", width=100, fg_color="gray40",
                      command=self._export_ctp).pack(side="left")
        ctk.CTkButton(hdr, text="+ Add tag", width=90,
                      command=lambda: self._add_rule()).pack(side="right")
        self.common = ctk.CTkOptionMenu(
            hdr, width=210, dynamic_resizing=False,
            values=["Common tags ▾"] + [
                f"{kw} ({_fmt_tag(tag)})" for tag, kw in ap.COMMON_TAGS],
            command=self._add_common)
        self.common.set("Common tags ▾")
        self.common.pack(side="right", padx=6)
        r += 1
        ctk.CTkLabel(
            f, text="With Basic on, standard identifiers are removed "
            "automatically — add rules only for EXTRA tags, or Keep to exempt "
            "one. Unlisted tags not covered by Basic are left UNCHANGED.",
            anchor="w", justify="left", text_color=MUTED,
            font=ctk.CTkFont(size=11)).grid(
            row=r, column=0, columnspan=2, sticky="w", padx=PAD, pady=(2, 4))
        r += 1

        self.rows_frame = ctk.CTkFrame(f, fg_color="transparent")
        self.rows_frame.grid(row=r, column=0, columnspan=2, sticky="ew",
                             padx=PAD)
        self.rows_frame.grid_columnconfigure(0, weight=1)
        r += 1

        dhdr = ctk.CTkFrame(f, fg_color="transparent")
        dhdr.grid(row=r, column=0, columnspan=2, sticky="ew", padx=PAD,
                  pady=(14, 0))
        ctk.CTkLabel(dhdr, text="Object / series drop rules",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        ctk.CTkButton(dhdr, text="+ Add rule", width=90,
                      command=lambda: self._add_drop_rule()).pack(side="right")
        self.presets = ctk.CTkOptionMenu(
            dhdr, width=230, dynamic_resizing=False,
            values=["Presets ▾"] + [label for label, _ in orl.PRESETS],
            command=self._add_preset)
        self.presets.set("Presets ▾")
        self.presets.pack(side="right", padx=6)
        r += 1
        ctk.CTkLabel(
            f, text="Drop (discard) or quarantine (set the original aside) whole "
            "objects — e.g. Secondary Capture, encapsulated PDFs, scanned docs. "
            "Series scope removes the whole series if any object matches.",
            anchor="w", justify="left", text_color=MUTED,
            font=ctk.CTkFont(size=11)).grid(
            row=r, column=0, columnspan=2, sticky="w", padx=PAD, pady=(2, 4))
        r += 1
        self.drop_rows_frame = ctk.CTkFrame(f, fg_color="transparent")
        self.drop_rows_frame.grid(row=r, column=0, columnspan=2, sticky="ew",
                                  padx=PAD)
        self.drop_rows_frame.grid_columnconfigure(0, weight=1)
        self._drop_rows: list[_DropRuleRow] = []

    # -- load / save -----------------------------------------------------
    @staticmethod
    def _set_check(cb, on):
        (cb.select if on else cb.deselect)()

    @staticmethod
    def _set_entry(entry, text):
        entry.delete(0, "end")
        if text:
            entry.insert(0, str(text))

    def _load(self, p: Project) -> None:
        self._set_check(self.basic, p.basic_profile)
        self._set_check(self.retain, p.retain_dates)
        self._set_check(self.shift, p.calculated_dates)
        self._set_check(self.rm_private, p.remove_private)
        self._set_entry(self.rm_groups, "|".join(p.remove_groups))
        self._set_entry(self.rm_tags, "|".join(p.remove_tags))
        self._set_entry(self.img_top, p.remove_image_top or "")
        self._set_entry(self.img_top_mod, ",".join(p.remove_image_top_modality))
        self._set_entry(self.img_left, p.remove_image_left or "")
        self._set_entry(self.img_left_mod,
                        ",".join(p.remove_image_left_modality))
        self._set_check(self.ocr_enable, p.ocr_enabled)
        self._set_entry(self.ocr_conf, p.ocr_min_confidence)
        self._set_entry(self.ocr_mod, ",".join(p.ocr_modalities))
        self._set_check(self.pseudo_enable, p.pseudonymize)
        self.pseudo_mode.set(p.pseudonym_mode or "sequential")
        for kw, cb in self.pseudo_tag_vars.items():
            self._set_check(cb, kw in (p.pseudonym_tags or []))
        for row in self._rows:
            row.destroy()
        self._rows = []
        for rule in p.rules:
            self._add_rule(rule if isinstance(rule, ap.AnonRule)
                           else ap.AnonRule(**rule))
        for row in self._drop_rows:
            row.destroy()
        self._drop_rows = []
        for d in (p.drop_rules or []):
            self._add_drop_rule(d if isinstance(d, orl.DropRule)
                                else orl.DropRule.from_dict(d))
        self.saved_lbl.configure(text="")

    def _add_rule(self, rule: ap.AnonRule | None = None) -> None:
        row = _RuleRow(self.rows_frame, rule or ap.AnonRule("", "REMOVE"),
                       on_delete=self._del_rule)
        row.frame.grid(sticky="ew", pady=1)
        self._rows.append(row)

    def _del_rule(self, row: _RuleRow) -> None:
        row.destroy()
        self._rows.remove(row)

    def _add_drop_rule(self, rule: orl.DropRule | None = None) -> None:
        row = _DropRuleRow(self.drop_rows_frame, rule or orl.DropRule(),
                           on_delete=self._del_drop_rule)
        row.frame.grid(sticky="ew", pady=1)
        self._drop_rows.append(row)

    def _del_drop_rule(self, row: _DropRuleRow) -> None:
        row.destroy()
        self._drop_rows.remove(row)

    def _add_preset(self, choice: str) -> None:
        self.presets.set("Presets ▾")
        for label, rule in orl.PRESETS:
            if label == choice:
                self._add_drop_rule(orl.DropRule.from_dict(rule.to_dict()))
                break

    def _add_common(self, choice: str) -> None:
        self.common.set("Common tags ▾")
        if "(" in choice and choice.endswith(")"):
            tag = choice[choice.rindex("(") + 1:-1].replace(",", "")
            self._add_rule(ap.AnonRule(tag, "REMOVE"))

    @staticmethod
    def _split(text, seps="|,"):
        import re
        return [t.strip() for t in re.split(f"[{seps}]", text or "")
                if t.strip()]

    @staticmethod
    def _int0(text) -> int:
        try:
            return max(0, int((text or "0").strip()))
        except ValueError:
            return 0

    def _collect_into(self, p) -> None:
        """Write every widget value into a project (no persistence)."""
        p.basic_profile = bool(self.basic.get())
        p.retain_dates = bool(self.retain.get())
        p.calculated_dates = bool(self.shift.get())
        p.remove_private = bool(self.rm_private.get())
        p.remove_groups = self._split(self.rm_groups.get(), "|,")
        p.remove_tags = self._split(self.rm_tags.get(), "|,")
        p.remove_image_top = self._int0(self.img_top.get())
        p.remove_image_top_modality = self._split(self.img_top_mod.get(), ",")
        p.remove_image_left = self._int0(self.img_left.get())
        p.remove_image_left_modality = self._split(self.img_left_mod.get(), ",")
        rules = [r.to_rule() for r in self._rows]
        p.rules = [r for r in rules if r is not None]
        p.ocr_enabled = bool(self.ocr_enable.get())
        try:
            p.ocr_min_confidence = max(0.0, min(1.0,
                                       float(self.ocr_conf.get() or "0.5")))
        except ValueError:
            p.ocr_min_confidence = 0.5
        p.ocr_modalities = self._split(self.ocr_mod.get(), ",")
        p.pseudonymize = bool(self.pseudo_enable.get())
        p.pseudonym_mode = self.pseudo_mode.get()
        p.pseudonym_tags = [kw for kw, cb in self.pseudo_tag_vars.items()
                            if cb.get()]
        drops = [r.to_rule() for r in self._drop_rows]
        p.drop_rules = [d.to_dict() for d in drops if d is not None]

    def _save(self) -> None:
        self._collect_into(self.app.active_project)
        self.app.save_active_project()
        # Refresh other views (e.g. the Run view's action summary). This also
        # reloads our own form, so set the confirmation label afterwards.
        self.app.notify_project_changed()
        self.saved_lbl.configure(text="Saved.")

    # -- CTP anonymizer-script interop ----------------------------------
    def _import_ctp(self) -> None:
        from tkinter import filedialog
        from ..engine import ctpscript
        path = filedialog.askopenfilename(
            title="Import CTP anonymizer script",
            filetypes=[("CTP script", "*.script *.xml"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                parsed = ctpscript.parse_script(fh.read())
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Import CTP", f"Could not parse: {exc}",
                                 parent=self)
            return
        # Replace the per-tag rules and set remove-private; leave other flags.
        for row in self._rows:
            row.destroy()
        self._rows = []
        for rule in parsed["rules"]:
            self._add_rule(rule)
        self._set_check(self.rm_private, parsed["remove_private"])
        msg = (f"Imported {len(parsed['rules'])} tag rule(s)"
               f"{'; remove-private ON' if parsed['remove_private'] else ''}."
               "\n\nReview, then press Save.")
        if parsed["unmapped"]:
            shown = "\n  - ".join(parsed["unmapped"][:20])
            extra = ("" if len(parsed["unmapped"]) <= 20
                     else f"\n  …and {len(parsed['unmapped']) - 20} more")
            msg += ("\n\nNOT imported (no direct equivalent — these are handled "
                    "by the Basic Profile / date policy, or aren't supported):"
                    f"\n  - {shown}{extra}")
        messagebox.showinfo("Import CTP", msg, parent=self)

    def _export_ctp(self) -> None:
        from tkinter import filedialog
        from ..engine import ctpscript
        from ..project import Project
        snap = Project(name=self.app.active_project.name)
        self._collect_into(snap)
        path = filedialog.asksaveasfilename(
            title="Export CTP anonymizer script", defaultextension=".script",
            filetypes=[("CTP script", "*.script"), ("XML", "*.xml")],
            initialfile=f"{snap.name}.script")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(ctpscript.to_script(snap))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Export CTP", f"Could not write: {exc}",
                                 parent=self)
            return
        messagebox.showinfo(
            "Export CTP", f"Wrote {len(snap.rules)} tag rule(s) to:\n{path}\n\n"
            "Note: the Basic Profile, date shifting, drop rules, pseudonyms and "
            "OCR are listed as comments — configure those in CTP separately.",
            parent=self)

    def _explain_basic(self) -> None:
        messagebox.showinfo("PS3.15 Basic Confidentiality Profile",
                            ap.BASIC_PROFILE_EXPLANATION, parent=self)

    # -- lifecycle -------------------------------------------------------
    def on_project_changed(self) -> None:
        self._load(self.app.active_project)
