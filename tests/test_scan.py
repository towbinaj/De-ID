"""Pre-run folder scan + the patient-separation guardrail."""

from __future__ import annotations

from pydicom.uid import generate_uid

from deid.engine import scan
from deid.project import Project, ProjectStore
from conftest import make_ct, save


def _folder(tmp_path, specs):
    """Write CTs described by ``specs`` (dicts of overrides) to tmp_path."""
    paths = []
    for i, over in enumerate(specs):
        p = tmp_path / f"f{i}.dcm"
        save(make_ct(**over), p)
        paths.append(p)
    return paths


def test_scan_counts_distinct_patients(tmp_path):
    files = _folder(tmp_path, [
        {"PatientID": "A", "StudyInstanceUID": "1.1"},
        {"PatientID": "A", "StudyInstanceUID": "1.1"},   # same patient+study
        {"PatientID": "B", "StudyInstanceUID": "1.2"},
        {"PatientID": "C", "StudyInstanceUID": "1.3", "Modality": "SC"},
    ])
    s = scan.scan_files(files)
    assert s.files == 4
    assert s.patient_count == 3            # A, B, C
    assert s.study_count == 3
    assert s.modalities.get("CT") == 3 and s.modalities.get("SC") == 1


def test_scan_blank_id_bucketed_but_names_separate(tmp_path):
    files = _folder(tmp_path, [
        {"PatientID": "", "PatientName": "X"},
        {"PatientID": "", "PatientName": "Y"},   # no ID, different name
    ])
    s = scan.scan_files(files)
    assert s.blank_id == 2
    assert s.patient_count == 2              # fell back to name


def test_scan_estimates_drops(tmp_path):
    files = _folder(tmp_path, [
        {"PatientID": "A"},
        {"PatientID": "A", "Modality": "SC"},
    ])
    rules = [{"tag": "Modality", "op": "equals", "value": "SC",
              "action": "drop", "scope": "instance"}]
    s = scan.scan_files(files, drop_rules=rules)
    assert s.would_drop == 1


def test_separation_warns_when_patients_would_merge(tmp_path):
    # Basic Profile on, no pseudonyms -> every PatientID blanked -> merged.
    files = _folder(tmp_path, [{"PatientID": "A"}, {"PatientID": "B"}])
    s = scan.scan_files(files)
    p = Project(name="plain", basic_profile=True, pseudonymize=False)
    level, msg = scan.patient_separation(p, s)
    assert level == "warn" and "same blank ID" in msg


def test_separation_ok_with_per_patient_pseudonyms(tmp_path):
    files = _folder(tmp_path, [{"PatientID": "A"}, {"PatientID": "B"}])
    s = scan.scan_files(files)
    p = Project(name="psn", basic_profile=True, pseudonymize=True,
                pseudonym_tags=["PatientID"])
    level, msg = scan.patient_separation(p, s)
    assert level == "ok" and "2 distinct patients" in msg


def test_separation_single_patient_is_not_a_warning(tmp_path):
    files = _folder(tmp_path, [{"PatientID": "A"}, {"PatientID": "A"}])
    s = scan.scan_files(files)
    p = Project(name="plain", basic_profile=True, pseudonymize=False)
    level, _ = scan.patient_separation(p, s)
    assert level == "info"


def test_default_project_keeps_patients_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("DEID_DATA", str(tmp_path))
    default = ProjectStore().ensure_default()
    assert default.pseudonymize is True
    assert "PatientID" in default.pseudonym_tags
    # So a two-patient folder is reported OK, not merged.
    files = _folder(tmp_path, [{"PatientID": "A"}, {"PatientID": "B"}])
    s = scan.scan_files(files)
    level, _ = scan.patient_separation(default, s)
    assert level == "ok"
