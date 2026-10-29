"""Stage a self-contained Tesseract OCR engine into ``assets/tesseract/`` so it
can be bundled into the De-ID exe (``De-ID.spec`` walks that folder).

Tesseract has no official portable zip, so this copies an existing Windows
install (e.g. the UB Mannheim build) - just the executable, its DLLs, and the
English + OSD traineddata (so the folder stays small). Install Tesseract once:

    winget install UB-Mannheim.TesseractOCR      (or: choco install tesseract)

then run:

    python scripts/fetch_tesseract.py [path-to-Tesseract-OCR]

Run before PyInstaller for a self-contained build. Without it, the exe falls
back to a system-installed / PATH tesseract at run time, and skips OCR if none
is found. The staged folder is git-ignored (it contains third-party binaries).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

DEST = Path(__file__).resolve().parent.parent / "assets" / "tesseract"
CANDIDATES = [r"C:\Program Files\Tesseract-OCR",
              r"C:\Program Files (x86)\Tesseract-OCR"]
KEEP_TESSDATA = {"eng.traineddata", "osd.traineddata"}


def find_install(argv: list) -> Path | None:
    if argv:
        return Path(argv[0])
    found = shutil.which("tesseract")
    if found:
        return Path(found).parent
    for c in CANDIDATES:
        if (Path(c) / "tesseract.exe").is_file():
            return Path(c)
    return None


def main() -> int:
    src = find_install(sys.argv[1:])
    if not src or not (src / "tesseract.exe").is_file():
        print("No Tesseract install found. Install it first, e.g.:\n"
              "  winget install UB-Mannheim.TesseractOCR\n"
              "then re-run this script (or pass the install dir as an argument).",
              file=sys.stderr)
        return 1
    if DEST.exists():
        shutil.rmtree(DEST)
    (DEST / "tessdata").mkdir(parents=True)
    n = 0
    for f in src.iterdir():                     # exe + DLLs (loaded from own dir)
        if f.is_file() and f.suffix.lower() in (".exe", ".dll"):
            shutil.copy2(f, DEST / f.name)
            n += 1
    td = src / "tessdata"
    for name in KEEP_TESSDATA:
        p = td / name
        if p.is_file():
            shutil.copy2(p, DEST / "tessdata" / name)
            n += 1
    for sub in ("configs", "tessconfigs"):      # 'tsv' output config lives here
        s = td / sub
        if s.is_dir():
            shutil.copytree(s, DEST / "tessdata" / sub)
    size = sum(p.stat().st_size for p in DEST.rglob("*") if p.is_file())
    print(f"Staged {n} file(s) ({size / 1e6:.0f} MB) from {src} -> {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
