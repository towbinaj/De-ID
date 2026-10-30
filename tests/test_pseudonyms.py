"""Persistent pseudonym crosswalk + engine integration."""

from __future__ import annotations

from pydicom.uid import generate_uid

from deid.engine.pseudonyms import PseudonymIndex, ProjectPseudonymizer
from deid.engine.processing import Processor
from deid.project import Project
from conftest import make_ct


def test_sequential_assign_reuse_and_distinct(tmp_path):
    idx = PseudonymIndex(tmp_path / "p.sqlite3")
    a = idx.pseudonym("C", "PatientID", "MRN1")
    assert a == "ID-000001"
    assert idx.pseudonym("C", "PatientID", "MRN1") == a      # reuse
    assert idx.pseudonym("C", "PatientID", "MRN2") == "ID-000002"  # distinct
    # blanks are never mapped
    assert idx.pseudonym("C", "PatientID", "  ") == ""


def test_scoped_by_project(tmp_path):
    idx = PseudonymIndex(tmp_path / "p.sqlite3")
    assert idx.pseudonym("A", "PatientID", "MRN1") == "ID-000001"
    # a different project numbers independently
    assert idx.pseudonym("B", "PatientID", "MRN1") == "ID-000001"


def test_persistence_across_instances(tmp_path):
    db = tmp_path / "p.sqlite3"
    first = PseudonymIndex(db)
    p = first.pseudonym("C", "AccessionNumber", "ACC9")
    first.close()
    second = PseudonymIndex(db)
    assert second.pseudonym("C", "AccessionNumber", "ACC9") == p


def test_hash_mode_is_stable(tmp_path):
    idx = PseudonymIndex(tmp_path / "p.sqlite3")
    h1 = idx.pseudonym("C", "PatientID", "MRN1", mode="hash")
    assert h1.startswith("ID-")
    assert idx.pseudonym("C", "PatientID", "MRN1", mode="hash") == h1


def test_custom_mode_trial_scheme(tmp_path):
    idx = PseudonymIndex(tmp_path / "p.sqlite3")
    # TrialName_0001, zero-padded; counter runs per kind.
    a = idx.pseudonym("T", "PatientID", "MRN1", mode="custom",
                      prefix="ACRIN6698", separator="_", pad=4)
    assert a == "ACRIN6698_0001"
    assert idx.pseudonym("T", "PatientID", "MRN2", mode="custom",
                         prefix="ACRIN6698", pad=4) == "ACRIN6698_0002"
    assert idx.pseudonym("T", "PatientID", "MRN1", mode="custom",
                         prefix="ACRIN6698", pad=4) == a          # reuse
    # Accessions number independently from 1 under the same prefix.
    assert idx.pseudonym("T", "AccessionNumber", "ACC1", mode="custom",
                         prefix="ACRIN6698", pad=4) == "ACRIN6698_0001"


def test_custom_mode_pad_zero_is_unpadded(tmp_path):
    idx = PseudonymIndex(tmp_path / "p.sqlite3")
    assert idx.pseudonym("T", "PatientID", "X", mode="custom",
                         prefix="TRIAL", pad=0) == "TRIAL_1"


def test_project_build_pseudonymizer_uses_custom_scheme(tmp_path):
    idx = PseudonymIndex(tmp_path / "p.sqlite3")
    proj = Project(name="Trial", basic_profile=True, pseudonymize=True,
                   pseudonym_tags=["PatientID", "AccessionNumber"],
                   pseudonym_mode="custom", pseudonym_prefix="TRIAL",
                   pseudonym_pad=3)
    ps = proj.build_pseudonymizer(idx)
    assert ps is not None
    cfg = proj.to_receiver_config()
    out = Processor(cfg, pseudonymizer=ps).process(
        make_ct(PatientID="MRN1", AccessionNumber="ACC1"))
    assert str(out.PatientID) == "TRIAL_001"
    assert str(out.AccessionNumber) == "TRIAL_001"
    assert proj.pseudonym_example(2) == "TRIAL_002"


def test_build_pseudonymizer_none_when_off():
    proj = Project(name="x", pseudonymize=False)
    assert proj.build_pseudonymizer(object()) is None


def test_reverse_lookup(tmp_path):
    idx = PseudonymIndex(tmp_path / "p.sqlite3")
    ps = idx.pseudonym("C", "PatientName", "DOE^JANE")
    assert idx.reverse("C", ps) == [("PatientName", "DOE^JANE")]


def test_entries_exclude_uids_by_default(tmp_path):
    idx = PseudonymIndex(tmp_path / "p.sqlite3")
    idx.pseudonym("C", "PatientID", "MRN1")
    idx.uid("C", generate_uid())
    assert idx.count("C") == 1                 # uid not counted
    assert idx.count("C", include_uids=True) == 2


def test_engine_pseudonyms_consistent_across_runs(tmp_path):
    idx = PseudonymIndex(tmp_path / "p.sqlite3")
    proj = Project(name="Cohort", basic_profile=True, pseudonymize=True,
                   pseudonym_tags=["PatientID", "PatientName"])
    cfg = proj.to_receiver_config()
    ps = ProjectPseudonymizer(idx, proj.name, proj.pseudonym_tags,
                              proj.pseudonym_mode)

    ds1 = make_ct(PatientID="MRN1", PatientName="DOE^JANE")
    out1 = Processor(cfg, pseudonymizer=ps).process(ds1)
    assert str(out1.PatientID).startswith("ID-")
    assert str(out1.PatientName).startswith("CASE-")
    assert str(out1.PatientIdentityRemoved) == "YES"

    # A later, independent run (new Processor, same index) reuses the mapping.
    ds2 = make_ct(PatientID="MRN1", PatientName="DOE^JANE")
    out2 = Processor(cfg, pseudonymizer=ps).process(ds2)
    assert str(out2.PatientID) == str(out1.PatientID)
    assert str(out2.PatientName) == str(out1.PatientName)

    # A different patient gets a different pseudonym.
    ds3 = make_ct(PatientID="MRN2", PatientName="ROE^RICH")
    out3 = Processor(cfg, pseudonymizer=ps).process(ds3)
    assert str(out3.PatientID) != str(out1.PatientID)

    # Re-identification works from the local index.
    assert idx.lookup("Cohort", "PatientID", "MRN1") == str(out1.PatientID)
