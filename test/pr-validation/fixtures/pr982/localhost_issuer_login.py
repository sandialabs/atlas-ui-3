"""End-to-end driver for PR #982: http OIDC issuers on RFC 6761 ``*.localhost`` names.

Stands up a minimal, real OIDC provider (discovery, JWKS, token endpoint that
issues an RS256-signed ID token) and configures Atlas with an ``http://``
issuer on a ``*.localhost`` name, as a local ingress setup would
(``http://keycloak.localhost``). Then drives the real Atlas app through login:
discovery, the authorization redirect, the callback, and an authenticated API
call. Before this change, every login failed with "OIDC issuer must be an
https:// URL".

It also checks that look-alike hosts are still refused before any network
fetch, so only the reserved ``.localhost`` suffix is widened.

Nothing inside Atlas is mocked. Browsers and most resolvers map ``*.localhost``
to loopback; where the host resolver does not (some Linux setups without
nss-myhostname or systemd-resolved), the name is pinned to 127.0.0.1 in this
process only, as a local DNS setup would.
"""

import base64
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

FAILURES = []

IDP_HOST = "keycloak.localhost"
CLIENT_ID = "atlas-validation"
USER_EMAIL = "validation-user@example.gov"
ISSUER_PATH = "/realms/atlas"
ISSUER = None          # filled in once the port is known
NONCE_HOLDER = {}


def check(label, condition, detail=""):
    if condition:
        print(f"PASSED: {label}")
    else:
        print(f"FAILED: {label} {detail}")
        FAILURES.append(label)


def ensure_localhost_name_resolves(name):
    """Pin ``name`` to 127.0.0.1 if the host resolver does not already."""
    try:
        socket.getaddrinfo(name, 80)
        return
    except socket.gaierror:
        pass
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        # httpx's async layer (anyio) passes the hostname as bytes, so match both forms.
        if host in (name, name.encode("ascii")):
            host = "127.0.0.1"
        return real_getaddrinfo(host, *args, **kwargs)

    socket.getaddrinfo = getaddrinfo
    print(f"Note: host resolver does not map {name}; pinned to 127.0.0.1 in-process")


# -- A minimal, real OIDC provider ----------------------------------------

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_private_pem = _key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
_public_numbers = _key.public_key().public_numbers()


def _b64(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


JWKS = {"keys": [{
    "kty": "RSA", "use": "sig", "alg": "RS256", "kid": "test-key",
    "n": _b64(_public_numbers.n), "e": _b64(_public_numbers.e),
}]}

DISCOVERY_HITS = []


def _idp_path(raw_path):
    """Path relative to the issuer, which carries a realm prefix like Keycloak's."""
    path = urlparse(raw_path).path
    return path[len(ISSUER_PATH):] if path.startswith(ISSUER_PATH) else None


class IdPHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = _idp_path(self.path)
        if path == "/.well-known/openid-configuration":
            DISCOVERY_HITS.append(self.headers.get("Host"))
            self._json({
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
                "code_challenge_methods_supported": ["S256"],
            })
        elif path == "/jwks":
            self._json(JWKS)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if _idp_path(self.path) != "/token":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        now = int(time.time())
        id_token = jwt.encode(
            {
                "iss": ISSUER, "aud": CLIENT_ID, "sub": "subject-1",
                "email": USER_EMAIL, "iat": now, "exp": now + 600,
                "nonce": NONCE_HOLDER.get("nonce"),
            },
            _private_pem, algorithm="RS256", headers={"kid": "test-key"},
        )
        self._json({
            "access_token": "user-access-token", "refresh_token": "user-refresh-token",
            "id_token": id_token, "token_type": "Bearer", "expires_in": 3600,
            "scope": "openid profile email",
        })


def start_idp():
    global ISSUER
    server = HTTPServer(("127.0.0.1", 0), IdPHandler)
    ISSUER = f"http://{IDP_HOST}:{server.server_port}{ISSUER_PATH}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    ensure_localhost_name_resolves(IDP_HOST)
    idp = start_idp()
    print(f"Mock IdP listening; issuer is {ISSUER}")

    os.environ.update({
        "FEATURE_OIDC_AUTH_ENABLED": "true",
        "OIDC_ISSUER": ISSUER,
        "OIDC_CLIENT_ID": CLIENT_ID,
        "OIDC_CLIENT_SECRET": "validation-secret",
        "OIDC_SESSION_SECRET": "validation-session-secret-at-least-32-chars",
        "OIDC_REDIRECT_URI": "http://testserver/auth/oidc/callback",
        "DEBUG_MODE": "false",
        "FEATURE_PROXY_SECRET_ENABLED": "true",
        "PROXY_SECRET": "validation-proxy-secret",
        "FEATURE_AGENT_PORTAL_ENABLED": "false",
        "SKIP_AUTHORIZATION_CHECKS": "false",
    })

    from starlette.testclient import TestClient

    import atlas.main as atlas_main

    check("OIDC login is enabled with an http *.localhost issuer",
          atlas_main.config.app_settings.feature_oidc_auth_enabled)

    client = TestClient(atlas_main.app)

    print("\n1. Login discovers the provider on the *.localhost issuer")
    response = client.get("/auth/oidc/login?next=/workspace", follow_redirects=False)
    location = response.headers.get("location", "")
    check("Login redirects to the discovered authorization endpoint",
          response.status_code in (302, 307) and location.startswith(f"{ISSUER}/authorize"),
          f"(got {response.status_code} {location[:100]})")
    check("Discovery was fetched from the *.localhost host",
          any(h and h.startswith(IDP_HOST) for h in DISCOVERY_HITS), str(DISCOVERY_HITS))
    params = parse_qs(urlparse(location).query)
    NONCE_HOLDER["nonce"] = params.get("nonce", [None])[0]

    print("\n2. Callback completes the login")
    state = params.get("state", [""])[0]
    response = client.get(
        f"/auth/oidc/callback?code=validation-code&state={state}", follow_redirects=False
    )
    check("Callback redirects to the requested next path",
          response.headers.get("location") == "/workspace",
          f"(got {response.status_code} {response.headers.get('location')})")

    response = client.get("/api/auth/oidc/status")
    body = response.json()
    check("Session is authenticated as the IdP user",
          body.get("authenticated") is True
          and (body.get("session") or {}).get("user") == USER_EMAIL, str(body))
    response = client.get("/api/config/shell")
    check("A real API endpoint accepts the session",
          response.status_code == 200 and response.json().get("user") == USER_EMAIL,
          f"(got {response.status_code})")

    print("\n3. Non-loopback http issuers are still refused")
    from atlas.core.oidc.discovery import OIDCDiscoveryError, _validate_issuer_url

    for issuer in (
        "http://idp.example.gov/realms/atlas",
        "http://localhost.example.gov/realms/atlas",
        "http://evil-localhost/realms/atlas",
        "http://localhost.evil.example/realms/atlas",
    ):
        try:
            _validate_issuer_url(issuer)
            refused = False
        except OIDCDiscoveryError:
            refused = True
        check(f"Refused {issuer}", refused)

    idp.shutdown()

    print("\n==========================================")
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("All PR #982 validation checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
