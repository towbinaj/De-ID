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
import re
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
    # Tesseract 5.x wants TESSDATA_PREFIX pointed at the tessdata directory that
    # holds the .traineddata files (older versions used its parent).
    tessdata = Path(exe).parent / "tessdata"
    if tessdata.is_dir():
        env["TESSDATA_PREFIX"] = str(tessdata)
    # --psm 11 = sparse text: find as much text as possible, any orientation.
    proc = subprocess.run(
        [exe, png_path, "stdout", "--psm", "11", "tsv"],
        capture_output=True, text=True, env=env, timeout=120,
        creationflags=_NO_WINDOW)
    return proc.stdout


# Burned-in text (ultrasound annotations, face sheets, scanned forms) is often
# small relative to the image. Tesseract's recall drops sharply on small text, so
# small images are upscaled before OCR until the shorter side reaches this target
# (capped) - this is what lets it catch the patient name on a US face sheet and
# dense lines on a printed document that native resolution misses. Boxes are
# mapped back to the original pixel coordinates afterwards so redaction is exact.
_OCR_TARGET_MIN = 1600
_OCR_MAX_SCALE = 4.0


def _ocr_scale(width: int, height: int) -> float:
    small = min(width, height)
    if small <= 0:
        return 1.0
    return max(1.0, min(_OCR_TARGET_MIN / small, _OCR_MAX_SCALE))


def _ocr_words(frame: np.ndarray, min_conf: float) -> list[dict]:
    """OCR one frame → word dicts: {text, conf, box:(y0,y1,x0,x1), line}.

    The frame is converted to grayscale and upscaled (small text is otherwise
    missed); detected boxes are scaled back to the frame's own coordinates.
    ``line`` is a (block, paragraph, line) key so the caller can regroup words
    into lines for multi-word (name / spelled-date) matching.
    """
    exe = tesseract_path()
    if not exe:
        return []
    from PIL import Image
    img = Image.fromarray(_to_uint8(frame))
    if img.mode != "L":                  # text detection is more robust in gray
        img = img.convert("L")
    scale = _ocr_scale(img.width, img.height)
    if scale > 1.0:
        img = img.resize((round(img.width * scale), round(img.height * scale)),
                         Image.LANCZOS)
    with tempfile.TemporaryDirectory() as td:
        png = os.path.join(td, "frame.png")
        img.save(png)
        tsv = _run_tsv(exe, png)
    words: list[dict] = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        try:
            text = (row.get("text") or "").strip()
            conf = float(row.get("conf", "-1"))
            if not text or conf < min_conf * 100:
                continue
            left, top = int(row["left"]), int(row["top"])
            w, h = int(row["width"]), int(row["height"])
            if scale > 1.0:              # map back to original pixel coordinates
                x0, y0 = int(left / scale), int(top / scale)
                x1 = int(np.ceil((left + w) / scale))
                y1 = int(np.ceil((top + h) / scale))
            else:
                x0, y0, x1, y1 = left, top, left + w, top + h
            words.append({
                "text": text, "conf": conf,
                "box": (y0, y1, x0, x1),
                "line": (row.get("block_num"), row.get("par_num"),
                         row.get("line_num")),
            })
        except (ValueError, KeyError, TypeError):
            continue
    return words


def _detect_boxes(frame: np.ndarray, min_conf: float) -> list:
    """Return [(y0, y1, x0, x1), ...] word boxes above the confidence floor."""
    return [w["box"] for w in _ocr_words(frame, min_conf)]


