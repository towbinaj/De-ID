# PyInstaller spec for De-ID (onedir).
# Build:  pyinstaller De-ID.spec --noconfirm --clean
# Output: dist/De-ID/De-ID.exe  (a folder; double-click the exe inside it)
#
# onedir (not onefile) by design: faster launch/close, and room for the
# optional OCR model files without re-extracting ~100 MB to %TEMP% each run.

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata
import os

datas = []
binaries = []
hiddenimports = []

_icon = os.path.join("assets", "icon.ico")
if os.path.exists(_icon):
    datas.append((_icon, "assets"))

# Optional OCR models (fetched by scripts/fetch_ocr.py; not in git). If absent,
# OCR rules are skipped at runtime with a clear log line.
for _root, _dirs, _files in os.walk(os.path.join("assets", "ocr-models")):
    for _f in _files:
        datas.append((os.path.join(_root, _f), _root))

# CustomTkinter ships theme JSON + assets; tkinterdnd2 ships the tkdnd binaries.
for pkg in ("customtkinter", "tkinterdnd2"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# pynetdicom registers SOP classes dynamically; pull in all submodules.
hiddenimports += collect_submodules("pynetdicom")
hiddenimports += collect_submodules("pydicom")

# dicom-anonymizer (PS3.15 Basic Profile) ships action-table data files.
_da, _db, _dh = collect_all("dicomanonymizer")
datas += _da
binaries += _db
hiddenimports += _dh

# Compressed-pixel decoders (OCR/preview on compressed images). pylibjpeg
# discovers its plugins via entry-point metadata, so collect the packages AND
# their metadata, plus the compiled plugin modules and pydicom's pixel handlers.
for pkg in ("pylibjpeg", "pylibjpeg_libjpeg", "pylibjpeg_openjpeg"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
for dist in ("pylibjpeg", "pylibjpeg-libjpeg", "pylibjpeg-openjpeg"):
    try:
        datas += copy_metadata(dist)
    except Exception:
        pass
hiddenimports += ["openjpeg", "libjpeg"]
hiddenimports += collect_submodules("pydicom.pixels")
hiddenimports += collect_submodules("pydicom.pixel_data_handlers")

# NOTE: the optional OCR extra (rapidocr-onnxruntime) is deliberately NOT bundled
# into the exe. onnxruntime's native libraries segfault (0xC0000005) when loaded
# from a PyInstaller build, which would crash the app. OCR therefore runs only
# when the app is launched from source with requirements-ocr.txt installed; in
# the packaged exe `ocr.available()` reports it absent and OCR rules are skipped.
# (Revisit if onnxruntime + PyInstaller packaging is made reliable.)

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # OCR deps are EXCLUDED: onnxruntime's native libs segfault under
    # PyInstaller. They're imported only inside deid.engine.ocr (lazily), so
    # PyInstaller would otherwise auto-bundle them; excluding keeps them out so
    # ocr.available() reports OCR absent in the exe and nothing tries to load it.
    excludes=["pytest", "tkinter.test", "test",
              "PIL._avif", "PIL.AvifImagePlugin",
              "rapidocr_onnxruntime", "onnxruntime", "cv2"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,       # onedir: binaries/datas go in COLLECT below
    name="De-ID",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,               # windowed GUI; no console flashes on launch
    disable_windowed_traceback=False,
    icon=_icon if os.path.exists(_icon) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="De-ID",
)
