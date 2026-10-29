"""Burned-in pixel-text detection + redaction (optional, RapidOCR/onnxruntime).

Attribute de-identification can't touch text burned into the pixels (ultrasound
annotations, screenshots, scanned forms). This finds text regions with RapidOCR
and blacks them out. It is an OPTIONAL extra: if ``rapidocr-onnxruntime`` isn't
installed, ``available()`` is False and redaction is skipped with a single log
line - the app still works.

For multi-frame objects the overlay is almost always static (e.g. a US cine), so
text is detected on the first frame and the same boxes are blanked on every
frame - fast and correct for the common case. Detection runs on an 8-bit view;
the boxes map 1:1 back onto the original-bit-depth pixels, which are what we zero.
"""

from __future__ import annotations

import threading

import numpy as np

from ..logging_setup import get_logger

log = get_logger("engine.ocr")

_engine = None
_engine_lock = threading.Lock()
_warned_missing = False


def available() -> bool:
    """True if the optional OCR dependency is installed.

    Checks for the module WITHOUT importing it - importing onnxruntime loads
    native libraries (slow, and in a frozen build it can crash the process), so
    the heavy import is deferred until OCR actually runs (see ``_get_engine``).
    """
    try:
        import importlib.util
        return importlib.util.find_spec("rapidocr_onnxruntime") is not None
    except Exception:  # noqa: BLE001
        return False


def _get_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
    return _engine


def _to_uint8(img: np.ndarray) -> np.ndarray:
    a = img.astype(np.float32)
    lo, hi = float(a.min()), float(a.max())
    a = (a - lo) / (hi - lo) * 255.0 if hi > lo else np.zeros_like(a)
    out = a.astype(np.uint8)
    if out.ndim == 3 and out.shape[2] == 4:      # drop alpha for OCR
        out = out[:, :, :3]
    return out


def _detect_boxes(frame: np.ndarray, min_conf: float) -> list:
    """Return [(y0, y1, x0, x1), ...] bounding boxes of confident text."""
    engine = _get_engine()
    result, _ = engine(_to_uint8(frame))
    boxes = []
    for item in (result or []):
        try:
            box, _text, score = item[0], item[1], item[2]
            if score is None or float(score) < min_conf:
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            boxes.append((int(min(ys)), int(max(ys)),
                          int(min(xs)), int(max(xs))))
        except (TypeError, IndexError, ValueError):
            continue
    return boxes


def redact_burned_in_text(ds, *, min_confidence: float = 0.5,
                          modalities=None) -> int:
    """Black out burned-in text in ``ds`` in place. Returns boxes redacted.

    No-op (returns 0) when OCR is unavailable, the modality is filtered out, or
    the pixels can't be read/written - never raises on those paths.
    """
    global _warned_missing
    if modalities:
        mod = str(getattr(ds, "Modality", "")).upper()
        if mod not in [m.upper() for m in modalities]:
            return 0
    if not available():
        if not _warned_missing:
            log.warning("OCR requested but rapidocr-onnxruntime is not "
                        "installed; skipping burned-in text redaction.")
            _warned_missing = True
        return 0
    try:
        ts = ds.file_meta.TransferSyntaxUID
        arr = ds.pixel_array
    except Exception as exc:  # noqa: BLE001
        log.warning("OCR skipped (cannot read pixels): %s", exc)
        return 0

    # Single frame (gray HxW or color HxWxC) vs multi-frame (leading frame axis).
    single = arr.ndim == 2 or (arr.ndim == 3 and arr.shape[2] in (3, 4))
    rep = arr if single else arr[0]
    try:
        boxes = _detect_boxes(rep, min_confidence)
    except Exception as exc:  # noqa: BLE001
        log.warning("OCR detection failed: %s", exc)
        return 0
    if not boxes:
        return 0

    for (y0, y1, x0, x1) in boxes:
        if single:
            arr[y0:y1 + 1, x0:x1 + 1, ...] = 0
        else:
            arr[:, y0:y1 + 1, x0:x1 + 1, ...] = 0

    try:
        ds.PixelData = arr.tobytes()
        if ts.is_compressed:
            from pydicom.uid import ExplicitVRLittleEndian
            ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
            ds["PixelData"].VR = "OW" if int(
                getattr(ds, "BitsAllocated", 16)) > 8 else "OB"
    except Exception as exc:  # noqa: BLE001
        log.warning("OCR write-back failed: %s", exc)
        return 0
    log.info("OCR redacted %d burned-in text region(s).", len(boxes))
    return len(boxes)
