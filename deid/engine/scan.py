"""Pre-run folder scan: a fast, header-only preview of a batch.

Before any file is written, De-ID reads just the headers of the selected files
and reports what the batch contains - how many files, and crucially how many
*distinct patients*. That lets the UI show, in plain words, that each patient
will keep a separate identity after de-identification, and warn clearly when a
project's settings would instead collapse every patient into one blank
identifier (the classic "everyone became the same patient" mistake when a whole
folder of mixed patients is run with the Basic Profile but no per-patient
pseudonyms).

Nothing here changes any file; it only reads headers (``stop_before_pixels``).
Patient values read here are PHI and must never be mirrored to the log file -
only counts leave this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pydicom import dcmread

from . import objectrules

# DICOM keywords that establish a patient's identity. If a project assigns a
# stable pseudonym to any of these, distinct patients stay distinct.
_PATIENT_ID_TAGS = {"patientid", "patientname", "otherpatientids"}


@dataclass
class ScanResult:
    files: int = 0
    unreadable: int = 0
    blank_id: int = 0                                  # files with no PatientID
    patients: set = field(default_factory=set)         # distinct patient keys
    studies: set = field(default_factory=set)
    series: set = field(default_factory=set)
    modalities: dict = field(default_factory=dict)     # modality -> count
    would_drop: int = 0
    would_quarantine: int = 0

    @property
    def patient_count(self) -> int:
        return len(self.patients)

    @property
    def study_count(self) -> int:
        return len(self.studies)

    @property
    def series_count(self) -> int:
        return len(self.series)

    def modality_summary(self, limit: int = 4) -> str:
        """e.g. 'CT 40, SC 8, MR 3' (busiest first)."""
        items = sorted(self.modalities.items(), key=lambda kv: -kv[1])
        shown = ", ".join(f"{m} {n}" for m, n in items[:limit])
        if len(items) > limit:
            shown += f", +{len(items) - limit} more"
        return shown or "unknown"


def _patient_key(ds) -> tuple[str, bool]:
    """A stable grouping key for one object's patient, and whether its ID is blank.

    Prefers PatientID; falls back to PatientName so two un-IDed studies with
    different names are still counted as different people.
    """
    pid = str(getattr(ds, "PatientID", "") or "").strip()
    if pid:
        return "id:" + pid, False
    name = str(getattr(ds, "PatientName", "") or "").strip()
    if name:
        return "name:" + name, True
    return "", True


def scan_files(files, drop_rules=None,
               should_cancel: Callable[[], bool] | None = None) -> ScanResult:
    """Read headers only and tally a :class:`ScanResult`.

    ``drop_rules`` (dicts or :class:`objectrules.DropRule`) are evaluated so the
    preview can estimate how many objects a project would drop / quarantine.
    """
    res = ScanResult()
    rules = objectrules.parse_rules(drop_rules)
    for f in files:
        if should_cancel and should_cancel():
            break
        res.files += 1
        try:
            ds = dcmread(str(Path(f)), stop_before_pixels=True, force=True)
        except Exception:  # noqa: BLE001 - count, keep going
            res.unreadable += 1
            continue
        key, blank = _patient_key(ds)
        res.patients.add(key)
        if blank:
            res.blank_id += 1
        suid = str(getattr(ds, "StudyInstanceUID", "") or "").strip()
        if suid:
            res.studies.add(suid)
        se = str(getattr(ds, "SeriesInstanceUID", "") or "").strip()
        if se:
            res.series.add(se)
        mod = str(getattr(ds, "Modality", "") or "").strip() or "?"
        res.modalities[mod] = res.modalities.get(mod, 0) + 1
        if rules:
            m = objectrules.evaluate(ds, rules)
            if m is not None:
                if m.action == "quarantine":
                    res.would_quarantine += 1
                else:
                    res.would_drop += 1
    return res


def _ids_stay_separate(project) -> bool:
    """True if the project assigns a per-patient pseudonym to an identity tag."""
    if getattr(project, "pseudonymize", False):
        tags = [str(t).replace(" ", "").lower()
                for t in (getattr(project, "pseudonym_tags", []) or [])]
        if any(t in _PATIENT_ID_TAGS for t in tags):
            return True
    # An external crosswalk morph keyed on a patient tag also keeps them apart.
    if getattr(project, "morph_enabled", False):
        key = str(getattr(project, "morph_key_tag", "") or "")
        # 0010,0020 = PatientID ; 0010,0010 = PatientName
        if key.replace(",", "").lower() in ("00100020", "00100010"):
            return True
    return False


def patient_separation(project, scan: ScanResult) -> tuple[str, str]:
    """Plain-English line about patient identity after de-id.

    Returns ``(level, message)`` with level in ``{'ok', 'warn', 'info'}`` so the
    UI can color it (green / amber / muted).
    """
    n = scan.patient_count
    if n == 0:
        return "info", ""
    sep = _ids_stay_separate(project)
    basic = getattr(project, "basic_profile", False)

    if sep:
        if n == 1:
            return "ok", "1 patient → 1 stable de-identified ID. The same " \
                         "patient keeps this ID on future runs."
        return "ok", (f"{n} distinct patients → {n} distinct, stable "
                      f"de-identified IDs. Each patient keeps the same ID "
                      f"across runs; patients are never merged.")

    # No per-patient pseudonym. If the Basic Profile is on, it blanks PatientID
    # for every file -> all patients become one indistinguishable identity.
    if basic:
        if n == 1:
            return "info", "1 patient; their ID will be blanked by the Basic " \
                           "Profile."
        return "warn", (f"⚠ {n} patients will ALL be written with the same "
                        f"blank ID — they'd be indistinguishable. Turn on "
                        f"“Keep patients separate” to give each a stable ID.")

    # Basic Profile off and no pseudonyms: original IDs pass through unchanged.
    return "info", (f"{n} patients; original patient IDs will be kept as-is "
                    f"(no Basic Profile, no pseudonyms).")


def overview(scan: ScanResult) -> str:
    """One compact, PHI-free line: counts + modalities."""
    if scan.files == 0:
        return "No files yet."
    bits = [f"{scan.files:,} file(s)",
            f"{scan.patient_count} patient(s)",
            f"{scan.study_count} study(ies)"]
    if scan.series_count:
        bits.append(f"{scan.series_count} series")
    line = " · ".join(bits)
    line += f"  ·  {scan.modality_summary()}"
    if scan.unreadable:
        line += f"  ·  {scan.unreadable} unreadable"
    return line
