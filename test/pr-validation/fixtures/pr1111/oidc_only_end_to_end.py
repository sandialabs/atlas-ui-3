"""End-to-end driver for PR #1111: OIDC-only mode (FEATURE_HEADER_AUTH_ENABLED=false).

Reuses the minimal real OIDC provider from the PR #892 fixture (discovery,
JWKS, RS256-signed ID tokens) and drives the real Atlas app against it with
header auth disabled. A valid proxy secret plus an arbitrary identity header
must not authenticate on HTTP or /ws; a completed OIDC login must; signed
download links must keep working.

Nothing inside Atlas is mocked: the only stand-in is the IdP itself.
"""

import importlib.util
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_spec = importlib.util.spec_from_file_location(
    "pr892_idp", Path(__file__).resolve().parents[1] / "pr892" / "oidc_end_to_end.py"
)
idp_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(idp_module)

check = idp_module.check
FAILURES = idp_module.FAILURES
PROXY_SECRET = "validation-proxy-secret"
SPOOFED = {"X-Proxy-Secret": PROXY_SECRET, "X-User-Email": "admin@example.gov"}


def main():
    idp = idp_module.start_idp()
    issuer = idp_module.ISSUER
    print(f"Mock IdP listening on {issuer}")

    os.environ.update({
        "FEATURE_HEADER_AUTH_ENABLED": "false",
        "FEATURE_OIDC_AUTH_ENABLED": "true",
        "OIDC_ISSUER": issuer,
        "OIDC_CLIENT_ID": idp_module.CLIENT_ID,
        "OIDC_CLIENT_SECRET": "validation-secret",
        "OIDC_SESSION_SECRET": "validation-session-secret-at-least-32-chars",
        "OIDC_REDIRECT_URI": "http://testserver/auth/oidc/callback",
        "DEBUG_MODE": "false",
        "FEATURE_PROXY_SECRET_ENABLED": "true",
        "PROXY_SECRET": PROXY_SECRET,
        "AUTH_USER_HEADER": "X-User-Email",
        "AUTH_USER_HEADER_TYPE": "email-string",
        "FEATURE_DOMAIN_WHITELIST_ENABLED": "false",
        "SKIP_AUTHORIZATION_CHECKS": "false",
    })

    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    import atlas.main as atlas_main
    from atlas.core.capabilities import generate_file_token

    settings = atlas_main.config.app_settings
    check("Header auth is disabled at startup", settings.feature_header_auth_enabled is False)
    check("OIDC login is enabled at startup", settings.feature_oidc_auth_enabled is True)

    stranger = TestClient(atlas_main.app)

    print("\n1. A valid proxy secret plus an identity header does not authenticate")
    response = stranger.get("/api/config/shell", headers=SPOOFED)
    check("Spoofed API call is 401", response.status_code == 401, f"(got {response.status_code})")
    response = stranger.get("/", headers=SPOOFED, follow_redirects=False)
    check("Spoofed browser navigation is sent to OIDC login",
          response.status_code == 302
          and response.headers.get("location", "").startswith("/auth/oidc/login"),
          f"(got {response.status_code} {response.headers.get('location')})")
    try:
        with stranger.websocket_connect("/ws", headers=SPOOFED):
            pass
        check("Spoofed WebSocket handshake is rejected", False, "(connected)")
    except WebSocketDisconnect as exc:
        check("Spoofed WebSocket handshake is rejected", exc.code == 1008, f"(code {exc.code})")

    print("\n2. A real OIDC login authenticates")
    client = TestClient(atlas_main.app)
    response = client.get("/auth/oidc/login?next=/", follow_redirects=False)
    params = parse_qs(urlparse(response.headers.get("location", "")).query)
    idp_module.NONCE_HOLDER["nonce"] = params.get("nonce", [None])[0]
    state = params.get("state", [""])[0]
    client.get(f"/auth/oidc/callback?code=validation-code&state={state}", follow_redirects=False)
    response = client.get("/api/config/shell", headers=SPOOFED)
    check("Logged-in API call succeeds", response.status_code == 200, f"(got {response.status_code})")
    check("The identity is the OIDC user, not the spoofed header",
          response.status_code == 200 and response.json().get("user") == idp_module.USER_EMAIL,
          f"(got {response.text[:120]})")
    try:
        with client.websocket_connect("/ws", headers=SPOOFED):
            pass
        check("Logged-in WebSocket handshake connects", True)
    except WebSocketDisconnect as exc:
        check("Logged-in WebSocket handshake connects", False, f"(code {exc.code} {exc.reason})")

    print("\n3. Signed download links still authenticate without a session")
    key = "users/validation-user@example.gov/missing.txt"
    token = generate_file_token(idp_module.USER_EMAIL, key)
    response = stranger.get(f"/api/files/download/{key}?token={token}")
    print(f"   /api/files/download with token -> {response.status_code}")
    check("Capability token passes authentication on /api/files/download",
          response.status_code not in (401, 302, 503), f"(got {response.status_code})")
    response = stranger.get(f"/mcp/files/download/{key}?token={token}")
    print(f"   /mcp/files/download with token -> {response.status_code}")
    check("Capability token passes authentication on /mcp/files/download",
          response.status_code not in (401, 302, 503), f"(got {response.status_code})")
    response = stranger.get(f"/api/files/download/{key}?token=forged", headers=SPOOFED)
    check("A forged token does not fall back to the header", response.status_code == 401,
          f"(got {response.status_code})")

    idp.shutdown()

    print("\n==========================================")
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("All PR #1111 validation checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
