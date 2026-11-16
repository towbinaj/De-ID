"""TLS (secure DICOM) helpers (vendored from the DICOM Toolkit).

DICOM-over-TLS peers are frequently identified by IP rather than a certificate
hostname, so hostname checking defaults off while certificate-chain verification
against a CA is kept on. Two ways to authenticate the peer are supported:

* **Pin the peer certificate** - point ``ca_file`` at the peer's own (often
  self-signed) certificate; the chain then verifies only against that cert, so
  only that exact peer is trusted even though the hostname is not checked.
* **Verify the hostname** - set ``check_hostname=True`` when the peer presents a
  certificate whose subject/SAN matches the host you connect to.

Disabling verification entirely (``verify=False`` -> ``CERT_NONE``) encrypts the
link but authenticates nothing (MITM / wrong-peer risk); it is an explicit,
logged opt-in for testing only.
"""

from __future__ import annotations

import ssl

from ..logging_setup import get_logger

log = get_logger("net.tls")


def client_context(ca_file: str = "", cert_file: str = "", key_file: str = "",
                   verify: bool = True,
                   check_hostname: bool = False) -> ssl.SSLContext:
    """SSL context for an outgoing (SCU) association.

    ``verify`` keeps certificate-chain verification on (default). ``ca_file``
    sets the trust anchor - point it at the peer's own certificate to pin it.
    ``check_hostname`` additionally requires the certificate to match the host.
    ``verify=False`` disables all peer authentication and is logged as insecure.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if not verify:
        # Encrypt-but-don't-authenticate. CERT_NONE requires check_hostname off.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        log.warning("TLS peer verification is DISABLED (CERT_NONE): the peer "
                    "identity is not checked. Use only for testing.")
    else:
        ctx.verify_mode = ssl.CERT_REQUIRED
        if ca_file:
            ctx.load_verify_locations(cafile=ca_file)
        else:
            ctx.load_default_certs()
        # Must be set after verify_mode; True needs CERT_REQUIRED.
        ctx.check_hostname = bool(check_hostname)
    if cert_file and key_file:
        ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
    return ctx


def client_tls_args(node):
    """``(ssl_context, server_hostname)`` for a :class:`~deid.model.Node`, or
    ``None`` when the node is not TLS. Honors the node's per-destination TLS
    authentication settings (chain verify, hostname check, pinned CA/cert)."""
    if not getattr(node, "tls", False):
        return None
    ctx = client_context(
        ca_file=getattr(node, "tls_ca_file", "") or "",
        verify=bool(getattr(node, "tls_verify", True)),
        check_hostname=bool(getattr(node, "tls_verify_hostname", False)))
    return (ctx, node.host)


def server_context(cert_file: str, key_file: str, ca_file: str = "",
                   require_client_cert: bool = False) -> ssl.SSLContext:
    """SSL context for the receiver (SCP). Requires a server cert + key."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
    if ca_file:
        ctx.load_verify_locations(cafile=ca_file)
    if require_client_cert:
        ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx
