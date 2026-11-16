"""Known-region burned-in PHI blanking (CTP DicomPixelAnonymizer style).

A fast, deterministic *first pass* that complements the OCR redaction in
``ocr.py``. Where OCR reads pixels to find text anywhere on an image, this blanks
**fixed rectangles** for devices whose burned-in layout is already known -- the
approach used by the MIRC/CTP ``DicomPixelAnonymizer`` and by St. Jude's iCore.

The two are meant to run together: known-region blanking instantly covers the
usual offenders (CT dose reports, modality banners) with zero OCR cost and no
dependence on text legibility, then OCR catches anything device-specific rules
don't know about. Known regions also handle cases OCR can't read at all (e.g.
faint gray-on-gray text) *when* the device is characterized.

Script format (a subset of the CTP DicomPixelAnonymizer script):

    { <condition> }
    (x, y, w, h)
    (x, y, w, h)

``<condition>`` is a boolean expression over DICOM tags:

* a tag is ``[gggg,eeee]`` or a DICOM keyword (``Manufacturer``, ``Modality``…);
* tests: ``.contains("s")`` / ``.containsIgnoreCase("s")`` / ``.equals("s")`` /
  ``.equalsIgnoreCase("s")`` / ``.startsWith("s")`` / ``.startsWithIgnoreCase("s")``
  / ``.endsWith("s")`` / ``.endsWithIgnoreCase("s")`` / ``.matches("regex")`` /
  ``.exists()``;
* ``*`` is AND, ``+`` is OR, ``!`` is NOT, with ``( )`` for grouping.

Each ``(x, y, w, h)`` is a rectangle in CTP order -- left, top, width, height --
which we convert to this project's inclusive ``(y0, y1, x0, x1)`` box and clamp
to the image. Text outside ``{…}``/``(…)`` (section headers, comments) is ignored.

NOTE (prototype): a small starter library is bundled in ``DEFAULT_REGION_SCRIPT``.
For real coverage, load the full community library from the MIRC CTP wiki
(http://mircwiki.rsna.org/index.php?title=The_CTP_DICOM_Pixel_Anonymizer) via
``load_region_script(path)``. As CTP's own header warns, a known-region pass is
never a guarantee -- always review output (that is what the Pixel PHI Scan is for).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pydicom.dataset import Dataset
from pydicom.tag import Tag

from ..logging_setup import get_logger

log = get_logger("engine.pixelregions")

Predicate = Callable[[Dataset], bool]


@dataclass
class RegionRule:
    """A matched signature and the rectangles to blank when it matches."""
    predicate: Predicate
    rects: list[tuple[int, int, int, int]]   # CTP order: (x, y, w, h)
    condition: str                           # raw text, for logging/debug


# --------------------------------------------------------------------------- #
# Tag value resolution
# --------------------------------------------------------------------------- #

def _tag_value(ds: Dataset, ref: str) -> tuple[str, bool]:
    """Resolve a tag reference to ``(string_value, present)``.

    ``ref`` is ``[gggg,eeee]`` or a DICOM keyword. Multi-valued elements are
    joined with the DICOM value delimiter so string tests behave predictably.
    """
    m = re.fullmatch(r"\[\s*([0-9A-Fa-f]{4})\s*,\s*([0-9A-Fa-f]{4})\s*\]", ref)
    try:
        if m:
            tag = Tag(int(m.group(1), 16), int(m.group(2), 16))
            if tag not in ds:
                return "", False
            raw = ds[tag].value
        else:
            if ref not in ds:   # keyword membership test works on a Dataset
                return "", False
            raw = getattr(ds, ref)
    except Exception:  # noqa: BLE001 - a malformed element never crashes a scan
        return "", False
    if raw is None:
        return "", True
    if isinstance(raw, (list, tuple)) or type(raw).__name__ == "MultiValue":
        return "\\".join(str(x) for x in raw), True
    return str(raw), True


_METHODS: dict[str, Callable[[str, str], bool]] = {
    "contains": lambda v, a: a in v,
    "containsignorecase": lambda v, a: a.lower() in v.lower(),
    "equals": lambda v, a: v == a,
    "equalsignorecase": lambda v, a: v.lower() == a.lower(),
    "startswith": lambda v, a: v.startswith(a),
    "startswithignorecase": lambda v, a: v.lower().startswith(a.lower()),
    "endswith": lambda v, a: v.endswith(a),
    "endswithignorecase": lambda v, a: v.lower().endswith(a.lower()),
    "matches": lambda v, a: re.search(a, v) is not None,
}


def _make_predicate(ref: str, method: str, arg: str) -> Predicate:
    meth = method.lower()

    if meth == "exists":
        return lambda ds: _tag_value(ds, ref)[1]

    fn = _METHODS.get(meth)
    if fn is None:
        log.warning("Unknown pixel-region test .%s(); rule disabled.", method)
        return lambda ds: False

    def predicate(ds: Dataset) -> bool:
        value, present = _tag_value(ds, ref)
        if not present:
            return False
        try:
            return fn(value, arg)
        except re.error as exc:
            log.warning("Bad regex in .matches(%r): %s", arg, exc)
            return False

    return predicate


# --------------------------------------------------------------------------- #
# Condition parser (recursive descent: OR of ANDs of NOT/atoms)
# --------------------------------------------------------------------------- #

_PRED_RE = re.compile(
    r"(\[[0-9A-Fa-f]{4}\s*,\s*[0-9A-Fa-f]{4}\]|[A-Za-z][A-Za-z0-9]*)"  # tag
    r"\s*\.\s*([A-Za-z]+)\s*"                                           # .method
    r"\(\s*(?:\"([^\"]*)\"|'([^']*)')?\s*\)"                            # ("arg")
)
_OP_RE = re.compile(r"[*+!()]")


def _tokenize(condition: str) -> list:
    """Tokens: ``("pred", predicate_fn)`` or ``("op", ch)``."""
    tokens: list = []
    i, n = 0, len(condition)
    while i < n:
        ch = condition[i]
        if ch.isspace():
            i += 1
            continue
        m = _PRED_RE.match(condition, i)
        if m:
            ref, method = m.group(1), m.group(2)
            arg = m.group(3) if m.group(3) is not None else (m.group(4) or "")
            tokens.append(("pred", _make_predicate(ref, method, arg)))
            i = m.end()
            continue
        mo = _OP_RE.match(condition, i)
        if mo:
            tokens.append(("op", mo.group(0)))
            i = mo.end()
            continue
        # Unrecognized char: skip it so one odd token can't kill the whole rule.
        i += 1
    return tokens


class _Parser:
    def __init__(self, tokens: list):
        self.toks = tokens
        self.pos = 0

    def _peek(self):
        return self.toks[self.pos] if self.pos < len(self.toks) else None

    def _eat(self):
        t = self.toks[self.pos]
        self.pos += 1
        return t

    def parse(self) -> Predicate:
        pred = self._parse_or()
        return pred if pred is not None else (lambda ds: False)

    def _parse_or(self) -> Predicate | None:
        left = self._parse_and()
        while self._peek() == ("op", "+"):
            self._eat()
            right = self._parse_and()
            left = self._combine(left, right, any)
        return left

    def _parse_and(self) -> Predicate | None:
        left = self._parse_not()
        while self._peek() == ("op", "*"):
            self._eat()
            right = self._parse_not()
            left = self._combine(left, right, all)
        return left

    def _parse_not(self) -> Predicate | None:
        if self._peek() == ("op", "!"):
            self._eat()
            inner = self._parse_not()
            if inner is None:
                return None
            return lambda ds: not inner(ds)
        return self._parse_atom()

    def _parse_atom(self) -> Predicate | None:
        tok = self._peek()
        if tok is None:
            return None
        if tok == ("op", "("):
            self._eat()
            inner = self._parse_or()
            if self._peek() == ("op", ")"):
                self._eat()
            return inner
        if tok[0] == "pred":
            self._eat()
            return tok[1]
        # Stray operator where an atom was expected: skip it.
        self._eat()
        return self._parse_atom()

    @staticmethod
    def _combine(a, b, reducer) -> Predicate:
        parts = [p for p in (a, b) if p is not None]
        if not parts:
            return lambda ds: False
        if len(parts) == 1:
            return parts[0]
        return lambda ds: reducer(p(ds) for p in parts)


def _compile_condition(condition: str) -> Predicate:
    return _Parser(_tokenize(condition)).parse()


# --------------------------------------------------------------------------- #
# Script parsing
# --------------------------------------------------------------------------- #

_RECT_RE = re.compile(r"\(\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\)")


def parse_pixel_script(text: str) -> list[RegionRule]:
    """Parse a CTP-style pixel script into :class:`RegionRule` objects.

    A rule is a ``{ condition }`` block followed by one or more ``(x,y,w,h)``
    rectangles. Anything else (headers, comments) is ignored.
    """
    rules: list[RegionRule] = []
    i, n = 0, len(text)
    while i < n:
        open_brace = text.find("{", i)
        if open_brace == -1:
            break
        close_brace = text.find("}", open_brace + 1)
        if close_brace == -1:
            break
        condition = text[open_brace + 1:close_brace]
        # Rectangles run from here up to the next signature block.
        next_brace = text.find("{", close_brace + 1)
        region_text = text[close_brace + 1:next_brace if next_brace != -1 else n]
        rects = [
            (int(a), int(b), int(c), int(d))
            for a, b, c, d in _RECT_RE.findall(region_text)
        ]
        if rects:
            rules.append(RegionRule(_compile_condition(condition), rects,
                                    " ".join(condition.split())))
        i = close_brace + 1
    return rules


def load_region_script(path: str) -> list[RegionRule]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return parse_pixel_script(fh.read())


# --------------------------------------------------------------------------- #
# Matching + redaction
# --------------------------------------------------------------------------- #

def _image_dims(ds: Dataset) -> tuple[int, int]:
    """(rows, columns), 0 when unknown (then clamping is left to numpy)."""
    try:
        return int(getattr(ds, "Rows", 0) or 0), int(getattr(ds, "Columns", 0) or 0)
    except Exception:  # noqa: BLE001
        return 0, 0


def matching_boxes(ds: Dataset,
                   rules: list[RegionRule]) -> list[tuple[int, int, int, int]]:
    """Return ``(y0, y1, x0, x1)`` inclusive boxes for every rule that matches.

    CTP ``(x, y, w, h)`` rectangles are converted and clamped to the image.
    """
    rows, cols = _image_dims(ds)
    boxes: list[tuple[int, int, int, int]] = []
    for rule in rules:
        try:
            if not rule.predicate(ds):
                continue
        except Exception:  # noqa: BLE001 - a bad rule never breaks the pass
            log.warning("Pixel-region rule failed to evaluate: %s", rule.condition)
            continue
        for (x, y, w, h) in rule.rects:
            if w <= 0 or h <= 0:
                continue
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = x + w - 1, y + h - 1
            if cols:
                x1 = min(x1, cols - 1)
            if rows:
                y1 = min(y1, rows - 1)
            if x1 >= x0 and y1 >= y0:
                boxes.append((y0, y1, x0, x1))
    return boxes


def redact_known_regions(ds: Dataset, rules: list[RegionRule] | None = None,
                         *, script_text: str | None = None) -> int:
    """Blank known burned-in-PHI rectangles in ``ds`` in place.

    Pass pre-parsed ``rules`` (fastest, reused across a batch), or ``script_text``
    to parse once here, or neither to use the bundled starter library. Returns
    the number of rectangles blanked. Reuses :func:`ocr.redact_boxes`, so it
    needs no Tesseract engine.
    """
    if rules is None:
        rules = (parse_pixel_script(script_text) if script_text is not None
                 else default_rules())
    boxes = matching_boxes(ds, rules)
    if not boxes:
        return 0
    from . import ocr
    n = ocr.redact_boxes(ds, boxes)
    if n:
        log.info("Known-region pass blanked %d rectangle(s).", n)
    return n


# --------------------------------------------------------------------------- #
# Bundled library
# --------------------------------------------------------------------------- #
#
# The full MIRC CTP "Burned-in Annotation Library"
# (deid/engine/recipes/DicomPixelAnonymizer.script) ships with the app and is the
# default when the pass is enabled with no custom script. It is third-party data
# from RSNA MIRC CTP (http://mircwiki.rsna.org) — its own attribution header is
# kept intact; De-ID's MIT license covers only De-ID's code, not that library.
# The small embedded STARTER below is a last-resort fallback for when the bundled
# file is missing (e.g. an incomplete build).

_LIBRARY_NAME = "DicomPixelAnonymizer.script"

STARTER_REGION_SCRIPT = """
Fallback starter library (subset) — used only if the bundled full MIRC CTP
DicomPixelAnonymizer.script is missing from the install.

