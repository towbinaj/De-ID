"""Importing destinations / settings / profiles from the DICOM Toolkit."""

from __future__ import annotations

import json

from deid import config, toolkit_import as ti
from deid.project import ProjectStore


def _toolkit_folder(tmp_path):
    tk = tmp_path / "DICOMToolkit-data"
    tk.mkdir()
    (tk / "destinations.json").write_text(json.dumps([
        {"name": "Research VNA", "aetitle": "RVNA", "host": "192.0.2.10",
         "port": 104, "group": "PACS / Storage"},
        {"name": "RIS Worklist", "aetitle": "WL", "host": "192.0.2.11",
         "port": 105, "group": "Worklist / MPPS"},   # not a C-STORE target
    ]), encoding="utf-8")
    (tk / "settings.json").write_text(json.dumps({
        "my_aetitle": "TKAE", "bug_report_email": "x@example.org",
        "appearance": "Dark", "ui_scale": 1.3, "window_geometry": "ignored"
    }), encoding="utf-8")
    (tk / "anon_profiles.json").write_text(json.dumps([
        {"name": "Keep ID", "basic_profile": True, "retain_dates": True,
         "remove_private": True,
         "rules": [{"tag": "00100020", "action": "KEEP", "value": ""}]},
    ]), encoding="utf-8")
    return tk


def test_import_destinations_filters_and_dedupes(tmp_path, monkeypatch):
    monkeypatch.setenv("DEID_DATA", str(tmp_path / "deid"))
    tk = _toolkit_folder(tmp_path)
    added, skipped = ti.import_destinations(tk)
    assert added == 1 and skipped == 0            # worklist entry excluded
    dests = config.load_destinations()
    assert [d.name for d in dests] == ["Research VNA"]
    # Re-import: the existing name is skipped.
    added2, skipped2 = ti.import_destinations(tk)
    assert added2 == 0 and skipped2 == 1


def test_import_settings_applies_overlap(tmp_path, monkeypatch):
    monkeypatch.setenv("DEID_DATA", str(tmp_path / "deid"))
    tk = _toolkit_folder(tmp_path)
    applied = ti.import_settings(tk)
    assert set(applied) == {"my_aetitle", "bug_report_email", "appearance",
                            "ui_scale"}
    s = config.Settings.load()
    assert s.my_aetitle == "TKAE" and s.appearance == "Dark"
    assert s.ui_scale == 1.3


def test_import_profiles_become_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("DEID_DATA", str(tmp_path / "deid"))
    tk = _toolkit_folder(tmp_path)
    assert ti.import_profiles(tk) == 1
    proj = ProjectStore().load("Keep ID")
    assert proj is not None
    assert proj.basic_profile is True
    assert proj.effective_date_mode() == "keep"      # retain_dates -> keep
    assert proj.custom_rules() == [("00100020", "KEEP", "")]
    # Re-import does not duplicate.
    assert ti.import_profiles(tk) == 0


def test_import_all_and_autodetect(tmp_path, monkeypatch):
    monkeypatch.setenv("DEID_DATA", str(tmp_path / "deid"))
    tk = _toolkit_folder(tmp_path)
    monkeypatch.setenv("DCMTOOLKIT_DATA", str(tk))
    assert ti.find_toolkit_data() == tk
    out = ti.import_all(tk)
    assert out["destinations_added"] == 1
    assert out["projects_added"] == 1
    assert "my_aetitle" in out["settings"]
