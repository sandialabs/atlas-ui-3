import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from core.middleware import AuthMiddleware


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

