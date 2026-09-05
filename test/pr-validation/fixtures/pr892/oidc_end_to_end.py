"""End-to-end driver for PR #892: OIDC login and delegated credentials.

Stands up a real (if minimal) OIDC provider on localhost -- discovery document,
JWKS, token endpoint that issues a genuinely RS256-signed ID token, and an
RFC 8693 token-exchange endpoint -- then drives the real Atlas app against it
over HTTP: login redirect, callback, an authenticated API call carrying no
identity header and no proxy secret, a delegated token exchange for an MCP
server, and revocation.

Nothing here is mocked inside Atlas: the only stand-in is the IdP itself.
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
EXCHANGE_CALLS = []
CLIENT_ID = "atlas-validation"
USER_EMAIL = "validation-user@example.gov"


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

    def do_POST(self):
        if urlparse(self.path).path != "/token":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
        grant = form.get("grant_type")

        if grant == "urn:ietf:params:oauth:grant-type:token-exchange":
            EXCHANGE_CALLS.append(form)
            return self._json({
                "access_token": "downstream-token-for-" + form.get("audience", "?"),
                "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "token_type": "Bearer", "expires_in": 300,
                "scope": form.get("scope", ""),
            })

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
            "access_token": "user-access-token", "refresh_token": "user-refresh-token",
            "id_token": id_token, "token_type": "Bearer", "expires_in": 3600,
            "scope": "openid profile email",
        })


NONCE_HOLDER = {}


def start_idp():
    global ISSUER
    server = HTTPServer(("127.0.0.1", 0), IdPHandler)
    ISSUER = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


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
        "OIDC_REDIRECT_URI": "http://testserver/auth/oidc/callback",
        "DEBUG_MODE": "false",
        "FEATURE_PROXY_SECRET_ENABLED": "true",
        "PROXY_SECRET": "validation-proxy-secret",
        "FEATURE_AGENT_PORTAL_ENABLED": "false",
        "SKIP_AUTHORIZATION_CHECKS": "false",
    })

    from starlette.testclient import TestClient

    import atlas.main as atlas_main

    check("OIDC login is enabled at startup",
          atlas_main.config.app_settings.feature_oidc_auth_enabled)

    order = [m.cls.__name__ for m in atlas_main.app.user_middleware]
    check("SessionMiddleware is outside AuthMiddleware",
          order.index("SessionMiddleware") < order.index("AuthMiddleware"),
          f"(order: {order})")

    client = TestClient(atlas_main.app)

    print("\n1. Unauthenticated browser request starts the login flow")
    response = client.get("/", follow_redirects=False)
    check("Unauthenticated browser is redirected to OIDC login",
          response.status_code == 302
          and response.headers.get("location", "").startswith("/auth/oidc/login"),
          f"(got {response.status_code} {response.headers.get('location')})")

    print("\n2. Login redirect is a real Authorization Code + PKCE request")
    response = client.get("/auth/oidc/login?next=/workspace", follow_redirects=False)
    location = response.headers.get("location", "")
    params = parse_qs(urlparse(location).query)
    check("Login redirects to the discovered authorization endpoint",
          location.startswith(f"{ISSUER}/authorize"), f"(got {location[:80]})")
    check("PKCE challenge method is S256", params.get("code_challenge_method") == ["S256"])
    check("A code challenge is present", bool(params.get("code_challenge", [""])[0]))
    check("response_type is code", params.get("response_type") == ["code"])
    NONCE_HOLDER["nonce"] = params.get("nonce", [None])[0]

    print("\n3. Callback exchanges the code and establishes a session")
    state = params.get("state", [""])[0]
    response = client.get(
        f"/auth/oidc/callback?code=validation-code&state={state}", follow_redirects=False
    )
    check("Callback redirects to the requested next path",
          response.headers.get("location") == "/workspace",
          f"(got {response.headers.get('location')})")

    print("\n4. The session authenticates API calls with no header or proxy secret")
    response = client.get("/api/auth/oidc/status")
    body = response.json()
    check("Status reports an authenticated session",
          response.status_code == 200 and body.get("authenticated") is True, str(body))
    check("Status names the logged-in user",
          (body.get("session") or {}).get("user") == USER_EMAIL, str(body))
    check("Status leaks no token material",
          "user-refresh-token" not in json.dumps(body)
          and "user-access-token" not in json.dumps(body))

    response = client.get("/api/config/shell")
    check("A real API endpoint accepts the session alone",
          response.status_code == 200, f"(got {response.status_code})")
    check("The API reports the OIDC identity",
          response.json().get("user") == USER_EMAIL)

    print("\n5. A fresh browser with no session is still rejected")
    stranger = TestClient(atlas_main.app)
    check("Unauthenticated API call is 401",
          stranger.get("/api/config/shell").status_code == 401)

    print("\n6. Delegation exchanges the user token for an audience-bound one")
    import asyncio

    from atlas.core.oidc.mcp_delegation import (
        mint_delegated_token_for_server,
        revoke_delegated_credentials,
    )
    from atlas.modules.config.models import MCPServerConfig

    server_config = MCPServerConfig(**{
        "url": "https://tools.example.gov/mcp",
        "auth_type": "delegated",
        "delegation": {"audience": "api://validation-tools", "scope": "tools.read"},
    }).model_dump()

    token = asyncio.run(
        mint_delegated_token_for_server(USER_EMAIL, "validation-tools", server_config)
    )
    check("A delegated token was minted", token is not None)
    check("The delegated token is not the user's own token",
          token is not None and token.access_token != "user-access-token")
    check("The IdP received an RFC 8693 token-exchange request", len(EXCHANGE_CALLS) == 1)
    if EXCHANGE_CALLS:
        call = EXCHANGE_CALLS[0]
        check("Exchange carried the user's token as subject_token",
              call.get("subject_token") == "user-access-token")
        check("Exchange carried the configured audience",
              call.get("audience") == "api://validation-tools", str(call.get("audience")))
        check("Exchange carried the configured scope",
              call.get("scope") == "tools.read", str(call.get("scope")))

    print("\n7. Revocation clears the delegated credential")
    from atlas.modules.mcp_tools.token_storage import get_token_storage

    get_token_storage().store_token(
        user_email=USER_EMAIL, server_name="validation-tools",
        token_value=token.access_token if token else "x", token_type="oauth_access",
        expires_at=time.time() + 300, metadata={"source": "delegation"},
    )
    removed = asyncio.run(revoke_delegated_credentials(USER_EMAIL))
    check("Revocation removed the stored delegated token", removed == 1, f"(removed={removed})")
    check("The delegated token is gone from storage",
          get_token_storage().get_token(USER_EMAIL, "validation-tools") is None)

    print("\n8. Logout drops the session")
    logout = client.get("/auth/oidc/logout", follow_redirects=False)
    check("Logout redirects to the provider end-session endpoint",
          logout.status_code == 302
          and logout.headers.get("location", "").startswith(f"{ISSUER}/logout"),
          f"(got {logout.status_code} {logout.headers.get('location')})")
    # The session is gone, so this client is now indistinguishable from a
    # stranger: the API rejects it rather than answering with a status body.
    after = client.get("/api/auth/oidc/status")
    check("The session no longer authenticates API calls",
          after.status_code == 401 or after.json().get("authenticated") is False,
          f"(got {after.status_code} {after.text[:80]})")

    idp.shutdown()

    print("\n==========================================")
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("All PR #892 validation checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
