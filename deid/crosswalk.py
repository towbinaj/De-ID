"""Re-identification crosswalk: the CSV that maps pseudonyms back to patients.

After a de-identification run, this is the key a (local, authorized) researcher
uses to go from a de-identified study back to the original patient / accession.
One row per **study** (a patient with several studies gets several rows sharing
the same patient mapping), so multi-study patients are never lost.

This file is PHI. It is written to the local, git-ignored ``crosswalks`` folder
(:func:`deid.paths.crosswalks_dir`) - never into the de-identified output folder,
which is the thing that gets shared - and must be kept secure and separate.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from . import paths
from .project import _slug

# (row-dict key, CSV header). The first four are the core request; the rest are
# there to disambiguate and verify re-identification - trim freely.
CROSSWALK_COLUMNS: list[tuple[str, str]] = [
    ("original_patient_id", "Original Patient ID"),
    ("new_patient_id", "New Patient ID"),
    ("original_accession", "Original Accession"),
    ("new_accession", "New Accession"),
    ("original_patient_name", "Original Patient Name"),
    ("original_study_date", "Original Study Date"),
    ("original_study_uid", "Original Study UID"),
    ("new_study_uid", "New Study UID"),
]


def write_crosswalk_csv(rows, path, columns=CROSSWALK_COLUMNS) -> int:
    """Write ``rows`` (list of dicts) to ``path`` as a CSV. Returns row count."""
    keys = [k for k, _ in columns]
    headers = [h for _, h in columns]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(headers)
        for r in rows:
            w.writerow([r.get(k, "") for k in keys])
    return len(rows)


def default_path(project_name: str, when: datetime | None = None) -> Path:
    """A timestamped path in the local crosswalks folder for this project."""
    ts = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return paths.crosswalks_dir() / f"{_slug(project_name)}_{ts}.csv"
