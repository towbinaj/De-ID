"""Plain image (JPEG/PNG) support in the Pixel PHI Scan."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from deid import imagefile, pixelscan
from deid.engine import ocr


def _text_png(path, text="John 1234567", size=(480, 140)):
    img = Image.new("L", size, 0)
    draw = ImageDraw.Draw(img)
    try:
        fnt = ImageFont.truetype("arial.ttf", 30)
    except Exception:  # noqa: BLE001
        fnt = ImageFont.load_default()
    draw.text((12, 48), text, fill=255, font=fnt)
    img.save(path)
    return str(path)


def test_is_image():
    assert imagefile.is_image("a.JPG") and imagefile.is_image("b.png")
    assert imagefile.is_image(Path("c.tiff"))
    assert not imagefile.is_image("d.dcm") and not imagefile.is_image("e.txt")


def test_redact_image_blanks_box(tmp_path):
    src = _text_png(tmp_path / "in.png")
    dest = tmp_path / "out" / "sub" / "in.png"
    n = imagefile.redact(src, [(48, 80, 12, 240)], dest)
    assert n == 1 and dest.exists()
    arr = np.asarray(Image.open(dest).convert("L"))
    assert (arr[48:81, 12:241] == 0).all()       # box region blanked
    assert imagefile.redact(src, [], tmp_path / "x.png") == 0


@pytest.mark.skipif(not ocr.available(), reason="no tesseract engine")
def test_scan_image_flags_suspects_then_clean(tmp_path):
    src = _text_png(tmp_path / "face.png", "John 1234567")
    out = tmp_path / "out"
    studies = pixelscan.scan_studies([Path(src)], redact=True, out_dir=str(out))
    insts = [i for st in studies for se in st.series for i in se.instances]
    assert len(insts) == 1
    inst = insts[0]
    assert inst.modality == "IMG"
    assert inst.redacted == 0                     # images never auto-redact
    suspects = [f for f in inst.findings if f["category"] == "suspect"]
    assert suspects                               # detected text -> suspects

    st, se = studies[0], studies[0].series[0]
    key = pixelscan.adjudication_key(st.study_uid, se.series_uid, "suspect",
                                     suspects[0]["text"])
    summary = pixelscan.redact_adjudicated(
        studies, {key: "phi"}, out, pixelscan.mirror_base([Path(src)]))
    assert summary["instances"] == 1 and summary["boxes"] >= 1
    assert inst.out_path and Path(inst.out_path).exists()
    assert Path(inst.out_path).suffix.lower() == ".png"   # stays an image
