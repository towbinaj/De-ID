"""OHIF integration: manifest building and the persistent-window control API.

These exercise the pure/server pieces (no bundled OHIF build and no browser
needed); the live render is verified separately.
"""

from __future__ import annotations

from urllib.parse import unquote

from pydicom.uid import generate_uid

from deid import ohif
from conftest import make_ct, save


def test_build_manifest_groups_studies_and_series(tmp_path):
    study = generate_uid()
    s1, s2 = generate_uid(), generate_uid()
    files = []
    for k, sid in enumerate((s1, s2)):
        for i in range(2):
            f = tmp_path / f"s{k}_{i}.dcm"
            save(make_ct(StudyInstanceUID=study, SeriesInstanceUID=sid,
                         extra={"InstanceNumber": i + 1}), f)
            files.append(f)
    m = ohif.build_manifest(files, lambda t: f"http://x/{t}")
    assert m.study_uids == [study]
    data = m.data
    assert len(data["studies"]) == 1
    assert len(data["studies"][0]["series"]) == 2
    assert m.instance_count == 4
    assert m.skipped == 0


def test_build_manifest_skips_non_images(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("not dicom", encoding="utf-8")
    m = ohif.build_manifest([p], lambda t: f"http://x/{t}")
    assert m.studies == [] and m.skipped == 1


def test_prepare_unified_and_goto(tmp_path, monkeypatch):
    monkeypatch.setenv("DEID_DATA", str(tmp_path / "d"))
    study = generate_uid()
    f = tmp_path / "a.dcm"
    save(make_ct(StudyInstanceUID=study), f)
    try:
        shell, m = ohif.prepare_unified([f])
        assert shell.endswith("/deid/shell")
        assert m.study_uids == [study]
        srv = ohif.server()
        assert ohif.goto(study, "SERIES-9") is True
        assert srv.target_seq == 1
        target = unquote(srv.target_url)
        assert f"StudyInstanceUIDs={study}" in target
        assert "SeriesInstanceUIDs=SERIES-9" in target
        assert srv.unified_mid in srv.target_url     # reuses the one manifest
    finally:
        ohif.shutdown()


def test_goto_without_prepare_returns_false():
    ohif.shutdown()                      # ensure a fresh server, nothing prepared
    try:
        assert ohif.goto("1.2.3") is False
    finally:
        ohif.shutdown()
