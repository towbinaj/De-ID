"""Local file discovery for DICOM objects."""

from __future__ import annotations

import re
from pathlib import Path

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(value: str, default: str = "") -> str:
    """Make a DICOM-derived string safe to use as a path component."""
    s = _ILLEGAL.sub("_", str(value or default)).strip().strip(".")
    return s or default


def looks_like_dicom(p: Path) -> bool:
    """Cheap check: a Part-10 preamble ('DICM' at byte 128)."""
    try:
        with open(p, "rb") as fh:
            fh.seek(128)
            return fh.read(4) == b"DICM"
    except OSError:
        return False


def find_dicom_files(root: Path) -> list[Path]:
    """Recursively collect files under ``root`` that have a DICOM preamble.

    Skips our own sidecar files (``.json`` / ``.log`` / ``.csv``). Part-10 files
    without a preamble are rare in practice; this keeps discovery fast and avoids
    trying to parse every unrelated file in a folder.
    """
    out: list[Path] = []
    root = Path(root)
    if root.is_file():
        return [root] if looks_like_dicom(root) else []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() not in (".json", ".log", ".csv",
                                                    ".txt", ".zip"):
            if looks_like_dicom(p):
                out.append(p)
    return out
