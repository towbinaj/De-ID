"""Re-identification crosswalk: per-study rows from a run + CSV writer."""

from __future__ import annotations

import csv

from deid import crosswalk
from deid.engine import fileproc
from deid.engine.pseudonyms import PseudonymIndex
from deid.project import Project
from conftest import make_ct, save


def _noop(*a):
    pass


def _run(tmp_path, specs, proj):
    src = tmp_path / "in"
    src.mkdir()
    files = []
    for i, over in enumerate(specs):
        p = src / f"f{i}.dcm"
        save(make_ct(**over), p)
        files.append(p)
    idx = PseudonymIndex(tmp_path / "p.sqlite3")
    return fileproc.deidentify_files(
        files, tmp_path / "out", proj.to_receiver_config(), _noop,
        pseudonymizer=proj.build_pseudonymizer(idx),
        drop_rules=proj.drop_rules)


def test_crosswalk_one_row_per_study(tmp_path):
    proj = Project(name="Trial", basic_profile=True, pseudonymize=True,
                   pseudonym_tags=["PatientID", "AccessionNumber"],
                   pseudonym_mode="custom", pseudonym_prefix="TRIAL",
                   pseudonym_pad=4)
    # Patient A: two instances in ONE study -> one row. Patient B: one study.
    res = _run(tmp_path, [
        {"PatientID": "A", "AccessionNumber": "ACC1",
         "StudyInstanceUID": "1.1"},
        {"PatientID": "A", "AccessionNumber": "ACC1",
         "StudyInstanceUID": "1.1"},
        {"PatientID": "B", "AccessionNumber": "ACC2",
         "StudyInstanceUID": "1.2"},
    ], proj)
    rows = res["crosswalk"]
    assert len(rows) == 2                               # per study, deduped
    by_pid = {r["original_patient_id"]: r for r in rows}
    assert by_pid["A"]["new_patient_id"] == "TRIAL_0001"
    assert by_pid["A"]["original_accession"] == "ACC1"
    assert by_pid["A"]["new_accession"] == "TRIAL_0001"
    assert by_pid["B"]["new_patient_id"] == "TRIAL_0002"
    # Study UID was remapped, so original != new.
    assert by_pid["A"]["original_study_uid"] == "1.1"
    assert by_pid["A"]["new_study_uid"] != "1.1"


def test_crosswalk_multiple_studies_per_patient(tmp_path):
    proj = Project(name="T", basic_profile=True, pseudonymize=True,
                   pseudonym_tags=["PatientID", "AccessionNumber"])
    res = _run(tmp_path, [
        {"PatientID": "A", "AccessionNumber": "ACC1",
         "StudyInstanceUID": "2.1"},
        {"PatientID": "A", "AccessionNumber": "ACC2",
         "StudyInstanceUID": "2.2"},
    ], proj)
    rows = res["crosswalk"]
    assert len(rows) == 2                               # two studies -> two rows
    assert {r["original_patient_id"] for r in rows} == {"A"}
    assert {r["new_patient_id"] for r in rows} == {"ID-000001"}   # same patient
    assert {r["original_accession"] for r in rows} == {"ACC1", "ACC2"}


def test_write_crosswalk_csv_headers_and_values(tmp_path):
    rows = [{"original_patient_id": "A", "new_patient_id": "TRIAL_0001",
             "original_accession": "ACC1", "new_accession": "TRIAL_0001"}]
    path = tmp_path / "cw.csv"
    n = crosswalk.write_crosswalk_csv(rows, path)
    assert n == 1
    with open(path, newline="", encoding="utf-8") as fh:
        got = list(csv.reader(fh))
    assert got[0][:4] == ["Original Patient ID", "New Patient ID",
                          "Original Accession", "New Accession"]
    assert got[1][:4] == ["A", "TRIAL_0001", "ACC1", "TRIAL_0001"]


def test_default_path_is_local_crosswalks_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("DEID_DATA", str(tmp_path))
    p = crosswalk.default_path("My Trial")
    assert p.parent.name == "crosswalks"
    assert p.parent.parent == tmp_path
    assert p.suffix == ".csv"