def classify_words(words: list[dict], phi) -> list[dict]:
    """Tag each OCR word against a :class:`phimatch.PhiSet`.

    Adds ``category`` to each word: 'name' / 'id' / 'date' when it matches this
    study's metadata, 'suspect' when it looks like PHI by pattern but isn't in
    the metadata (a review flag, not an auto-redact), or '' otherwise. Returns
    only the words that got a non-empty category.
    """
    from . import phimatch
    cats: dict[int, str] = {}
    # Per-word metadata matches.
    for i, w in enumerate(words):
        c = phimatch.match_token(w["text"], phi)
        if c:
            cats[i] = c
    # Multi-word spelled dates, matched per line then mapped back to word boxes.
    by_line: dict = {}
    for i, w in enumerate(words):
        by_line.setdefault(w["line"], []).append(i)
    for idxs in by_line.values():
        idxs.sort(key=lambda j: words[j]["box"][2])     # left-to-right
        toks = [words[j]["text"] for j in idxs]
        for (s, e) in phimatch.match_phrases(toks, phi):
            for j in idxs[s:e]:
                cats.setdefault(j, "date")
    # Pattern-based suspects for anything still unmatched.
    for i, w in enumerate(words):
        if i not in cats and _is_suspect(w["text"]):
            cats[i] = "suspect"
    out = []
    for i, c in cats.items():
        w = dict(words[i])
        w["category"] = c
        out.append(w)
    return out


# Common imaging / overlay terms that look alarming but are not PHI. Keeps the
# "suspect" pass from flagging legitimate acquisition text.
_SCAN_TERMS = {
    "KV", "KVP", "MA", "MAS", "MM", "CM", "SEC", "FPS", "HZ", "FOV", "WL", "WW",
    "TR", "TE", "TI", "ETL", "NEX", "SLICE", "THK", "GANTRY", "TILT", "ZOOM",
    "MAG", "AP", "PA", "LAT", "SUP", "INF", "ANT", "POST", "LEFT", "RIGHT",
    "SAG", "COR", "AX", "AXIAL", "PRONE", "SUPINE", "STD", "SOFT", "BONE",
    "HU", "BPM", "CINE", "DOSE", "CTDI", "DLP", "SE", "IM", "SL",
}

_DATE_RE = re.compile(r"^\d{1,4}[/.\-]\d{1,2}([/.\-]\d{1,4})?$")
_NAME_RE = re.compile(r"^[A-Z][a-z]{2,}$")      # a single Capitalised word


def _is_suspect(text: str) -> bool:
    t = text.strip()
    up = re.sub(r"[^A-Z0-9]", "", t.upper())
    if not up or up in _SCAN_TERMS:
        return False
    if _DATE_RE.match(t):                       # date-like with separators
        return True
    if up.isdigit() and len(up) >= 6:           # long bare number (MRN/acc-like)
        return True
    if _NAME_RE.match(t) and up not in _SCAN_TERMS:   # a name-like word
        return True
    return False


def _modality_ok(ds, modalities) -> bool:
    if not modalities:
        return True
    mod = str(getattr(ds, "Modality", "")).upper()
    return mod in [m.upper() for m in modalities]


def _warn_unavailable() -> None:
    global _warned_missing
    if not _warned_missing:
        log.warning("OCR requested but no tesseract executable was found; "
                    "skipping burned-in text handling.")
        _warned_missing = True


def _representative_frame(arr):
    """(frame, is_single) — a 2D/RGB frame to OCR (first frame of a stack)."""
    single = arr.ndim == 2 or (arr.ndim == 3 and arr.shape[2] in (3, 4))
    return (arr if single else arr[0]), single


def _blank_boxes(arr, boxes, single) -> None:
    for (y0, y1, x0, x1) in boxes:
        if single:
            arr[y0:y1 + 1, x0:x1 + 1, ...] = 0
        else:
            arr[:, y0:y1 + 1, x0:x1 + 1, ...] = 0


def _write_back(ds, arr, ts) -> bool:
    try:
        ds.PixelData = arr.tobytes()
        if ts.is_compressed:
            from pydicom.uid import ExplicitVRLittleEndian
            ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
            ds["PixelData"].VR = "OW" if int(
                getattr(ds, "BitsAllocated", 16)) > 8 else "OB"
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("OCR write-back failed: %s", exc)
        return False


def redact_boxes(ds, boxes) -> int:
    """Black out the given ``(y0, y1, x0, x1)`` boxes in ``ds`` in place.

    No OCR - used to apply human-confirmed (adjudicated) regions, so it works
    even without a Tesseract engine. On a multi-frame object the boxes are
    blanked on every frame (burned-in overlays repeat). Returns the number of
    boxes written, or 0 if the pixels can't be read or written back.
    """
    boxes = list(boxes or [])
    if not boxes:
        return 0
    try:
        ts = ds.file_meta.TransferSyntaxUID
        arr = ds.pixel_array
    except Exception as exc:  # noqa: BLE001
        log.warning("Redact skipped (cannot read pixels): %s", exc)
        return 0
    _, single = _representative_frame(arr)
    _blank_boxes(arr, boxes, single)
    if _write_back(ds, arr, ts):
        return len(boxes)
    return 0


