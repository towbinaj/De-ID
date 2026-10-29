"""The receive → auto-de-identify → export queue.

Ties together the inbox SCP (and/or a watch-folder), the de-id engine, and the
C-STORE exporter. A background worker drains pending objects: each is
de-identified against the ACTIVE project (profile + drop rules + pseudonyms) to
the output folder, then optionally exported to a destination. Objects that
arrived in our own inbox are deleted after processing; files picked up from a
user watch-folder are left in place (only marked as seen).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from . import paths
from .logging_setup import get_logger
from .engine import fileops, fileproc
from .engine.pseudonyms import ProjectPseudonymizer
from .net import scu
from .net.scp import InboxSCP
from .project import _slug

log = get_logger("queue")

# Don't touch a file modified within this window - it may still be being written.
_STABLE_SECONDS = 1.0


class QueueController:
    def __init__(self, *, settings, pseudonyms, project_provider: Callable,
                 destination_provider: Callable,
                 on_event: Callable[[dict], None] | None = None,
                 poll_interval: float = 1.5):
        self.settings = settings
        self.pseudonyms = pseudonyms
        self.project_provider = project_provider
        self.destination_provider = destination_provider
        self.on_event = on_event
        self.poll_interval = poll_interval

        self.scp: InboxSCP | None = None
        self.inbox_dir: Path | None = None
        self.output_dir: Path | None = None
        self.watch_dirs: list[Path] = []
        self._seen: set[str] = set()
        self._pending: list[Path] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.running = False
        self.counts = {"processed": 0, "written": 0, "dropped": 0,
                       "quarantined": 0, "failed": 0, "exported": 0,
                       "export_failed": 0}

    # -- lifecycle -------------------------------------------------------
    def start(self, *, inbox_dir, output_dir, watch_dirs=(), enable_scp=True,
              aetitle="DEID_SCP", port=11112) -> None:
        if self.running:
            return
        self.inbox_dir = Path(inbox_dir)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir = Path(output_dir) if output_dir else None
        self.watch_dirs = [Path(w) for w in watch_dirs if w]
        self._seen = set()
        self._pending = []
        self._stop.clear()
        if enable_scp:
            self.scp = InboxSCP(aetitle, port, self.inbox_dir,
                                on_receive=self._enqueue)
            self.scp.start()
        self.running = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        self._emit("status", f"Queue started (inbox {self.inbox_dir}).")

    def stop(self) -> None:
        self._stop.set()
        if self.scp is not None:
            self.scp.stop()
            self.scp = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self.running = False
        self._emit("status", "Queue stopped.")

    # -- intake ----------------------------------------------------------
    def _enqueue(self, path) -> None:
        with self._lock:
            self._pending.append(Path(path))

    def _scan(self) -> None:
        """Pick up new files from the inbox and any watch folders."""
        now = time.time()
        dirs = ([self.inbox_dir] if self.inbox_dir else []) + self.watch_dirs
        for d in dirs:
            if not d or not d.exists():
                continue
            for f in d.rglob("*"):
                if not f.is_file() or f.suffix.lower() in (
                        ".json", ".log", ".csv", ".txt"):
                    continue
                key = str(f)
                if key in self._seen:
                    continue
                try:
                    if now - f.stat().st_mtime < _STABLE_SECONDS:
                        continue        # still being written
                except OSError:
                    continue
                if fileops.looks_like_dicom(f):
                    self._seen.add(key)
                    self._enqueue(f)

    def _drain(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                if not self._pending:
                    return
                path = self._pending.pop(0)
            self._process(path)

    # -- processing ------------------------------------------------------
    def _owned(self, path: Path) -> bool:
        """True if ``path`` is in our inbox (safe to delete after processing)."""
        try:
            return self.inbox_dir is not None and \
                self.inbox_dir.resolve() in path.resolve().parents
        except OSError:
            return False

    def _process(self, path: Path) -> None:
        proj = self.project_provider()
        if proj is None:
            return
        cfg = proj.to_receiver_config()
        pseudonymizer = None
        if proj.pseudonymize and proj.pseudonym_tags:
            pseudonymizer = ProjectPseudonymizer(
                self.pseudonyms, proj.name, proj.pseudonym_tags,
                proj.pseudonym_mode)
        outdir = (self.output_dir or
                  paths.data_dir() / "queue-out" / _slug(proj.name))
        qroot = paths.quarantine_dir() / _slug(proj.name)
        try:
            res = fileproc.deidentify_files(
                [path], outdir, cfg, on_item=lambda *a: None,
                pseudonymizer=pseudonymizer, drop_rules=proj.drop_rules,
                quarantine_root=qroot)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.counts["failed"] += 1
            self._emit("error", f"processing failed: {exc}")
            return

        with self._lock:
            self.counts["processed"] += 1
            self.counts["written"] += len(res["written"])
            self.counts["dropped"] += res["dropped"]
            self.counts["quarantined"] += res["quarantined"]
            self.counts["failed"] += res["failed"]

        if res["dropped"]:
            self._emit("dropped", "object dropped by rule")
        elif res["quarantined"]:
            self._emit("quarantined", "original set aside")
        elif res["written"]:
            self._export(res["written"])

        if self._owned(path):
            try:
                path.unlink()
            except OSError:
                pass

    def _export(self, written: list) -> None:
        node = self.destination_provider()
        if node is None:
            self._emit("processed", "de-identified (no export destination)")
            return
        tls_args = None
        if getattr(node, "tls", False):
            from .net.tls import client_context
            tls_args = (client_context(verify=False), node.host)
        try:
            sr = scu.c_store(self.settings.my_aetitle, node, written,
                             timeout=node.timeout, tls_args=tls_args)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.counts["export_failed"] += len(written)
            self._emit("error", f"export failed: {exc}")
            return
        with self._lock:
            self.counts["exported"] += sr.sent
            self.counts["export_failed"] += sr.failed
        self._emit("exported", f"→ {node.name}: sent {sr.sent}, "
                   f"failed {sr.failed}")

    # -- worker ----------------------------------------------------------
    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                self._scan()
                self._drain()
            except Exception:  # noqa: BLE001
                log.exception("Queue worker iteration failed")
            self._stop.wait(self.poll_interval)
        self._drain()   # final pass

    def _emit(self, kind: str, detail: str) -> None:
        if self.on_event:
            try:
                self.on_event({"time": time.strftime("%H:%M:%S"),
                               "kind": kind, "detail": detail})
            except Exception:  # noqa: BLE001
                pass

    def snapshot(self) -> dict:
        with self._lock:
            c = dict(self.counts)
        c["received"] = self.scp.stats.received if self.scp else 0
        c["listening"] = bool(self.scp and self.scp.listening)
        return c
