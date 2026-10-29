"""Queue view: receive → auto-de-identify → export.

Start a C-STORE inbox and/or watch a folder; each arriving object is
de-identified against the ACTIVE project and (optionally) exported to a
destination. Raw incoming objects live only in the local inbox; the live feed
shows modalities and counts, never patient identifiers.
"""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from .. import paths
from ..queue import QueueController
from .base import View
from .theme import MUTED
from .widgets import DestinationPicker, LogBox, PAD


class QueueView(View):
    key = "queue"
    title = "Queue"
    description = ("Receive → auto-de-identify (active project) → export. "
                  "A running inbox for frequent work.")

    def build(self) -> None:
        self.controller: QueueController | None = None
        self.watch_dir: Path | None = None
        self.out_dir: Path | None = None

        self.body.grid_rowconfigure(3, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

        cfgf = ctk.CTkFrame(self.body)
        cfgf.grid(row=0, column=0, sticky="ew", padx=PAD, pady=PAD)
        cfgf.grid_columnconfigure(1, weight=1)

        r = 0
        self.scp_enabled = ctk.CTkCheckBox(
            cfgf, text="Receive by C-STORE (inbox SCP)")
        self.scp_enabled.select()
        self.scp_enabled.grid(row=r, column=0, columnspan=2, sticky="w",
                              padx=PAD, pady=(PAD, 2))
        r += 1
        self.recv_lbl = ctk.CTkLabel(cfgf, text="", text_color=MUTED,
                                     anchor="w")
        self.recv_lbl.grid(row=r, column=0, columnspan=2, sticky="w",
                           padx=PAD * 3, pady=(0, 4))
        r += 1

        wrow = ctk.CTkFrame(cfgf, fg_color="transparent")
        wrow.grid(row=r, column=0, columnspan=2, sticky="ew", padx=PAD, pady=2)
        self.watch_enabled = ctk.CTkCheckBox(wrow, text="Also watch a folder:")
        self.watch_enabled.pack(side="left")
        self.watch_lbl = ctk.CTkLabel(wrow, text="(none)", text_color=MUTED)
        self.watch_lbl.pack(side="left", padx=PAD)
        ctk.CTkButton(wrow, text="Choose…", width=90,
                      command=self._pick_watch).pack(side="left")
        r += 1

        orow = ctk.CTkFrame(cfgf, fg_color="transparent")
        orow.grid(row=r, column=0, columnspan=2, sticky="ew", padx=PAD, pady=2)
        ctk.CTkLabel(orow, text="Output folder:").pack(side="left")
        self.out_lbl = ctk.CTkLabel(orow, text="(project default)",
                                    text_color=MUTED)
        self.out_lbl.pack(side="left", padx=PAD)
        ctk.CTkButton(orow, text="Choose…", width=90,
                      command=self._pick_out).pack(side="left")
        r += 1

        erow = ctk.CTkFrame(cfgf, fg_color="transparent")
        erow.grid(row=r, column=0, columnspan=2, sticky="ew", padx=PAD, pady=2)
        self.export_enabled = ctk.CTkCheckBox(erow, text="Export to:")
        self.export_enabled.pack(side="left")
        self.picker = DestinationPicker(erow, self.app, label="",
                                        remember_key="QueueExport")
        self.picker.pack(side="left", fill="x", expand=True, padx=PAD)
        r += 1

        btnrow = ctk.CTkFrame(self.body, fg_color="transparent")
        btnrow.grid(row=1, column=0, sticky="w", padx=PAD, pady=(0, 2))
        self.start_btn = ctk.CTkButton(btnrow, text="Start queue",
                                       command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ctk.CTkButton(btnrow, text="Stop", width=80,
                                      state="disabled", fg_color="#a33",
                                      hover_color="#c44", command=self._stop)
        self.stop_btn.pack(side="left", padx=PAD)

        self.status_lbl = ctk.CTkLabel(
            self.body, text="Idle.", anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"))
        self.status_lbl.grid(row=2, column=0, sticky="w", padx=PAD, pady=(4, 0))

        self.feed = LogBox(self.body, logger_name="")   # no PHI to the log file
        self.feed.grid(row=3, column=0, sticky="nsew", padx=PAD, pady=PAD)

        self._refresh_labels()

    # -- helpers ---------------------------------------------------------
    def _refresh_labels(self) -> None:
        s = self.app.settings
        self.recv_lbl.configure(
            text=f"AE {s.receiver_aetitle} · port {s.receiver_port}  "
            "(change in Settings)")
        p = self.app.active_project
        default_out = p.output_dir or "De-ID-data/queue-out/<project>"
        self.out_lbl.configure(
            text=str(self.out_dir) if self.out_dir else f"(default: {default_out})")

    def on_show(self) -> None:
        self._refresh_labels()

    def on_project_changed(self) -> None:
        self._refresh_labels()

    def on_destinations_changed(self) -> None:
        self.picker.refresh()

    def _pick_watch(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self.watch_dir = Path(folder)
            self.watch_lbl.configure(text=folder)
            self.watch_enabled.select()

    def _pick_out(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self.out_dir = Path(folder)
            self.out_lbl.configure(text=folder)

    # -- lifecycle -------------------------------------------------------
    def _start(self) -> None:
        if self.controller and self.controller.running:
            return
        s = self.app.settings
        self.controller = QueueController(
            settings=s, pseudonyms=self.app.pseudonyms,
            project_provider=lambda: self.app.active_project,
            destination_provider=self._dest,
            on_event=self._on_event)
        watch = [self.watch_dir] if (self.watch_enabled.get()
                                     and self.watch_dir) else []
        inbox = paths.data_dir() / "inbox"
        try:
            self.controller.start(
                inbox_dir=inbox,
                output_dir=str(self.out_dir) if self.out_dir else "",
                watch_dirs=watch, enable_scp=bool(self.scp_enabled.get()),
                aetitle=s.receiver_aetitle, port=s.receiver_port)
        except Exception as exc:  # noqa: BLE001
            self.feed.write(f"[ERROR] could not start: {exc}")
            self.controller = None
            return
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.feed.write("--- Queue started ---")
        self._poll_status()

    def _stop(self) -> None:
        if self.controller:
            self.controller.stop()
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status_lbl.configure(text="Stopped.")

    def _dest(self):
        if not self.export_enabled.get():
            return None
        return self.picker.get_node()

    def _on_event(self, ev: dict) -> None:
        self.after(0, lambda: self.feed.write(
            f"{ev['time']}  {ev['kind']}: {ev['detail']}"))

    def _poll_status(self) -> None:
        if not (self.controller and self.controller.running):
            return
        c = self.controller.snapshot()
        listen = (f"listening :{self.app.settings.receiver_port}"
                  if c.get("listening") else "watch-only")
        self.status_lbl.configure(
            text=f"● Running ({listen})   received {c['received']}   "
            f"processed {c['processed']}   exported {c['exported']}   "
            f"dropped {c['dropped']}   quarantined {c['quarantined']}   "
            f"failed {c['failed'] + c['export_failed']}",
            text_color=("#2e8b57", "#43c59e"))
        self.after(1000, self._poll_status)
