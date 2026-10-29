"""Burned-in text OCR redaction: engine behavior + Processor integration."""

from __future__ import annotations

import pytest

from deid.engine import ocr
from deid.engine.processing import Processor
from deid.project import Project
from conftest import make_ct


def _gray8(size: int = 16, fill: int = 255):
    ds = make_ct()
    ds.Rows = ds.Columns = size
    ds.BitsAllocated = ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelData = bytes([fill]) * (size * size)
    return ds


def test_available_returns_bool():
    assert isinstance(ocr.available(), bool)


def test_modality_filter_skips():
    ds = _gray8()
    before = ds.PixelData
    # Modality CT is filtered out when the rule targets US only -> no-op.
    assert ocr.redact_burned_in_text(ds, modalities=["US"]) == 0
    assert ds.PixelData == before


def test_unavailable_is_noop(monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: False)
    ds = _gray8()
    before = ds.PixelData
    assert ocr.redact_burned_in_text(ds) == 0
    assert ds.PixelData == before


def test_redacts_detected_region(monkeypatch):
    # Deterministic: force availability + a fixed detected box, then confirm the
    # region is zeroed and written back (independent of the OCR model).
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


def test_real_engine_loads_if_installed():
    pytest.importorskip("rapidocr_onnxruntime")
    assert ocr.available()
    assert ocr._get_engine() is not None