def scan_and_redact(ds, phi=None, *, min_confidence: float = 0.5,
                    modalities=None, redact: bool = False) -> tuple[list, int]:
    """Single OCR pass: classify burned-in text and, if ``redact``, blank PHI.

    Returns ``(findings, redacted)``. ``findings`` are classify_words dicts
    ('name'/'id'/'date' matched this study's metadata, or 'suspect' by pattern).
    When ``redact`` is set, the boxes matched to metadata (not suspects) are
    blacked out IN PLACE and ``redacted`` is the count; suspects are never
    auto-redacted (they need review). ``phi`` is built from ``ds`` if omitted.
    No-op when OCR is unavailable, the modality is filtered out, or pixels can't
    be read.
    """
    if not _modality_ok(ds, modalities):
        return [], 0
    if not available():
        _warn_unavailable()
        return [], 0
    if phi is None:
        from . import phimatch
        phi = phimatch.build_phi(ds)
    try:
        ts = ds.file_meta.TransferSyntaxUID
        arr = ds.pixel_array
    except Exception as exc:  # noqa: BLE001
        log.warning("OCR skipped (cannot read pixels): %s", exc)
        return [], 0
    rep, single = _representative_frame(arr)
    try:
        hits = classify_words(_ocr_words(rep, min_confidence), phi)
    except Exception as exc:  # noqa: BLE001
        log.warning("OCR scan failed: %s", exc)
        return [], 0
    redacted = 0
    if redact:
        boxes = [h["box"] for h in hits if h["category"] != "suspect"]
        if boxes:
            _blank_boxes(arr, boxes, single)
            if _write_back(ds, arr, ts):
                redacted = len(boxes)
                log.info("OCR redacted %d PHI region(s).", redacted)
    return hits, redacted


def scan_dataset(ds, phi=None, *, min_confidence: float = 0.5,
                 modalities=None) -> list[dict]:
    """Classify burned-in text WITHOUT modifying pixels (read-only wrapper)."""
    return scan_and_redact(ds, phi, min_confidence=min_confidence,
                           modalities=modalities, redact=False)[0]


def redact_burned_in_text(ds, *, min_confidence: float = 0.5, modalities=None,
                          mode: str = "all", phi=None) -> int:
    """Black out burned-in text in ``ds`` in place. Returns boxes redacted.

    ``mode`` = 'all' blanks every detected text region (highest recall);
    'phi' blanks only regions whose text matches this study's metadata PHI
    (precise — keeps scan parameters). No-op (returns 0) when OCR is unavailable,
    the modality is filtered out, or pixels can't be read/written.
    """
    if mode == "phi":
        return scan_and_redact(ds, phi, min_confidence=min_confidence,
                               modalities=modalities, redact=True)[1]
    # mode 'all': blank every detected region.
    if not _modality_ok(ds, modalities):
        return 0
    if not available():
        _warn_unavailable()
        return 0
    try:
        ts = ds.file_meta.TransferSyntaxUID
        arr = ds.pixel_array
    except Exception as exc:  # noqa: BLE001
        log.warning("OCR skipped (cannot read pixels): %s", exc)
        return 0
    rep, single = _representative_frame(arr)
    try:
        boxes = _detect_boxes(rep, min_confidence)
    except Exception as exc:  # noqa: BLE001
        log.warning("OCR detection failed: %s", exc)
        return 0
    if not boxes or not _write_back_after_blank(ds, arr, boxes, single, ts):
        return 0
    log.info("OCR redacted %d burned-in text region(s) [mode=all].", len(boxes))
    return len(boxes)


def _write_back_after_blank(ds, arr, boxes, single, ts) -> bool:
    _blank_boxes(arr, boxes, single)
    return _write_back(ds, arr, ts)
