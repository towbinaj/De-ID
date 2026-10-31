"""Study-level pixel-scan report: statuses, grouping, and CSV writers."""

from __future__ import annotations

import csv

import pytest

from deid import pixelscan
from deid.pixelscan import InstanceReport, instance_status, build_studies
from deid.engine import ocr
from conftest import make_ct, save


def _inst(study, series, cat_counts, redacted=0, **kw):
    findings = []
    for cat, n in cat_counts.items():
        findings += [{"category": cat, "text": f"{cat}{i}", "conf": 90,
                      "box": (0, 1, 0, 1)} for i in range(n)]
    return InstanceReport(
        path=kw.get("path", "f.dcm"), sop=kw.get("sop", "1"),
        study_uid=study, series_uid=series,
        series_number=kw.get("series_number", "1"), series_desc="", modality="US",
        accession=kw.get("accession", "ACC"), patient_id="MRN",
        findings=findings, redacted=redacted)


def test_instance_status_rules():
    assert instance_status(0, 0, 0) == pixelscan.STATUS_PASS
    assert instance_status(2, 0, 2) == pixelscan.STATUS_CORRECTED   # PHI fixed
    assert instance_status(2, 0, 0) == pixelscan.STATUS_REVIEW      # PHI unfixed
    assert instance_status(0, 1, 0) == pixelscan.STATUS_REVIEW      # suspect
    assert instance_status(3, 1, 3) == pixelscan.STATUS_REVIEW      # suspect wins


def test_build_studies_groups_and_rolls_up_status():
    insts = [
        _inst("S1", "A", {"name": 1}, redacted=1, accession="ACC1"),   # corrected
        _inst("S1", "B", {}, redacted=0),                              # pass
        _inst("S2", "C", {"suspect": 1}),                              # review
    ]
    studies = build_studies(insts)
    assert len(studies) == 2
    s1 = next(s for s in studies if s.study_uid == "S1")
    assert s1.n_series == 2 and s1.accession == "ACC1"
    assert s1.status == pixelscan.STATUS_CORRECTED       # worst of corrected/pass
    s2 = next(s for s in studies if s.study_uid == "S2")
    assert s2.status == pixelscan.STATUS_REVIEW


def test_overview_csv(tmp_path):
    studies = build_studies([
        _inst("S1", "A", {"name": 2}, redacted=2, accession="ACC1")])
    path = tmp_path / "ov.csv"
    pixelscan.write_overview_csv(studies, path)
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == pixelscan._OVERVIEW_COLS
    row = dict(zip(rows[0], rows[1]))
    assert row["accession"] == "ACC1" and row["phi_hits"] == "2"
    assert row["redacted"] == "2" and row["status"] == pixelscan.STATUS_CORRECTED


def test_findings_csv_marks_redacted(tmp_path):
    studies = build_studies([
        _inst("S1", "A", {"name": 1}, redacted=1),
        _inst("S2", "C", {"suspect": 1}, redacted=0)])
    path = tmp_path / "fn.csv"
    n = pixelscan.write_findings_csv(studies, path)
    assert n == 2
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    by_cat = {r[6]: r for r in rows[1:]}        # category column
    assert by_cat["name"][10] == "yes"          # redacted flag set for PHI
    assert by_cat["suspect"][10] == ""          # suspects never auto-redacted


def test_default_report_paths_are_local(tmp_path, monkeypatch):
    monkeypatch.setenv("DEID_DATA", str(tmp_path))
    ov, fn = pixelscan.default_report_paths("Trial A")
    assert ov.parent.name == "pixelscan" and fn.parent.name == "pixelscan"
    assert ov.name.endswith("_overview.csv") and fn.name.endswith("_findings.csv")


def test_findings_csv_records_adjudication(tmp_path):
    studies = build_studies([
        _inst("S1", "A", {"suspect": 2}, accession="ACC1")])
    adj = {
        pixelscan.adjudication_key("S1", "A", "suspect", "suspect0"): "phi",
        pixelscan.adjudication_key("S1", "A", "suspect", "suspect1"): "ok",
    }
    path = tmp_path / "fn.csv"
    pixelscan.write_findings_csv(studies, path, adj)
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == pixelscan._DETAIL_COLS
    assert rows[0][-1] == "adjudicated"
    ti, ai = rows[0].index("text"), rows[0].index("adjudicated")
    by_text = {r[ti]: r[ai] for r in rows[1:]}
    assert by_text["suspect0"] == "phi"
    assert by_text["suspect1"] == "ok"


def test_findings_csv_adjudication_blank_by_default(tmp_path):
    studies = build_studies([_inst("S1", "A", {"suspect": 1})])
    path = tmp_path / "fn.csv"
    pixelscan.write_findings_csv(studies, path)
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[1][-1] == ""          # no decision -> empty adjudicated cell


@pytest.mark.skipif(not ocr.available(), reason="no tesseract engine")
def test_scan_studies_blank_image_is_pass(tmp_path):
    save(make_ct(PatientID="A", StudyInstanceUID="1.1"), tmp_path / "a.dcm")
    studies = pixelscan.scan_studies([tmp_path / "a.dcm"], min_confidence=0.5)
    # Every scanned study is reported (overview includes clean ones); a blank
    # synthetic image has no burned-in text, so it passes.
    assert len(studies) == 1
    assert studies[0].status == pixelscan.STATUS_PASS
    assert studies[0].n_phi == 0 and studies[0].n_suspects == 0


# -- corrected-copy output layout (preserve the input folder structure) ------
def test_mirror_base_none_when_empty():
    assert pixelscan.mirror_base([]) is None


def test_corrected_dest_preserves_structure_below_common_base(tmp_path):
    root = tmp_path / "drop"
    (root / "StudyA").mkdir(parents=True)
    (root / "StudyB" / "Ser2").mkdir(parents=True)
    fa = root / "StudyA" / "img001.dcm"
    fb = root / "StudyB" / "Ser2" / "img002.dcm"
    fa.write_bytes(b"x")
    fb.write_bytes(b"x")
    out = tmp_path / "out"

    base = pixelscan.mirror_base([fa, fb])
    assert base == root.resolve()
    # Original subfolders AND filenames are kept under the output folder.
    assert pixelscan._corrected_dest(fa, out, base) == out / "StudyA" / "img001.dcm"
    assert pixelscan._corrected_dest(fb, out, base) == \
        out / "StudyB" / "Ser2" / "img002.dcm"


def test_corrected_dest_single_file_is_flat(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    f = d / "only.dcm"
    f.write_bytes(b"x")
    out = tmp_path / "out"
    base = pixelscan.mirror_base([f])
    assert base == d.resolve()
    assert pixelscan._corrected_dest(f, out, base) == out / "only.dcm"


def test_corrected_dest_falls_back_to_name_when_not_under_base(tmp_path):
    (tmp_path / "other").mkdir()
    f = tmp_path / "other" / "x.dcm"
    f.write_bytes(b"x")
    out = tmp_path / "out"
    base = tmp_path / "somewhere" / "else"      # f is not under this base
    assert pixelscan._corrected_dest(f, out, base) == out / "x.dcm"
