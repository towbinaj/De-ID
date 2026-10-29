"""Burned-in text OCR redaction (Tesseract subprocess): behavior + wiring."""

from __future__ import annotations

from deid.engine import ocr
from deid.engine.processing import Processor
from deid.project import Project
from conftest import make_ct

# A minimal Tesseract TSV: a header then a confident word and a low-conf word.
_TSV = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext\n"
    "5\t1\t1\t1\t1\t1\t10\t20\t100\t30\t95.5\tSMITH\n"
    "5\t1\t1\t1\t1\t2\t5\t5\t40\t8\t9.0\tnoise\n"
)


def _gray8(size: int = 16, fill: int = 255):
    ds = make_ct()
    ds.Rows = ds.Columns = size
    ds.BitsAllocated = ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelData = bytes([fill]) * (size * size)
    return ds


def test_available_returns_bool():
    assert isinstance(ocr.available(), bool)


def test_tsv_parsing_filters_by_confidence(monkeypatch):
    import numpy as np
    monkeypatch.setattr(ocr, "tesseract_path", lambda: "tesseract")
    monkeypatch.setattr(ocr, "_run_tsv", lambda exe, png: _TSV)
    boxes = ocr._detect_boxes(np.zeros((64, 128), dtype="uint8"),
                              min_conf=0.5)
    # Only the 95.5-conf word survives; the 9.0 one is below 50%.
    assert boxes == [(20, 50, 10, 110)]


def test_modality_filter_skips():
    ds = _gray8()
    before = ds.PixelData
    assert ocr.redact_burned_in_text(ds, modalities=["US"]) == 0
    assert ds.PixelData == before


def test_unavailable_is_noop(monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: False)
    ds = _gray8()
    before = ds.PixelData
    assert ocr.redact_burned_in_text(ds) == 0
    assert ds.PixelData == before


def test_redacts_detected_region(monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "_detect_boxes", lambda frame, conf: [(0, 3, 0, 3)])
    ds = _gray8(16, fill=255)
    n = ocr.redact_burned_in_text(ds, min_confidence=0.1)
    assert n == 1
    out = ds.pixel_array
    assert out[0:4, 0:4].sum() == 0            # the box was blacked out
    assert out.sum() < 255 * 16 * 16           # but not the whole image


def test_processor_invokes_ocr_when_enabled(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(ocr, "redact_burned_in_text",
                        lambda ds, **kw: calls.__setitem__("n", calls["n"] + 1))
    cfg = Project(name="t", basic_profile=True, ocr_enabled=True,
                  ocr_min_confidence=0.4).to_receiver_config()
    Processor(cfg).process(make_ct())
    assert calls["n"] == 1


def test_processor_skips_ocr_when_disabled(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(ocr, "redact_burned_in_text",
                        lambda ds, **kw: calls.__setitem__("n", calls["n"] + 1))
    cfg = Project(name="t", basic_profile=True, ocr_enabled=False)\
        .to_receiver_config()
    Processor(cfg).process(make_ct())
    assert calls["n"] == 0
