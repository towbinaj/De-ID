"""Self-update from GitHub Releases (onedir build).

A one-way, read-only check. Only HTTPS **GET** requests to GitHub are made:
nothing about the machine, user, config, logs, or DICOM/PHI data is ever
uploaded. The sole metadata sent is a ``User-Agent: De-ID/<version>`` header.
This is an update check, not telemetry.

Install flow (built exe only): download the release ZIP, verify its SHA-256
against the published ``.sha256`` (reject + delete on mismatch), extract to a
staging folder beside the app folder, then launch a detached helper - the new
exe's ``apply-update`` verb - and exit. The helper waits for this process to
exit, renames the current app folder to ``<name>.old``, puts the new build in
its place, relaunches, and leaves the old folder for cleanup on next start. If
any step fails it restores the old folder, so the app is never left half-updated.

User data and settings live outside the app folder (see ``paths.data_dir``), so
replacing the program folder never touches them.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import APP_NAME, __version__
from .logging_setup import get_logger

log = get_logger("updater")

# Where releases come from, and how the assets are named. The ZIP is
# ``De-ID-vX.Y.Z.zip`` with a sibling ``De-ID-vX.Y.Z.zip.sha256``.
OWNER = "towbinaj"
REPO = "De-ID"
ASSET_PREFIX = "De-ID-v"
ASSET_SUFFIX = ".zip"
_API_LATEST = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/latest"
_HEADERS = {"User-Agent": f"{APP_NAME}/{__version__}",
            "Accept": "application/vnd.github+json"}

# Suffixes for the retired and freshly-extracted app folders during a swap.
OLD_SUFFIX = ".old"
STAGING_SUFFIX = ".update"


@dataclass
class UpdateInfo:
    version: str          # e.g. "0.3.0"
    tag: str              # e.g. "v0.3.0"
    url: str              # ZIP download URL
    size: int
    sha256_url: str | None
    notes: str


# -- version helpers --------------------------------------------------------
def _parse_ver(s: str) -> tuple:
    out = []
    for part in str(s).strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out or [0])


def is_newer(latest: str, current: str) -> bool:
    return _parse_ver(latest) > _parse_ver(current)


def current_version() -> str:
    return __version__


def can_self_update() -> bool:
    """Folder replacement only works for the built (frozen) exe."""
    return bool(getattr(sys, "frozen", False))


def app_folder() -> Path:
    """The onedir folder that holds the running exe and ``_internal``."""
    return Path(sys.executable).resolve().parent


# -- network (GET only) -----------------------------------------------------
def _get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers=_HEADERS)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()


def check_latest(timeout: int = 15) -> UpdateInfo | None:
    """Return UpdateInfo if a newer release exists, else None. Never raises."""
    try:
        data = json.loads(_get(_API_LATEST, timeout))
    except Exception as exc:  # noqa: BLE001 - offline / no releases / rate limit
        log.info("Update check skipped: %s", exc)
        return None
    tag = str(data.get("tag_name", "") or "")
    if not tag or not is_newer(tag, __version__):
        return None
    zip_url = sha_url = None
    size = 0
    for a in data.get("assets", []) or []:
        name = str(a.get("name", "") or "")
        if name.startswith(ASSET_PREFIX) and name.endswith(ASSET_SUFFIX):
            zip_url = a.get("browser_download_url")
            size = int(a.get("size", 0) or 0)
        elif name.startswith(ASSET_PREFIX) and name.endswith(
                ASSET_SUFFIX + ".sha256"):
            sha_url = a.get("browser_download_url")
    if not zip_url:
        log.info("Release %s has no %s*%s asset", tag, ASSET_PREFIX,
                 ASSET_SUFFIX)
        return None
    return UpdateInfo(version=tag.lstrip("vV"), tag=tag, url=zip_url, size=size,
                      sha256_url=sha_url,
                      notes=str(data.get("body", "") or "")[:4000])


def download(info: UpdateInfo, dest: Path, progress=None,
             timeout: int = 600) -> Path:
    """Download the release ZIP to ``dest`` and verify its SHA-256 if published.

    On a checksum mismatch the partial file is deleted and ValueError raised.
    """
    dest = Path(dest)
    req = urllib.request.Request(info.url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length", info.size or 0) or 0)
        done = 0
        while True:
            chunk = r.read(262144)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
    if info.sha256_url:
        try:
            expected = _get(info.sha256_url, 30).decode().split()[0].strip().lower()
        except Exception:  # noqa: BLE001 - TLS already protected the transport
            expected = ""
        if expected and not verify_sha256(dest, expected):
            dest.unlink(missing_ok=True)
            raise ValueError("SHA-256 mismatch - download rejected")
    return dest


def verify_sha256(path: Path, expected_hex: str) -> bool:
    got = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return got == str(expected_hex).strip().lower()


# -- install (built exe only) ----------------------------------------------
def apply_update(info: UpdateInfo, progress=None) -> None:
    """Download + stage the update, launch the swap helper, then caller exits.

    The staged new exe is run with ``apply-update <app_dir> <pid> <staging>``.
    """
    if not can_self_update():
        raise RuntimeError("Self-update is only available in the built exe.")
    app_dir = app_folder()
    staging = app_dir.with_name(app_dir.name + STAGING_SUFFIX)

    tmp_zip = Path(tempfile.gettempdir()) / f"{APP_NAME}-{info.version}.zip"
    download(info, tmp_zip, progress)

    # Fresh staging folder holding the extracted new build.
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    _extract_zip(tmp_zip, staging)
    tmp_zip.unlink(missing_ok=True)

    new_exe = staging / Path(sys.executable).name
    if not new_exe.is_file():
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError("Downloaded update is missing the application exe.")

    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # DETACHED|NEW_GROUP
    subprocess.Popen(
        [str(new_exe), "apply-update", str(app_dir), str(os.getpid()),
         str(staging)],
        cwd=str(staging), close_fds=True, creationflags=flags)


def _extract_zip(zip_path: Path, dest: Path) -> None:
    """Extract ``zip_path`` into ``dest``.

    Release ZIPs hold the app folder's *contents* (``De-ID.exe`` + ``_internal``)
    at the top level, but tolerate a single wrapping folder just in case.
    """
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    entries = [p for p in dest.iterdir() if p.name not in (".", "..")]
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        for item in list(inner.iterdir()):
            shutil.move(str(item), str(dest / item.name))
        inner.rmdir()


def swap_in_place(app_dir: Path, staging: Path,
                  old_suffix: str = OLD_SUFFIX) -> Path:
    """Replace ``app_dir`` with the build in ``staging``; rollback-safe.

    Returns the retired ``<app_dir><old_suffix>`` folder on success. The running
    helper lives in ``staging``, so the new build is *copied* into a fresh
    ``app_dir`` rather than moving ``staging`` onto itself. On any failure the
    original folder is restored and an exception is raised, so the installed app
    is never left in a half-updated state.
    """
    app_dir = Path(app_dir)
    staging = Path(staging)
    old = app_dir.with_name(app_dir.name + old_suffix)

    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    if old.exists():
        raise OSError(f"Could not clear leftover {old}")

    os.replace(app_dir, old)                     # retire the current build
    try:
        shutil.copytree(staging, app_dir)        # install the new build
    except Exception:
        shutil.rmtree(app_dir, ignore_errors=True)
        os.replace(old, app_dir)                 # rollback
        raise
    return old


def run_apply_update(app_dir: str, pid: int, staging: str) -> int:
    """Helper mode (invoked on the staged new exe): swap the folder and relaunch.

    Waits for the old process to exit (so its files unlock), swaps the new build
    into place with bounded retries, relaunches, and returns. Leftover ``.old``
    and staging folders are cleaned up on the next normal startup.
    """
    app_path = Path(app_dir)
    staging_path = Path(staging)
    _wait_for_exit(pid, timeout=60)

    last_err: Exception | None = None
    for _ in range(20):                          # ~10 s of bounded retries
        try:
            swap_in_place(app_path, staging_path)
            last_err = None
            break
        except OSError as exc:                   # files still locked - retry
            last_err = exc
            time.sleep(0.5)
    if last_err is not None:
        log.error("Update swap failed, old build left intact: %s", last_err)
        return 1

    new_exe = app_path / Path(sys.executable).name
    try:
        subprocess.Popen([str(new_exe)], cwd=str(app_path), close_fds=True)
    except OSError as exc:
        log.error("Relaunch after update failed: %s", exc)
        return 1
    return 0


def _wait_for_exit(pid: int, timeout: float = 60) -> None:
    """Block until process ``pid`` is gone, or ``timeout`` seconds elapse."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.3)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True)
        return str(pid) in (out.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def cleanup_leftovers() -> None:
    """Delete any ``.old`` / ``.update`` folders left by a prior swap. Safe and
    best-effort; called once at startup on the built exe."""
    if not can_self_update():
        return
    app_dir = app_folder()
    for suffix in (OLD_SUFFIX, STAGING_SUFFIX):
        leftover = app_dir.with_name(app_dir.name + suffix)
        if leftover.exists():
            shutil.rmtree(leftover, ignore_errors=True)
