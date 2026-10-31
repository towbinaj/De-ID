"""Burned-in PHI pixel scan: a study-level QA + correction pass.

OCRs each file's pixels and classifies the text against the file's own metadata
(see engine/phimatch.py), then rolls the findings up into a study → series →
instance report with a status per level:

* **pass**         - no PHI and no suspects.
* **corrected**    - metadata-matched PHI was found and redacted (needs the scan
  run with correction on); nothing left to review.
* **needs review** - pattern-based "suspects" (date-like / long-number / name-
  like text not matchable to this file's metadata) a human must check.

A study's status is the worst of its series; a series' is the worst of its
instances (review > corrected > pass).

With correction on, the metadata-matched boxes are blacked out and a corrected
copy of each changed file is written to the chosen output folder. This fixes
PIXELS only - run the metadata de-identification (Quick / Run) as well.

Findings and reports contain the burned-in text, i.e. PHI: reports are written
to the local, git-ignored pixelscan folder, shown in the UI but never logged.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from pydicom import dcmread

from . import paths
from .engine import ocr, phimatch
from .logging_setup import get_logger
from .project import _slug

log = get_logger("pixelscan")

PHI_CATEGORIES = ("name", "id", "date")

STATUS_PASS = "pass"
STATUS_CORRECTED = "corrected"
STATUS_REVIEW = "needs review"
_RANK = {STATUS_PASS: 0, STATUS_CORRECTED: 1, STATUS_REVIEW: 2}


def worst(statuses) -> str:
    return max(statuses, key=lambda s: _RANK.get(s, 0), default=STATUS_PASS)


def instance_status(n_phi: int, n_suspects: int, redacted: int) -> str:
    """Status for one instance from its finding counts + whether PHI was fixed."""
    if n_suspects:
        return STATUS_REVIEW
    if n_phi:
        return STATUS_CORRECTED if redacted else STATUS_REVIEW
    return STATUS_PASS


def _s(ds, kw: str) -> str:
    return str(getattr(ds, kw, "") or "").strip()


@dataclass
class InstanceReport:
    path: str
    sop: str
    study_uid: str
    series_uid: str
    series_number: str
    series_desc: str
    modality: str
    accession: str = ""
    patient_id: str = ""
    findings: list = field(default_factory=list)
    redacted: int = 0
    out_path: str = ""

    @property
    def n_phi(self) -> int:
        return sum(1 for f in self.findings if f["category"] in PHI_CATEGORIES)

    @property
    def n_suspects(self) -> int:
        return sum(1 for f in self.findings if f["category"] == "suspect")

    @property
    def status(self) -> str:
        return instance_status(self.n_phi, self.n_suspects, self.redacted)


@dataclass
class SeriesReport:
    series_uid: str
    series_number: str
    series_desc: str
    modality: str
    instances: list = field(default_factory=list)

    @property
    def n_phi(self) -> int:
        return sum(i.n_phi for i in self.instances)

    @property
    def n_suspects(self) -> int:
        return sum(i.n_suspects for i in self.instances)

    @property
    def n_redacted(self) -> int:
        return sum(i.redacted for i in self.instances)

    @property
    def status(self) -> str:
        return worst([i.status for i in self.instances])


@dataclass
class StudyReport:
    study_uid: str
    accession: str
    patient_id: str
    series: list = field(default_factory=list)

    @property
    def modalities(self) -> str:
        return ", ".join(sorted({s.modality for s in self.series if s.modality}))

    @property
    def n_series(self) -> int:
        return len(self.series)

    @property
    def n_instances(self) -> int:
        return sum(len(s.instances) for s in self.series)

    @property
    def n_phi(self) -> int:
        return sum(s.n_phi for s in self.series)

    @property
    def n_suspects(self) -> int:
        return sum(s.n_suspects for s in self.series)

    @property
    def n_redacted(self) -> int:
        return sum(s.n_redacted for s in self.series)

    @property
    def status(self) -> str:
        return worst([s.status for s in self.series])


def build_studies(instances: list[InstanceReport]) -> list[StudyReport]:
    """Group instance reports into study → series, preserving first-seen order."""
    studies: dict[str, StudyReport] = {}
    series_idx: dict[tuple, SeriesReport] = {}
    for inst in instances:
        st = studies.get(inst.study_uid)
        if st is None:
            st = StudyReport(inst.study_uid, inst.accession, inst.patient_id)
            studies[inst.study_uid] = st
        skey = (inst.study_uid, inst.series_uid)
        se = series_idx.get(skey)
        if se is None:
            se = SeriesReport(inst.series_uid, inst.series_number,
                              inst.series_desc, inst.modality)
            series_idx[skey] = se
            st.series.append(se)
        se.instances.append(inst)
    return list(studies.values())


def mirror_base(files) -> Path | None:
    """The common parent directory of ``files``, used to mirror their layout
    under the output folder. ``None`` when they share no base (e.g. different
    drives) or the list is empty, in which case output falls back to flat names.
    """
    parents = []
    for f in files:
        try:
            parents.append(str(Path(f).resolve().parent))
        except OSError:
            continue
    if not parents:
        return None
    try:
        return Path(os.path.commonpath(parents))
    except ValueError:            # paths on different drives / mixed roots
        return None


def _corrected_dest(src: Path, out: Path, base: Path | None) -> Path:
    """Where a corrected copy of ``src`` goes under ``out``: its path relative to
    ``base`` (preserving the original folder structure and filename), or just the
    filename when it is not under ``base``.
    """
    rel = Path(src.name)
    if base is not None:
        try:
            rel = src.resolve().relative_to(base)
        except (ValueError, OSError):
            rel = Path(src.name)
    return out / rel


def scan_studies(files, *, redact: bool = False, out_dir=None,
                 min_confidence: float = 0.4, modalities=None,
                 should_cancel: Callable[[], bool] | None = None,
                 on_item: Callable[[int, int, Path, int], None] | None = None
                 ) -> list[StudyReport]:
    """Scan ``files`` for burned-in PHI; optionally redact matches to copies.

    When ``redact`` and ``out_dir`` are set, metadata-matched boxes are blacked
    out and a corrected copy of each changed file is written under ``out_dir``,
    mirroring the input folder structure (relative to the files' common base).
    """
    files = [Path(f) for f in files]
    out = Path(out_dir) if out_dir else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    base = mirror_base(files) if out else None
    do_redact = bool(redact and out)
    instances: list[InstanceReport] = []
    total = len(files)
    for i, f in enumerate(files, 1):
        if should_cancel and should_cancel():
            break
        f = Path(f)
        try:
            ds = dcmread(str(f), force=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("Pixel scan: cannot read %s: %s", f.name, exc)
            if on_item:
                on_item(i, total, f, -1)
            continue
        phi = phimatch.build_phi(ds)
        findings, redacted = ocr.scan_and_redact(
            ds, phi, min_confidence=min_confidence, modalities=modalities,
            redact=do_redact)
        out_path = ""
        if redacted and out:
            dest = _corrected_dest(f, out, base)
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                ds.save_as(str(dest), enforce_file_format=True)
                out_path = str(dest)
            except Exception as exc:  # noqa: BLE001
                log.warning("Pixel scan: cannot write corrected %s: %s",
                            f.name, exc)
        inst = InstanceReport(
            path=str(f), sop=_s(ds, "SOPInstanceUID"),
            study_uid=_s(ds, "StudyInstanceUID"),
            series_uid=_s(ds, "SeriesInstanceUID"),
            series_number=_s(ds, "SeriesNumber"),
            series_desc=_s(ds, "SeriesDescription"),
            modality=_s(ds, "Modality"),
            accession=_s(ds, "AccessionNumber"),
            patient_id=_s(ds, "PatientID"), findings=findings,
            redacted=redacted, out_path=out_path)
        instances.append(inst)
        if on_item:
            on_item(i, total, f, len(findings))
    return build_studies(instances)


# -- adjudication --------------------------------------------------------
def adjudication_key(study_uid: str, series_uid: str, category: str,
                     text: str) -> tuple:
    """Stable key for one reviewable finding, shared by the review UI and the
    findings CSV so a human's decision on a de-duplicated review row maps to all
    the instances that share that (study, series, category, text)."""
    return (study_uid, series_uid, category, text)


# -- report writers ------------------------------------------------------
_OVERVIEW_COLS = ["study_uid", "accession", "patient_id", "modalities",
                  "series", "instances", "phi_hits", "suspects", "redacted",
                  "status"]
_DETAIL_COLS = ["study_uid", "accession", "series_number", "series_desc",
                "sop_instance", "modality", "category", "text", "confidence",
                "box", "redacted", "corrected_file", "adjudicated"]


def write_overview_csv(studies: list[StudyReport], path) -> int:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(_OVERVIEW_COLS)
        for st in studies:
            w.writerow([st.study_uid, st.accession, st.patient_id,
                        st.modalities, st.n_series, st.n_instances, st.n_phi,
                        st.n_suspects, st.n_redacted, st.status])
    return len(studies)


def write_findings_csv(studies: list[StudyReport], path,
                       adjudications: dict | None = None) -> int:
    """Write the per-finding detail CSV. ``adjudications`` maps an
    :func:`adjudication_key` to a human decision (e.g. ``"ok"`` / ``"phi"``),
    recorded in the ``adjudicated`` column."""
    adj = adjudications or {}
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(_DETAIL_COLS)
        for st in studies:
            for se in st.series:
                for inst in se.instances:
                    for f in inst.findings:
                        y0, y1, x0, x1 = f["box"]
                        is_phi = f["category"] in PHI_CATEGORIES
                        key = adjudication_key(st.study_uid, se.series_uid,
                                               f["category"], f["text"])
                        w.writerow([
                            st.study_uid, st.accession, se.series_number,
                            se.series_desc, inst.sop, inst.modality,
                            f["category"], f["text"], f"{f['conf']:.0f}",
                            f"{x0},{y0},{x1},{y1}",
                            "yes" if (is_phi and inst.redacted) else "",
                            inst.out_path, adj.get(key, "")])
                        n += 1
    return n


def default_report_paths(label: str = "scan", when: datetime | None = None):
    """(overview_path, findings_path) in the local pixelscan folder."""
    ts = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    base = paths.pixelscan_dir()
    stem = _slug(label)
    return (base / f"{stem}_{ts}_overview.csv",
            base / f"{stem}_{ts}_findings.csv")
