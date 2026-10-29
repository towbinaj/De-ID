"""Project model: persistence, engine mapping, and the ProjectStore."""

from __future__ import annotations

from pydicom.uid import generate_uid

from deid.project import Project, ProjectStore
from deid.engine.anon_profiles import AnonRule
from deid.engine.processing import Processor
from conftest import make_ct


def test_project_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("DEID_DATA", str(tmp_path))
    p = Project(name="Trial A", basic_profile=True, calculated_dates=True,
                rules=[AnonRule("00100020", "KEEP"),
                       AnonRule("00081030", "REPLACE", "ANON")],
                remove_image_top=10, remove_image_top_modality=["US"],
                output_dir=str(tmp_path / "out"))
    store = ProjectStore()
    store.save(p)
    loaded = store.load("Trial A")
    assert loaded is not None
    assert loaded.calculated_dates is True
    assert loaded.remove_image_top == 10
    assert loaded.remove_image_top_modality == ["US"]
    assert loaded.custom_rules() == [("00100020", "KEEP", ""),
                                     ("00081030", "REPLACE", "ANON")]


def test_store_list_and_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("DEID_DATA", str(tmp_path))
    store = ProjectStore()
    store.save(Project(name="One"))
    store.save(Project(name="Two"))
    assert set(store.names()) == {"One", "Two"}
    store.delete("One")
    assert store.names() == ["Two"]


def test_ensure_default_creates_one(tmp_path, monkeypatch):
    monkeypatch.setenv("DEID_DATA", str(tmp_path))
    store = ProjectStore()
    default = store.ensure_default()
    assert default.basic_profile is True
    assert store.names() == [default.name]


def test_to_receiver_config_drives_engine():
    # A project with Basic on + a KEEP + a REPLACE should de-identify correctly.
    p = Project(name="x", basic_profile=True,
                rules=[AnonRule("00100020", "KEEP"),
                       AnonRule("00081030", "REPLACE", "ANON STUDY")])
    ds = make_ct(StudyInstanceUID=generate_uid(), PatientName="DOE^JANE",
                 PatientID="MRN1")
    ds.StudyDescription = "SECRET"
    out = Processor(p.to_receiver_config()).process(ds)
    assert str(out.PatientName) == ""              # Basic emptied it
    assert str(out.PatientID) == "MRN1"            # KEEP exempted it
    assert str(out.StudyDescription) == "ANON STUDY"


def test_summary_mentions_actions():
    p = Project(name="x", basic_profile=True, calculated_dates=True,
                remove_image_top=5)
    s = p.summary()
    assert "Basic Profile" in s and "shift dates" in s and "pixel" in s
