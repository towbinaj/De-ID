"""Queue controller: watch an inbox → de-identify → export, end to end."""

from __future__ import annotations

import time

from pydicom import dcmread

from deid.config import Settings
from deid.engine.pseudonyms import PseudonymIndex
from deid.project import Project
from deid.queue import QueueController
from conftest import make_ct, save, storage_scp


def _wait(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_queue_deidentifies_and_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("DEID_DATA", str(tmp_path))
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    out = tmp_path / "out"
    save(make_ct(PatientName="DOE^JANE", PatientID="MRN1"), inbox / "x.dcm")

    with storage_scp() as (node, received):
        proj = Project(name="Q", basic_profile=True)
        idx = PseudonymIndex(tmp_path / "p.sqlite3")
        ctrl = QueueController(
            settings=Settings(my_aetitle="DEID"), pseudonyms=idx,
            project_provider=lambda: proj,
            destination_provider=lambda: node,
            on_event=lambda e: None, poll_interval=0.2)
        ctrl.start(inbox_dir=inbox, output_dir=str(out), enable_scp=False)
        ok = _wait(lambda: ctrl.snapshot()["exported"] >= 1)
        ctrl.stop()
        idx.close()

    assert ok, "queue did not export within the timeout"
    snap = ctrl.snapshot()
    assert snap["processed"] >= 1 and snap["exported"] >= 1
    # Output written and de-identified.
    written = list(out.rglob("*.dcm"))
    assert written and str(dcmread(str(written[0])).PatientName) == ""
    # The exported object (captured by the SCP) is de-identified too.
    assert received and str(received[0].PatientName) == ""
    # The inbox original was consumed (we own the inbox).
    assert not list(inbox.glob("*.dcm"))


def test_queue_drops_and_does_not_export(tmp_path, monkeypatch):
    monkeypatch.setenv("DEID_DATA", str(tmp_path))
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    out = tmp_path / "out"
    sc = make_ct(PatientName="DOE^JANE")
    sc.Modality = "SC"
    save(sc, inbox / "sc.dcm")

    with storage_scp() as (node, received):
        from deid.engine.objectrules import DropRule
        proj = Project(name="Q", basic_profile=True,
                       drop_rules=[DropRule("Modality", "equals", "SC",
                                            "drop").to_dict()])
        idx = PseudonymIndex(tmp_path / "p.sqlite3")
        ctrl = QueueController(
            settings=Settings(my_aetitle="DEID"), pseudonyms=idx,
            project_provider=lambda: proj,
            destination_provider=lambda: node,
            on_event=lambda e: None, poll_interval=0.2)
        ctrl.start(inbox_dir=inbox, output_dir=str(out), enable_scp=False)
        ok = _wait(lambda: ctrl.snapshot()["dropped"] >= 1)
        ctrl.stop()
        idx.close()

    assert ok
    assert ctrl.snapshot()["exported"] == 0
    assert not received                      # nothing exported
    assert not list(out.rglob("*.dcm"))      # nothing written
