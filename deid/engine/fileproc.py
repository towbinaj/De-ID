"""Batch de-identification of local files to an output folder.

The core de-id loop shared by the Run view (local files) and, later, the
receive/export queue. One :class:`Processor` is reused across the whole batch so
UID remapping stays consistent within a study. Reports progress through an
``on_item(index, total, path, ok, detail)`` callback (the same signature the
progress widget expects).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from pydicom import dcmread

from ..logging_setup import get_logger
from .processing import Processor, ReceiverConfig

log = get_logger("engine.fileproc")

OnItem = Callable[[int, int, Path, bool, str], None]


def _out_name(ds, src: Path) -> str:
    """Output filename: the (possibly remapped) SOP Instance UID, else source."""
    uid = str(getattr(ds, "SOPInstanceUID", "") or "").strip()
    return f"{uid}.dcm" if uid else f"{src.stem}.dcm"


def deidentify_files(files: list[Path], out_dir: str | Path,
                     cfg: ReceiverConfig, on_item: OnItem,
                     should_cancel: Callable[[], bool] | None = None,
                     processor: Processor | None = None,
                     pseudonymizer=None) -> dict:
    """De-identify ``files`` into ``out_dir``. Returns {'ok','failed','written'}.

    ``processor`` may be supplied so a long-running queue can keep one UID map
    across calls; otherwise a fresh one is created for this batch. A
    ``pseudonymizer`` (``ProjectPseudonymizer``) enables the persistent
    consistent-pseudonym crosswalk.
    """
    proc = processor or Processor(cfg, pseudonymizer=pseudonymizer)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    total = len(files)
    ok = failed = 0
    written: list[str] = []
    for i, f in enumerate(files, 1):
        f = Path(f)
        if should_cancel and should_cancel():
            break
        try:
            ds = dcmread(str(f))
            proc.process(ds)
            dest = out / _out_name(ds, f)
            ds.save_as(str(dest), enforce_file_format=True)
            written.append(str(dest))
            ok += 1
            on_item(i, total, f, True, "de-identified")
        except Exception as exc:  # noqa: BLE001 - report, keep going
            failed += 1
            log.warning("De-id failed for %s: %s", f.name, exc)
            on_item(i, total, f, False, str(exc))
    return {"ok": ok, "failed": failed, "written": written}
