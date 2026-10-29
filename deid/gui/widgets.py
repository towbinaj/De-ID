"""Reusable GUI widgets and threading helpers.

Tkinter is not thread-safe, so long-running DICOM operations run on a worker
thread and marshal their progress/results back to the UI thread via
``widget.after(0, ...)``.
"""

from __future__ import annotations

import threading
import traceback
from typing import Callable

import customtkinter as ctk

from ..logging_setup import get_logger
from .theme import MUTED, mono

PAD = 10


class LogBox(ctk.CTkTextbox):
    """A read-only, auto-scrolling text area for streaming progress.

    ``write()`` also mirrors to the central rotating log (tagged with the view
    name). ``set_text()`` does NOT mirror - use it for content that may contain
    tag values (PHI), which must never reach the persistent log file.
    """

    def __init__(self, master, logger_name: str = "", **kw):
        super().__init__(master, **kw)
        self.configure(state="disabled", wrap="word", font=mono(14))
        self._logger = get_logger(logger_name) if logger_name else None

    def write(self, message: str) -> None:
        self.configure(state="normal")
        self.insert("end", message + "\n")
        self.see("end")
        self.configure(state="disabled")
        if self._logger is not None:
            for line in str(message).splitlines() or [""]:
                if line.strip():
                    self._logger.info(line)

    def clear(self) -> None:
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.configure(state="disabled")

    def set_text(self, text: str) -> None:
        """Replace all contents WITHOUT mirroring to the central log file."""
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.insert("end", text)
        self.configure(state="disabled")


def run_threaded(widget, work: Callable[[], object],
                 on_done: Callable[[object], None] | None = None,
                 on_error: Callable[[Exception, str], None] | None = None) -> None:
    """Run ``work()`` on a thread; deliver result/exception on the UI thread."""

    def runner():
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            widget.after(0, lambda e=exc, t=tb:
                         (on_error or _default_error)(e, t))
            return
        if on_done:
            widget.after(0, lambda: on_done(result))

    threading.Thread(target=runner, daemon=True).start()


def _default_error(exc: Exception, tb: str) -> None:
    print(tb)


def ui_progress(widget, logbox: LogBox) -> Callable[[str], None]:
    """Return a progress callback safe to call from a worker thread."""

    def cb(message: str) -> None:
        widget.after(0, lambda: logbox.write(message))

    return cb


def build_drop_zone(app, parent, on_paths, action_verb: str, button_specs):
    """A prominent drag-and-drop area with the view's Add buttons inside it.

    ``button_specs`` is a list of ``(text, command, width)``. Returns
    ``(zone_frame, count_label)`` - the caller updates ``count_label``.
    """
    default_border = ("gray60", "gray45")
    zone = ctk.CTkFrame(parent, border_width=2, border_color=default_border,
                        corner_radius=10)
    zone.grid_columnconfigure(0, weight=1)

    big = ctk.CTkLabel(zone, text="↓   Drag files or folders here",
                       font=ctk.CTkFont(size=16, weight="bold"))
    big.grid(row=0, column=0, pady=(12, 2))
    sub = ctk.CTkLabel(zone, text=f"then press {action_verb}"
                                  "  —  or use the buttons below",
                       text_color=MUTED)
    sub.grid(row=1, column=0, pady=(0, 8))

    btnrow = ctk.CTkFrame(zone, fg_color="transparent")
    btnrow.grid(row=2, column=0, pady=(0, 12))
    for text, cmd, width in button_specs:
        ctk.CTkButton(btnrow, text=text, command=cmd, width=width).pack(
            side="left", padx=6)
    count = ctk.CTkLabel(btnrow, text="No files.", text_color=MUTED)
    count.pack(side="left", padx=12)

    def enter():
        zone.configure(border_color="#4f9dde")
        big.configure(text="↓   Release to add files / folders")

    def leave():
        zone.configure(border_color=default_border)
        big.configure(text="↓   Drag files or folders here")

    ok = app.enable_drop(zone, on_paths, on_enter=enter, on_leave=leave)
    for w in (big, sub, btnrow):
        app.enable_drop(w, on_paths, on_enter=enter, on_leave=leave)
    if not ok:
        big.configure(text="Add files or a folder")
        sub.configure(text="(drag-and-drop unavailable on this system)")
    return zone, count


def section(master, text: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(master, text=text,
                        font=ctk.CTkFont(size=15, weight="bold"), anchor="w")
