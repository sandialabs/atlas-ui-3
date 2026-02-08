from main import app
from starlette.testclient import TestClient


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


def test_system_status_endpoint():
    """Test the system status endpoint returns expected data structure."""
    client = TestClient(app)

    # Test with admin user
    r = client.get("/admin/system-status", headers={"X-User-Email": "admin@example.com"})
    assert r.status_code == 200

    data = r.json()

    # Check response structure
    assert "overall_status" in data
    assert "components" in data
    assert "checked_by" in data

    # Overall status should be "healthy" or "warning"
    assert data["overall_status"] in ("healthy", "warning")

    # Components should be a list
    assert isinstance(data["components"], list)

    # Check that expected components are present
    component_names = [c["component"] for c in data["components"]]
    assert "Configuration" in component_names
    assert "Logging" in component_names

    # Each component should have required fields
    for component in data["components"]:
        assert "component" in component
        assert "status" in component
        assert "details" in component
        assert component["status"] in ("healthy", "warning", "error")


def test_system_status_requires_admin():
    """Test that system status endpoint requires admin access."""
    client = TestClient(app)

    # Non-admin user should be denied
    r = client.get("/admin/system-status", headers={"X-User-Email": "user@example.com"})
    assert r.status_code in (302, 403)
