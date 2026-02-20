import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from atlas.core.middleware import AuthMiddleware


@pytest.mark.parametrize("debug_mode, header, expected_status", [
    (True, None, 200),
    (True, "user@example.com", 200),
    (False, None, 302),
    (False, "user@example.com", 200),
])
def test_auth_middleware(debug_mode, header, expected_status):
    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    # Add an /auth route to receive redirects
    @app.get("/auth")
    def auth():
        return {"login": True}

    app.add_middleware(AuthMiddleware, debug_mode=debug_mode)
    client = TestClient(app)

    headers = {"X-User-Email": header} if header else {}
    resp = client.get("/ping", headers=headers)
    if expected_status == 302:
        # TestClient follows redirects by default; check final URL
        assert resp.url.path == "/auth"
    else:
        assert resp.status_code == expected_status


def test_auth_middleware_custom_header():
    """Test that custom auth header name can be configured."""
    from fastapi import Request

    app = FastAPI()

    @app.get("/ping")
    def ping(request: Request):
        # Return the authenticated user email
        return {"user": request.state.user_email}

    # Add an /auth route to receive redirects
    @app.get("/auth")
    def auth():
        return {"login": True}

    # Use a custom header name
    app.add_middleware(AuthMiddleware, debug_mode=False, auth_header_name="X-Authenticated-User")
    client = TestClient(app)

    # Test with the custom header
    headers = {"X-Authenticated-User": "custom@example.com"}
    resp = client.get("/ping", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["user"] == "custom@example.com"

    # Test that the old header doesn't work
    headers = {"X-User-Email": "old@example.com"}
    resp = client.get("/ping", headers=headers)
    # Should redirect because the configured header is missing
    assert resp.url.path == "/auth"


def test_auth_middleware_custom_header_debug_mode():
    """Test that custom auth header works in debug mode."""
    from fastapi import Request

    app = FastAPI()

    @app.get("/ping")
    def ping(request: Request):
        return {"user": request.state.user_email}

    app.add_middleware(AuthMiddleware, debug_mode=True, auth_header_name="X-Remote-User")
    client = TestClient(app)

    # Test with the custom header
    headers = {"X-Remote-User": "debug@example.com"}
    resp = client.get("/ping", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["user"] == "debug@example.com"


def test_proxy_secret_disabled_default_behavior():
    """Test that with proxy secret disabled, normal auth behavior works."""
    from fastapi import Request

    app = FastAPI()

    @app.get("/ping")
    def ping(request: Request):
        return {"user": request.state.user_email}

    @app.get("/auth")
    def auth():
        return {"login": True}

    # Proxy secret disabled (default)
    app.add_middleware(
        AuthMiddleware,
        debug_mode=False,
        proxy_secret_enabled=False
    )
    client = TestClient(app)

    # Should work with just user header
    headers = {"X-User-Email": "user@example.com"}
    resp = client.get("/ping", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["user"] == "user@example.com"


def test_proxy_secret_enabled_valid_secret():
    """Test that with valid proxy secret, request succeeds."""
    from fastapi import Request

    app = FastAPI()

    @app.get("/ping")
    def ping(request: Request):
        return {"user": request.state.user_email}

    @app.get("/auth")
    def auth():
        return {"login": True}

    app.add_middleware(
        AuthMiddleware,
        debug_mode=False,
        proxy_secret_enabled=True,
        proxy_secret_header="X-Proxy-Secret",
        proxy_secret="my-secret-123"
    )
    client = TestClient(app)

    # Should work with both proxy secret and user header
    headers = {
        "X-Proxy-Secret": "my-secret-123",
        "X-User-Email": "user@example.com"
    }
    resp = client.get("/ping", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["user"] == "user@example.com"


def test_proxy_secret_enabled_invalid_secret():
    """Test that with invalid proxy secret, request is rejected."""
    from fastapi import Request

    app = FastAPI()

    @app.get("/ping")
    def ping(request: Request):
        return {"user": request.state.user_email}

    @app.get("/auth")
    def auth():
        return {"login": True}

    app.add_middleware(
        AuthMiddleware,
        debug_mode=False,
        proxy_secret_enabled=True,
        proxy_secret_header="X-Proxy-Secret",
        proxy_secret="my-secret-123"
    )
    client = TestClient(app)

    # Should redirect with wrong secret
    headers = {
        "X-Proxy-Secret": "wrong-secret",
        "X-User-Email": "user@example.com"
    }
    resp = client.get("/ping", headers=headers)
    assert resp.url.path == "/auth"


def test_proxy_secret_enabled_missing_secret():
    """Test that with missing proxy secret, request is rejected."""
    from fastapi import Request

    app = FastAPI()

    @app.get("/ping")
    def ping(request: Request):
        return {"user": request.state.user_email}

    @app.get("/auth")
    def auth():
        return {"login": True}

    app.add_middleware(
        AuthMiddleware,
        debug_mode=False,
        proxy_secret_enabled=True,
        proxy_secret_header="X-Proxy-Secret",
        proxy_secret="my-secret-123"
    )
    client = TestClient(app)

    # Should redirect with missing secret
    headers = {"X-User-Email": "user@example.com"}
    resp = client.get("/ping", headers=headers)
    assert resp.url.path == "/auth"


def test_proxy_secret_enabled_api_endpoint_returns_401():
    """Test that API endpoints return 401 instead of redirecting when proxy secret is invalid."""
    app = FastAPI()

    @app.get("/api/data")
    def api_data():
        return {"data": "value"}

    @app.get("/auth")
    def auth():
        return {"login": True}

    app.add_middleware(
        AuthMiddleware,
        debug_mode=False,
        proxy_secret_enabled=True,
        proxy_secret_header="X-Proxy-Secret",
        proxy_secret="my-secret-123"
    )
    client = TestClient(app, raise_server_exceptions=False)

    # Should return 401 for API endpoint with wrong secret
    headers = {
        "X-Proxy-Secret": "wrong-secret",
        "X-User-Email": "user@example.com"
    }
    resp = client.get("/api/data", headers=headers, follow_redirects=False)
    assert resp.status_code == 401


def test_proxy_secret_custom_redirect_url():
    """Test that custom redirect URL is used when proxy secret validation fails."""
    from fastapi import Request

    app = FastAPI()

    @app.get("/ping")
    def ping(request: Request):
        return {"user": request.state.user_email}

    @app.get("/custom-login")
    def custom_login():
        return {"login": True}

    app.add_middleware(
        AuthMiddleware,
        debug_mode=False,
        proxy_secret_enabled=True,
        proxy_secret_header="X-Proxy-Secret",
        proxy_secret="my-secret-123",
        auth_redirect_url="/custom-login"
    )
    client = TestClient(app)

    # Should redirect to custom URL with missing secret
    headers = {"X-User-Email": "user@example.com"}
    resp = client.get("/ping", headers=headers)
    assert resp.url.path == "/custom-login"


def test_auth_redirect_url_without_proxy_secret():
    """Test that custom redirect URL works for regular auth failures too."""
    from fastapi import Request

    app = FastAPI()

    @app.get("/ping")
    def ping(request: Request):
        return {"user": request.state.user_email}

    @app.get("/sso-login")
    def sso_login():
        return {"login": True}

    app.add_middleware(
        AuthMiddleware,
        debug_mode=False,
        auth_redirect_url="/sso-login"
    )
    client = TestClient(app)

    # Should redirect to custom URL when user header is missing
    resp = client.get("/ping", headers={})
    assert resp.url.path == "/sso-login"


def test_proxy_secret_does_not_skip_auth_endpoint():
    """Test that the configured auth endpoint is accessible even without proxy secret."""
    app = FastAPI()

    @app.get("/auth")
    def auth():
        return {"login": True}

    app.add_middleware(
        AuthMiddleware,
        debug_mode=False,
        proxy_secret_enabled=True,
        proxy_secret_header="X-Proxy-Secret",
        proxy_secret="my-secret-123"
    )
    client = TestClient(app)

    # Auth endpoint should be accessible without secret
    resp = client.get("/auth", headers={})
    assert resp.status_code == 200
    assert resp.json()["login"] is True


def test_proxy_secret_debug_mode_bypasses_validation():
    """Test that debug mode still works when proxy secret is enabled."""
    from fastapi import Request

    app = FastAPI()

    @app.get("/ping")
    def ping(request: Request):
        return {"user": request.state.user_email}

    app.add_middleware(
        AuthMiddleware,
        debug_mode=True,
        proxy_secret_enabled=True,
        proxy_secret_header="X-Proxy-Secret",
        proxy_secret="my-secret-123"
    )
    client = TestClient(app)

    # In debug mode, should work without proxy secret but still need user auth
    headers = {"X-User-Email": "debug@example.com"}
    resp = client.get("/ping", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["user"] == "debug@example.com"


def test_health_endpoint_bypasses_auth():
    """Test that /api/health endpoint bypasses authentication middleware."""
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"status": "healthy"}

    @app.get("/api/other")
    def other():
        return {"data": "test"}

    # Add an /auth route to receive redirects
    @app.get("/auth")
    def auth():
        return {"login": True}

    # Add middleware with auth required (debug_mode=False)
    app.add_middleware(AuthMiddleware, debug_mode=False)
    client = TestClient(app)

    # Health endpoint should work without auth header
    health_resp = client.get("/api/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["status"] == "healthy"

    # Other API endpoints should still require auth (return 401)
    other_resp = client.get("/api/other")
    assert other_resp.status_code == 401
    assert "Unauthorized" in other_resp.json()["detail"]


def test_heartbeat_endpoint_bypasses_auth():
    """Test that /api/heartbeat endpoint bypasses authentication middleware."""
    app = FastAPI()

    @app.get("/api/heartbeat")
    def heartbeat():
        return {"status": "ok"}

    @app.get("/api/other")
    def other():
        return {"data": "test"}

    @app.get("/auth")
    def auth():
        return {"login": True}

    app.add_middleware(AuthMiddleware, debug_mode=False)
    client = TestClient(app)

    # Heartbeat should work without auth header
    resp = client.get("/api/heartbeat")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # Other API endpoints should still require auth
    other_resp = client.get("/api/other")
    assert other_resp.status_code == 401


def test_heartbeat_bypasses_auth_with_proxy_secret():
    """Test that heartbeat bypasses both auth and proxy secret validation."""
    app = FastAPI()

    @app.get("/api/heartbeat")
    def heartbeat():
        return {"status": "ok"}

    @app.get("/auth")
    def auth():
        return {"login": True}

    app.add_middleware(
        AuthMiddleware,
        debug_mode=False,
        proxy_secret_enabled=True,
        proxy_secret_header="X-Proxy-Secret",
        proxy_secret="my-secret-123"
    )
    client = TestClient(app)

    # Heartbeat should work without proxy secret or auth header
    resp = client.get("/api/heartbeat")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

