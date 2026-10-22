"""OIDC-only mode: FEATURE_HEADER_AUTH_ENABLED=false.

With header auth disabled an OIDC login session is the only credential. These
tests pin down that the trusted identity header is never consulted -- above
all that a valid proxy secret plus an arbitrary identity header does not
bypass OIDC -- on both the HTTP middleware and the /ws handshake, that signed
download capability tokens keep working, and that startup refuses a
configuration with no way to sign in.
"""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from atlas.core.auth_mode import AuthConfigurationError, validate_auth_modes
from atlas.core.capabilities import generate_file_token
from atlas.core.middleware import AuthMiddleware
from atlas.core.oidc.session import SESSION_COOKIE_KEY, get_session_store
from atlas.core.session_middleware import SessionMiddleware

PROXY_SECRET = "proxy-secret"
SPOOFED = {"X-Proxy-Secret": PROXY_SECRET, "X-User-Email": "admin@example.gov"}


def _build_app(
    *, header_auth_enabled=False, debug_mode=False, proxy_secret_enabled=True, proxy_secret=PROXY_SECRET
):
    app = FastAPI()

    @app.get("/api/whoami")
    async def whoami(request: Request):
        return {"user": request.state.user_email}

    @app.get("/workspace/abc")
    async def page(request: Request):
        return {"user": request.state.user_email}

    @app.get("/api/files/download/{key}")
    async def api_download(request: Request, key: str):
        return {"user": request.state.user_email}

    @app.get("/mcp/files/download/{key}")
    async def mcp_download(request: Request, key: str):
        return {"user": request.state.user_email}

    # Under /auth/oidc/, which AuthMiddleware skips, so the test can plant a
    # login session cookie the same way the real callback route does.
    @app.get("/auth/oidc/test-establish")
    async def establish(request: Request, sid: str):
        request.session[SESSION_COOKIE_KEY] = sid
        return {"ok": True}

    app.add_middleware(
        AuthMiddleware,
        debug_mode=debug_mode,
        proxy_secret_enabled=proxy_secret_enabled,
        proxy_secret=proxy_secret,
        oidc_enabled=True,
        header_auth_enabled=header_auth_enabled,
    )
    app.add_middleware(SessionMiddleware, secret_key="test-session-secret")
    return app


@pytest.fixture
def session_store():
    store = get_session_store()
    store.clear()
    yield store
    store.clear()


