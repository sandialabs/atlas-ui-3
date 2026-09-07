"""End-to-end driver for the MCP OAuth 2.1 authorization flow.

Stands up a real (if minimal) OAuth-protected MCP deployment on localhost:

- an MCP endpoint that answers an unauthenticated ``initialize`` with ``401``
  and a genuine RFC 9728 ``WWW-Authenticate: ... resource_metadata="..."``
  challenge, and serves tools once a bearer token is presented;
- the protected-resource document that challenge points at;
- an authorization server publishing RFC 8414 metadata with an RFC 7591
  ``registration_endpoint``, plus authorize, token and revoke endpoints that
  really validate PKCE, the redirect URI and the issued codes.

The real Atlas app is then driven against it over HTTP through its own
routes. Nothing inside Atlas is mocked: the only stand-ins are the MCP server
and its provider, which is exactly what a third-party deployment would be.
"""

import base64
import hashlib
import json
import os
import secrets
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"PASSED: {label}")
    else:
        print(f"FAILED: {label} {detail}")
        FAILURES.append(label)


# -- A real, minimal OAuth-protected MCP server ---------------------------

ORIGIN = None  # filled in once the port is known
SERVER_NAME = "validation-oauth-mcp"
USER_EMAIL = "test@example.com"
ATLAS_BASE = "http://testserver"
EXPECTED_REDIRECT = f"{ATLAS_BASE}/api/mcp/auth/{SERVER_NAME}/oauth/callback"

