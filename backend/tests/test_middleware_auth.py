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