class TestHttpOidcOnly:
    def test_valid_proxy_secret_and_identity_header_do_not_bypass_oidc(self, session_store):
        response = TestClient(_build_app()).get("/api/whoami", headers=SPOOFED)
        assert response.status_code == 401

    def test_same_request_authenticates_when_header_auth_is_on(self, session_store):
        # Control: proves the request above is a working header login that
        # only the flag turns away.
        client = TestClient(_build_app(header_auth_enabled=True))
        response = client.get("/api/whoami", headers=SPOOFED)
        assert response.json() == {"user": "admin@example.gov"}

    def test_unauthenticated_api_call_is_401(self, session_store):
        client = TestClient(_build_app(proxy_secret_enabled=False))
        assert client.get("/api/whoami").status_code == 401

    def test_browser_navigation_starts_oidc_login(self, session_store):
        client = TestClient(_build_app())
        response = client.get("/workspace/abc?tab=1", headers=SPOOFED, follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"].startswith("/auth/oidc/login?next=")
        assert "workspace" in response.headers["location"]

    @pytest.mark.parametrize("headers", [{}, {"X-User-Email": "admin@example.gov"}])
    def test_debug_mode_does_not_fall_back_to_header_or_test_user(self, session_store, headers):
        client = TestClient(_build_app(debug_mode=True))
        assert client.get("/api/whoami", headers=headers).status_code == 401

    def test_oidc_session_still_authenticates(self, session_store):
        session = session_store.create(user_id="user@example.gov")
        client = TestClient(_build_app())
        client.get(f"/auth/oidc/test-establish?sid={session.session_id}")
        # The spoofed header must not override the logged-in identity either.
        response = client.get("/api/whoami", headers=SPOOFED)
        assert response.json() == {"user": "user@example.gov"}

    def test_mcp_download_capability_token_still_works(self, session_store):
        token = generate_file_token("owner@example.gov", "users/owner/report.pdf")
        client = TestClient(_build_app())
        response = client.get(f"/mcp/files/download/report?token={token}")
        assert response.json() == {"user": "owner@example.gov"}

    def test_api_download_capability_token_still_works(self, session_store):
        token = generate_file_token("owner@example.gov", "users/owner/report.pdf")
        client = TestClient(_build_app())
        response = client.get(
            f"/api/files/download/report?token={token}",
            headers={"X-Proxy-Secret": PROXY_SECRET},
        )
        assert response.json() == {"user": "owner@example.gov"}

    def test_proxy_secret_gate_does_not_apply(self, session_store):
        # FEATURE_PROXY_SECRET_ENABLED defaults to true; an OIDC-only
        # deployment with no proxy and no PROXY_SECRET must still offer login
        # and serve signed downloads instead of failing closed with 503.
        client = TestClient(_build_app(proxy_secret=None))
        page = client.get("/workspace/abc", follow_redirects=False)
        assert page.status_code == 302
        assert page.headers["location"].startswith("/auth/oidc/login?next=")
        assert client.get("/api/whoami", headers={"X-User-Email": "a@example.gov"}).status_code == 401
        token = generate_file_token("owner@example.gov", "users/owner/report.pdf")
        download = client.get(f"/api/files/download/report?token={token}")
        assert download.json() == {"user": "owner@example.gov"}

    def test_invalid_capability_token_does_not_fall_back_to_header(self, session_store):
        client = TestClient(_build_app())
        response = client.get("/api/files/download/report?token=forged", headers=SPOOFED)
        assert response.status_code == 401


@pytest.fixture
def ws_factory():
    """Patch the /ws endpoint's app factory with an OIDC-only configuration."""
    with patch("main.app_factory") as factory:
        settings = MagicMock()
        settings.debug_mode = False
        settings.test_user = "test@test.com"
        settings.auth_user_header = "X-User-Email"
        settings.auth_user_header_type = "email-string"
        settings.feature_proxy_secret_enabled = True
        settings.proxy_secret = PROXY_SECRET
        settings.proxy_secret_header = "X-Proxy-Secret"
        settings.feature_oidc_auth_enabled = True
        settings.feature_header_auth_enabled = False
        factory.get_config_manager.return_value.app_settings = settings

        chat_service = MagicMock()
        chat_service.handle_attach_file = AsyncMock(return_value={"type": "file_attach", "success": True})
        chat_service.end_session = AsyncMock()
        chat_service.session_repository.get = AsyncMock(return_value=None)
        factory.create_chat_service.return_value = chat_service
        yield factory


def _ws_client():
    from main import app

    return TestClient(app)


def _connected_user(factory):
    return factory.create_chat_service.call_args[0][0].user_email


class TestWebSocketOidcOnly:
    def test_valid_proxy_secret_and_identity_header_are_rejected(self, ws_factory):
        with patch("main._resolve_oidc_websocket_user", AsyncMock(return_value=None)):
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with _ws_client().websocket_connect("/ws", headers=SPOOFED):
                    pass
        assert exc_info.value.code == 1008
        assert "Authentication required" in exc_info.value.reason
        ws_factory.create_chat_service.assert_not_called()

    def test_same_handshake_connects_when_header_auth_is_on(self, ws_factory):
        settings = ws_factory.get_config_manager.return_value.app_settings
        settings.feature_header_auth_enabled = True
        with patch("main._resolve_oidc_websocket_user", AsyncMock(return_value=None)):
            with _ws_client().websocket_connect("/ws", headers=SPOOFED) as ws:
                ws.send_json({"type": "attach_file", "s3_key": "users/admin@example.gov/x.txt"})
                assert _connected_user(ws_factory) == "admin@example.gov"

    def test_rejection_names_sign_in_even_without_a_proxy_secret(self, ws_factory):
        settings = ws_factory.get_config_manager.return_value.app_settings
        settings.proxy_secret = None
        with patch("main._resolve_oidc_websocket_user", AsyncMock(return_value=None)):
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with _ws_client().websocket_connect("/ws", headers={"X-User-Email": "a@example.gov"}):
                    pass
        assert exc_info.value.code == 1008
        assert exc_info.value.reason == "Authentication required. Please sign in."

    @pytest.mark.parametrize("path", ["/ws", "/ws?user=admin@example.gov"])
    def test_debug_mode_fallbacks_are_rejected(self, ws_factory, path):
        settings = ws_factory.get_config_manager.return_value.app_settings
        settings.debug_mode = True
        with patch("main._resolve_oidc_websocket_user", AsyncMock(return_value=None)):
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with _ws_client().websocket_connect(path, headers={"X-User-Email": "admin@example.gov"}):
                    pass
        assert exc_info.value.code == 1008
        ws_factory.create_chat_service.assert_not_called()

    def test_oidc_session_connects_and_ignores_the_header(self, ws_factory):
        with patch("main._resolve_oidc_websocket_user", AsyncMock(return_value="user@example.gov")):
            with _ws_client().websocket_connect("/ws", headers=SPOOFED) as ws:
                ws.send_json({"type": "attach_file", "s3_key": "users/user@example.gov/x.txt"})
                assert _connected_user(ws_factory) == "user@example.gov"


def _run_atlas(script, env_overrides):
    """Run ``script`` in a fresh interpreter so main.py's import-time setup runs."""
    atlas_dir = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(atlas_dir.parent), **env_overrides}
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=atlas_dir, env=env, capture_output=True, text=True, timeout=120,
    )


