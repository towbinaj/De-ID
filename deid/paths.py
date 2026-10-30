"""Filesystem locations for De-ID.

The app is designed to run as a portable, double-click executable (a PyInstaller
``onedir`` folder). Config, projects, the pseudonym index, logs, and quarantined
originals live in a ``De-ID-data`` folder next to the executable when that
location is writable (portable mode), otherwise they fall back to the user's
%APPDATA% (installed mode). This keeps a copied-to-a-USB-stick deployment
self-contained while still working when installed to a read-only Program Files.

Everything written here is local and git-ignored - it can contain PHI (the
pseudonym crosswalk, quarantined objects) and site config, so it must never
reach the public repository.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DATA_FOLDER_NAME = "De-ID-data"
ENV_OVERRIDE = "DEID_DATA"


def is_frozen() -> bool:
    """True when running from a PyInstaller-built executable."""
    return getattr(sys, "frozen", False)


def app_dir() -> Path:
    """Directory containing the running program (exe dir, or project root)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # deid/paths.py -> project root is two levels up
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """Directory holding bundled read-only resources (icons, OCR models).

    Under PyInstaller onedir this is the exe's folder; onefile would use
    ``sys._MEIPASS``.
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return Path(__file__).resolve().parent.parent


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def data_dir() -> Path:
    """Directory for user config, projects, the pseudonym index, and logs.

    Prefers a portable folder next to the executable; falls back to %APPDATA%.
    Can be overridden with the ``DEID_DATA`` environment variable (used by tests).
    """
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        d = Path(override)
        d.mkdir(parents=True, exist_ok=True)
        return d

    portable = app_dir() / DATA_FOLDER_NAME
    if _writable(portable):
        return portable

    base = os.environ.get("APPDATA") or str(Path.home())
    fallback = Path(base) / "De-ID"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def config_path() -> Path:
    return data_dir() / "settings.json"


def destinations_path() -> Path:
    return data_dir() / "destinations.json"


def anon_profiles_path() -> Path:
    return data_dir() / "anon_profiles.json"


def projects_dir() -> Path:
    """Folder holding saved de-id Project JSON files (one per project)."""
    d = data_dir() / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pseudonym_db_path() -> Path:
    """SQLite file for the persistent pseudonym crosswalk (re-id, local only)."""
    return data_dir() / "pseudonyms.sqlite3"


def quarantine_dir() -> Path:
    """Where object-drop rules move quarantined originals (kept, not deleted)."""
    d = data_dir() / "quarantine"
    d.mkdir(parents=True, exist_ok=True)
    return d


def crosswalks_dir() -> Path:
    """Per-run re-identification crosswalk CSVs (PHI — local only, never shared).

    Deliberately NOT the de-identified output folder: this is the key that maps
    pseudonyms back to real patients, so it must never travel with the output.
    """
    d = data_dir() / "crosswalks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pixelscan_dir() -> Path:
    """Reports from the burned-in-PHI pixel scan (PHI — local only, never shared).

    The report lists the actual burned-in text found, so it is PHI and stays in
    the local, git-ignored data folder like the re-id crosswalks.
    """
    d = data_dir() / "pixelscan"
    d.mkdir(parents=True, exist_ok=True)
    return d


def reports_dir() -> Path:
    """Folder for per-run processing reports (folder status, failures)."""
    d = data_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d
