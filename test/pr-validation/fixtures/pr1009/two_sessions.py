"""End-to-end driver for PR #1009: delegation uses the user's newest usable session.

The same user signs in to the real Atlas app twice (two browsers). The minimal
OIDC provider below then ends the first login's IdP session, as an idle timeout
would: that login's access token is past expiry and its refresh token is refused
with invalid_grant. A delegated token exchange must use the second login's token,
without trying the dead refresh token, instead of failing with "sign in again".

Nothing here is mocked inside Atlas: the only stand-in is the IdP itself (the
same minimal provider as PR #892's validation).
"""

import base64
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"PASSED: {label}")
    else:
        print(f"FAILED: {label} {detail}")
        FAILURES.append(label)


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

ISSUER = None          # filled in once the port is known
CLIENT_ID = "atlas-validation"
USER_EMAIL = "validation-user@example.gov"
LOGINS = []            # tokens issued per login: access, refresh
REFRESH_CALLS = []     # refresh tokens presented
EXCHANGE_CALLS = []    # token-exchange requests
NONCE_HOLDER = {}


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
        path = urlparse(self.path).path
        if path == "/.well-known/openid-configuration":
            return self._json({
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
                "end_session_endpoint": f"{ISSUER}/logout",
                "code_challenge_methods_supported": ["S256"],
                "grant_types_supported": [
                    "authorization_code", "refresh_token",
                    "urn:ietf:params:oauth:grant-type:token-exchange",
                ],
            })
        if path == "/jwks":
            return self._json(JWKS)
        self.send_response(404)
        self.end_headers()
        return None

    def do_POST(self):
        if urlparse(self.path).path != "/token":
            self.send_response(404)
            self.end_headers()
            return None
        length = int(self.headers.get("Content-Length", 0))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
        grant = form.get("grant_type")

        if grant == "urn:ietf:params:oauth:grant-type:token-exchange":
            EXCHANGE_CALLS.append(form)
            return self._json({
                "access_token": "downstream-token-for-" + form.get("subject_token", "?"),
                "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "token_type": "Bearer", "expires_in": 300,
            })
        if grant == "refresh_token":
            REFRESH_CALLS.append(form.get("refresh_token"))
            # Every login's IdP session has ended by the time Atlas refreshes in
            # this scenario: the refresh is refused, as Keycloak does.
            return self._json({"error": "invalid_grant", "error_description": "Token is not active"}, 400)

        # authorization_code: a new login, with its own tokens. The first login's
        # access token is already within Atlas's refresh margin.
        n = len(LOGINS) + 1
        LOGINS.append((f"access-token-{n}", f"refresh-token-{n}"))
        now = int(time.time())
        id_token = jwt.encode(
            {
                "iss": ISSUER, "aud": CLIENT_ID, "sub": "subject-1",
                "email": USER_EMAIL, "iat": now, "exp": now + 600,
                "nonce": NONCE_HOLDER.get("nonce"),
            },
            _private_pem, algorithm="RS256", headers={"kid": "test-key"},
        )
        return self._json({
            "access_token": f"access-token-{n}", "refresh_token": f"refresh-token-{n}",
            "id_token": id_token, "token_type": "Bearer",
            "expires_in": 1 if n == 1 else 3600,
            "scope": "openid profile email",
        })


def start_idp():
    global ISSUER
    server = HTTPServer(("127.0.0.1", 0), IdPHandler)
    ISSUER = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def login(client, label):
    response = client.get("/auth/oidc/login?next=/", follow_redirects=False)
    params = parse_qs(urlparse(response.headers.get("location", "")).query)
    NONCE_HOLDER["nonce"] = params.get("nonce", [None])[0]
    state = params.get("state", [""])[0]
    response = client.get(
        f"/auth/oidc/callback?code={label}&state={state}", follow_redirects=False,
    )
    # Do not make an authenticated request from the stale browser: refresh-on-use
    # would correctly end its session before we can test delegation's selection.
    check(f"{label}: login completed",
          response.status_code == 302 and response.headers.get("location") == "/")


def main():
    idp = start_idp()
    print(f"Mock IdP listening on {ISSUER}")

    os.environ.update({
        "FEATURE_OIDC_AUTH_ENABLED": "true",
        "FEATURE_OIDC_DELEGATION_ENABLED": "true",
        "OIDC_DELEGATION_PROVIDER": "token_exchange",
        "OIDC_ISSUER": ISSUER,
        "OIDC_CLIENT_ID": CLIENT_ID,
        "OIDC_CLIENT_SECRET": "validation-secret",
        "OIDC_SESSION_SECRET": "validation-session-secret-at-least-32-chars",
        "MCP_TOKEN_ENCRYPTION_KEY": "validation-mcp-token-key-at-least-32-chars",
        "OIDC_REDIRECT_URI": "http://testserver/auth/oidc/callback",
        "DEBUG_MODE": "false",
        "FEATURE_PROXY_SECRET_ENABLED": "true",
        "PROXY_SECRET": "validation-proxy-secret",
        "SKIP_AUTHORIZATION_CHECKS": "false",
    })

    from starlette.testclient import TestClient

    import atlas.main as atlas_main

    print("\n1. The same user signs in from two browsers")
    first, second = TestClient(atlas_main.app), TestClient(atlas_main.app)
    login(first, "first-login")
    time.sleep(1.1)  # distinct creation times; the first login's token is now expired
    login(second, "second-login")
    check("The IdP issued two logins", len(LOGINS) == 2, str(LOGINS))

    print("\n2. Delegation uses the second login, not the first (dead) one")
    import asyncio

    from atlas.core.oidc.mcp_delegation import mint_delegated_token_for_server
    from atlas.modules.config.models import MCPServerConfig

    server_config = MCPServerConfig(**{
        "url": "https://tools.example.gov/mcp",
        "auth_type": "delegated",
        "delegation": {"audience": "api://validation-tools", "scope": "tools.read"},
    }).model_dump()
    token = asyncio.run(mint_delegated_token_for_server(USER_EMAIL, "validation-tools", server_config))
    check("A delegated token was minted", token is not None)
    check("The exchange used the second login's access token",
          len(EXCHANGE_CALLS) == 1 and EXCHANGE_CALLS[0].get("subject_token") == "access-token-2",
          str([c.get("subject_token") for c in EXCHANGE_CALLS]))
    check("The first login's dead refresh token was not presented",
          "refresh-token-1" not in REFRESH_CALLS, str(REFRESH_CALLS))

    print("\n3. Once the second login signs out, the first one's ended IdP session shows")
    second.get("/auth/oidc/logout", follow_redirects=False)
    # Another audience, so the delegated-token cache can't answer for it.
    token = asyncio.run(mint_delegated_token_for_server(USER_EMAIL, "other-tools", {
        **server_config, "delegation": {"audience": "api://other-tools", "scope": "tools.read"}}))
    check("No token is minted from a session whose refresh the IdP refuses", token is None)
    check("Its refresh was tried and refused", "refresh-token-1" in REFRESH_CALLS, str(REFRESH_CALLS))

    idp.shutdown()

    print("\n==========================================")
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("All PR #1009 validation checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
