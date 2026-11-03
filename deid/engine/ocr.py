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
    # Capture bytes and decode as UTF-8 (what Tesseract emits); decoding as the
    # Windows locale (cp1252, the `text=True` default) crashed on any non-cp1252
    # byte - common on noisy images - which silently dropped ALL findings.
    proc = subprocess.run(
        [exe, png_path, "stdout", "--psm", "11", "tsv"],
        capture_output=True, env=env, timeout=120, creationflags=_NO_WINDOW)
    return proc.stdout.decode("utf-8", "replace")


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


def _highpass(img):
    """Local-contrast image: subtract a large-radius blur (the local grey
    background) so low-contrast UI text on a panel stands out, then stretch.
    This is what lets OCR read gray-on-gray electronic face sheets."""
    from PIL import Image, ImageFilter, ImageOps
    r = max(6, round(min(img.size) / 14))
    blur = img.filter(ImageFilter.GaussianBlur(radius=r))
    hp = (np.asarray(img).astype(np.int16)
          - np.asarray(blur).astype(np.int16) + 128)
    hp = np.clip(hp, 0, 255).astype(np.uint8)
    return ImageOps.autocontrast(Image.fromarray(hp), cutoff=1)


def _tsv_words(exe, img, min_conf: float, scale: float) -> list[dict]:
    """Run Tesseract on one prepared image and parse its TSV to word dicts,
    mapping boxes back to the original (pre-upscale) coordinates."""
    with tempfile.TemporaryDirectory() as td:
        png = os.path.join(td, "frame.png")
        img.save(png)
        tsv = _run_tsv(exe, png)
    out: list[dict] = []
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
            out.append({
                "text": text, "conf": conf, "box": (y0, y1, x0, x1),
                "line": (row.get("block_num"), row.get("par_num"),
                         row.get("line_num"))})
        except (ValueError, KeyError, TypeError):
            continue
    return out