REGISTRATIONS = []          # RFC 7591 registration bodies received
AUTHORIZE_REQUESTS = []     # authorize query strings received
TOKEN_REQUESTS = []         # token endpoint form bodies received
REVOCATIONS = []            # revoked token values
ISSUED_CODES = {}           # code -> (code_challenge, redirect_uri)
ACTIVE_ACCESS_TOKENS = set()
REFRESH_TOKENS = {}         # refresh token -> client_id


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class ProviderHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, payload, status=200, headers=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/.well-known/oauth-protected-resource/mcp":
            return self._json({
                "resource": ORIGIN,
                "authorization_servers": [ORIGIN],
                "bearer_methods_supported": ["header"],
                "scopes_supported": ["read", "write"],
            })

        if path == "/.well-known/oauth-authorization-server":
            return self._json({
                "issuer": ORIGIN,
                "authorization_endpoint": f"{ORIGIN}/authorize",
                "token_endpoint": f"{ORIGIN}/token",
                "registration_endpoint": f"{ORIGIN}/register",
                "revocation_endpoint": f"{ORIGIN}/revoke",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
                "scopes_supported": ["read", "write"],
            })

        if path == "/authorize":
            params = parse_qs(urlparse(self.path).query)
            AUTHORIZE_REQUESTS.append(params)
            redirect_uri = params.get("redirect_uri", [""])[0]
            # A real provider only redirects to a registered URI.
            if redirect_uri != EXPECTED_REDIRECT:
                return self._json({"error": "invalid_redirect_uri"}, status=400)
            code = secrets.token_urlsafe(16)
            ISSUED_CODES[code] = (
                params.get("code_challenge", [""])[0], redirect_uri
            )
            state = params.get("state", [""])[0]
            location = f"{redirect_uri}?code={code}&state={state}"
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()
            return

        return self._json({"error": "not_found"}, status=404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode() if length else ""

        if path == "/mcp":
            authorization = self.headers.get("Authorization") or ""
            presented = authorization[7:] if authorization.startswith("Bearer ") else ""
            if presented not in ACTIVE_ACCESS_TOKENS:
                return self._json(
                    {"error": "missing_token"},
                    status=401,
                    headers={
                        "WWW-Authenticate": (
                            f'Bearer resource_metadata='
                            f'"{ORIGIN}/.well-known/oauth-protected-resource/mcp"'
                        )
                    },
                )
            return self._json({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})

        if path == "/register":
            body = json.loads(raw)
            REGISTRATIONS.append(body)
            if EXPECTED_REDIRECT not in body.get("redirect_uris", []):
                return self._json({"error": "invalid_redirect_uri"}, status=400)
            return self._json(
                {
                    "client_id": f"dcr-{len(REGISTRATIONS)}",
                    "client_id_issued_at": 0,
                    "client_secret_expires_at": 0,
                    "redirect_uris": body["redirect_uris"],
                },
                status=201,
            )

        if path == "/token":
            form = {key: value[0] for key, value in parse_qs(raw).items()}
            TOKEN_REQUESTS.append(form)
            grant = form.get("grant_type")

            if grant == "authorization_code":
                code = form.get("code", "")
                if code not in ISSUED_CODES:
                    return self._json({"error": "invalid_grant"}, status=400)
                challenge, redirect_uri = ISSUED_CODES.pop(code)
                # PKCE is really verified, not just accepted.
                if _s256(form.get("code_verifier", "")) != challenge:
                    return self._json({"error": "invalid_grant"}, status=400)
                if form.get("redirect_uri") != redirect_uri:
                    return self._json({"error": "invalid_grant"}, status=400)
                access = f"at-{secrets.token_urlsafe(8)}"
                refresh = f"rt-{secrets.token_urlsafe(8)}"
                ACTIVE_ACCESS_TOKENS.add(access)
                REFRESH_TOKENS[refresh] = form.get("client_id")
                return self._json({
                    "access_token": access, "refresh_token": refresh,
                    "token_type": "Bearer", "expires_in": 3600, "scope": "read write",
                })

            if grant == "refresh_token":
                presented = form.get("refresh_token", "")
                if presented not in REFRESH_TOKENS:
                    return self._json({"error": "invalid_grant"}, status=400)
                access = f"at-refreshed-{secrets.token_urlsafe(6)}"
                ACTIVE_ACCESS_TOKENS.add(access)
                return self._json({
                    "access_token": access, "token_type": "Bearer",
                    "expires_in": 3600, "scope": "read write",
                })

            return self._json({"error": "unsupported_grant_type"}, status=400)

        if path == "/revoke":
            form = {key: value[0] for key, value in parse_qs(raw).items()}
            presented = form.get("token", "")
            REVOCATIONS.append(presented)
            ACTIVE_ACCESS_TOKENS.discard(presented)
            REFRESH_TOKENS.pop(presented, None)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        return self._json({"error": "not_found"}, status=404)


def start_provider():
    global ORIGIN
    server = HTTPServer(("127.0.0.1", 0), ProviderHandler)
    ORIGIN = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    provider = start_provider()

    storage_dir = tempfile.mkdtemp(prefix="atlas-pr898-")
    os.environ.update({
        "MCP_TOKEN_ENCRYPTION_KEY": "pr898-validation-encryption-key-at-least-32-chars",
        "MCP_TOKEN_STORAGE_DIR": storage_dir,
        "MCP_OAUTH_REDIRECT_BASE_URL": ATLAS_BASE,
        "DEBUG_MODE": "true",
        "FEATURE_PROXY_SECRET_ENABLED": "false",
        "FEATURE_OIDC_AUTH_ENABLED": "false",
        # Header-auth deployment with no OIDC/Globus login: the MCP OAuth flow
        # supplies its own session secret so it still has somewhere to hold
        # the PKCE verifier and state.
        "MCP_OAUTH_SESSION_SECRET": "pr898-validation-session-secret-at-least-32-chars",
    })

    from starlette.testclient import TestClient

    import atlas.main as atlas_main
    from atlas.infrastructure.app_factory import app_factory

    # Register the OAuth-protected server exactly as an operator's mcp.json
    # entry would appear once loaded.
    manager = app_factory.get_mcp_manager()
    manager.servers_config[SERVER_NAME] = {
        "description": "Validation OAuth MCP server",
        "url": f"{ORIGIN}/mcp",
        "transport": "http",
        "auth_type": "oauth",
        "groups": ["users"],
        "enabled": True,
    }

    client = TestClient(atlas_main.app, headers={"X-User-Email": USER_EMAIL})

    print("\n1. The server reports as requiring OAuth and advertises a start URL")
    response = client.get("/api/mcp/auth/status")
    check("Auth status is served", response.status_code == 200,
          f"(got {response.status_code} {response.text[:120]})")
    entries = {row["server_name"]: row for row in response.json().get("servers", [])}
    entry = entries.get(SERVER_NAME, {})
    check("The OAuth server appears in auth status", bool(entry), str(list(entries)))
    check("It is reported as unauthenticated", entry.get("authenticated") is False)
    check("It advertises the OAuth start URL",
          entry.get("oauth_start_url") == f"/api/mcp/auth/{SERVER_NAME}/oauth/start",
          str(entry.get("oauth_start_url")))

    print("\n2. The advertised start route exists and redirects to the provider")
    response = client.get(
        f"/api/mcp/auth/{SERVER_NAME}/oauth/start", follow_redirects=False
    )
    check("Start route is not a 404", response.status_code != 404,
          f"(got {response.status_code})")
    check("Start route redirects", response.status_code == 302,
          f"(got {response.status_code} {response.text[:200]})")
    location = response.headers.get("location", "")
    check("It redirects to the discovered authorization endpoint",
          location.startswith(f"{ORIGIN}/authorize"), f"(got {location[:100]})")

    params = parse_qs(urlparse(location).query)
    check("PKCE challenge method is S256", params.get("code_challenge_method") == ["S256"])
    check("A code challenge is present", bool(params.get("code_challenge", [""])[0]))
    check("response_type is code", params.get("response_type") == ["code"])
    check("The redirect_uri is the Atlas callback",
          params.get("redirect_uri") == [EXPECTED_REDIRECT],
          str(params.get("redirect_uri")))
    check("Scopes come from the protected-resource document",
          params.get("scope") == ["read write"], str(params.get("scope")))
    check("An RFC 8707 resource indicator is sent",
          params.get("resource") == [ORIGIN], str(params.get("resource")))

    print("\n3. Atlas registered itself via Dynamic Client Registration")
    check("The provider received a registration request", len(REGISTRATIONS) == 1,
          f"(got {len(REGISTRATIONS)})")
    if REGISTRATIONS:
        body = REGISTRATIONS[0]
        check("Registration declared the Atlas callback",
              body.get("redirect_uris") == [EXPECTED_REDIRECT])
        check("Registration asked for the refresh_token grant",
              "refresh_token" in body.get("grant_types", []))
        check("Registration is a public client using PKCE",
              body.get("token_endpoint_auth_method") == "none")
    check("The authorize request used the issued client_id",
          params.get("client_id") == ["dcr-1"], str(params.get("client_id")))

    print("\n4. A forged callback state is rejected")
    forged = client.get(
        f"/api/mcp/auth/{SERVER_NAME}/oauth/callback",
        params={"code": "anything", "state": "not-the-issued-state"},
        follow_redirects=False,
    )
    check("Unknown state is rejected",
          "mcp_auth_error=invalid_state" in forged.headers.get("location", ""),
          f"(got {forged.headers.get('location')})")

    print("\n5. The user consents and the provider redirects back to Atlas")
    import httpx

    consent = httpx.get(location, follow_redirects=False)
    check("The provider accepted the authorization request",
          consent.status_code == 302, f"(got {consent.status_code} {consent.text[:120]})")
    callback_params = parse_qs(urlparse(consent.headers["location"]).query)
    authorization_code = callback_params.get("code", [""])[0]
    returned_state = callback_params.get("state", [""])[0]
    check("The provider issued an authorization code", bool(authorization_code))

    response = client.get(
        f"/api/mcp/auth/{SERVER_NAME}/oauth/callback",
        params={"code": authorization_code, "state": returned_state},
        follow_redirects=False,
    )
    check("The callback succeeds",
          "mcp_auth_success=1" in response.headers.get("location", ""),
          f"(got {response.headers.get('location')})")

    print("\n6. The code was redeemed with a verified PKCE verifier")
    exchanges = [form for form in TOKEN_REQUESTS if form.get("grant_type") == "authorization_code"]
    check("The provider received exactly one code exchange", len(exchanges) == 1,
          f"(got {len(exchanges)})")
    if exchanges:
        check("The exchange carried a code_verifier",
              bool(exchanges[0].get("code_verifier")))
        check("The exchange carried the resource indicator",
              exchanges[0].get("resource") == ORIGIN, str(exchanges[0].get("resource")))

    print("\n7. Tokens are stored per user and encrypted at rest")
    from atlas.modules.mcp_tools.token_storage import get_token_storage

    stored = get_token_storage().get_token(USER_EMAIL, SERVER_NAME)
    check("An access token is stored for the user", stored is not None)
    check("It is recorded as an OAuth access token",
          stored is not None and stored.token_type == "oauth_access")
    check("A refresh token was stored", stored is not None and bool(stored.refresh_token))
    check("The stored token is the one the provider issued",
          stored is not None and stored.token_value in ACTIVE_ACCESS_TOKENS)

    token_file = os.path.join(storage_dir, "mcp_tokens.enc")
    raw_bytes = open(token_file, "rb").read()
    check("Tokens are not written in plaintext",
          stored is not None and stored.token_value.encode() not in raw_bytes)

    registration_file = os.path.join(storage_dir, "mcp_oauth_clients.enc")
    check("The client registration was persisted", os.path.exists(registration_file))
    check("The registration is not written in plaintext",
          b"dcr-1" not in open(registration_file, "rb").read())

    print("\n8. Status now reports the server as authenticated")
    entry = {
        row["server_name"]: row
        for row in client.get("/api/mcp/auth/status").json()["servers"]
    }.get(SERVER_NAME, {})
    check("Status reports the server as authenticated",
          entry.get("authenticated") is True, str(entry))
    check("Status reports a refresh token is held",
          entry.get("has_refresh_token") is True, str(entry))

    print("\n9. Another user is unaffected (per-user isolation)")
    stranger = TestClient(atlas_main.app, headers={"X-User-Email": "other@example.com"})
    stranger_entry = {
        row["server_name"]: row
        for row in stranger.get("/api/mcp/auth/status").json()["servers"]
    }.get(SERVER_NAME, {})
    check("A different user is still unauthenticated",
          stranger_entry.get("authenticated") is False, str(stranger_entry))
    check("No token exists for the other user",
          get_token_storage().get_token("other@example.com", SERVER_NAME) is None)

    print("\n10. The stored token actually authenticates against the MCP server")
    probe = httpx.post(
        f"{ORIGIN}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers={"Authorization": f"Bearer {stored.token_value}"},
    )
    check("The MCP server accepts the issued token", probe.status_code == 200,
          f"(got {probe.status_code})")
    unauth = httpx.post(
        f"{ORIGIN}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    check("The MCP server still rejects an unauthenticated call",
          unauth.status_code == 401, f"(got {unauth.status_code})")
    check("Its challenge carries resource_metadata",
          "resource_metadata" in (unauth.headers.get("www-authenticate") or ""))

    print("\n11. An expired access token is refreshed silently")
    import asyncio
    import time

    get_token_storage().store_token(
        user_email=USER_EMAIL, server_name=SERVER_NAME,
        token_value=stored.token_value, token_type="oauth_access",
        expires_at=time.time() - 60, refresh_token=stored.refresh_token,
        scopes=stored.scopes, metadata=stored.metadata,
    )
    check("The token now reads as expired",
          get_token_storage().get_valid_token(USER_EMAIL, SERVER_NAME) is None)

    refreshed = asyncio.run(
        manager._refresh_oauth_token(
            USER_EMAIL, SERVER_NAME, manager.servers_config[SERVER_NAME]
        )
    )
    check("A refreshed token was obtained", refreshed is not None)
    check("The refreshed token is new",
          refreshed is not None and refreshed.token_value != stored.token_value)
    check("The provider received a refresh_token grant",
          any(form.get("grant_type") == "refresh_token" for form in TOKEN_REQUESTS))
    check("The refresh token was carried forward",
          refreshed is not None and refreshed.refresh_token == stored.refresh_token)
    check("The server is usable again without user interaction",
          get_token_storage().get_valid_token(USER_EMAIL, SERVER_NAME) is not None)

    print("\n12. Disconnect clears the token and revokes it at the provider")
    response = client.delete(f"/api/mcp/auth/{SERVER_NAME}/token")
    check("Disconnect succeeds", response.status_code == 200,
          f"(got {response.status_code} {response.text[:120]})")
    check("Disconnect reports revocation at the provider",
          response.json().get("revoked_at_provider") is True, response.text[:160])
    check("The provider received a revocation", len(REVOCATIONS) >= 1)
    check("The token is gone from storage",
          get_token_storage().get_token(USER_EMAIL, SERVER_NAME) is None)

    final = {
        row["server_name"]: row
        for row in client.get("/api/mcp/auth/status").json()["servers"]
    }.get(SERVER_NAME, {})
    check("Status reports the server as unauthenticated again",
          final.get("authenticated") is False, str(final))

    provider.shutdown()

    print("\n==========================================")
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("All MCP OAuth validation checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
