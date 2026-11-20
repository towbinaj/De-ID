"""Structural sanitize pass: detect and strip smuggled-payload vectors.

De-identification cleans *who* an object is about. Sanitization is a different
job: removing the non-clinical binary elements a DICOM object can carry that are
identifiable attack vectors regardless of PHI - the places a crafted payload can
ride along into a downstream PACS / VNA / viewer. This pass strips the ones we
can remove deterministically while leaving the primary image usable:

* **Preamble polyglot** - a non-zero 128-byte preamble can smuggle a valid
  PE/MZ executable header (CVE-2019-11687). We only *detect + report* it here;
  the actual zeroing is an unconditional invariant in ``processing.py`` so every
  written object gets a clean preamble whether or not this pass runs.
* **Icon Image Sequence** ``(0088,0200)`` - a second, independent pixel blob that
  routes through the same image decoders and can itself carry burned-in text.
* **Overlay planes** - groups ``6000``-``60FF``. ``OverlayData`` is an arbitrary
  bitplane that can hide data or burned-in annotation.
* **Curve data** - retired groups ``5000``-``50FF``; arbitrary binary.
* **Encapsulated document** ``(0042,0011)`` - an embedded file (PDF/CDA/STL/...).
  We strip the embedded stream; if the object's SOP Class *is* an encapsulated
  document (it has no clinical image), we flag it so the caller can drop or
  quarantine the whole object.

This does NOT address malicious *primary* pixel data (a crafted JPEG/JPEG2000/RLE
bitstream exploiting a decoder) - that is neutralized only by transcoding, a
separate, costlier pass. Sanitization is a structural control, not a malware
scanner: it removes identifiable carriers, it does not prove an object is safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydicom.dataset import Dataset

from ..logging_setup import get_logger

log = get_logger("engine.sanitize")

# Tags / groups we strip.
_ICON_IMAGE_SEQUENCE = 0x00880200
_ENCAPSULATED_DOCUMENT = 0x00420011
# Overlay groups are the even groups 0x6000..0x601E; curve groups 0x5000..0x501E
# (retired). We strip the whole bands to catch repeats and odd private overlays.
_OVERLAY_GROUP_LO, _OVERLAY_GROUP_HI = 0x6000, 0x60FF
_CURVE_GROUP_LO, _CURVE_GROUP_HI = 0x5000, 0x50FF

# SOP Class UIDs whose primary payload IS an embedded document (no clinical
# image). Stripping the document stream empties them, so they are flagged.
_ENCAPSULATED_SOP_CLASSES = frozenset({
    "1.2.840.10008.5.1.4.1.1.104.1",   # Encapsulated PDF
    "1.2.840.10008.5.1.4.1.1.104.2",   # Encapsulated CDA
    "1.2.840.10008.5.1.4.1.1.104.3",   # Encapsulated STL
    "1.2.840.10008.5.1.4.1.1.104.4",   # Encapsulated OBJ
    "1.2.840.10008.5.1.4.1.1.104.5",   # Encapsulated MTL
})


@dataclass
class SanitizeConfig:
    """Which structural carriers to strip. Defaults strip everything safe to
    remove while keeping the primary image usable."""
    enabled: bool = False
    strip_icon_images: bool = True
    strip_overlays: bool = True
    strip_curves: bool = True
    strip_encapsulated: bool = True


@dataclass
class SanitizeReport:
    """What the sanitize pass found / removed on one object. Counts only - never
    element values, which can be PHI and must stay out of logs."""
    preamble_payload: bool = False
    preamble_signature: str = ""   # e.g. "MZ (DOS/PE executable header)"
    icon_sequences: int = 0
    overlay_groups: int = 0
    curve_groups: int = 0
    encapsulated_docs: int = 0
    is_encapsulated_object: bool = False
    stripped_tags: list = field(default_factory=list)   # ["60003000", ...]

    @property
    def any_found(self) -> bool:
        return bool(self.preamble_payload or self.icon_sequences
                    or self.overlay_groups or self.curve_groups
                    or self.encapsulated_docs or self.is_encapsulated_object)

    def findings(self) -> list[str]:
        """Human-readable, value-free finding lines (safe to log / show)."""
        out: list[str] = []
        if self.preamble_payload:
            sig = self.preamble_signature or "non-zero payload"
            out.append(f"non-empty preamble ({sig})")
        if self.icon_sequences:
            out.append(f"{self.icon_sequences} icon image sequence(s)")
        if self.overlay_groups:
            out.append(f"{self.overlay_groups} overlay plane group(s)")
        if self.curve_groups:
            out.append(f"{self.curve_groups} curve data group(s)")
        if self.encapsulated_docs:
            out.append(f"{self.encapsulated_docs} encapsulated document(s)")
        if self.is_encapsulated_object:
            out.append("object SOP Class is an encapsulated document "
                       "(consider a drop/quarantine rule)")
        return out


def _preamble_signature(preamble: bytes) -> str:
    """Name a recognizable payload in a non-zero preamble, else a generic note."""
    if preamble[:2] == b"MZ":
        return "MZ (DOS/PE executable header)"
    if preamble[:4] == b"\x7fELF":
        return "ELF (executable)"
    if preamble[:4] == b"%PDF":
        return "PDF header"
    if preamble[:2] == b"PK":
        return "ZIP/PK archive header"
    return "non-zero payload"


def detect_preamble(ds: Dataset, report: SanitizeReport) -> None:
    """Record whether the 128-byte preamble carries a payload (does not zero it;
    zeroing is an unconditional invariant in processing.neutralize_preamble)."""
    current = getattr(ds, "preamble", None)
    if current and any(current):
        report.preamble_payload = True
        report.preamble_signature = _preamble_signature(bytes(current))


def sanitize_dataset(ds: Dataset, cfg: SanitizeConfig | None = None) -> SanitizeReport:
    """Strip identifiable smuggled-payload carriers from ``ds`` in place.

    Returns a :class:`SanitizeReport` of what was found / removed. A ``None`` or
    disabled config still runs preamble *detection* (cheap, read-only) and
    returns an otherwise-empty report, so callers always get the polyglot flag.
    """
    report = SanitizeReport()
    detect_preamble(ds, report)
    if cfg is None or not cfg.enabled:
        return report

    # SOP-class flag (read before we strip anything).
    sop_class = str(getattr(ds, "SOPClassUID", "") or "")
    if sop_class in _ENCAPSULATED_SOP_CLASSES:
        report.is_encapsulated_object = True

    # Collect tags to delete first - never mutate while iterating the dataset.
    # Overlay/curve planes span many elements per group; count distinct groups.
    to_delete = []
    overlay_groups: set[int] = set()
    curve_groups: set[int] = set()
    for elem in ds:
        tag = elem.tag
        group = tag.group
        t = int(tag)
        if cfg.strip_icon_images and t == _ICON_IMAGE_SEQUENCE:
            report.icon_sequences += 1
            to_delete.append(tag)
        elif cfg.strip_encapsulated and t == _ENCAPSULATED_DOCUMENT:
            report.encapsulated_docs += 1
            to_delete.append(tag)
        elif (cfg.strip_overlays
              and _OVERLAY_GROUP_LO <= group <= _OVERLAY_GROUP_HI
              and group % 2 == 0):
            overlay_groups.add(group)
            to_delete.append(tag)
        elif (cfg.strip_curves
              and _CURVE_GROUP_LO <= group <= _CURVE_GROUP_HI
              and group % 2 == 0):
            curve_groups.add(group)
            to_delete.append(tag)
    report.overlay_groups = len(overlay_groups)
    report.curve_groups = len(curve_groups)

    for tag in to_delete:
        try:
            del ds[tag]
            report.stripped_tags.append(f"{tag.group:04X}{tag.element:04X}")
        except KeyError:
            pass
    return report