**** CT dose reports ****
  { [0008,0060].containsIgnoreCase("CT") *
    [0008,0070].containsIgnoreCase("GE MEDICAL") *
    [0008,103e].containsIgnoreCase("Dose Report") }
  (0,0,512,110)

  { Modality.containsIgnoreCase("CT") *
    [0040,0310].containsIgnoreCase("DLP") }
  (0,0,512,110)

**** Secondary Capture / explicitly flagged burned-in annotation ****
  { BurnedInAnnotation.equalsIgnoreCase("YES") *
    Modality.equalsIgnoreCase("SC") }
  (0,0,1024,80)
"""

# Back-compat alias (older callers referenced DEFAULT_REGION_SCRIPT).
DEFAULT_REGION_SCRIPT = STARTER_REGION_SCRIPT


def bundled_library_path() -> str | None:
    """Locate the bundled full MIRC CTP library, source or frozen, or None."""
    candidates = [Path(__file__).resolve().parent / "recipes" / _LIBRARY_NAME]
    try:
        from .. import paths
        res = paths.resource_dir()
        candidates.append(res / "deid" / "engine" / "recipes" / _LIBRARY_NAME)
        candidates.append(res / "recipes" / _LIBRARY_NAME)
    except Exception:  # noqa: BLE001
        pass
    for c in candidates:
        if os.path.isfile(c):
            return str(c)
    return None


_DEFAULT_RULES: list[RegionRule] | None = None


def default_rules() -> list[RegionRule]:
    """The default region library (cached): the bundled full MIRC CTP library
    when present, else the embedded starter fallback."""
    global _DEFAULT_RULES
    if _DEFAULT_RULES is None:
        path = bundled_library_path()
        if path:
            try:
                _DEFAULT_RULES = load_region_script(path)
            except OSError as exc:
                log.warning("Bundled region library %s unreadable (%s); using "
                            "the starter fallback.", path, exc)
                _DEFAULT_RULES = parse_pixel_script(STARTER_REGION_SCRIPT)
        else:
            log.info("Bundled region library not found; using starter fallback.")
            _DEFAULT_RULES = parse_pixel_script(STARTER_REGION_SCRIPT)
    return _DEFAULT_RULES