def _settings(*, header, oidc):
    return SimpleNamespace(feature_header_auth_enabled=header, feature_oidc_auth_enabled=oidc)


class TestStartupValidation:
    @pytest.mark.parametrize("header, oidc", [(True, False), (True, True), (False, True)])
    def test_a_usable_mode_starts(self, header, oidc):
        validate_auth_modes(_settings(header=header, oidc=oidc), oidc_requested=oidc)

    def test_both_disabled_fails(self):
        with pytest.raises(AuthConfigurationError, match="No authentication mode"):
            validate_auth_modes(_settings(header=False, oidc=False), oidc_requested=False)

    def test_oidc_unavailable_with_header_auth_disabled_fails(self):
        with pytest.raises(AuthConfigurationError, match="configuration is incomplete"):
            validate_auth_modes(_settings(header=False, oidc=False), oidc_requested=True)

    def test_real_app_ignores_identity_headers(self):
        # The HTTP tests above build their own app; this one checks main.py
        # actually wires the flag into AuthMiddleware, end to end.
        script = (
            "from fastapi.testclient import TestClient\n"
            "from atlas.main import app\n"
            "client = TestClient(app)\n"
            "h = {'X-Proxy-Secret': 'proxy-secret', 'X-User-Email': 'admin@example.gov'}\n"
            "print(client.get('/api/config', headers=h).status_code)\n"
            "print(client.get('/', headers=h, follow_redirects=False).headers.get('location'))\n"
        )
        result = _run_atlas(script, {
            "FEATURE_HEADER_AUTH_ENABLED": "false",
            "FEATURE_OIDC_AUTH_ENABLED": "true",
            "OIDC_SESSION_SECRET": "test-session-secret",
            "OIDC_ISSUER": "https://idp.example.gov",
            "OIDC_CLIENT_ID": "atlas",
            "FEATURE_PROXY_SECRET_ENABLED": "true",
            "PROXY_SECRET": PROXY_SECRET,
            "DEBUG_MODE": "false",
        })
        assert result.returncode == 0, result.stderr
        status, location = result.stdout.strip().splitlines()[-2:]
        assert status == "401"
        assert location.startswith("/auth/oidc/login")

    def test_app_import_fails_when_oidc_is_unavailable(self):
        # End to end through main.py: OIDC is requested but startup disables
        # it (no session secret), and with header auth off the app must not
        # come up at all.
        result = _run_atlas("import atlas.main", {
            "FEATURE_HEADER_AUTH_ENABLED": "false",
            "FEATURE_OIDC_AUTH_ENABLED": "true",
            "OIDC_SESSION_SECRET": "",
            "OIDC_ISSUER": "https://idp.example.gov",
            "OIDC_CLIENT_ID": "atlas",
        })
        assert result.returncode != 0
        assert "AuthConfigurationError" in result.stderr
