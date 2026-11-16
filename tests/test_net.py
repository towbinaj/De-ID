"""SCU: C-ECHO and C-STORE against a throwaway local SCP."""

from __future__ import annotations

import ssl

from deid.model import Node
from deid.net import scu
from deid.net.scp import InboxSCP
from deid.net.tls import client_context, client_tls_args
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
    res = scu.c_store("DEID", Node("x", "AE", "127.0.0.1", 11112), [])
    assert res.failed == 0 and res.sent == 0 and res.errors


# -- Receiver hardening: bind scope + calling-AE allowlist -----------------

def test_inbox_scp_defaults_to_loopback(tmp_path):
    scp = InboxSCP("DEID_SCP", 0, tmp_path / "inbox")
    assert scp.bind_host == "127.0.0.1"      # not reachable off-box by default
    assert scp.allowed_aets == []            # empty = any peer


def test_inbox_scp_allowlist_rejects_unlisted_calling_ae(tmp_path):
    scp = InboxSCP("DEID_SCP", 0, tmp_path / "inbox",
                   bind_host="127.0.0.1", allowed_aets=["GOOD_SCU"])
    scp.start()
    try:
        port = scp._server.server_address[1]
        node = Node("t", "DEID_SCP", "127.0.0.1", port, timeout=10)
        # A calling AE that is not on the allowlist must be refused...
        assert not scu.c_echo("BAD_SCU", node, timeout=10).success
        # ...while a listed one is accepted.
        assert scu.c_echo("GOOD_SCU", node, timeout=10).success
    finally:
        scp.stop()


def test_inbox_scp_no_allowlist_accepts_any(tmp_path):
    scp = InboxSCP("DEID_SCP", 0, tmp_path / "inbox", bind_host="127.0.0.1")
    scp.start()
    try:
        port = scp._server.server_address[1]
        node = Node("t", "DEID_SCP", "127.0.0.1", port, timeout=10)
        assert scu.c_echo("ANYONE", node, timeout=10).success
    finally:
        scp.stop()


# -- TLS peer authentication ------------------------------------------------

def test_client_context_verifies_peer_by_default():
    ctx = client_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is False        # off for IP-addressed PACS


def test_client_context_hostname_check_is_opt_in():
    ctx = client_context(check_hostname=True)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_client_context_insecure_mode_is_explicit():
    ctx = client_context(verify=False)
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_client_tls_args_none_when_not_tls():
    assert client_tls_args(Node("n", "AE", "192.0.2.9", 104, tls=False)) is None


def test_client_tls_args_honors_node_settings():
    node = Node("n", "AE", "192.0.2.9", 104, tls=True,
                tls_verify=True, tls_verify_hostname=True)
    ctx, host = client_tls_args(node)
    assert host == "192.0.2.9"
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_client_tls_args_insecure_node_disables_verify():
    node = Node("n", "AE", "192.0.2.9", 104, tls=True, tls_verify=False)
    ctx, _ = client_tls_args(node)
    assert ctx.verify_mode == ssl.CERT_NONE


def test_node_tls_fields_round_trip():
    node = Node("n", "AE", "192.0.2.9", 104, tls=True, tls_verify=False,
                tls_verify_hostname=True, tls_ca_file="C:/certs/pacs.pem")
    back = Node.from_dict(node.to_dict())
    assert back.tls_verify is False
    assert back.tls_verify_hostname is True
    assert back.tls_ca_file == "C:/certs/pacs.pem"
