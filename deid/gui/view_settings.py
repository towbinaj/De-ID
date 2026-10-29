"""Settings view: appearance, the receiver AE/port, and export destinations.

Destinations and receiver settings are local (git-ignored) - they hold real AE
titles / hosts and never belong in the repository.
"""

from __future__ import annotations

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
        self._build_prefs()
        self._build_receiver()
        self._build_destinations()

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
        ctk.CTkButton(hdr, text="+ Add", width=70,
                      command=self._add_dest).pack(side="right")
        self.dest_list = ctk.CTkFrame(f, fg_color="transparent")
        self.dest_list.grid(row=8, column=0, columnspan=2, sticky="ew",
                            padx=PAD)
        self.dest_list.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(f, text=f"Stored locally in {paths.destinations_path()}",
                     text_color=MUTED, font=ctk.CTkFont(size=11),
                     anchor="w").grid(row=9, column=0, columnspan=2,
                                      sticky="w", padx=PAD, pady=(4, 0))
        self._render_dests()

    def _row(self, r, label, widget):
        ctk.CTkLabel(self.form, text=label, anchor="w", width=160).grid(
            row=r, column=0, sticky="w", padx=PAD, pady=6)
        widget.grid(row=r, column=1, sticky="w", padx=PAD, pady=6)

    # -- destinations CRUD ----------------------------------------------
    def _render_dests(self) -> None:
        for w in self.dest_list.winfo_children():
            w.destroy()
        if not self.app.destinations:
            ctk.CTkLabel(self.dest_list, text="No destinations yet.",
                         text_color=MUTED, anchor="w").grid(sticky="w", pady=2)
            return
        for i, node in enumerate(self.app.destinations):
            row = ctk.CTkFrame(self.dest_list, fg_color="transparent")
            row.grid(row=i, column=0, sticky="ew", pady=1)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=node.label, anchor="w").grid(
                row=0, column=0, sticky="w")
            ctk.CTkButton(row, text="Test", width=56,
                          command=lambda n=node: self._test(n)).grid(
                row=0, column=1, padx=2)
            ctk.CTkButton(row, text="Edit", width=56,
                          command=lambda n=node: self._edit_dest(n)).grid(
                row=0, column=2, padx=2)
            ctk.CTkButton(row, text="Delete", width=66, fg_color="#a33",
                          hover_color="#c44",
                          command=lambda n=node: self._delete_dest(n)).grid(
                row=0, column=3, padx=2)

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

    def _test(self, node: Node) -> None:
        from ..net import scu
        from ..net.tls import client_context

        def work():
            tls_args = (client_context(verify=False), node.host) if node.tls \
                else None
            return scu.c_echo(self.app.settings.my_aetitle, node,
                              timeout=node.timeout, tls_args=tls_args)

        def done(res):
            box = messagebox.showinfo if res.success else messagebox.showerror
            box("C-ECHO", res.message, parent=self)

        run_threaded(self, work, done,
                     lambda e, tb: messagebox.showerror("C-ECHO", str(e),
                                                        parent=self))

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
