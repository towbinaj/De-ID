"""Known-region (CTP DicomPixelAnonymizer-style) burned-in PHI blanking."""

from __future__ import annotations

import numpy as np
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from deid.engine import pixelregions as pr
from deid.engine.pixelregions import (
    parse_pixel_script, matching_boxes, redact_known_regions,
    _compile_condition, default_rules,
)
from deid.engine.processing import Processor, ReceiverConfig


def _image(rows=100, cols=512, value=200, **tags) -> Dataset:
    """A minimal single-frame 8-bit grayscale image filled with ``value``."""
    ds = Dataset()
    ds.Rows, ds.Columns = rows, cols
    ds.BitsAllocated = ds.BitsStored = 8
    ds.HighBit = 7
    ds.SamplesPerPixel = 1
    ds.PixelRepresentation = 0
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelData = np.full((rows, cols), value, dtype=np.uint8).tobytes()
    ds.SOPInstanceUID = generate_uid()
    for k, v in tags.items():
        setattr(ds, k, v)
    fm = FileMetaDataset()
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = fm
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


# -- condition parsing / evaluation ----------------------------------------

def test_condition_tag_and_keyword_forms():
    ds = Dataset()
    ds.Modality = "CT"
    ds.Manufacturer = "GE MEDICAL SYSTEMS"
    p = _compile_condition(
        '[0008,0060].equals("CT") * Manufacturer.containsIgnoreCase("ge medical")')
    assert p(ds) is True


def test_and_binds_tighter_than_or():
    ds = Dataset()
    ds.Modality = "CT"
    # (MR AND contains X) OR (CT): the CT disjunct must make it True even though
    # the first conjunction is False — proving '*' binds tighter than '+'.
    p = _compile_condition(
        'Modality.equals("MR") * Manufacturer.contains("X") + Modality.equals("CT")')
    assert p(ds) is True
    # If '*' did NOT bind tighter it would parse as MR AND (X OR CT) -> False.


def test_not_and_exists_and_grouping():
    ds = Dataset()
    ds.Modality = "CT"
    ds.PatientName = "DOE^JANE"
    assert _compile_condition('!Modality.equals("MR")')(ds) is True
    assert _compile_condition("PatientName.exists()")(ds) is True
    assert _compile_condition("AccessionNumber.exists()")(ds) is False
    assert _compile_condition(
        '(Modality.equals("MR") + Modality.equals("CT")) * PatientName.exists()'
    )(ds) is True


def test_unknown_method_disables_rule_safely():
    ds = Dataset()
    ds.Modality = "CT"
    assert _compile_condition('Modality.wibble("CT")')(ds) is False


# -- script parsing + box geometry -----------------------------------------

def test_parse_and_match_converts_ctp_rect_to_inclusive_box():
    script = '{ Modality.equalsIgnoreCase("XC") }\n(10, 5, 20, 8)'
    rules = parse_pixel_script(script)
    assert len(rules) == 1
    ds = _image(rows=50, cols=50, Modality="XC")
    # CTP (x=10, y=5, w=20, h=8) -> inclusive (y0=5, y1=12, x0=10, x1=29)
    assert matching_boxes(ds, rules) == [(5, 12, 10, 29)]


def test_non_match_yields_no_boxes():
    rules = parse_pixel_script('{ Modality.equals("XC") }\n(0,0,10,10)')
    ds = _image(Modality="CT")
    assert matching_boxes(ds, rules) == []


def test_rect_clamped_to_image_and_offscreen_dropped():
    rules = parse_pixel_script(
        '{ Modality.equals("XC") }\n(0,0,100,100)\n(50,50,10,10)')
    ds = _image(rows=20, cols=20, Modality="XC")
    # First rect clamps to the 20x20 image; second is fully off-screen -> dropped.
    assert matching_boxes(ds, rules) == [(0, 19, 0, 19)]


def test_headers_and_comments_between_blocks_are_ignored():
    script = """
    **** section header ****
    { Modality.equals("XC") }
    (1, 2, 3, 4)

    some stray prose
    { Modality.equals("US") }
    (5, 6, 7, 8)
    """
    rules = parse_pixel_script(script)
    assert len(rules) == 2


# -- redaction -------------------------------------------------------------

def test_redact_known_regions_blanks_only_the_rectangle():
    rules = parse_pixel_script('{ Modality.equals("XC") }\n(10, 5, 20, 8)')
    ds = _image(rows=50, cols=50, value=200, Modality="XC")
    n = redact_known_regions(ds, rules)
    assert n == 1
    arr = ds.pixel_array
    assert (arr[5:13, 10:30] == 0).all()      # rectangle blanked
    assert arr[0, 0] == 200                    # outside untouched
    assert arr[49, 49] == 200


def test_bundled_full_library_is_loaded():
    from deid.engine.pixelregions import bundled_library_path, default_rules
    assert bundled_library_path() is not None       # shipped in the repo
    assert len(default_rules()) > 100               # the full MIRC CTP library


def test_bundled_starter_library_matches_ge_ct_dose_report():
    ds = _image(rows=120, cols=512, value=180,
                Modality="CT", Manufacturer="GE MEDICAL SYSTEMS",
                SeriesDescription="CT Dose Report")
    boxes = matching_boxes(ds, default_rules())
    assert boxes, "GE CT dose report should match the bundled starter library"
    n = redact_known_regions(ds, default_rules())
    assert n >= 1
    assert (ds.pixel_array[0:110, :] == 0).all()


# -- Processor integration -------------------------------------------------

def test_processor_runs_known_region_first_pass(tmp_path):
    script = tmp_path / "pix.script"
    script.write_text('{ Modality.equalsIgnoreCase("XC") }\n(10,5,20,8)\n',
                      encoding="utf-8")
    ds = _image(rows=50, cols=50, value=200, Modality="XC")
    cfg = ReceiverConfig(pixel_regions_enabled=True,
                         pixel_regions_script=str(script))
    Processor(cfg).process(ds)
    arr = ds.pixel_array
    assert (arr[5:13, 10:30] == 0).all()
    assert arr[0, 0] == 200


def test_processor_skips_known_regions_when_disabled():
    ds = _image(rows=50, cols=50, value=200, Modality="CT",
                Manufacturer="GE MEDICAL SYSTEMS",
                SeriesDescription="CT Dose Report")
    Processor(ReceiverConfig()).process(ds)      # pixel_regions_enabled defaults False
    assert (ds.pixel_array == 200).all()         # pixels untouched
