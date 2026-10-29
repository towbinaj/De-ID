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
- **Projects**: a named JSON config bundling profile + dates + object-drop rules +
  pixel/OCR + pseudonym policy + source + destination; pick one and run with a click.
- **Object/series drop rules**: drop or quarantine whole SOP instances / series by
  rule (e.g. `Modality == SC`, Secondary Capture / Encapsulated PDF, scanned docs).
- **Pseudonym index**: a persistent SQLite crosswalk so the same patient/accession
  maps to the same pseudonym across runs and days (re-id is local-only).
- **Receive → auto-anon → export queue**: a C-STORE SCP inbox / watch-folder that
  de-identifies against the active project and forwards to a research PACS/VNA.
- **OCR burned-in text**: RapidOCR-based redaction of burned-in PHI (optional).
- **CTP anonymizer-script** import/export for RSNA/CTP interoperability.

## Security & privacy — non-negotiable
- **The GitHub repo is public and must stay secret-free.** Never commit real AE
  titles, hostnames, IPs, ports tied to a site, usernames, emails, or any PHI —
  including in **tests, comments, sample data, or commit messages**.
- **Secret-scan every diff before committing.** Placeholders only: `example.org`,
  RFC 5737 doc IPs (`192.0.2.x`), fake AEs (`TESTPACS`), synthetic names.
- Real destinations/credentials, the **pseudonym crosswalk**, and quarantined
  originals live only in the local, git-ignored `De-ID-data/` (`settings.json`,
  `destinations.json`, `projects/`, `pseudonyms.sqlite3`, `quarantine/`).
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
  `pseudonyms.py`, `ocr.py`, `ctpscript.py`.
- `deid/`: `paths.py` (portable `De-ID-data`, `DEID_DATA` override), `config.py`
  (local settings), `support.py` (scrub + bug report), `logging_setup.py`,
  `project.py` (the Project model), `net/` (scu/scp for inbox+export), `gui/`
  (`app.py` nav-rail shell + `view_*.py`).
- GUI: a left nav-rail switches focused views (Projects · Rules · Pseudonyms · Run ·
  Queue · Settings); build panels lazily, keep heavy work off the UI thread.
- Packaging is **onedir** by design (faster launch/close, room for OCR models) —
  diverges from the Toolkit's onefile; flip the spec's COLLECT back to a onefile EXE
  if a single portable file is ever wanted.

## When adding features
- Add/keep engine tests (synthetic DICOM only — never real studies or a populated
  index). GUI smoke-tests go in a scratchpad, not the committed suite.
- Update `README.md` / `docs/` when behavior changes.