def _ocr_words(frame: np.ndarray, min_conf: float) -> list[dict]:
    """OCR one frame → word dicts: {text, conf, box:(y0,y1,x0,x1), line}.

    Runs two passes - the upscaled grayscale frame and a local-contrast
    (high-pass) version - and merges the words, so low-contrast text (gray
    electronic face sheets) is caught without losing the plain detections.
    Boxes are in the frame's own coordinates.
    """
    exe = tesseract_path()
    if not exe:
        return []
    from PIL import Image
    base = Image.fromarray(_to_uint8(frame))
    if base.mode != "L":                 # text detection is more robust in gray
        base = base.convert("L")
    scale = _ocr_scale(base.width, base.height)

    def prep(im):
        if scale > 1.0:
            im = im.resize((round(im.width * scale), round(im.height * scale)),
                           Image.LANCZOS)
        return im

    words: list[dict] = []
    seen: set = set()
    for img in (prep(base), prep(_highpass(base))):
        for w in _tsv_words(exe, img, min_conf, scale):
            # dedupe the same word found in both passes (coarse position key).
            key = (w["text"].upper(), w["box"][0] // 24, w["box"][2] // 24)
            if key in seen:
                continue
            seen.add(key)
            words.append(w)
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

# Common ALL-CAPS labels on images (orientation, anatomy, exam/mode words) that
# are not PHI - excluded from the all-caps "name-like" suspect check so the
# review list is not flooded. The user can still flag anything we let through.
_COMMON_LABELS = {
    # orientation / view
    "CORONAL", "SAGITTAL", "AXIAL", "TRANS", "TRANSVERSE", "LONG",
    "LONGITUDINAL", "OBLIQUE", "DECUBITUS", "SUPINE", "PRONE", "ERECT",
    "LEFT", "RIGHT", "MIDLINE", "LATERAL", "MEDIAL", "ANTERIOR", "POSTERIOR",
    "SUPERIOR", "INFERIOR", "PROXIMAL", "DISTAL", "MID", "UPPER", "LOWER",
    # anatomy
    "HEAD", "NECK", "CHEST", "ABDOMEN", "PELVIS", "BRAIN", "SPINE", "KIDNEY",
    "LIVER", "SPLEEN", "BLADDER", "THYROID", "HEART", "AORTA", "CAROTID",
    "RENAL", "HEPATIC", "BREAST", "SCROTUM", "TESTIS", "OVARY", "UTERUS",
    "THORAX", "LUNG", "FEMUR", "HIP", "KNEE", "SHOULDER", "WRIST", "ANKLE",
    "FOOT", "HAND", "ELBOW", "THIGH", "CALF", "GALLBLADDER", "PANCREAS",
    # exam / mode / general
    "NEONATAL", "PEDIATRIC", "ROUTINE", "GENERAL", "STUDY", "EXAM", "VIEW",
    "IMAGE", "SERIES", "DOPPLER", "COLOR", "COLOUR", "POWER", "PULSE", "FLOW",
    "MODE", "FREEZE", "DUAL", "HARMONIC", "COMPOUND", "DEPTH", "FOCUS",
    "FRAME", "ZOOM", "MEASURE", "AREA", "GAIN", "GENERAL", "WITH", "WITHOUT",
    "ECHO", "SCAN", "COMPLETE", "LIMITED", "PORTABLE", "STAT",
    # common ultrasound on-screen control labels
    "GEN", "DYN", "OFF", "RES", "PEN", "MAP", "FRQ", "THI", "CRI", "SRI",
}

_DATE_RE = re.compile(r"^\d{1,4}[/.\-]\d{1,2}([/.\-]\d{1,4})?$")
_NAME_RE = re.compile(r"^[A-Z][a-z]{2,}$")      # a single Capitalised word
_ALPHA_RE = re.compile(r"[^A-Za-z']")


def _is_suspect(text: str) -> bool:
    t = text.strip()
    up = re.sub(r"[^A-Z0-9]", "", t.upper())
    if not up or up in _SCAN_TERMS:
        return False
    if _DATE_RE.match(t):                       # date-like with separators
        return True
    if up.isdigit() and len(up) >= 6:           # long bare number (MRN/acc-like)
        return True
    # Name checks run on the alphabetic core so trailing punctuation does not
    # hide a name - e.g. "Doe," ("Lastname, Firstname") or "SMITH:".
    alpha = _ALPHA_RE.sub("", t)
    if _NAME_RE.match(alpha):                   # Title-case word (e.g. "Smith")
        return True
    if (3 <= len(alpha) <= 20 and alpha.isupper()   # ALL-CAPS name ("O'BRIEN")
            and up not in _COMMON_LABELS):
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
        # pydicom's pixel_array converts any YBR colour (compressed JPEG cines or
        # uncompressed YBR_FULL/422) to interleaved RGB, so the stored bytes are
        # now RGB. Relabel them, or a DICOM viewer renders the output green.
        pi = str(getattr(ds, "PhotometricInterpretation", "")).upper()
        if arr.ndim >= 3 and arr.shape[-1] == 3 and pi.startswith("YBR"):
            ds.PhotometricInterpretation = "RGB"
            ds.PlanarConfiguration = 0
            ds.SamplesPerPixel = 3
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("OCR write-back failed: %s", exc)
        return False


def _ensure_transfer_syntax(ds) -> None:
    """Make ``ds`` decodable when it was read without file meta.

    Some exported DICOM have no part-10 file meta / TransferSyntaxUID, so
    ``pixel_array`` raises and the object silently escapes redaction. Default a
    missing transfer syntax to Implicit VR Little Endian (the DICOM default for
    a dataset with no file meta), which lets uncompressed pixels decode.
    """
    from pydicom.dataset import FileMetaDataset
    from pydicom.uid import ImplicitVRLittleEndian
    fm = getattr(ds, "file_meta", None)
    if fm is None:
        ds.file_meta = fm = FileMetaDataset()
    if "TransferSyntaxUID" not in fm:
        fm.TransferSyntaxUID = ImplicitVRLittleEndian


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
        _ensure_transfer_syntax(ds)
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
        _ensure_transfer_syntax(ds)
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
