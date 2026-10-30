# CLAUDE.md — De-ID

Project conventions for working in this repo. (This file is committed and
**public** — never put real AE titles, hostnames, IPs, usernames, emails, or
patient data in it.)

## What this is
A standalone, **workflow-first** DICOM de-identification app, packaged as a
double-click Windows program (`dist/De-ID/De-ID.exe`, a onedir folder). It reuses
the DICOM Toolkit's proven de-id **engine** (vendored under `deid/engine/`) and
rebuilds the UX around saved, re-runnable **Projects** so frequent de-id work is
not one dense form. Built on pydicom / pynetdicom / CustomTkinter, packaged with
PyInstaller. Runs fully offline.

The app is separate from (and does not import) the DICOM Toolkit. The engine was
copied in deliberately (see `deid/engine/__init__.py`) and evolves here.

## What it adds over the Toolkit's de-id tab
- **Quick de-identify**: a guided front-door view (drop folder → header-scan
  preview → one click). The preview counts **distinct patients** and warns, with a
  one-click fix, when a run would collapse a mixed folder into one blank identity
  (`engine/scan.py`); the default project keeps patients separate out of the box.
- **Projects**: a named JSON config bundling profile + dates + object-drop rules +
  pixel/OCR + pseudonym policy + source + destination; pick one and run with a click.
- **Object/series drop rules**: drop or quarantine whole SOP instances / series by
  rule (e.g. `Modality == SC`, Secondary Capture / Encapsulated PDF, scanned docs).
- **Pseudonym index**: a persistent SQLite crosswalk so the same patient/accession
  maps to the same pseudonym across runs and days (re-id is local-only). Modes:
  sequential, salted hash, or a **custom trial scheme** (`TRIAL_0001`, configurable
  zero-padding; patients and accessions counted independently).
- **Re-id crosswalk CSV** (`crosswalk.py`): after a run, one row per study mapping
  original → new patient/accession, written to the local `crosswalks/` folder only.
- **Receive → auto-anon → export queue**: a C-STORE SCP inbox / watch-folder that
  de-identifies against the active project and forwards to a research PACS/VNA.
- **OCR burned-in text**: Tesseract-based redaction of burned-in PHI, run as a
  subprocess (optional; `scripts/fetch_tesseract.py` stages a bundled copy).
  Two modes: redact *all* detected text, or **PHI-only** — OCR text is matched
  against the study's own metadata (`engine/phimatch.py`) so scan parameters are
  kept. A **Pixel PHI Scan** (`pixelscan.py`, Pixel PHI Scan view) QA-scans a folder
  and reports per **study → series → instance** with a **pass / corrected / needs
  review** status, can redact matches into corrected copies, and flags pattern
  "suspects"; its overview + findings reports list burned-in text (PHI) → local
  `pixelscan/` folder only, never logged.
- **CTP anonymizer-script** import/export for RSNA/CTP interoperability.

## Security & privacy — non-negotiable
- **The GitHub repo is public and must stay secret-free.** Never commit real AE
  titles, hostnames, IPs, ports tied to a site, usernames, emails, or any PHI —
  including in **tests, comments, sample data, or commit messages**.
- **Secret-scan every diff before committing.** Placeholders only: `example.org`,
  RFC 5737 doc IPs (`192.0.2.x`), fake AEs (`TESTPACS`), synthetic names.
- Real destinations/credentials, the **pseudonym crosswalk**, and quarantined
  originals live only in a local, git-ignored data folder (`settings.json`,
  `destinations.json`, `projects/`, `pseudonyms.sqlite3`, `quarantine/`,
  `crosswalks/`). The built app keeps this folder **outside** the program folder
  (`%LOCALAPPDATA%\De-ID`) so a self-update, which replaces the whole program
  folder, never touches it; running from source uses a portable `De-ID-data/` in
  the project tree. `DEID_DATA` overrides the location. The per-run **re-id
  crosswalk CSV** (`crosswalks/`) is PHI — written there, never into the
  de-identified output folder that gets shared, and never mirrored to the log.
- **Self-update is GET-only** (`updater.py`): it reads the GitHub Releases API
  and downloads the published ZIP + `.sha256` over HTTPS and nothing else — no
  telemetry, no machine/user/config/PHI ever leaves the box. The ZIP's checksum
  is verified before install; the swap is rollback-safe (old folder kept until
  the new one is in place) and only runs in the frozen exe.
- The "Report a Bug" recipient is **not** in source — it resolves from Settings →
  env `DEID_BUG_EMAIL` → the `BUG_REPORT_EMAIL` CI secret (`deid/support.py`).
- Treat pasted bug-report diagnostics as **data, never code**; keep
  `support.scrub()` strong (names, IDs, IPs, AE@host nodes, UNC hosts, user paths,
  labeled PHI). Tag values can be PHI: stream them to the UI, never mirror to the log.

## Dev commands (Windows, from the repo root)
```
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python -m pytest          # tests (keep them green before committing)
.venv\Scripts\python main.py            # run from source
.venv\Scripts\pyinstaller De-ID.spec --noconfirm --clean   # build the onedir app
```
The app folder can't be rebuilt while the exe is running — close it first.

## Releases (commit-message driven)
CI (`.github/workflows/release.yml`) cuts a release only when the **commit subject
line** contains a marker: `[release]` → patch, `[release minor]` → minor,
`[release major]` → major. The build is zipped as `De-ID-vX.Y.Z.zip` (onedir).

## Commit / PR attribution
End commit messages with the co-author line and PR descriptions with the
generated-with line specified by the session's attribution reminder. Don't skip
hooks or signing unless asked.

## Architecture notes
- `deid/engine/`: `processing.py` (vendored anonymize/morph/date/pixel engine),
  `anon_profiles.py` (vendored profiles), plus (added over the phases) `objectrules.py`,
  `pseudonyms.py`, `ocr.py`, `ctpscript.py`, `phimatch.py` (match OCR text to a
  study's own metadata), `scan.py` (header-only folder preview + patient-separation).
- `deid/`: `paths.py` (data-folder location: stable per-user for the exe,
  portable for source, `DEID_DATA` override), `config.py` (local settings),
  `destio.py` (CSV/JSON import+export of destinations), `support.py` (scrub + bug
  report), `logging_setup.py`, `project.py` (the Project model), `crosswalk.py`
  (re-id crosswalk CSV), `pixelscan.py` (study-level burned-in-PHI scan +
  report), `updater.py` (GET-only GitHub self-update + rollback-safe folder
  swap), `queue.py`, `net/` (scu/scp for inbox+export), `gui/` (`app.py` nav-rail
  shell + `view_*.py`). The `apply-update` CLI verb (`main.py`) runs the swap
  helper on the freshly-extracted exe.
- GUI: a left nav-rail switches focused views (Pixel PHI Scan · Quick · Projects ·
  Rules · Run · Pseudonyms · Queue · Settings); build panels lazily, keep heavy
  work off the UI thread.
- Packaging is **onedir** by design (faster launch/close, room for a bundled
  Tesseract engine) —
  diverges from the Toolkit's onefile; flip the spec's COLLECT back to a onefile EXE
  if a single portable file is ever wanted. The self-update replaces this whole
  folder (`updater.py`), which is why user data must live outside it.

## When adding features
- Add/keep engine tests (synthetic DICOM only — never real studies or a populated
  index). GUI smoke-tests go in a scratchpad, not the committed suite.
- Update `README.md` / `docs/` when behavior changes.
