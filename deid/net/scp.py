"""Inbox Storage SCP: receive C-STORE objects and write them, UNMODIFIED, to a
local inbox folder for the queue worker to de-identify.

Receiving and de-identifying are deliberately decoupled: raw (PHI-bearing)
objects land only in the local, git-ignored inbox, and the queue worker picks
them up, de-identifies against the active project, and exports the result. This
also lets a plain watch-folder feed the same pipeline.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pynetdicom import AE, evt, AllStoragePresentationContexts
from pynetdicom.sop_class import Verification

from ..logging_setup import get_logger

log = get_logger("net.scp")


@dataclass
class ReceiverStats:
    listening: bool = False
    port: int = 0
    aetitle: str = ""
    received: int = 0
    failed: int = 0
    started_at: float = 0.0
    recent: deque = field(default_factory=lambda: deque(maxlen=20))


class InboxSCP:
    """A non-blocking Storage SCP that writes raw objects into ``inbox_dir``."""

    def __init__(self, aetitle: str, port: int, inbox_dir, *,
                 on_receive: Callable[[Path], None] | None = None,
                 ssl_context=None, bind_host: str = "127.0.0.1",
                 allowed_aets=None):
        self.aetitle = aetitle
        self.port = int(port)
        self.inbox_dir = Path(inbox_dir)
        self.on_receive = on_receive
        self.ssl_context = ssl_context
        # Bind loopback by default so the inbox is not reachable from other
        # machines unless the operator opts in (set "0.0.0.0" for all
        # interfaces). ``allowed_aets`` restricts which calling AE titles may
        # associate; empty/None accepts any peer.
        self.bind_host = str(bind_host or "127.0.0.1").strip()
        self.allowed_aets = [a.strip() for a in (allowed_aets or []) if a.strip()]
        self.stats = ReceiverStats(port=self.port, aetitle=aetitle)
        self._server = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        ae = AE(ae_title=self.aetitle)
        if self.allowed_aets:
            # Only these calling AE titles may open an association.
            ae.require_calling_aet = list(self.allowed_aets)
        ae.add_supported_context(Verification)
        for cx in AllStoragePresentationContexts:
            ae.add_supported_context(cx.abstract_syntax)
        handlers = [(evt.EVT_C_STORE, self._handle_store),
                    (evt.EVT_C_ECHO, lambda e: 0x0000)]
        if self.bind_host not in ("127.0.0.1", "::1", "localhost") \
                and not self.allowed_aets:
            log.warning("Inbox SCP binding %s with no calling-AE allowlist - "
                        "any peer that can reach port %d may send objects.",
                        self.bind_host, self.port)
        self._server = ae.start_server(
            (self.bind_host, self.port), block=False, evt_handlers=handlers,
            ssl_context=self.ssl_context)
        self.stats.listening = True
        self.stats.started_at = time.time()
        log.info("Inbox SCP listening on %s:%d as %s (allowlist: %s)",
                 self.bind_host, self.port, self.aetitle,
                 ", ".join(self.allowed_aets) if self.allowed_aets else "any")

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        self.stats.listening = False
        log.info("Inbox SCP stopped.")

    @property
    def listening(self) -> bool:
        return self.stats.listening

    def _handle_store(self, event):
        try:
            ds = event.dataset
            ds.file_meta = event.file_meta
            sop = str(getattr(ds, "SOPInstanceUID", "") or uuid.uuid4().hex)
            # Flat inbox; a unique suffix guards against SOP UID collisions.
            dest = self.inbox_dir / f"{sop}_{uuid.uuid4().hex[:8]}.dcm"
            ds.save_as(str(dest), enforce_file_format=True)
            with self._lock:
                self.stats.received += 1
                # Modality only in the event feed - never patient identifiers.
                self.stats.recent.appendleft(
                    (time.strftime("%H:%M:%S"),
                     str(getattr(ds, "Modality", "?"))))
            if self.on_receive:
                try:
                    self.on_receive(dest)
                except Exception:  # noqa: BLE001
                    log.exception("on_receive callback failed")
            return 0x0000
        except Exception as exc:  # noqa: BLE001
            log.exception("Failed to store incoming object: %s", exc)
            with self._lock:
                self.stats.failed += 1
            return 0xA700
