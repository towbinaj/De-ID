"""Self-updater: version compare, offline path, checksum, folder swap/rollback.

These never touch the network (``_get`` / ``urlopen`` are monkeypatched) and
never require the frozen exe (the folder-swap core is a pure function).
"""

from __future__ import annotations

import hashlib
import json

import pytest

from deid import paths, updater


class _FakeResp:
    """Minimal stand-in for an http response usable as a context manager."""

    def __init__(self, data: bytes):
        self._data = data
        self.headers = {"Content-Length": str(len(data))}

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            data, self._data = self._data, b""
            return data
        chunk, self._data = self._data[:n], self._data[n:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# -- version comparison -----------------------------------------------------
def test_is_newer_lenient():
    assert updater.is_newer("v0.3.0", "0.2.0")
    assert updater.is_newer("0.2.10", "0.2.9")      # not lexicographic
    assert updater.is_newer("v1.0.0", "0.9.9")
    assert not updater.is_newer("v0.2.0", "0.2.0")
    assert not updater.is_newer("0.1.0", "0.2.0")


# -- release check (GET-only, never raises) ---------------------------------
def test_check_latest_offline(monkeypatch):
    def boom(url, timeout=15):
        raise OSError("offline")
    monkeypatch.setattr(updater, "_get", boom)
    assert updater.check_latest() is None


def test_check_latest_not_newer(monkeypatch):
    payload = {"tag_name": "v0.0.1", "assets": []}
    monkeypatch.setattr(updater, "_get",
                        lambda url, timeout=15: json.dumps(payload).encode())
    assert updater.check_latest() is None


def test_check_latest_newer_with_asset(monkeypatch):
    payload = {
        "tag_name": "v99.0.0", "body": "release notes",
        "assets": [
            {"name": "De-ID-v99.0.0.zip", "size": 123,
             "browser_download_url": "https://example.org/De-ID-v99.0.0.zip"},
            {"name": "De-ID-v99.0.0.zip.sha256",
             "browser_download_url":
                 "https://example.org/De-ID-v99.0.0.zip.sha256"},
        ]}
    monkeypatch.setattr(updater, "_get",
                        lambda url, timeout=15: json.dumps(payload).encode())
    info = updater.check_latest()
    assert info is not None
    assert info.version == "99.0.0" and info.tag == "v99.0.0"
    assert info.url.endswith("De-ID-v99.0.0.zip")
    assert info.sha256_url.endswith(".sha256")
    assert info.size == 123


def test_check_latest_newer_without_zip_asset(monkeypatch):
    payload = {"tag_name": "v99.0.0",
               "assets": [{"name": "notes.txt",
                           "browser_download_url": "https://example.org/n"}]}
    monkeypatch.setattr(updater, "_get",
                        lambda url, timeout=15: json.dumps(payload).encode())
    assert updater.check_latest() is None


# -- checksum ---------------------------------------------------------------
def test_verify_sha256(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello")
    good = hashlib.sha256(b"hello").hexdigest()
    assert updater.verify_sha256(p, good)
    assert updater.verify_sha256(p, good.upper())      # case-insensitive
    assert not updater.verify_sha256(p, "0" * 64)


def test_download_accepts_good_checksum(tmp_path, monkeypatch):
    data = b"the real zip bytes"
    good = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda req, timeout=600: _FakeResp(bytes(data)))
    monkeypatch.setattr(updater, "_get",
                        lambda url, timeout=30: f"{good}  x.zip".encode())
    info = updater.UpdateInfo("9.9.9", "v9.9.9", "https://example.org/x.zip",
                              len(data), "https://example.org/x.zip.sha256", "")
    dest = tmp_path / "x.zip"
    out = updater.download(info, dest)
    assert out == dest and dest.read_bytes() == data


def test_download_rejects_bad_checksum(tmp_path, monkeypatch):
    data = b"tampered bytes"
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda req, timeout=600: _FakeResp(bytes(data)))
    monkeypatch.setattr(updater, "_get",
                        lambda url, timeout=30: b"deadbeef  x.zip")
    info = updater.UpdateInfo("9.9.9", "v9.9.9", "https://example.org/x.zip",
                              len(data), "https://example.org/x.zip.sha256", "")
    dest = tmp_path / "x.zip"
    with pytest.raises(ValueError):
        updater.download(info, dest)
    assert not dest.exists()        # partial file deleted on mismatch


# -- folder swap / rollback (pure function) ---------------------------------
def _make_build(folder, marker):
    folder.mkdir(parents=True)
    (folder / "De-ID.exe").write_text(marker, encoding="utf-8")
    internal = folder / "_internal"
    internal.mkdir()
    (internal / "lib.dll").write_text(marker, encoding="utf-8")


def test_swap_in_place_success(tmp_path):
    app = tmp_path / "De-ID"
    staging = tmp_path / "De-ID.update"
    _make_build(app, "old")
    _make_build(staging, "new")
    old = updater.swap_in_place(app, staging)
    assert (app / "De-ID.exe").read_text() == "new"
    assert (app / "_internal" / "lib.dll").read_text() == "new"
    assert old.name == "De-ID.old"
    assert (old / "De-ID.exe").read_text() == "old"   # previous build kept
    assert staging.exists()                            # staging left for cleanup


def test_swap_in_place_rolls_back_on_failure(tmp_path, monkeypatch):
    app = tmp_path / "De-ID"
    staging = tmp_path / "De-ID.update"
    _make_build(app, "old")
    _make_build(staging, "new")

    def boom(src, dst, *a, **k):
        raise OSError("copy failed mid-way")
    monkeypatch.setattr(updater.shutil, "copytree", boom)

    with pytest.raises(OSError):
        updater.swap_in_place(app, staging)
    # Original build restored; no half-updated state, no leftover .old.
    assert (app / "De-ID.exe").read_text() == "old"
    assert (app / "_internal" / "lib.dll").read_text() == "old"
    assert not (tmp_path / "De-ID.old").exists()


def test_apply_update_noop_from_source():
    assert updater.can_self_update() is False
    with pytest.raises(RuntimeError):
        updater.apply_update(
            updater.UpdateInfo("1.0.0", "v1.0.0", "u", 0, None, ""))


# -- data location / migration ----------------------------------------------
def test_source_uses_portable(tmp_path, monkeypatch):
    monkeypatch.delenv("DEID_DATA", raising=False)
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    monkeypatch.setattr(paths, "app_dir", lambda: tmp_path)
    assert paths.data_dir() == tmp_path / "De-ID-data"


def test_frozen_uses_stable_dir_and_migrates_once(tmp_path, monkeypatch):
    monkeypatch.delenv("DEID_DATA", raising=False)
    appdir = tmp_path / "app"
    appdir.mkdir()
    portable = appdir / "De-ID-data"
    portable.mkdir()
    (portable / "settings.json").write_text('{"my_aetitle":"OLD"}',
                                             encoding="utf-8")
    (portable / "projects").mkdir()
    (portable / "projects" / "p.json").write_text("{}", encoding="utf-8")

    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(paths, "app_dir", lambda: appdir)
    monkeypatch.setattr(paths, "_portable_migration_done", False)

    stable = local / "De-ID"
    assert paths.data_dir() == stable
    assert (stable / "settings.json").read_text(encoding="utf-8") == \
        '{"my_aetitle":"OLD"}'
    assert (stable / "projects" / "p.json").exists()
    assert (portable / "settings.json").exists()       # source never deleted
