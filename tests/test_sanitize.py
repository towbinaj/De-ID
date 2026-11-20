"""Structural sanitize pass: strip smuggled-payload carriers."""

from __future__ import annotations

from pydicom.uid import generate_uid

from deid.engine.processing import ReceiverConfig, Processor
from deid.engine.sanitize import (SanitizeConfig, sanitize_dataset,
                                   detect_preamble, SanitizeReport)
from conftest import make_ct

_PE_PREAMBLE = b"MZ\x90\x00" + b"\x00" * 124
_ENCAP_PDF = "1.2.840.10008.5.1.4.1.1.104.1"


def _with_overlay(ds, group=0x6000):
    """Add an overlay plane (rows/cols + data) in an even overlay group."""
    ds.add_new((group, 0x0010), "US", 4)          # OverlayRows
    ds.add_new((group, 0x0011), "US", 4)          # OverlayColumns
    ds.add_new((group, 0x3000), "OW", b"\x00" * 2)  # OverlayData
    return ds


def _with_curve(ds, group=0x5000):
    ds.add_new((group, 0x0005), "US", 1)          # CurveDimensions (retired)
    ds.add_new((group, 0x3000), "OW", b"\x00" * 2)  # CurveData (retired)
    return ds


def _with_icon(ds):
    from pydicom.dataset import Dataset
    icon = Dataset()
    icon.Rows = 2
    icon.Columns = 2
    icon.PixelData = bytes(4)
    ds.IconImageSequence = [icon]                 # (0088,0200)
    return ds


# -- detection -------------------------------------------------------------

def test_detect_preamble_flags_pe_header():
    ds = make_ct()
    ds.preamble = _PE_PREAMBLE
    rep = SanitizeReport()
    detect_preamble(ds, rep)
    assert rep.preamble_payload is True
    assert "MZ" in rep.preamble_signature


def test_detect_preamble_clean_is_quiet():
    ds = make_ct()
    ds.preamble = b"\x00" * 128
    rep = SanitizeReport()
    detect_preamble(ds, rep)
    assert rep.preamble_payload is False


def test_disabled_config_still_detects_preamble_but_strips_nothing():
    ds = _with_overlay(make_ct())
    ds.preamble = _PE_PREAMBLE
    rep = sanitize_dataset(ds, SanitizeConfig(enabled=False))
    assert rep.preamble_payload is True           # always reported
    assert rep.overlay_groups == 0                # nothing stripped
    assert (0x6000, 0x3000) in ds                 # overlay still present


def test_none_config_detects_preamble_only():
    ds = _with_overlay(make_ct())
    ds.preamble = _PE_PREAMBLE
    rep = sanitize_dataset(ds, None)
    assert rep.preamble_payload is True
    assert (0x6000, 0x3000) in ds


# -- stripping -------------------------------------------------------------

def test_strips_overlay_planes():
    ds = _with_overlay(make_ct())
    rep = sanitize_dataset(ds, SanitizeConfig(enabled=True))
    assert rep.overlay_groups == 1
    assert (0x6000, 0x0010) not in ds
    assert (0x6000, 0x3000) not in ds
    assert "PixelData" in ds                      # primary image untouched


def test_counts_distinct_overlay_groups():
    ds = make_ct()
    _with_overlay(ds, 0x6000)
    _with_overlay(ds, 0x6002)
    rep = sanitize_dataset(ds, SanitizeConfig(enabled=True))
    assert rep.overlay_groups == 2
    assert (0x6002, 0x3000) not in ds


def test_strips_curve_data():
    ds = _with_curve(make_ct())
    rep = sanitize_dataset(ds, SanitizeConfig(enabled=True))
    assert rep.curve_groups == 1
    assert (0x5000, 0x3000) not in ds


def test_strips_icon_image_sequence():
    ds = _with_icon(make_ct())
    assert "IconImageSequence" in ds
    rep = sanitize_dataset(ds, SanitizeConfig(enabled=True))
    assert rep.icon_sequences == 1
    assert "IconImageSequence" not in ds


def test_strips_encapsulated_document_and_flags_object():
    ds = make_ct(SOPClassUID=_ENCAP_PDF)
    ds.add_new(0x00420011, "OB", b"%PDF-1.7 payload")   # EncapsulatedDocument
    rep = sanitize_dataset(ds, SanitizeConfig(enabled=True))
    assert rep.encapsulated_docs == 1
    assert rep.is_encapsulated_object is True
    assert (0x0042, 0x0011) not in ds


def test_subflags_can_keep_overlays():
    ds = _with_overlay(_with_icon(make_ct()))
    cfg = SanitizeConfig(enabled=True, strip_overlays=False)
    rep = sanitize_dataset(ds, cfg)
    assert rep.overlay_groups == 0
    assert (0x6000, 0x3000) in ds                 # kept
    assert rep.icon_sequences == 1
    assert "IconImageSequence" not in ds          # still stripped


def test_clean_object_reports_nothing():
    rep = sanitize_dataset(make_ct(), SanitizeConfig(enabled=True))
    assert rep.any_found is False
    assert rep.findings() == []


# -- Processor integration -------------------------------------------------

def test_processor_sanitize_strips_carriers_when_enabled():
    ds = _with_overlay(_with_icon(make_ct(StudyInstanceUID=generate_uid())))
    cfg = ReceiverConfig(basic_profile=True, sanitize_enabled=True)
    out = Processor(cfg).process(ds)
    assert (0x6000, 0x3000) not in out
    assert "IconImageSequence" not in out
    assert "PixelData" in out


def test_processor_leaves_carriers_when_sanitize_off():
    # basic_profile off so sanitize is the ONLY thing that could strip overlays.
    ds = _with_overlay(make_ct(StudyInstanceUID=generate_uid()))
    cfg = ReceiverConfig(basic_profile=False, sanitize_enabled=False)
    out = Processor(cfg).process(ds)
    assert (0x6000, 0x3000) in out                # not stripped
