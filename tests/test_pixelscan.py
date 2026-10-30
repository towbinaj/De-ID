"""Pixel-scan report writer + folder scan (OCR-gated)."""

from __future__ import annotations

import csv

import pytest

from deid import pixelscan
from deid.engine import ocr
from conftest import make_ct, save


def test_file_findings_split_phi_and_suspects():
    ff = pixelscan.FileFindings("f.dcm", "US", [
        {"text": "DOE", "category": "name", "conf": 90, "box": (0, 1, 0, 1)},
        {"text": "X9", "category": "suspect", "conf": 80, "box": (0, 1, 0, 1)},
    ])
    assert len(ff.phi) == 1 and ff.phi[0]["category"] == "name"
    assert len(ff.suspects) == 1


def test_write_report_csv_round_trip(tmp_path):
    results = [pixelscan.FileFindings("study/a.dcm", "CT", [
        {"text": "DOE JANE", "category": "name", "conf": 92,
         "box": (10, 25, 5, 80)},
        {"text": "01/31/2020", "category": "date", "conf": 88,
         "box": (30, 45, 5, 90)},
    ])]
    path = tmp_path / "report.csv"
    n = pixelscan.write_report_csv(results, path)
    assert n == 2
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["file", "modality", "category", "text", "confidence",
                       "box"]
    assert rows[1][2] == "name" and rows[1][3] == "DOE JANE"
    assert rows[1][5] == "5,10,80,25"              # x0,y0,x1,y1


def test_default_report_path_is_local(tmp_path, monkeypatch):
    monkeypatch.setenv("DEID_DATA", str(tmp_path))
    p = pixelscan.default_report_path("Trial A")
    assert p.parent.name == "pixelscan" and p.parent.parent == tmp_path


@pytest.mark.skipif(not ocr.available(),
                    reason="no tesseract engine available")
def test_scan_folder_runs_without_findings_on_blank(tmp_path):
    # A blank synthetic image has no text; scan should return no findings.
    save(make_ct(PatientID="A"), tmp_path / "a.dcm")
    out = pixelscan.scan_folder([tmp_path / "a.dcm"], min_confidence=0.5)
    assert out == []
