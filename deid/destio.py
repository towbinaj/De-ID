"""Import / export of export destinations as CSV or JSON.

Mirrors the DICOM Toolkit's destinations Export / Import file so the two apps can
exchange destination lists. The CSV uses the Toolkit's 6-column layout
(``name, aetitle, host, port, calling_aetitle, group``) with a header row; JSON
is the same shape as ``destinations.json``. De-ID only sends C-STORE, so only
storage-group rows are imported - worklist / HL7 rows in a Toolkit file are
ignored.

Everything here is the user's own local data (real AE titles / hosts). It is
written only to a path the user picks or the local, git-ignored data folder, and
never committed or sent anywhere.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from . import config
from .model import Node

# Groups that are C-STORE targets. A De-ID destination is always storage; a
# Toolkit file may also hold worklist / HL7 rows, which are not export targets.
_STORAGE_GROUPS = {"", "PACS / Storage"}
_CSV_HEADER = ["name", "aetitle", "host", "port", "calling_aetitle", "group"]


@dataclass
class ImportResult:
    """Outcome of reading a destinations file (before merging into saved)."""

    nodes: list[Node]
    warnings: list[str]
    skipped_nonstorage: int
    source: str


# -- export -----------------------------------------------------------------
def export_csv(nodes: list[Node], path) -> None:
    """Write destinations to a re-importable CSV (Toolkit-compatible header)."""
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(_CSV_HEADER)
        for n in nodes:
            writer.writerow([n.name, n.aetitle, n.host, n.port,
                             n.calling_aetitle, "PACS / Storage"])


def export_json(nodes: list[Node], path) -> None:
    """Write destinations as JSON (same shape as destinations.json)."""
    Path(path).write_text(
        json.dumps([n.to_dict() for n in nodes], indent=2), encoding="utf-8")


# -- import -----------------------------------------------------------------
def _node_from_cols(cols: list[str]) -> tuple[Node | None, bool]:
    """Build a Node from a CSV row. Returns (node, was_nonstorage).

    A header row or malformed row yields (None, False); a well-formed row whose
    group is worklist / HL7 yields (None, True) so the caller can count it.
    """
    if len(cols) < 4:
        return None, False
    name, aetitle, host, port = cols[0], cols[1], cols[2], cols[3]
    if not aetitle or not host:
        return None, False
    try:
        port_i = int(port)
    except ValueError:
        return None, False                 # includes the "port" header cell
    if not (1 <= port_i <= 65535):
        return None, False
    calling = cols[4].strip() if len(cols) >= 5 else ""
    group = cols[5].strip() if len(cols) >= 6 else ""
    if group not in _STORAGE_GROUPS:
        return None, True
    return Node(name=name or aetitle, aetitle=aetitle, host=host, port=port_i,
                calling_aetitle=calling), False


def import_csv_file(path) -> ImportResult:
    """Import destinations from a CSV file (with or without a header row)."""
    path = Path(path)
    nodes: list[Node] = []
    skipped = 0
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.reader(fh):
            if not row:
                continue
            node, nonstorage = _node_from_cols([c.strip() for c in row])
            if node is not None:
                nodes.append(node)
            elif nonstorage:
                skipped += 1
    return _finalize(nodes, skipped, path.name)


def import_json_file(path) -> ImportResult:
    """Import destinations from a destinations.json-style file."""
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("JSON must be a list of destinations.")
    nodes: list[Node] = []
    skipped = 0
    for d in raw:
        if not isinstance(d, dict):
            continue
        group = str(d.get("group", "") or "").strip()
        if group not in _STORAGE_GROUPS:
            skipped += 1
            continue
        nodes.append(Node.from_dict(d))    # Node.from_dict ignores the group key
    return _finalize(nodes, skipped, path.name)


def import_any(path) -> ImportResult:
    """Import from a .json or .csv file, chosen by extension (.csv otherwise)."""
    path = Path(path)
    if path.suffix.lower() == ".json":
        return import_json_file(path)
    return import_csv_file(path)


def _finalize(nodes: list[Node], skipped: int, source: str) -> ImportResult:
    """Drop nameless rows, dedupe on DICOM identity, collect validation hints."""
    unique: dict[tuple[str, str, int], Node] = {}
    for n in nodes:
        if not n.name:
            continue
        key = (n.aetitle.upper(), n.host, n.port)
        unique.setdefault(key, n)
    out = list(unique.values())
    warnings: list[str] = []
    for n in out:
        for problem in n.validate():
            warnings.append(f"{n.name}: {problem}")
    out.sort(key=lambda x: x.name.lower())
    return ImportResult(nodes=out, warnings=warnings,
                        skipped_nonstorage=skipped, source=source)


def merge_into_saved(result: ImportResult) -> tuple[int, int]:
    """Merge imported nodes into the saved destinations by DICOM identity.

    An entry whose (AE, host, port) already exists is skipped; a new entry whose
    friendly name collides with a different endpoint is disambiguated by host.
    Returns (added, skipped_existing).
    """
    existing = config.load_destinations()
    keys = {(n.aetitle.upper(), n.host, int(n.port)) for n in existing}
    names = {n.name for n in existing}
    added = skipped = 0
    for n in result.nodes:
        key = (n.aetitle.upper(), n.host, int(n.port))
        if key in keys:
            skipped += 1
            continue
        if n.name in names:
            n = Node.from_dict({**n.to_dict(), "name": f"{n.name} ({n.host})"})
        existing.append(n)
        keys.add(key)
        names.add(n.name)
        added += 1
    if added:
        config.save_destinations(existing)
    return added, skipped
