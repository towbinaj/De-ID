"""Batch de-identification of local files to an output folder.

The core de-id loop shared by the Run view (local files) and, later, the
receive/export queue. One :class:`Processor` is reused across the whole batch so
UID remapping stays consistent within a study. Object/series drop rules run
first: matching objects are dropped (omitted from the output) or quarantined
(original copied aside), never de-identified. Progress is reported through an
``on_item(index, total, path, ok, detail)`` callback.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from pydicom import dcmread

from .. import paths
from ..logging_setup import get_logger
from . import objectrules
from .processing import Processor, ReceiverConfig

log = get_logger("engine.fileproc")

OnItem = Callable[[int, int, Path, bool, str], None]


def _out_name(ds, src: Path) -> str:
    uid = str(getattr(ds, "SOPInstanceUID", "") or "").strip()
    return f"{uid}.dcm" if uid else f"{src.stem}.dcm"


def _quarantine(src: Path, root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    dest = root / src.name
    i = 1
    while dest.exists():
        dest = root / f"{src.stem}_{i}{src.suffix}"
        i += 1
    shutil.copy2(src, dest)


def _prescan_series(files: list[Path], series_rules) -> dict:
    """SeriesInstanceUID -> action for series any object of which matches."""
    drop: dict[str, str] = {}
    for f in files:
        try:
            hdr = dcmread(str(f), stop_before_pixels=True)
        except Exception:  # noqa: BLE001
            continue
        m = objectrules.evaluate(hdr, series_rules)
        if m is not None:
            suid = str(getattr(hdr, "SeriesInstanceUID", "") or "")
            if suid and suid not in drop:
                drop[suid] = m.action
    return drop


def deidentify_files(files: list[Path], out_dir: str | Path,
                     cfg: ReceiverConfig, on_item: OnItem,
                     should_cancel: Callable[[], bool] | None = None,
                     processor: Processor | None = None,
                     pseudonymizer=None, drop_rules=None,
                     quarantine_root: str | Path | None = None) -> dict:
    """De-identify ``files`` into ``out_dir``.

    Returns {'ok','failed','dropped','quarantined','written'}. A ``pseudonymizer``
    enables the persistent pseudonym crosswalk; ``drop_rules`` (list of
    :class:`objectrules.DropRule` or dicts) drop/quarantine whole objects/series.
    """
    rules = objectrules.parse_rules(drop_rules)
    inst_rules, series_rules = objectrules.split_by_scope(rules)
    drop_series = _prescan_series(files, series_rules) if series_rules else {}
    qroot = Path(quarantine_root) if quarantine_root else paths.quarantine_dir()

    proc = processor or Processor(cfg, pseudonymizer=pseudonymizer)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    total = len(files)
    ok = failed = dropped = quarantined = 0
    written: list[str] = []

    for i, f in enumerate(files, 1):
        f = Path(f)
        if should_cancel and should_cancel():
            break
        try:
            ds = dcmread(str(f))
            # Decide drop/quarantine: series scope first, then instance scope.
            suid = str(getattr(ds, "SeriesInstanceUID", "") or "")
            action = drop_series.get(suid)
            if action is None:
                m = objectrules.evaluate(ds, inst_rules)
                action = m.action if m is not None else None
            if action == "drop":
                dropped += 1
                on_item(i, total, f, True, "dropped by rule")
                continue
            if action == "quarantine":
                _quarantine(f, qroot)
                quarantined += 1
                on_item(i, total, f, True, "quarantined (original set aside)")
                continue

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

    return {"ok": ok, "failed": failed, "dropped": dropped,
            "quarantined": quarantined, "written": written}
