"""SCU: C-ECHO and C-STORE against a throwaway local SCP."""

from __future__ import annotations

from deid.net import scu
from conftest import make_ct, save, storage_scp


def test_c_echo_success(tmp_path):
    with storage_scp() as (node, _received):
        res = scu.c_echo("DEID", node, timeout=10)
    assert res.success


def test_c_store_sends_files(tmp_path):
    a = save(make_ct(), tmp_path / "a.dcm")
    b = save(make_ct(), tmp_path / "b.dcm")
    with storage_scp() as (node, received):
        res = scu.c_store("DEID", node, [a, b], timeout=10)
    assert res.sent == 2 and res.failed == 0
    assert len(received) == 2


def test_c_store_no_files():
    # No association needed; empty selection is reported, not crashed.
    from deid.model import Node
    res = scu.c_store("DEID", Node("x", "AE", "127.0.0.1", 11112), [])
    assert res.failed == 0 and res.sent == 0 and res.errors
