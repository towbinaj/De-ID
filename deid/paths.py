"""Filesystem locations for De-ID.

The built app is a PyInstaller ``onedir`` folder, distributed as a ZIP and
updated in place by replacing that whole folder (see ``updater``). So user data
must live **outside** the app folder, or a self-update would wipe it:

* **Built exe:** data lives in a stable per-user folder (``%LOCALAPPDATA%\\De-ID``
  on Windows), untouched by replacing the program folder. On first run of a build
  that uses this layout, an older portable ``De-ID-data`` folder sitting next to
  the exe is migrated over once (copied, never deleted).
* **From source (dev):** data stays in a portable ``De-ID-data`` folder in the
  project tree, so a checkout is self-contained and tests are unaffected.

Either location can be overridden with the ``DEID_DATA`` environment variable
(used by tests). Everything written here is local and git-ignored - it can
contain PHI (the pseudonym crosswalk, quarantined objects) and site config, so
it must never reach the public repository.
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


_portable_migration_done = False


def _portable_dir() -> Path:
    """The legacy portable data folder next to the running program."""
    return app_dir() / DATA_FOLDER_NAME


def _stable_user_dir() -> Path:
    """A stable per-user folder outside the app folder (survives updates)."""
    base = (os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or str(Path.home()))
    return Path(base) / "De-ID"


def _migrate_portable_once(stable: Path) -> None:
    """One-time carry-over of an older portable ``De-ID-data`` folder beside the
    exe (pre-stable-location layout). Runs only while the stable folder has no
    settings yet; copies entries that are not already there and never deletes
    the source, so it is safe to call repeatedly.
    """
    global _portable_migration_done
    if _portable_migration_done:
        return
    _portable_migration_done = True
    if (stable / "settings.json").exists():
        return
    old = _portable_dir()
    try:
        if not old.is_dir() or old.resolve() == stable.resolve():
            return
        entries = list(old.iterdir())
    except OSError:
        return
    import shutil
    for item in entries:
        dest = stable / item.name
        if dest.exists():
            continue
        try:
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        except OSError:
            continue


def data_dir() -> Path:
    """Directory for user config, projects, the pseudonym index, and logs.

    Built exe: a stable per-user folder (so a self-update, which replaces the
    app folder, never touches it), migrating an older portable folder once. From
    source: the portable folder in the project tree. ``DEID_DATA`` overrides both.
    """
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        d = Path(override)
        d.mkdir(parents=True, exist_ok=True)
        return d

    if is_frozen():
        stable = _stable_user_dir()
        stable.mkdir(parents=True, exist_ok=True)
        _migrate_portable_once(stable)
        return stable

    # Running from source (dev): keep a self-contained portable folder.
    portable = _portable_dir()
    if _writable(portable):
        return portable
    stable = _stable_user_dir()
    stable.mkdir(parents=True, exist_ok=True)
    return stable


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
