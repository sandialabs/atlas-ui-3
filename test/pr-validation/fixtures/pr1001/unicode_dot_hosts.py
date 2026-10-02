"""End-to-end driver for PR #1001: Unicode-dot internal hosts and ``testserver`` trust.

Runs Atlas's real MCP OAuth discovery chain (``get_server_oauth_metadata``:
the unauthenticated 401 probe, RFC 9728 protected-resource metadata, RFC 8414
authorization-server metadata) against a TLS mock "remote" MCP server at
``https://mcp.validation.test``. Its protected-resource document names an
authorization server, and the same mock answers as that authorization server
on every host, with metadata that would be accepted if Atlas fetched it.

- Control: a public-looking issuer (``auth.validation.test``) is discovered.
- ``https://a\\u3002localhost``: httpx connects to ``a.localhost``. Before
  #1001, the raw-hostname check saw a non-internal name, so Atlas fetched it
  and adopted it as the authorization server. Now it is refused and the mock
  sees no request for that host.
- ``testserver`` is an ordinary name now: an MCP server there is not given
  loopback trust, and an ``http://testserver`` OIDC issuer is refused.

Hostnames are pinned to 127.0.0.1 in this process only, and the mock's
self-signed certificate is trusted through ``SSL_CERT_FILE``. Nothing inside
Atlas is mocked.
"""

import asyncio
import datetime
import ipaddress
import json
import os
import socket
import ssl
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

FAILURES = []
PINNED = {"mcp.validation.test", "auth.validation.test", "a.localhost", "testserver"}
HITS = []
PORT = None


def check(label, condition, detail=""):
    print(f"{'PASSED' if condition else 'FAILED'}: {label}" + ("" if condition else f" {detail}"))
    if not condition:
        FAILURES.append(label)


def pin_names():
    real = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        name = host.decode("ascii") if isinstance(host, bytes) else host
        if name in PINNED:
            host = "127.0.0.1"
        return real(host, *args, **kwargs)

    socket.getaddrinfo = getaddrinfo


def make_cert(directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "atlas-pr1001-validation")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName(
            [x509.DNSName(n) for n in sorted(PINNED)]
            + [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path = os.path.join(directory, "cert.pem")
    key_path = os.path.join(directory, "key.pem")
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    return cert_path, key_path


AUTH_ISSUER = {"value": None}  # the issuer the protected-resource document names


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _host(self):
        return (self.headers.get("Host") or "").rsplit(":", 1)[0]

    def _json(self, payload, status=200, headers=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        HITS.append((self._host(), "POST", self.path))
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        origin = f"https://mcp.validation.test:{PORT}"
        self._json({"error": "unauthorized"}, 401, {
            "WWW-Authenticate":
                f'Bearer resource_metadata="{origin}/.well-known/oauth-protected-resource/mcp"',
        })

    def do_GET(self):
        host = self._host()
        HITS.append((host, "GET", self.path))
        if host == "mcp.validation.test" and self.path.startswith("/.well-known/oauth-protected-resource"):
            self._json({
                "resource": f"https://mcp.validation.test:{PORT}/mcp",
                "authorization_servers": [AUTH_ISSUER["value"]],
            })
        elif host != "mcp.validation.test" and self.path == "/.well-known/oauth-authorization-server":
            # Answer as whichever authorization server was named, with metadata
            # that would be accepted -- so only Atlas's own refusal stops it.
            issuer = AUTH_ISSUER["value"]
            self._json({
                "issuer": issuer,
                "authorization_endpoint": f"https://{host}:{PORT}/authorize",
                "token_endpoint": f"https://{host}:{PORT}/token",
                "code_challenge_methods_supported": ["S256"],
            })
        else:
            self._json({"error": "not_found"}, 404)


def start_server(cert_path, key_path):
    global PORT
    server = HTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    PORT = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def discover(issuer):
    from atlas.modules.mcp_tools.mcp_oauth import (
        MCPOAuthError,
        clear_metadata_cache,
        get_server_oauth_metadata,
    )

    AUTH_ISSUER["value"] = issuer
    HITS.clear()
    clear_metadata_cache()
    try:
        metadata = await get_server_oauth_metadata(f"https://mcp.validation.test:{PORT}/mcp")
        return metadata.authorization_server.issuer, None
    except MCPOAuthError as exc:
        return None, str(exc)


async def main():
    tmp = tempfile.mkdtemp(prefix="pr1001-")
    cert_path, key_path = make_cert(tmp)
    os.environ["SSL_CERT_FILE"] = cert_path
    pin_names()
    server = start_server(cert_path, key_path)
    print(f"TLS mock listening on 127.0.0.1:{PORT}")

    print("\n1. Control: a public-looking authorization server is discovered")
    control = f"https://auth.validation.test:{PORT}"
    issuer, error = await discover(control)
    check("Discovery adopts auth.validation.test", issuer == control, f"(error: {error})")

    print("\n2. A remote document naming https://a。localhost is refused")
    hostile = f"https://a。localhost:{PORT}"
    issuer, error = await discover(hostile)
    check("Discovery does not adopt the Unicode-dot localhost issuer", issuer is None,
          f"(adopted {issuer!r})")
    check("Discovery fails instead of using it", error is not None)
    a_hits = [h for h in HITS if h[0] == "a.localhost"]
    check("No request reached a.localhost", not a_hits, str(a_hits))

    print("\n3. testserver gets no loopback trust")
    from atlas.core.oidc.discovery import OIDCDiscoveryError, _validate_issuer_url
    from atlas.modules.mcp_tools.mcp_oauth import is_loopback_url

    check("A testserver MCP server is not treated as loopback",
          is_loopback_url(f"https://testserver:{PORT}/mcp") is False)
    try:
        _validate_issuer_url("http://testserver/realms/atlas")
        refused = False
    except OIDCDiscoveryError:
        refused = True
    check("An http://testserver OIDC issuer is refused", refused)

    server.shutdown()
    print("\n==========================================")
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("All PR #1001 validation checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
