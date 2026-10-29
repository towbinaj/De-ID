"""Import configuration from the companion DICOM Toolkit app.

The Toolkit stores its config in a ``DICOMToolkit-data`` folder (destinations,
settings, and optionally anonymization profiles). De-ID can reuse those:

* export destinations  -> De-ID destinations (PACS/Storage entries only),
* app settings         -> the overlapping fields (My AE, bug email, appearance),
* anonymization profiles -> De-ID projects (flags + per-tag rules).

Everything read here is the user's own local data; it is written only into
De-ID's own local, git-ignored data folder - never committed or sent anywhere.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import config, paths
from .model import Node
from .project import Project, ProjectStore
from .engine.anon_profiles import AnonRule

# The Toolkit's storage-destination group label (others are worklist / HL7).
_STORAGE_GROUPS = {"", "PACS / Storage"}


def find_toolkit_data() -> Path | None:
    """Best guess at the Toolkit's data folder, or None (UI offers a picker)."""
    candidates = []
    env = os.environ.get("DCMTOOLKIT_DATA")
    if env:
        candidates.append(Path(env))
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "DICOMToolkit")
    # Siblings of the De-ID install (dev tree and a built Toolkit next door).
    base = paths.app_dir()
    for up in (base, base.parent, base.parent.parent):
        candidates.append(up / "DICOMToolkit" / "DICOMToolkit-data")
        candidates.append(up / "DICOMToolkit" / "dist" / "DICOMToolkit-data")
    for c in candidates:
        try:
            if c.is_dir() and any((c / f).is_file() for f in (
                    "destinations.json", "settings.json", "anon_profiles.json")):
                return c
        except OSError:
            continue
    return None


def _load_json(folder: Path, name: str):
    p = Path(folder) / name
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def import_destinations(folder) -> tuple[int, int]:
    """Merge the Toolkit's storage destinations. Returns (added, skipped)."""
    raw = _load_json(folder, "destinations.json")
    if not isinstance(raw, list):
        return 0, 0
    existing = config.load_destinations()
    names = {n.name for n in existing}
    added = skipped = 0
    for d in raw:
        if not isinstance(d, dict):
            continue
        group = str(d.get("group", "") or "").strip()
        if group not in _STORAGE_GROUPS:
            continue                       # worklist / HL7: not a C-STORE target
        node = Node.from_dict(d)           # Node.from_dict ignores the group key
        if not node.name or node.name in names:
            skipped += 1
            continue
        existing.append(node)
        names.add(node.name)
        added += 1
    if added:
        config.save_destinations(existing)
    return added, skipped


def import_settings(folder) -> list[str]:
    """Copy overlapping settings. Returns the list of applied field names."""
    raw = _load_json(folder, "settings.json")
    if not isinstance(raw, dict):
        return []
    s = config.Settings.load()
    applied = []
    for key in ("my_aetitle", "bug_report_email", "appearance", "ui_scale"):
        if key in raw and raw[key] not in (None, ""):
            setattr(s, key, raw[key])
            applied.append(key)
    if applied:
        s.save()
    return applied


def import_profiles(folder) -> int:
    """Map Toolkit anonymization profiles to De-ID projects. Returns added."""
    raw = _load_json(folder, "anon_profiles.json")
    if not isinstance(raw, list):
        return 0
    store = ProjectStore()
    existing = set(store.names())
    added = 0
    for p in raw:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        name = str(p["name"])
        if name in existing:
            continue
        rules = [AnonRule(**{k: r.get(k, "") for k in ("tag", "action", "value")})
                 for r in p.get("rules", []) if isinstance(r, dict)]
        proj = Project(
            name=name,
            basic_profile=bool(p.get("basic_profile", True)),
            remove_private=bool(p.get("remove_private", True)),
            date_mode="keep" if p.get("retain_dates") else "remove",
            rules=rules)
        store.save(proj)
        existing.add(name)
        added += 1
    return added


def import_all(folder, *, destinations=True, settings=True,
               profiles=True) -> dict:
    """Run the selected imports from ``folder``. Returns a summary dict."""
    out = {"folder": str(folder), "destinations_added": 0,
           "destinations_skipped": 0, "settings": [], "projects_added": 0}
    if destinations:
        out["destinations_added"], out["destinations_skipped"] = \
            import_destinations(folder)
    if settings:
        out["settings"] = import_settings(folder)
    if profiles:
        out["projects_added"] = import_profiles(folder)
    return out
