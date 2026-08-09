import pytest
from main import app
from starlette.testclient import TestClient

from atlas.modules.config import config_manager

# Admin group membership is mocked only in debug mode (see core.auth), so these
# tests must request it explicitly to pass in both CI legs (DEBUG_MODE true and
# false). See the mock_admin_authorization fixture in conftest.py.
pytestmark = pytest.mark.usefixtures("mock_admin_authorization")


def test_admin_routes_require_admin(monkeypatch):
    client = TestClient(app)

    # Non-admin user should be redirected/forbidden depending on middleware
    # Provide a non-admin email
    r = client.get("/admin/", headers={"X-User-Email": "user@example.com"})
    assert r.status_code in (302, 403)

    # Admin access when user is in admin group (mocked via config in core.auth)
    r2 = client.get("/admin/", headers={"X-User-Email": config_manager.app_settings.admin_test_user})
    # admin_test_user is an admin via the debug-only mock group table, which
    # mock_admin_authorization enables regardless of the CI leg's DEBUG_MODE.
    assert r2.status_code == 200
    data = r2.json()
    assert data.get("available_endpoints") is not None


def test_system_status_endpoint():
    """Test the system status endpoint returns expected data structure."""
    client = TestClient(app)

    # Test with admin user
    r = client.get("/admin/system-status", headers={"X-User-Email": config_manager.app_settings.admin_test_user})
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


def test_admin_routes_allow_any_user_with_skip_authorization_checks(skip_auth_checks_env):
    """SKIP_AUTHORIZATION_CHECKS (dev-only) should let a user with no admin
    configuration reach admin routes, without needing ADMIN_TEST_USER set.

    Uses the ``skip_auth_checks_env`` fixture so both ``DEBUG_MODE`` and
    ``SKIP_AUTHORIZATION_CHECKS`` are saved and restored and the ConfigManager
    cache is reset on exit (Copilot review on PR #758).
    """
    client = TestClient(app)
    r = client.get("/admin/", headers={"X-User-Email": "not-configured-anywhere@example.com"})
    assert r.status_code == 200
