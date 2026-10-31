"""Object/series drop + quarantine rules, and their batch integration."""

from __future__ import annotations

from pydicom import dcmread
from pydicom.uid import generate_uid, SecondaryCaptureImageStorage

from deid.engine import objectrules as orl
from deid.engine import fileops, fileproc
from deid.project import Project
from conftest import make_ct, save


def _sc(**over):
    return make_ct(Modality="SC", SOPClassUID=SecondaryCaptureImageStorage,
                   **over)


def test_rule_matches_operators():
    ds = make_ct(Modality="SC")
    ds.SeriesDescription = "Scanned Consent Form"
    assert orl.rule_matches(ds, orl.DropRule("Modality", "equals", "sc"))
    assert not orl.rule_matches(ds, orl.DropRule("Modality", "equals", "CT"))
    assert orl.rule_matches(ds, orl.DropRule("Modality", "not_equals", "CT"))
    assert orl.rule_matches(ds, orl.DropRule("SeriesDescription", "contains",
                                             "consent"))
    assert orl.rule_matches(ds, orl.DropRule("SeriesDescription", "regex",
                                             r"scan|paper"))
    assert orl.rule_matches(ds, orl.DropRule("Modality", "in", "US|SC|MG"))
    assert orl.rule_matches(ds, orl.DropRule("Modality", "exists", ""))
    assert orl.rule_matches(ds, orl.DropRule("BodyPartExamined", "absent", ""))


def test_evaluate_returns_first_match():
    ds = _sc()
    rules = [orl.DropRule("Modality", "equals", "MR"),
             orl.DropRule("Modality", "equals", "SC", "quarantine")]
    m = orl.evaluate(ds, rules)
    assert m is not None and m.action == "quarantine"


def test_fileproc_drops_instances(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    save(make_ct(PatientName="DOE^JANE"), src / "ct.dcm")
    save(_sc(PatientName="DOE^JANE"), src / "sc.dcm")
    files = fileops.find_dicom_files(src)
    cfg = Project(name="t", basic_profile=True).to_receiver_config()
    rules = [orl.DropRule("Modality", "equals", "SC", "drop").to_dict()]
    res = fileproc.deidentify_files(files, tmp_path / "out", cfg,
                                    on_item=lambda *a: None, drop_rules=rules)
    assert res["dropped"] == 1 and res["ok"] == 1
    written = list((tmp_path / "out").rglob("*.dcm"))   # output is nested by IDs
    assert len(written) == 1
    assert str(dcmread(str(written[0])).Modality) == "CT"


def test_fileproc_quarantines_original(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    save(_sc(PatientName="DOE^JANE"), src / "sc.dcm")
    files = fileops.find_dicom_files(src)
    cfg = Project(name="t", basic_profile=True).to_receiver_config()
    qdir = tmp_path / "quar"
    rules = [orl.DropRule("Modality", "equals", "SC", "quarantine").to_dict()]
    res = fileproc.deidentify_files(files, tmp_path / "out", cfg,
                                    on_item=lambda *a: None, drop_rules=rules,
                                    quarantine_root=qdir)
    assert res["quarantined"] == 1 and res["ok"] == 0
    assert not list((tmp_path / "out").rglob("*.dcm"))    # nothing written
    quarantined = list(qdir.glob("*.dcm"))
    assert len(quarantined) == 1
    # The quarantined copy is the ORIGINAL (not de-identified).
    assert str(dcmread(str(quarantined[0])).PatientName) == "DOE^JANE"


def test_series_scope_removes_whole_series(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    series = generate_uid()
    a = make_ct(SeriesInstanceUID=series)
    a.SeriesDescription = "SCANNED CONSENT"
    save(a, src / "a.dcm")
    b = make_ct(SeriesInstanceUID=series)           # no matching description
    save(b, src / "b.dcm")
    files = fileops.find_dicom_files(src)
    cfg = Project(name="t", basic_profile=True).to_receiver_config()
    rules = [orl.DropRule("SeriesDescription", "regex", r"scan|consent",
                          "drop", "series").to_dict()]
    res = fileproc.deidentify_files(files, tmp_path / "out", cfg,
                                    on_item=lambda *a: None, drop_rules=rules)
    # Both objects of the series go, even though only one carried the text.
    assert res["dropped"] == 2 and res["ok"] == 0
