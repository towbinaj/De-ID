"""DICOM SCU operations used by De-ID: C-ECHO and C-STORE (export).

Lean subset of the DICOM Toolkit's SCU: enough to test a destination and to
C-STORE de-identified output to a research PACS/VNA. Synchronous; run on a
worker thread by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from pydicom import dcmread
from pynetdicom import AE
from pynetdicom.presentation import (StoragePresentationContexts,
                                     VerificationPresentationContexts,
                                     build_context)
from pynetdicom.sop_class import register_uid

from ..logging_setup import get_logger
from ..model import Node

log = get_logger("net.scu")

Progress = Callable[[str], None]
OnFile = Callable[[int, int, Path, bool, str], None]


def _noop(_: str) -> None:
    pass


@dataclass
class EchoResult:
    success: bool
    message: str


@dataclass
class StoreResult:
    sent: int = 0
    failed: int = 0
    warnings: int = 0
    errors: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.sent + self.failed


def c_echo(my_aetitle: str, node: Node, timeout: int = 30,
           progress: Progress = _noop, tls_args=None) -> EchoResult:
    ae = AE(ae_title=node.calling(my_aetitle))
    ae.requested_contexts = VerificationPresentationContexts
    ae.acse_timeout = ae.dimse_timeout = timeout
    assoc = ae.associate(node.host, node.port, ae_title=node.aetitle,
                         tls_args=tls_args)
    if not assoc.is_established:
        return EchoResult(False, "Association rejected / aborted / failed.")
    try:
        status = assoc.send_c_echo()
        if status and status.Status == 0x0000:
            return EchoResult(True, "Success - remote AE responded to C-ECHO.")
        code = f"0x{status.Status:04x}" if status else "no response"
        return EchoResult(False, f"C-ECHO returned status {code}.")
    finally:
        assoc.release()


_STD_STORAGE = {str(cx.abstract_syntax) for cx in StoragePresentationContexts}
_registered_private: set = set()


def _ensure_storage_registered(sop_uid: str) -> None:
    s = str(sop_uid)
    if s in _STD_STORAGE or s in _registered_private:
        return
    _registered_private.add(s)
    try:
        from pynetdicom.service_class import StorageServiceClass
        register_uid(s, f"PrivateStorage{len(_registered_private)}",
                     StorageServiceClass)
    except Exception:  # noqa: BLE001
        pass


def _contexts_for_files(files: list, progress: Progress = _noop) -> list:
    """Presentation contexts for the exact SOP classes + transfer syntaxes in
    the files, topped up with standard storage contexts (128-context cap)."""
    from pydicom.uid import ExplicitVRLittleEndian
    contexts: dict = {}
    for f in files:
        try:
            ds = dcmread(str(f), stop_before_pixels=True, force=True)
            fm = getattr(ds, "file_meta", None)
            sop = getattr(fm, "MediaStorageSOPClassUID", None) if fm else None
            ts = getattr(fm, "TransferSyntaxUID", None) if fm else None
            if not sop:
                sop = getattr(ds, "SOPClassUID", None)
            if not ts:
                ts = ExplicitVRLittleEndian
        except Exception:  # noqa: BLE001
            continue
        if not sop:
            continue
        key = (str(sop), str(ts))
        if key not in contexts and len(contexts) < 128:
            _ensure_storage_registered(sop)
            contexts[key] = build_context(sop, ts)
    ctxs = list(contexts.values())
    covered = {str(k[0]) for k in contexts}
    for cx in StoragePresentationContexts:
        if len(ctxs) >= 128:
            break
        if str(cx.abstract_syntax) not in covered:
            ctxs.append(cx)
            covered.add(str(cx.abstract_syntax))
    return ctxs


def c_store(my_aetitle: str, node: Node, files: Iterable[Path],
            progress: Progress = _noop, timeout: int = 30, tls_args=None,
            on_file: OnFile | None = None) -> StoreResult:
    """Send DICOM files via C-STORE over one association."""
    files = [Path(f) for f in files]
    result = StoreResult()
    total = len(files)
    if not files:
        result.errors.append("No files selected.")
        return result

    contexts = _contexts_for_files(files, progress)
    if not contexts:
        result.errors.append("No readable DICOM presentation contexts.")
        result.failed = total
        return result

    ae = AE(ae_title=node.calling(my_aetitle))
    ae.requested_contexts = contexts
    ae.acse_timeout = ae.dimse_timeout = ae.network_timeout = timeout
    assoc = ae.associate(node.host, node.port, ae_title=node.aetitle,
                         tls_args=tls_args)
    if not assoc.is_established:
        progress("Association rejected by the peer.")
        for i, f in enumerate(files, 1):
            if on_file:
                on_file(i, total, f, False, "association rejected")
        result.failed = total
        result.errors.append("Association rejected.")
        return result
    try:
        for i, f in enumerate(files, 1):
            ok, code = False, "error"
            try:
                ds = dcmread(str(f), force=True)
                if "SOPClassUID" not in ds or "SOPInstanceUID" not in ds:
                    raise ValueError("not a DICOM object (missing SOP UID)")
                status = assoc.send_c_store(ds)
                if status and status.Status == 0x0000:
                    ok, code = True, "0x0000"
                elif status and status.Status in (0xB000, 0xB006, 0xB007):
                    ok, code = True, f"0x{status.Status:04x}"
                    result.warnings += 1
                else:
                    code = f"0x{status.Status:04x}" if status else "no-response"
            except Exception as exc:  # noqa: BLE001
                code = str(exc).strip().replace("\n", " ")[:200] or "error"
            if ok:
                result.sent += 1
            else:
                result.failed += 1
                if len(result.errors) < 5000:
                    result.errors.append(f"{f}: {code}")
            if on_file:
                on_file(i, total, f, ok, code)
    finally:
        assoc.release()
    progress(f"Done. Sent {result.sent:,}, failed {result.failed:,}.")
    return result
