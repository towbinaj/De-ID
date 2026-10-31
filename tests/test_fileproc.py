"""End-to-end local file de-identification: scan -> process -> write."""

from __future__ import annotations

from pathlib import Path

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
    written = list(out.rglob("*.dcm"))
    assert len(written) == 3
    # Output is organized by de-identified IDs, not dumped flat.
    assert not list(out.glob("*.dcm"))     # nothing sits at the top level
    studies = set()
    for w in written:
        rel = w.relative_to(out)
        assert len(rel.parts) == 4         # <pid>/<study>/<series>/<sop>.dcm
        ds = dcmread(str(w))
        assert str(ds.PatientName) == ""
        assert str(ds.PatientID) == ""
        assert str(ds.PatientIdentityRemoved) == "YES"
        assert rel.name == f"{ds.SOPInstanceUID}.dcm"
        assert rel.parts[2] == str(ds.SeriesInstanceUID)
        studies.add(str(ds.StudyInstanceUID))
    assert len(studies) == 1          # one remapped StudyInstanceUID for all


def test_out_relpath_groups_by_deidentified_ids():
    ds = make_ct(PatientID="ID-000007", StudyInstanceUID="1.2.3",
                 SeriesInstanceUID="1.2.3.4", sop_instance="1.2.3.4.5")
    rel = fileproc._out_relpath(ds, Path("orig/whatever.dcm"))
    assert rel == Path("ID-000007") / "1.2.3" / "1.2.3.4" / "1.2.3.4.5.dcm"


def test_out_relpath_defaults_when_ids_missing():
    ds = make_ct(PatientID="", StudyInstanceUID="", SeriesInstanceUID="")
    rel = fileproc._out_relpath(ds, Path("x/orig.dcm"))
    assert rel.parts[:3] == ("unknown-patient", "unknown-study",
                             "unknown-series")
    assert rel.name.endswith(".dcm")


def test_out_relpath_sanitizes_components():
    ds = make_ct(PatientID="a/b c:*", StudyInstanceUID="1.1",
                 SeriesInstanceUID="1.2", sop_instance="1.3")
    rel = fileproc._out_relpath(ds, Path("x/o.dcm"))
    assert rel == Path("a_b_c__") / "1.1" / "1.2" / "1.3.dcm"


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
