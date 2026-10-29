"""Burned-in pixel-text detection + redaction via Tesseract (subprocess).

Attribute de-identification can't touch text burned into the pixels (ultrasound
annotations, screenshots, scanned forms). This finds text regions with the
Tesseract OCR engine and blacks them out.

Tesseract is run as a SEPARATE PROCESS (not an in-process native library), which
is deliberate: it can't crash the app, and it packages reliably into the exe
(unlike onnxruntime-based engines, whose native libraries segfault under
PyInstaller). It is OPTIONAL - if no ``tesseract`` executable is found,
``available()`` is False and redaction is skipped with a single log line.

The binary is resolved from, in order: ``DEID_TESSERACT`` env var, the bundled
copy under ``assets/tesseract/``, ``PATH``, then the usual Windows install dirs.
For multi-frame objects the overlay is almost always static, so text is detected
on the first frame and the same boxes are blanked on every frame.
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .. import paths
from ..logging_setup import get_logger

log = get_logger("engine.ocr")

_warned_missing = False
_EXE = "tesseract.exe" if os.name == "nt" else "tesseract"
# Don't flash a console window when calling tesseract on Windows.
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _candidate_paths():
    env = os.environ.get("DEID_TESSERACT")
    if env:
        yield env
    yield str(paths.resource_dir() / "assets" / "tesseract" / _EXE)
    yield str(paths.app_dir() / "assets" / "tesseract" / _EXE)
    found = shutil.which("tesseract")
    if found:
        yield found
    for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        yield p


def tesseract_path() -> str | None:
    """Resolve a usable tesseract executable, or None."""
    for c in _candidate_paths():
        try:
            if c and Path(c).is_file():
                return c
        except OSError:
            continue
    return None


def available() -> bool:
    """True if a tesseract executable is available (no heavy import)."""
    return tesseract_path() is not None


def _to_uint8(img: np.ndarray) -> np.ndarray:
    a = img.astype(np.float32)
    lo, hi = float(a.min()), float(a.max())
    a = (a - lo) / (hi - lo) * 255.0 if hi > lo else np.zeros_like(a)
    out = a.astype(np.uint8)
    if out.ndim == 3 and out.shape[2] == 4:      # drop alpha
        out = out[:, :, :3]
    return out


def _run_tsv(exe: str, png_path: str) -> str:
    env = dict(os.environ)
    parent = Path(exe).parent
    if (parent / "tessdata").is_dir():
        env.setdefault("TESSDATA_PREFIX", str(parent))
    # --psm 11 = sparse text: find as much text as possible, any orientation.
    proc = subprocess.run(
        [exe, png_path, "stdout", "--psm", "11", "tsv"],
        capture_output=True, text=True, env=env, timeout=120,
        creationflags=_NO_WINDOW)
    return proc.stdout


def _detect_boxes(frame: np.ndarray, min_conf: float) -> list:
    """Return [(y0, y1, x0, x1), ...] word boxes above the confidence floor."""
    exe = tesseract_path()
    if not exe:
        return []
    from PIL import Image
    img = Image.fromarray(_to_uint8(frame))
    boxes = []
    with tempfile.TemporaryDirectory() as td:
        png = os.path.join(td, "frame.png")
        img.save(png)
        tsv = _run_tsv(exe, png)
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        try:
            text = (row.get("text") or "").strip()
            conf = float(row.get("conf", "-1"))
            if not text or conf < min_conf * 100:
                continue
            left, top = int(row["left"]), int(row["top"])
            w, h = int(row["width"]), int(row["height"])
            boxes.append((top, top + h, left, left + w))
        except (ValueError, KeyError, TypeError):
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
            log.warning("OCR requested but no tesseract executable was found; "
                        "skipping burned-in text redaction.")
            _warned_missing = True
        return 0
    try:
        ts = ds.file_meta.TransferSyntaxUID
        arr = ds.pixel_array
    except Exception as exc:  # noqa: BLE001
        log.warning("OCR skipped (cannot read pixels): %s", exc)
        return 0

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
