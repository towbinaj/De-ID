"""Entry point for De-ID.

Normal use: double-click the executable (or ``python main.py``) to open the GUI.
"""

import sys


def _selfcheck() -> int:
    """Headless diagnostic: write step-by-step progress to selfcheck.txt so a
    crash (even a native abort) is pinpointed by the last line written."""
    from deid import paths
    out = paths.data_dir() / "selfcheck.txt"
    out.write_text("", encoding="utf-8")

    def w(line: str) -> None:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    w("start")
    w("building app...")
    try:
        from deid.gui.app import App
        app = App()
        app.withdraw()
        app.update()
        w("app built; select projects")
        app.select("projects")
        app.update()
        w("select rules")
        app.select("rules")
        app.update()
        w("rules OK")
        app.destroy()
    except Exception:  # noqa: BLE001
        import traceback
        w("APP EXCEPTION:\n" + traceback.format_exc())
    w("ocr check...")
    try:
        from deid.engine import ocr
        w(f"tesseract resolved: {ocr.tesseract_path()}")
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
        from pydicom.dataset import Dataset, FileMetaDataset
        from pydicom.uid import (generate_uid, ExplicitVRLittleEndian,
                                 SecondaryCaptureImageStorage)
        img = Image.new("L", (420, 100), 255)
        drw = ImageDraw.Draw(img)
        try:
            fnt = ImageFont.truetype("arial.ttf", 36)
        except Exception:  # noqa: BLE001
            fnt = ImageFont.load_default()
        drw.text((10, 30), "PATIENT SMITH JOHN", fill=0, font=fnt)
        arr = np.array(img)
        ds = Dataset()
        ds.Rows, ds.Columns = arr.shape
        ds.BitsAllocated = ds.BitsStored = 8
        ds.HighBit = 7
        ds.PixelRepresentation = 0
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.Modality = "OT"
        ds.SOPClassUID = SecondaryCaptureImageStorage
        ds.SOPInstanceUID = generate_uid()
        ds.PixelData = arr.tobytes()
        fm = FileMetaDataset()
        fm.MediaStorageSOPClassUID = ds.SOPClassUID
        fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
        fm.TransferSyntaxUID = ExplicitVRLittleEndian
        ds.file_meta = fm
        n = ocr.redact_burned_in_text(ds, min_confidence=0.5)
        w(f"ocr redacted regions: {n}")
    except Exception:  # noqa: BLE001
        import traceback
        w("OCR EXCEPTION:\n" + traceback.format_exc())
    w("done")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "selfcheck":
        return _selfcheck()
    from deid.gui.app import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
