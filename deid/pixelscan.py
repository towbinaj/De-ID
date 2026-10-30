"""Burned-in PHI pixel scan: a QA pass over a folder of DICOM files.

Reads each file and OCRs its pixels WITHOUT modifying anything, then classifies
the text found against the file's own metadata (see engine/phimatch.py). Use it
to answer "did de-identification leave any burned-in PHI behind?":

* Scan the ORIGINAL files to see what PHI is burned into the pixels (matched to
  the metadata: name / id / date).
* Scan the DE-IDENTIFIED output to confirm nothing identifying remains - there
  the metadata is already clean, so hits come from the pattern-based "suspect"
  pass (date-like / long-number / name-like text that is not a scan parameter).

The report lists the actual burned-in text, so it is PHI: it is written to the
local, git-ignored ``pixelscan`` folder, never the shared output, and text
values are shown in the UI but never written to the central log.
"""

from __future__ import annotations

import csv
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

# PHI categories (matched to metadata) vs. the review-only pattern flag.
PHI_CATEGORIES = ("name", "id", "date")


@dataclass
class FileFindings:
    path: str
    modality: str
    findings: list = field(default_factory=list)   # classify_words dicts

    @property
    def phi(self) -> list:
        return [f for f in self.findings if f["category"] in PHI_CATEGORIES]

    @property
    def suspects(self) -> list:
        return [f for f in self.findings if f["category"] == "suspect"]


def scan_folder(files, *, min_confidence: float = 0.5, modalities=None,
                should_cancel: Callable[[], bool] | None = None,
                on_item: Callable[[int, int, Path, int], None] | None = None
                ) -> list[FileFindings]:
    """OCR-scan ``files`` for burned-in text. Returns only files with findings."""
    results: list[FileFindings] = []
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
        finds = ocr.scan_dataset(ds, phi, min_confidence=min_confidence,
                                 modalities=modalities)
        if finds:
            results.append(FileFindings(
                str(f), str(getattr(ds, "Modality", "") or ""), finds))
        if on_item:
            on_item(i, total, f, len(finds))
    return results


_COLUMNS = ["file", "modality", "category", "text", "confidence", "box"]


def write_report_csv(results: list[FileFindings], path) -> int:
    """Write every finding to ``path``. Returns the number of finding rows."""
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(_COLUMNS)
        for r in results:
            for f in r.findings:
                y0, y1, x0, x1 = f["box"]
                w.writerow([r.path, r.modality, f["category"], f["text"],
                            f"{f['conf']:.0f}", f"{x0},{y0},{x1},{y1}"])
                n += 1
    return n


def default_report_path(label: str = "scan",
                        when: datetime | None = None) -> Path:
    ts = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return paths.pixelscan_dir() / f"{_slug(label)}_{ts}.csv"
