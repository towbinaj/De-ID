"""Persistent pseudonym crosswalk (local SQLite).

The same original value (a PatientID, AccessionNumber, PatientName, …) always
maps to the same pseudonym across runs, batches, and days - so a patient keeps a
stable study id in a research cohort, and you (and only you) can re-identify from
the local index. Two assignment modes:

* ``sequential`` - human-friendly running numbers, e.g. ``CASE-000123``.
* ``hash``       - a salted, deterministic short hash (per-database salt), so
  the pseudonym is stable but not a guessable counter.

This file lives in the git-ignored ``De-ID-data`` folder and contains PHI (the
original → pseudonym mapping). It never belongs in the repository and is never
sent anywhere by the app.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from datetime import datetime

from pydicom.uid import generate_uid

from .. import paths

# Human-readable prefixes for sequential pseudonyms, by DICOM keyword.
_PREFIXES = {
    "PatientID": "ID",
    "PatientName": "CASE",
    "AccessionNumber": "ACC",
    "StudyID": "STU",
    "OtherPatientIDs": "OID",
}
_DEFAULT_PREFIX = "PSN"
_UID_KIND = "__uid__"


def _prefix_for(kind: str) -> str:
    return _PREFIXES.get(kind, _DEFAULT_PREFIX)


class PseudonymIndex:
    """Thread-safe SQLite crosswalk. One instance per app is plenty."""

    def __init__(self, path=None):
        self.path = str(path or paths.pseudonym_db_path())
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure()

    def _ensure(self) -> None:
        with self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS mappings ("
                " project TEXT NOT NULL, kind TEXT NOT NULL,"
                " original TEXT NOT NULL, pseudonym TEXT NOT NULL,"
                " created TEXT NOT NULL,"
                " PRIMARY KEY (project, kind, original))")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS counters ("
                " project TEXT NOT NULL, kind TEXT NOT NULL, n INTEGER NOT NULL,"
                " PRIMARY KEY (project, kind))")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,"
                " value TEXT NOT NULL)")
            cur = self._conn.execute("SELECT value FROM meta WHERE key='salt'")
            if cur.fetchone() is None:
                self._conn.execute(
                    "INSERT INTO meta(key, value) VALUES('salt', ?)",
                    (os.urandom(16).hex(),))

    def _salt(self) -> str:
        cur = self._conn.execute("SELECT value FROM meta WHERE key='salt'")
        row = cur.fetchone()
        return row[0] if row else ""

    # -- assignment ------------------------------------------------------
    def _existing(self, project: str, kind: str, original: str):
        cur = self._conn.execute(
            "SELECT pseudonym FROM mappings WHERE project=? AND kind=? "
            "AND original=?", (project, kind, original))
        row = cur.fetchone()
        return row[0] if row else None

    def _next_counter(self, project: str, kind: str) -> int:
        cur = self._conn.execute(
            "SELECT n FROM counters WHERE project=? AND kind=?",
            (project, kind))
        row = cur.fetchone()
        n = (row[0] if row else 0) + 1
        self._conn.execute(
            "INSERT INTO counters(project, kind, n) VALUES(?,?,?) "
            "ON CONFLICT(project, kind) DO UPDATE SET n=excluded.n",
            (project, kind, n))
        return n

    def pseudonym(self, project: str, kind: str, original: str,
                  mode: str = "sequential", *, prefix: str = "",
                  separator: str = "_", pad: int = 0) -> str:
        """Return the stable pseudonym for ``original`` (assigning on first use).

        Modes:

        * ``sequential`` → ``<KIND>-000001`` (fixed 6-digit running number),
        * ``hash``       → ``<KIND>-<salted hash>``,
        * ``custom``     → ``<prefix><separator><n>`` with ``n`` zero-padded to
          ``pad`` digits, e.g. ``TRIAL_0001`` (``prefix`` is typically the trial
          name; the counter runs per kind, so patients and accessions number
          independently from 1). When ``prefix`` is empty the kind label is used.

        Empty/blank originals are never mapped (returns "").
        """
        original = (original or "").strip()
        if not original:
            return ""
        with self._lock:
            found = self._existing(project, kind, original)
            if found is not None:
                return found
            if mode == "hash":
                digest = hashlib.sha256(
                    f"{self._salt()}|{project}|{kind}|{original}".encode()
                ).hexdigest()[:10].upper()
                pseudonym = f"{_prefix_for(kind)}-{digest}"
            elif mode == "custom":
                n = self._next_counter(project, kind)
                label = (prefix or "").strip() or _prefix_for(kind)
                sep = separator if separator is not None else "_"
                num = f"{n:0{pad}d}" if pad and pad > 0 else str(n)
                pseudonym = f"{label}{sep}{num}"
            else:
                n = self._next_counter(project, kind)
                pseudonym = f"{_prefix_for(kind)}-{n:06d}"
            with self._conn:
                self._conn.execute(
                    "INSERT INTO mappings(project, kind, original, pseudonym,"
                    " created) VALUES(?,?,?,?,?)",
                    (project, kind, original, pseudonym,
                     datetime.now().isoformat(timespec="seconds")))
            return pseudonym

    def uid(self, project: str, original: str) -> str:
        """Stable replacement UID for ``original`` (persisted across runs)."""
        original = (original or "").strip()
        if not original:
            return ""
        with self._lock:
            found = self._existing(project, _UID_KIND, original)
            if found is not None:
                return found
            new_uid = generate_uid()
            with self._conn:
                self._conn.execute(
                    "INSERT INTO mappings(project, kind, original, pseudonym,"
                    " created) VALUES(?,?,?,?,?)",
                    (project, _UID_KIND, original, new_uid,
                     datetime.now().isoformat(timespec="seconds")))
            return new_uid

    # -- lookups / maintenance ------------------------------------------
    def lookup(self, project: str, kind: str, original: str):
        with self._lock:
            return self._existing(project, kind, (original or "").strip())

    def reverse(self, project: str, pseudonym: str) -> list:
        """Re-identify: original value(s) behind a pseudonym, as (kind, original)."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT kind, original FROM mappings WHERE project=? AND "
                "pseudonym=?", (project, pseudonym))
            return [(r[0], r[1]) for r in cur.fetchall()]

    def entries(self, project: str, query: str = "", limit: int = 500,
                include_uids: bool = False) -> list:
        """Rows (kind, original, pseudonym, created) for the browser view."""
        sql = ("SELECT kind, original, pseudonym, created FROM mappings "
               "WHERE project=?")
        args: list = [project]
        if not include_uids:
            sql += " AND kind<>?"
            args.append(_UID_KIND)
        if query:
            sql += " AND (original LIKE ? OR pseudonym LIKE ?)"
            args += [f"%{query}%", f"%{query}%"]
        sql += " ORDER BY kind, pseudonym LIMIT ?"
        args.append(limit)
        with self._lock:
            return [tuple(r) for r in self._conn.execute(sql, args).fetchall()]

    def count(self, project: str, include_uids: bool = False) -> int:
        sql = "SELECT COUNT(*) FROM mappings WHERE project=?"
        args: list = [project]
        if not include_uids:
            sql += " AND kind<>?"
            args.append(_UID_KIND)
        with self._lock:
            return int(self._conn.execute(sql, args).fetchone()[0])

    def clear(self, project: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM mappings WHERE project=?", (project,))
            self._conn.execute("DELETE FROM counters WHERE project=?", (project,))

    def export_csv(self, project: str, path, include_uids: bool = False) -> int:
        """Write the re-id crosswalk to ``path`` (local only; contains PHI)."""
        import csv
        rows = self.entries(project, limit=10_000_000,
                            include_uids=include_uids)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["kind", "original", "pseudonym", "created"])
            w.writerows(rows)
        return len(rows)

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass


class ProjectPseudonymizer:
    """Adapter the engine uses: binds an index to one project + policy."""

    def __init__(self, index: PseudonymIndex, project: str,
                 tags: list, mode: str = "sequential", *,
                 prefix: str = "", separator: str = "_", pad: int = 0):
        self.index = index
        self.project = project
        self.tags = list(tags or [])
        self.mode = mode or "sequential"
        self.prefix = prefix or ""
        self.separator = separator if separator is not None else "_"
        self.pad = int(pad or 0)

    def pseudonym_for(self, kind: str, original: str) -> str:
        return self.index.pseudonym(self.project, kind, original, self.mode,
                                    prefix=self.prefix,
                                    separator=self.separator, pad=self.pad)

    def uid_for(self, original: str) -> str:
        return self.index.uid(self.project, original)
