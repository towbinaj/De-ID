"""Settings view: appearance, the receiver AE/port, and export destinations.

Destinations and receiver settings are local (git-ignored) - they hold real AE
titles / hosts and never belong in the repository.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from .. import config, paths
from ..model import Node
from .base import View
from .theme import SCALE_OPTIONS, apply_scale, scale_name, MUTED
from .widgets import PAD, run_threaded, section


class NodeDialog(ctk.CTkToplevel):
    """Add / edit one export destination."""

    def __init__(self, app, node: Node | None = None, on_save=None):
        super().__init__(app)
        self.app = app
        self.on_save = on_save
        self.title("Destination")
        self.geometry("420x360")
        self.transient(app)
        self.grab_set()
        node = node or Node(name="", aetitle="", host="", port=104)

        self._entries = {}
        for label, key, val in (("Name", "name", node.name),
                                ("AE Title", "aetitle", node.aetitle),
                                ("Host", "host", node.host),
                                ("Port", "port", str(node.port)),
                                ("Calling AE (optional)", "calling_aetitle",
                                 node.calling_aetitle)):
            ctk.CTkLabel(self, text=label, anchor="w").pack(anchor="w",
                                                            padx=PAD, pady=(8, 0))
            e = ctk.CTkEntry(self)
            e.insert(0, val)
            e.pack(fill="x", padx=PAD)
            self._entries[key] = e
        self.tls = ctk.CTkCheckBox(self, text="Use TLS")
        if node.tls:
            self.tls.select()
        self.tls.pack(anchor="w", padx=PAD, pady=PAD)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=PAD, pady=PAD)
        ctk.CTkButton(bar, text="Save", command=self._save).pack(side="left")
        ctk.CTkButton(bar, text="Cancel", fg_color="gray40",
                      command=self.destroy).pack(side="left", padx=PAD)

    def _save(self) -> None:
        g = {k: e.get().strip() for k, e in self._entries.items()}
        try:
            port = int(g["port"] or "0")
        except ValueError:
            port = 0
        node = Node(name=g["name"], aetitle=g["aetitle"], host=g["host"],
                    port=port, calling_aetitle=g["calling_aetitle"],
                    tls=bool(self.tls.get()))
        problems = node.validate()
        if problems:
            messagebox.showerror("Destination", "\n".join(problems), parent=self)
            return
        if self.on_save:
            self.on_save(node)
        self.destroy()


class SettingsView(View):
    key = "settings"
    title = "Settings"
    description = "Appearance, the receiver, and export destinations (all local)."

    def build(self) -> None:
        self.body.grid_rowconfigure(0, weight=1)
        self.body.grid_columnconfigure(0, weight=1)
        self.form = ctk.CTkScrollableFrame(self.body)
        self.form.grid(row=0, column=0, sticky="nsew", padx=PAD, pady=PAD)
        self.form.grid_columnconfigure(1, weight=1)
        self._health: dict = {}          # key -> {"status","ms","ok"}
        self._health_cells: dict = {}    # key -> (status_lbl, latency_lbl)
        self._build_prefs()
        self._build_receiver()
        self._build_destinations()
        self._build_about()

    # -- preferences -----------------------------------------------------
    def _build_prefs(self) -> None:
        s = self.app.settings
        f = self.form
        section(f, "Appearance").grid(row=0, column=0, columnspan=2,
                                      sticky="w", padx=PAD, pady=(2, 4))
        self.appearance = ctk.CTkOptionMenu(
            f, values=["System", "Light", "Dark"], command=self._set_appearance)
        self.appearance.set(s.appearance)
        self._row(1, "Appearance", self.appearance)
        self.scale = ctk.CTkOptionMenu(f, values=list(SCALE_OPTIONS),
                                       command=self._set_scale)
        self.scale.set(scale_name(s.ui_scale))
        self._row(2, "Text size", self.scale)
        self.bug_email = ctk.CTkEntry(f, width=300,
                                      placeholder_text="you@example.org")
        if s.bug_report_email:
            self.bug_email.insert(0, s.bug_report_email)
        self.bug_email.bind("<FocusOut>", lambda _e: self._save_prefs())
        self._row(3, "Bug-report email", self.bug_email)

    def _build_receiver(self) -> None:
        s = self.app.settings
        f = self.form
        section(f, "Receiver (C-STORE inbox)").grid(
            row=4, column=0, columnspan=2, sticky="w", padx=PAD, pady=(12, 4))
        self.recv_ae = ctk.CTkEntry(f, width=200)
        self.recv_ae.insert(0, s.receiver_aetitle)
        self.recv_ae.bind("<FocusOut>", lambda _e: self._save_prefs())
        self._row(5, "Receiver AE Title", self.recv_ae)
        self.recv_port = ctk.CTkEntry(f, width=120)
        self.recv_port.insert(0, str(s.receiver_port))
        self.recv_port.bind("<FocusOut>", lambda _e: self._save_prefs())
        self._row(6, "Receiver port", self.recv_port)

    def _build_destinations(self) -> None:
        f = self.form
        hdr = ctk.CTkFrame(f, fg_color="transparent")
        hdr.grid(row=7, column=0, columnspan=2, sticky="ew", padx=PAD,
                 pady=(12, 2))
        ctk.CTkLabel(hdr, text="Export destinations (PACS / VNA)",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        self.check_btn = ctk.CTkButton(hdr, text="Check all", width=90,
                                       command=self._check_all)
        self.check_btn.pack(side="left", padx=PAD)
        ctk.CTkButton(hdr, text="+ Add", width=70,
                      command=self._add_dest).pack(side="right")
        # Import / export toolbar (CSV or JSON, Toolkit-compatible).
        bar = ctk.CTkFrame(f, fg_color="transparent")
        bar.grid(row=8, column=0, columnspan=2, sticky="ew", padx=PAD,
                 pady=(0, 2))
        ctk.CTkButton(bar, text="Export…", width=90,
                      command=self._export).pack(side="left")
        ctk.CTkButton(bar, text="Import file…", width=110,
                      command=self._import_file).pack(side="left", padx=PAD)
        ctk.CTkButton(bar, text="Import from DICOM Toolkit…", width=200,
                      fg_color="gray40",
                      command=self._import_toolkit).pack(side="left")
        self.dest_list = ctk.CTkFrame(f, fg_color="transparent")
        self.dest_list.grid(row=9, column=0, columnspan=2, sticky="ew",
                            padx=PAD)
        self.dest_list.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(f, text=f"Stored locally in {paths.destinations_path()}",
                     text_color=MUTED, font=ctk.CTkFont(size=11),
                     anchor="w").grid(row=10, column=0, columnspan=2,
                                      sticky="w", padx=PAD, pady=(4, 0))
        self._render_dests()

    def _build_about(self) -> None:
        from .. import __version__
        f = self.form
        section(f, "About").grid(row=11, column=0, columnspan=2, sticky="w",
                                 padx=PAD, pady=(12, 4))
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.grid(row=12, column=0, columnspan=2, sticky="w", padx=PAD)
        ctk.CTkLabel(row, text=f"Version {__version__}").pack(side="left")
        ctk.CTkButton(
            row, text="Check for updates", width=150,
            command=lambda: self.app.check_for_updates(manual=True)).pack(
            side="left", padx=PAD)

    def _row(self, r, label, widget):
        ctk.CTkLabel(self.form, text=label, anchor="w", width=160).grid(
            row=r, column=0, sticky="w", padx=PAD, pady=6)
        widget.grid(row=r, column=1, sticky="w", padx=PAD, pady=6)

    # -- destinations CRUD ----------------------------------------------
    @staticmethod
    def _health_key(node) -> str:
        return f"{node.aetitle}@{node.host}:{node.port}"

    @staticmethod
    def _health_color(h):
        if h and h.get("ok") is True:
            return "#2a2"
        if h and h.get("ok") is False:
            return "#d33"
        return MUTED

    def _render_dests(self) -> None:
        for w in self.dest_list.winfo_children():
            w.destroy()
        self._health_cells = {}
        if not self.app.destinations:
            ctk.CTkLabel(self.dest_list, text="No destinations yet.",
                         text_color=MUTED, anchor="w").grid(sticky="w", pady=2)
            return
        for i, node in enumerate(self.app.destinations):
            row = ctk.CTkFrame(self.dest_list, fg_color="transparent")
            row.grid(row=i, column=0, sticky="ew", pady=1)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=node.label, anchor="w").grid(
                row=0, column=0, sticky="ew")
            key = self._health_key(node)
            h = self._health.get(key)
            status = ctk.CTkLabel(row, width=80, anchor="w",
                                  text=(h.get("status") if h else "—"),
                                  text_color=self._health_color(h))
            status.grid(row=0, column=1, padx=4)
            lat = ctk.CTkLabel(
                row, width=64, anchor="e", text_color=MUTED,
                text=(f"{h['ms']} ms" if h and h.get("ms") is not None else ""))
            lat.grid(row=0, column=2, padx=4)
            self._health_cells[key] = (status, lat)
            ctk.CTkButton(row, text="Edit", width=56,
                          command=lambda n=node: self._edit_dest(n)).grid(
                row=0, column=3, padx=2)
            ctk.CTkButton(row, text="Delete", width=66, fg_color="#a33",
                          hover_color="#c44",
                          command=lambda n=node: self._delete_dest(n)).grid(
                row=0, column=4, padx=2)

    def _persist_dests(self) -> None:
        config.save_destinations(self.app.destinations)
        self.app.broadcast_destinations_changed()
        self._render_dests()

    def _add_dest(self) -> None:
        def save(node):
            if any(n.name == node.name for n in self.app.destinations):
                messagebox.showerror("Destination",
                                     "A destination with that name exists.",
                                     parent=self)
                return
            self.app.destinations.append(node)
            self._persist_dests()
        NodeDialog(self.app, on_save=save)

    def _edit_dest(self, node: Node) -> None:
        def save(new):
            self.app.destinations = [new if n is node else n
                                     for n in self.app.destinations]
            self._persist_dests()
        NodeDialog(self.app, node=node, on_save=save)

    def _delete_dest(self, node: Node) -> None:
        if not messagebox.askyesno("Delete", f"Delete '{node.name}'?",
                                   parent=self):
            return
        self.app.destinations = [n for n in self.app.destinations if n is not node]
        self._persist_dests()

    def _export(self) -> None:
        from tkinter import filedialog
        from .. import destio
        nodes = list(self.app.destinations)
        if not nodes:
            messagebox.showinfo("Export", "No destinations to export.",
                                parent=self)
            return
        path = filedialog.asksaveasfilename(
            title="Export destinations", defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("JSON", "*.json")],
            initialfile="destinations.csv", parent=self)
        if not path:
            return
        try:
            if path.lower().endswith(".json"):
                destio.export_json(nodes, path)
            else:
                destio.export_csv(nodes, path)
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc), parent=self)
            return
        messagebox.showinfo(
            "Export", f"Exported {len(nodes)} destination(s) to:\n{path}",
            parent=self)

    def _import_file(self) -> None:
        from tkinter import filedialog
        from .. import destio
        path = filedialog.askopenfilename(
            title="Import destinations (CSV or JSON)",
            filetypes=[("Destinations", "*.csv *.json"),
                       ("CSV", "*.csv"), ("JSON", "*.json"),
                       ("All files", "*.*")], parent=self)
        if not path:
            return
        try:
            result = destio.import_any(Path(path))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Import failed", str(exc), parent=self)
            return
        added, skipped = destio.merge_into_saved(result)
        self.app.destinations = config.load_destinations()
        self.app.broadcast_destinations_changed()
        self._render_dests()
        msg = [f"Imported {added} new destination(s)."]
        if skipped:
            msg.append(f"Skipped {skipped} already present.")
        if result.skipped_nonstorage:
            n = result.skipped_nonstorage
            msg.append(f"Ignored {n} non-storage (worklist / HL7) "
                       f"entr{'y' if n == 1 else 'ies'}.")
        if result.warnings:
            msg.append("")
            msg.append("Check these entries:")
            msg.extend(result.warnings[:10])
        messagebox.showinfo("Import complete", "\n".join(msg), parent=self)

    def _import_toolkit(self) -> None:
        from tkinter import filedialog
        from .. import toolkit_import as ti
        folder = ti.find_toolkit_data()
        if folder is not None:
            if not messagebox.askyesno(
                    "Import from DICOM Toolkit",
                    f"Found Toolkit data at:\n{folder}\n\nImport destinations, "
                    "settings, and any profiles from here?", parent=self):
                folder = None
        if folder is None:
            chosen = filedialog.askdirectory(
                title="Select the DICOMToolkit-data folder")
            if not chosen:
                return
            folder = chosen
        try:
            summary = ti.import_all(folder)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Import", f"Import failed: {exc}", parent=self)
            return
        # Reload app state from disk and refresh the UI.
        self.app.destinations = config.load_destinations()
        self.app.settings = config.Settings.load()
        self.app.broadcast_destinations_changed()
        self.app.refresh_project_menu()
        self._render_dests()
        self._reload_prefs_widgets()
        ctk.set_appearance_mode(self.app.settings.appearance)
        apply_scale(self.app.settings.ui_scale)
        messagebox.showinfo(
            "Import complete",
            f"Destinations added: {summary['destinations_added']} "
            f"(skipped {summary['destinations_skipped']})\n"
            f"Settings applied: {', '.join(summary['settings']) or 'none'}\n"
            f"Projects added: {summary['projects_added']}", parent=self)

    def _reload_prefs_widgets(self) -> None:
        s = self.app.settings
        self.appearance.set(s.appearance)
        self.scale.set(scale_name(s.ui_scale))
        self.bug_email.delete(0, "end")
        if s.bug_report_email:
            self.bug_email.insert(0, s.bug_report_email)

    def _set_health(self, key: str, status: str, ms, ok) -> None:
        h = self._health.setdefault(key, {})
        h.update(status=status, ms=ms, ok=ok)
        cell = self._health_cells.get(key)
        if cell:
            st, lat = cell
            st.configure(text=status, text_color=self._health_color(h))
            lat.configure(text=f"{ms} ms" if ms is not None else "")

    def _check_all(self) -> None:
        """Ping every destination with C-ECHO; show status inline (no popups)."""
        from ..net import scu
        from ..net.tls import client_context
        nodes = list(self.app.destinations)
        if not nodes:
            return
        self.check_btn.configure(state="disabled")
        my_ae = self.app.settings.my_aetitle
        done = {"n": 0}
        lock = threading.Lock()

        def finish():
            with lock:
                done["n"] += 1
                complete = done["n"] >= len(nodes)
            if complete:
                self.check_btn.configure(state="normal")

        for node in nodes:
            key = self._health_key(node)
            self._set_health(key, "testing…", None, None)

            def work(n=node):
                t0 = time.time()
                tls = (client_context(verify=False), n.host) if n.tls else None
                res = scu.c_echo(my_ae, n, timeout=min(n.timeout, 15),
                                 tls_args=tls)
                return res, int((time.time() - t0) * 1000)

            def ok_cb(result, key=key):
                res, ms = result
                if res.success:
                    self._set_health(key, "OK", ms, True)
                else:
                    self._set_health(key, "FAIL", None, False)
                finish()

            def err_cb(_exc, _tb, key=key):
                self._set_health(key, "ERROR", None, False)
                finish()

            run_threaded(self, work, ok_cb, err_cb)

    # -- preference setters ---------------------------------------------
    def _set_appearance(self, mode: str) -> None:
        ctk.set_appearance_mode(mode)
        self.app.settings.appearance = mode
        self.app.settings.save()

    def _set_scale(self, name: str) -> None:
        value = SCALE_OPTIONS.get(name, 1.15)
        apply_scale(value)
        self.app.settings.ui_scale = value
        self.app.settings.save()

    def _save_prefs(self) -> None:
        s = self.app.settings
        s.bug_report_email = self.bug_email.get().strip()
        s.receiver_aetitle = self.recv_ae.get().strip() or "DEID_SCP"
        try:
            s.receiver_port = int(self.recv_port.get().strip() or "11112")
        except ValueError:
            pass
        s.save()
