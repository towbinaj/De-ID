"""Plain image files (JPEG / PNG / ...) in the Pixel PHI Scan.

Scanners sometimes export burned-in images as plain JPEG/PNG alongside DICOM.
These are not DICOM, so there is no header metadata to match against: every
detected name / date / long number is a review *suspect* the user adjudicates,
and cleaning writes a blacked-out copy in the same image format. (The scan's
metadata-matching auto-redaction applies to DICOM only.)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .engine.ocr import _blank_boxes

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif"}


def is_image(path) -> bool:
    """True for a supported plain-image file (by extension)."""
    return Path(path).suffix.lower() in IMAGE_EXTS


def load_gray(path) -> np.ndarray:
    """Grayscale pixel array for OCR (same width/height as the original)."""
    from PIL import Image
    with Image.open(path) as im:
        return np.asarray(im.convert("L"))


def redact(src, boxes, dest) -> int:
    """Black out ``boxes`` in the image at ``src`` and save to ``dest``.

    Writes in the destination's format (kept from the original extension) and
    returns the number of boxes blanked, or 0 if nothing was written.
    """
    from PIL import Image
    boxes = list(boxes or [])
    if not boxes:
        return 0
    with Image.open(src) as im:
        mode = im.mode
        arr = np.array(im)
    single = arr.ndim == 2 or (arr.ndim == 3 and arr.shape[2] in (3, 4))
    _blank_boxes(arr, boxes, single)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = Image.fromarray(arr, mode if arr.ndim > 2 else "L")
    # JPEG can't hold alpha; drop it. Keep PNG/others as-is.
    if dest.suffix.lower() in (".jpg", ".jpeg") and out.mode not in ("RGB", "L"):
        out = out.convert("RGB")
    out.save(dest)
    return len(boxes)
