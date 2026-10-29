# De-ID

A standalone, **workflow-first** DICOM de-identification app for Windows — one
double-click program built for *frequent* de-id work.

It reuses the proven de-identification engine from the DICOM Toolkit (PS3.15 Basic
Confidentiality Profile, consistent UID remapping, age-preserving date shifting,
crosswalk morphing, pixel blanking) and wraps it in a workflow designed around
saved, re-runnable **Projects** instead of one dense form.

> **Status:** early development. The engine and scaffolding are in place; the
> workflow UI and the features below are being built in phases.

## Why a separate app

The DICOM Toolkit's "De-identify Files" tab does the job but crams ~20 controls
into a single pane — fine occasionally, tiring when you de-identify every day.
De-ID keeps the same engine and rebuilds the experience:

- **Projects** — define a de-id recipe once (profile, dates, drop rules, pixel/OCR,
  pseudonym policy, source, destination) and re-run it on new files with one click.
- **Object/series drop rules** — automatically drop or quarantine whole instances
  or series by rule (Secondary Capture, encapsulated PDFs, scanned documents, …),
  which commonly carry burned-in PHI.
- **Consistent pseudonyms** — a persistent local crosswalk so the same patient or
  accession always maps to the same anonymized ID across runs and days
  (re-identification is local-only, never leaves your machine).
- **Receive → anonymize → export queue** — a C-STORE inbox / watch-folder that
  de-identifies incoming studies against the active project and forwards them to a
  research PACS/VNA.
- **Burned-in pixel text (OCR)** — detect and redact burned-in PHI, beyond fixed
  top/left rectangle blanking. OCR uses the **Tesseract** engine, run as a
  subprocess. It's optional: if no `tesseract` executable is found (bundled,
  on `PATH`, or via the `DEID_TESSERACT` env var), OCR rules are skipped. To
  enable it, install Tesseract (`winget install UB-Mannheim.TesseractOCR`); to
  bundle it into the exe, run `python scripts/fetch_tesseract.py` before building.
- **CTP compatibility** — import/export RSNA/CTP anonymizer scripts.

## Privacy & security

Everything identifying stays **local**. The pseudonym crosswalk, quarantined
originals, destinations, and settings live in a git-ignored `De-ID-data/` folder
next to the app. The public repository contains no real AE titles, hosts, IPs,
credentials, or patient data. Diagnostics in a bug report are PHI-scrubbed before
they leave the machine.

**De-ID is a tool, not a guarantee.** The PS3.15 Basic Profile acts on standard
confidentiality attributes only; it does not by itself remove burned-in pixel text
or unusual private identifiers. Always verify output before sharing.

## Develop / run from source (Windows)

```bash
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python -m pytest
.venv\Scripts\python main.py
```

Build the double-click app (a `dist/De-ID/` folder containing `De-ID.exe`):

```bash
.venv\Scripts\pyinstaller De-ID.spec --noconfirm --clean
```

## License

TBD.
