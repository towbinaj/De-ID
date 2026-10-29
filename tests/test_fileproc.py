"""End-to-end local file de-identification: scan -> process -> write."""

from __future__ import annotations

from pydicom import dcmread
from pydicom.uid import generate_uid

from deid.engine import fileops, fileproc
from deid.project import Project
from conftest import make_ct, save


def _write_study(folder, n=3):
    study = generate_uid()
    for i in range(n):
        ds = make_ct(StudyInstanceUID=study, PatientName="DOE^JANE",
                     PatientID="MRN42")
        save(ds, folder / f"img{i}.dcm")
    return study


def test_scan_finds_part10_files(tmp_path):
    _write_study(tmp_path, n=3)
    (tmp_path / "notes.txt").write_text("not dicom", encoding="utf-8")
    found = fileops.find_dicom_files(tmp_path)
    assert len(found) == 3


def test_deidentify_files_writes_and_anonymizes(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    _write_study(src, n=3)
    out = tmp_path / "out"
    files = fileops.find_dicom_files(src)

    seen = []
    cfg = Project(name="t", basic_profile=True).to_receiver_config()
    res = fileproc.deidentify_files(
        files, out, cfg,
        on_item=lambda i, t, p, ok, d: seen.append((p, ok)))

    assert res["ok"] == 3 and res["failed"] == 0
    written = list(out.glob("*.dcm"))
    assert len(written) == 3
    # Every written object is de-identified and consistently re-UID'd.
    studies = set()
    for w in written:
        ds = dcmread(str(w))
        assert str(ds.PatientName) == ""
        assert str(ds.PatientID) == ""
        assert str(ds.PatientIdentityRemoved) == "YES"
        studies.add(str(ds.StudyInstanceUID))
    assert len(studies) == 1          # one remapped StudyInstanceUID for all


def test_cancel_stops_early(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    _write_study(src, n=5)
    files = fileops.find_dicom_files(src)
    cfg = Project(name="t", basic_profile=True).to_receiver_config()
    # Cancel after the first item.
    state = {"n": 0}

    def cancel():
        return state["n"] >= 1

    def on_item(i, t, p, ok, d):
        state["n"] += 1

    res = fileproc.deidentify_files(files, tmp_path / "out", cfg, on_item,
                                    should_cancel=cancel)
    assert res["ok"] < 5              # stopped before finishing all five
