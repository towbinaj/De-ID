"""Manual redaction annotator for the Pixel PHI Scan review.

OCR cannot read every burned-in field - e.g. an electronic face sheet whose
input values sit at ~1-6 gray levels of contrast, below what OCR can resolve.
This window shows one instance's image and lets the user draw redaction
rectangles over whatever OCR missed, blank the whole image in one click, or mark
the object to be excluded from the output. The rectangles come back in ORIGINAL
pixel coordinates, so the existing Clean step blanks them with the same no-OCR
path it uses for adjudicated suspects (``ocr.redact_boxes`` / ``imagefile.redact``).
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from .theme import MUTED
from .widgets import PAD

# Cap the on-screen image; boxes are always stored in original pixel coordinates
# so the redaction stays exact regardless of this display scaling.
_MAX_W, _MAX_H = 1100, 740
_BOX_COLOR = "#e05a5a"


def load_display_image(path):
    """``(PIL RGB image, (width, height))`` for a DICOM or plain-image instance.

    For DICOM the representative (first) frame is shown; a drawn box applies to
    every frame when cleaned, matching how burned-in overlays repeat.
    """
    from PIL import Image
    from .. import imagefile
    p = str(path)
    if imagefile.is_image(p):
        with Image.open(p) as im:
            rgb = im.convert("RGB")
        return rgb, rgb.size
    from pydicom import dcmread
    from ..engine import ocr
    ds = dcmread(p, force=True)
    ocr._ensure_transfer_syntax(ds)
    frame, _single = ocr._representative_frame(ds.pixel_array)
    im = Image.fromarray(ocr._to_uint8(frame))
    if im.mode != "RGB":
        im = im.convert("RGB")
    return im, im.size


class BoxEditor(ctk.CTkToplevel):
    """Modal annotator. After ``wait_window`` read ``.result``: ``None`` if
    cancelled, else ``{"boxes": [(y0,y1,x0,x1), ...], "redact_all": bool,
    "drop": bool}`` with boxes in original pixel coordinates."""

    def __init__(self, master, path, existing: dict | None = None):
        super().__init__(master)
        self.title("Edit redactions")
        self.result = None
        existing = existing or {}
        self._boxes: list[tuple] = list(existing.get("boxes") or [])
        self._redact_all = bool(existing.get("redact_all"))
        self._drag_start = None
        self._temp = None

        try:
            img, (w, h) = load_display_image(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Edit redactions",
                                 f"Could not open the image:\n{exc}",
                                 parent=master)
            self.after(0, self.destroy)
            return
        self._w, self._h = w, h
        self._scale = min(_MAX_W / w, _MAX_H / h, 1.0)
        dw, dh = max(1, round(w * self._scale)), max(1, round(h * self._scale))
        from PIL import ImageTk
        self._photo = ImageTk.PhotoImage(img.resize((dw, dh)))

        ctk.CTkLabel(
            self, anchor="w", text_color=MUTED, wraplength=dw + 40,
            text="Drag to draw redaction boxes over any PHI OCR missed. "
                 f"Image {w}×{h}px.").grid(row=0, column=0, sticky="ew",
                                           padx=PAD, pady=(PAD, 4))

        self.canvas = tk.Canvas(self, width=dw, height=dh, highlightthickness=0,
                                cursor="crosshair", background="#111111")
        self.canvas.grid(row=1, column=0, padx=PAD)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=PAD, pady=PAD)
        self.whole_btn = ctk.CTkButton(bar, text="Redact whole image", width=150,
                                       fg_color="#b5651d", hover_color="#c8761f",
                                       command=self._toggle_whole)
        self.whole_btn.pack(side="left")
        ctk.CTkButton(bar, text="Undo last", width=90, fg_color="gray40",
                      command=self._undo).pack(side="left", padx=PAD)
        ctk.CTkButton(bar, text="Clear boxes", width=100, fg_color="gray40",
                      command=self._clear).pack(side="left")
        self.drop_var = ctk.BooleanVar(value=bool(existing.get("drop")))
        ctk.CTkCheckBox(bar, text="Exclude object from output",
                        variable=self.drop_var).pack(side="left", padx=PAD)
        ctk.CTkButton(bar, text="Save", width=90,
                      command=self._save).pack(side="right")
        ctk.CTkButton(bar, text="Cancel", width=80, fg_color="gray40",
                      command=self._cancel).pack(side="right", padx=PAD)

        self.count_lbl = ctk.CTkLabel(self, text="", anchor="w",
                                      text_color=MUTED)
        self.count_lbl.grid(row=3, column=0, sticky="ew", padx=PAD, pady=(0, PAD))

        self._redraw()
        self.transient(master)
        self.update_idletasks()
        self.grab_set()

    # -- coordinate mapping ---------------------------------------------
    def _to_original(self, x0, y0, x1, y1) -> tuple:
        s = self._scale
        ox0, ox1 = sorted((int(x0 / s), int(x1 / s)))
        oy0, oy1 = sorted((int(y0 / s), int(y1 / s)))
        ox0, ox1 = max(0, ox0), min(self._w - 1, ox1)
        oy0, oy1 = max(0, oy0), min(self._h - 1, oy1)
        return (oy0, oy1, ox0, ox1)                 # (y0, y1, x0, x1)

    def _to_display(self, box) -> tuple:
        y0, y1, x0, x1 = box
        s = self._scale
        return (x0 * s, y0 * s, x1 * s, y1 * s)

    # -- drawing --------------------------------------------------------
    def _on_press(self, e) -> None:
        if self._redact_all:
            return
        self._drag_start = (e.x, e.y)
        self._temp = self.canvas.create_rectangle(
            e.x, e.y, e.x, e.y, outline=_BOX_COLOR, width=2)

    def _on_drag(self, e) -> None:
        if self._temp is not None and self._drag_start:
            x0, y0 = self._drag_start
            self.canvas.coords(self._temp, x0, y0, e.x, e.y)

    def _on_release(self, e) -> None:
        if self._temp is None or not self._drag_start:
            return
        x0, y0 = self._drag_start
        self.canvas.delete(self._temp)
        self._temp = None
        self._drag_start = None
        if abs(e.x - x0) < 4 or abs(e.y - y0) < 4:     # ignore stray clicks
            return
        self._boxes.append(self._to_original(x0, y0, e.x, e.y))
        self._redraw()

    def _redraw(self) -> None:
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        if self._redact_all:
            self.canvas.create_rectangle(
                0, 0, self.canvas.winfo_reqwidth(), self.canvas.winfo_reqheight(),
                fill="#000000", stipple="gray50", outline=_BOX_COLOR, width=3)
            self.canvas.create_text(
                self.canvas.winfo_reqwidth() / 2,
                self.canvas.winfo_reqheight() / 2,
                text="WHOLE IMAGE", fill=_BOX_COLOR,
                font=("TkDefaultFont", 20, "bold"))
        else:
            for box in self._boxes:
                dx0, dy0, dx1, dy1 = self._to_display(box)
                self.canvas.create_rectangle(dx0, dy0, dx1, dy1,
                                             outline=_BOX_COLOR, width=2,
                                             fill="#000000", stipple="gray25")
        self._update_count()

    def _update_count(self) -> None:
        if self._redact_all:
            self.count_lbl.configure(text="Whole image will be blacked out.")
        else:
            n = len(self._boxes)
            self.count_lbl.configure(
                text=f"{n} box(es) drawn." if n else "No boxes drawn yet.")

    def _toggle_whole(self) -> None:
        self._redact_all = not self._redact_all
        self.whole_btn.configure(
            text="Undo whole image" if self._redact_all else "Redact whole image")
        self._redraw()

    def _undo(self) -> None:
        if self._boxes:
            self._boxes.pop()
            self._redraw()

    def _clear(self) -> None:
        self._boxes = []
        self._redraw()

    # -- finish ---------------------------------------------------------
    def _save(self) -> None:
        boxes = list(self._boxes)
        if self._redact_all:                 # one full-image box covers it all
            boxes = [(0, self._h - 1, 0, self._w - 1)]
        self.result = {"boxes": boxes, "redact_all": self._redact_all,
                       "drop": bool(self.drop_var.get())}
        self.grab_release()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.grab_release()
        self.destroy()


def edit_redactions(master, path, existing: dict | None = None) -> dict | None:
    """Open the annotator modally and return its result (or ``None``)."""
    editor = BoxEditor(master, path, existing)
    if not editor.winfo_exists():            # failed to load the image
        return None
    master.wait_window(editor)
    return editor.result
