# De-ID

A standalone, **workflow-first** DICOM de-identification app for Windows — one
double-click program built for *frequent* de-id work.

It reuses the proven de-identification engine from the DICOM Toolkit (PS3.15 Basic
Confidentiality Profile, consistent UID remapping, age-preserving date shifting,
crosswalk morphing, pixel blanking) and wraps it in a workflow designed around
saved, re-runnable **Projects** instead of one dense form.

## Download

Grab the latest `De-ID-vX.Y.Z.zip` from the
[**Releases**](https://github.com/towbinaj/De-ID/releases) page, unzip it, and run
`De-ID.exe` — no install. Each release also ships a `.sha256` to verify the zip.

## Why a separate app

The DICOM Toolkit's "De-identify Files" tab does the job but crams ~20 controls
into a single pane — fine occasionally, tiring when you de-identify every day.
De-ID keeps the same engine and rebuilds the experience around focused views.

## What it does

- **Quick de-identify** — a guided front door: drop a folder, read a plain-English
  preview, press one button. No need to visit the other tabs first.
- **Keeps patients separate** — before a run, a header-only scan reports how many
  **distinct patients** a folder holds and warns (with a one-click fix) when a
  setting would collapse a mixed folder into one blank identity. On by default via
  per-patient pseudonyms.
- **Projects** — define a de-id recipe once (profile, dates, drop rules, pixel/OCR,
  pseudonym policy, source, destination) and re-run it on new files with one click.
- **Consistent pseudonyms, your way** — a persistent local crosswalk so the same
  patient or accession always maps to the same anonymized ID across runs and days.
  Sequential (`ID-000001`), salted hash, or a **trial scheme** like `TRIAL_0001`
  with a configurable number of leading zeros (patients and accessions numbered
  independently). Re-identification is local-only.
- **Re-identification crosswalk** — after a run, a CSV maps each study back to the
  original patient/accession (one row per study), written to a local, secure
  folder — never into the de-identified output that gets shared.
- **Object/series drop rules** — automatically drop or quarantine whole instances
  or series by rule (Secondary Capture, encapsulated PDFs, scanned documents, …),
  which commonly carry burned-in PHI.
- **Burned-in pixel text (OCR)** — detect and redact burned-in PHI beyond fixed
  rectangle blanking. Two modes: redact **all** detected text, or **PHI-only**,
  which compares OCR'd text to the study's **own metadata** (name / MRN /
  accession / dates) so legitimate scan parameters are kept. OCR uses the
  **Tesseract** engine, run as a subprocess; it's optional and skipped cleanly if
  no engine is found (the built app bundles a copy).
- **Pixel PHI Scan** — a read-only QA pass that reports burned-in PHI per **study →
  series → instance** with a status — **pass** / **corrected** / **needs review** —
  and can redact the metadata-matched PHI into corrected copies. Pattern-based
  "suspects" (date / long-number / name-like text not in the metadata) are flagged
  for a human. Overview + per-finding CSV reports are written to a local, secure
  folder.
- **Receive → anonymize → export queue** — a C-STORE inbox / watch-folder that
  de-identifies incoming studies against the active project and forwards them to a
  research PACS/VNA.
- **CTP compatibility** — import/export RSNA/CTP anonymizer scripts.

## Privacy & security

Everything identifying stays **local**. The pseudonym crosswalk, re-id crosswalk
CSVs, pixel-scan reports, quarantined originals, destinations, and settings live in
a git-ignored `De-ID-data/` folder next to the app. The public repository contains
no real AE titles, hosts, IPs, credentials, or patient data. Diagnostics in a bug
report are PHI-scrubbed before they leave the machine.

**De-ID is a tool, not a guarantee.** The PS3.15 Basic Profile acts on standard
confidentiality attributes only; burned-in pixel text and unusual private
identifiers need the OCR / drop-rule / pixel-scan features, and even then OCR is
best-effort. Always verify output before sharing — the Pixel PHI Scan is there to
help you check.

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

To bundle the Tesseract OCR engine into the build, run
`python scripts/fetch_tesseract.py` first (needs a local Tesseract install, e.g.
`winget install UB-Mannheim.TesseractOCR`).

## License

TBD.
