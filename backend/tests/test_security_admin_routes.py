from starlette.testclient import TestClient

from main import app


def test_admin_routes_require_admin(monkeypatch):
    client = TestClient(app)

    # Non-admin user should be redirected/forbidden depending on middleware
    # Provide a non-admin email
    r = client.get("/admin/", headers={"X-User-Email": "user@example.com"})
    assert r.status_code in (302, 403)

    # Admin access when user is in admin group (mocked via config in core.auth)
    r2 = client.get("/admin/", headers={"X-User-Email": "admin@example.com"})
    # In debug mode off, should allow if auth module says admin@example.com is admin
    assert r2.status_code == 200
    data = r2.json()
    assert data.get("available_endpoints") is not None
