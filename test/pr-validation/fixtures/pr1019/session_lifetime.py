"""Exercise real Atlas routes with the existing minimal HTTP OIDC provider."""

import asyncio
import os
import secrets
import sys
import tempfile
import threading
import time
from http.server import HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pr1009"))
import two_sessions as provider  # noqa: E402


class RefreshingIdP(provider.IdPHandler):
    mode = "success"
    refresh_calls = 0

    def _json(self, payload, status=200):
        if "access_token" in payload:
            # Within Atlas's refresh margin, so every use exercises the IdP.
            payload = {**payload, "expires_in": 1}
        return super()._json(payload, status)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        form = parse_qs(body.decode())
        if form.get("grant_type") == ["refresh_token"]:
            type(self).refresh_calls += 1
            if self.mode == "refused":
                return self._json({"error": "invalid_grant"}, 400)
            if self.mode == "unavailable":
                return self._json({"error": "temporarily_unavailable"}, 503)
            return self._json({
                "access_token": "refreshed-access", "refresh_token": "rotated-refresh",
                "token_type": "Bearer",
            })
        # Reuse the provider's authorization-code and token-exchange handling.
        from io import BytesIO

        self.rfile = BytesIO(body)
        return super().do_POST()


def login(client):
    response = client.get("/auth/oidc/login", follow_redirects=False)
    params = parse_qs(urlparse(response.headers["location"]).query)
    provider.NONCE_HOLDER["nonce"] = params["nonce"][0]
    response = client.get(
        "/auth/oidc/callback", params={"code": "login", "state": params["state"][0]},
        follow_redirects=False,
    )
    assert response.status_code == 302 and response.headers["location"] == "/"


def exercise():
    import atlas.main as atlas_main
    from atlas.core.oidc.delegation import get_delegation_manager
    from atlas.core.oidc.mcp_delegation import mint_delegated_token_for_server
    from atlas.core.oidc.session import get_session_store
    from atlas.modules.mcp_tools.token_storage import get_token_storage

    client = TestClient(atlas_main.app)
    login(client)
    response = client.get("/api/auth/oidc/status")
    assert response.status_code == 200 and response.json()["authenticated"]
    assert RefreshingIdP.refresh_calls == 1
    print("PASSED: non-delegated HTTP use refreshes the access token")

    RefreshingIdP.mode = "unavailable"
    assert client.get("/api/auth/oidc/status").status_code == 200
    RefreshingIdP.mode = "success"
    assert client.get("/api/auth/oidc/status").status_code == 200
    assert RefreshingIdP.refresh_calls == 3
    print("PASSED: an IdP outage retains the session and later use retries")

    token = asyncio.run(mint_delegated_token_for_server(provider.USER_EMAIL, "tools", {
        "auth_type": "delegated", "url": "https://tools.example.gov/mcp",
        "delegation": {"audience": "api://validation-tools"},
    }))
    assert token is not None
    storage = get_token_storage()
    storage.store_token(
        user_email=provider.USER_EMAIL, server_name="tools", token_value=token.access_token,
        token_type="oauth_access", expires_at=time.time() + 300,
        metadata={"source": "delegation"},
    )
    RefreshingIdP.mode = "refused"
    assert client.get("/api/auth/oidc/status").status_code == 401
    assert not get_session_store().iter_sessions()
    assert not storage.get_user_tokens(provider.USER_EMAIL)
    assert get_delegation_manager().invalidate_user(provider.USER_EMAIL) == 0
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("/auth/oidc/login")
    print("PASSED: invalid_grant removes the login and delegated credentials; reload starts sign-in")

    RefreshingIdP.mode = "success"
    login(client)
    with client.websocket_connect("/ws") as socket:
        RefreshingIdP.mode = "refused"
        socket.send_json({"type": "attach_file", "s3_key": "unused"})
        ended = socket.receive_json()
        assert ended.get("type") == "session_ended" and "sign in" in ended.get("reason", ""), ended
        try:
            socket.receive_json()
        except WebSocketDisconnect as exc:
            assert exc.code == 1008
        else:
            raise AssertionError("An invalidated OIDC socket accepted another operation")
    print("PASSED: an already-open OIDC WebSocket refuses further use")


def main():
    idp = HTTPServer(("127.0.0.1", 0), RefreshingIdP)
    provider.ISSUER = f"http://127.0.0.1:{idp.server_port}"
    threading.Thread(target=idp.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="atlas-oidc-validation-") as state:
            os.environ.update({
                "FEATURE_OIDC_AUTH_ENABLED": "true",
                "FEATURE_OIDC_DELEGATION_ENABLED": "true",
                "OIDC_DELEGATION_PROVIDER": "token_exchange",
                "OIDC_ISSUER": provider.ISSUER,
                "OIDC_CLIENT_ID": provider.CLIENT_ID,
                "OIDC_CLIENT_SECRET": secrets.token_urlsafe(32),
                "OIDC_SESSION_SECRET": secrets.token_urlsafe(32),
                "MCP_TOKEN_ENCRYPTION_KEY": secrets.token_urlsafe(32),
                "OIDC_REDIRECT_URI": "http://testserver/auth/oidc/callback",
                "DEBUG_MODE": "false",
                "FEATURE_PROXY_SECRET_ENABLED": "false",
                "FEATURE_AGENT_PORTAL_ENABLED": "false",
                "FEATURE_WEBSOCKET_ORIGIN_CHECK_ENABLED": "false",
                "APP_LOG_DIR": state,
                "MCP_TOKEN_STORAGE_DIR": f"{state}/tokens",
                "CHAT_HISTORY_DB_URL": f"duckdb:///{state}/history.db",
                "AGENT_PORTAL_DB_URL": f"duckdb:///{state}/portal.db",
            })
            exercise()
    finally:
        idp.shutdown()
        idp.server_close()


if __name__ == "__main__":
    main()
