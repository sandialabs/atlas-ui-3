import json
import os
from pathlib import Path

import pytest
from main import app
from starlette.testclient import TestClient

from atlas.infrastructure.app_factory import app_factory
from atlas.modules.config import config_manager

_IS_PRODUCTION = os.environ.get("DEBUG_MODE", "true").lower() == "false"


def _configure_test_overrides(tmp_path: Path, monkeypatch):
    # Keep config changes isolated per test
    monkeypatch.setattr(config_manager.app_settings, "app_config_dir", str(tmp_path))
    monkeypatch.setattr(config_manager.app_settings, "mcp_config_file", "mcp.json")

    # Avoid any side effects from attempting to reload MCP servers during add/remove.
    monkeypatch.setattr(app_factory, "get_mcp_manager", lambda: None)


@pytest.mark.skipif(_IS_PRODUCTION, reason="Requires debug mode for mock admin access")
def test_admin_mcp_available_servers_returns_inventory(monkeypatch, tmp_path):
    _configure_test_overrides(tmp_path, monkeypatch)

    client = TestClient(app)
    response = client.get(
        "/admin/mcp/available-servers",
        headers={"X-User-Email": "admin@example.com"},
    )
    assert response.status_code == 200

    data = response.json()
    assert "available_servers" in data
    assert isinstance(data["available_servers"], dict)

    # Repo should ship at least one example server.
    assert len(data["available_servers"]) > 0

    # Spot-check expected shape.
    first_name = next(iter(data["available_servers"]))
    first = data["available_servers"][first_name]
    assert "config" in first
    assert "source_file" in first


@pytest.mark.skipif(_IS_PRODUCTION, reason="Requires debug mode for mock admin access")
def test_admin_mcp_active_servers_empty_when_no_override_file(monkeypatch, tmp_path):
    _configure_test_overrides(tmp_path, monkeypatch)

    # Ensure there is no mcp.json in overrides
    assert not (tmp_path / "mcp.json").exists()

    client = TestClient(app)
    response = client.get(
        "/admin/mcp/active-servers",
        headers={"X-User-Email": "admin@example.com"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["active_servers"] == {}
    assert "config_path" in data


@pytest.mark.skipif(_IS_PRODUCTION, reason="Requires debug mode for mock admin access")
def test_admin_mcp_add_server_persists_to_overrides(monkeypatch, tmp_path):
    _configure_test_overrides(tmp_path, monkeypatch)

    client = TestClient(app)

    available = client.get(
        "/admin/mcp/available-servers",
        headers={"X-User-Email": "admin@example.com"},
    ).json()["available_servers"]

    server_name = next(iter(available.keys()))

    add_response = client.post(
        "/admin/mcp/add-server",
        headers={"X-User-Email": "admin@example.com"},
        json={"server_name": server_name},
    )
    assert add_response.status_code == 200
    add_data = add_response.json()
    assert add_data["server_name"] == server_name

    # Active endpoint should reflect the new server.
    active = client.get(
        "/admin/mcp/active-servers",
        headers={"X-User-Email": "admin@example.com"},
    ).json()["active_servers"]
    assert server_name in active

    # And it should be persisted in overrides/mcp.json.
    persisted_path = tmp_path / "mcp.json"
    assert persisted_path.exists()
    persisted = json.loads(persisted_path.read_text(encoding="utf-8"))
    assert server_name in persisted

    # Re-adding returns the already_active response.
    add_again = client.post(
        "/admin/mcp/add-server",
        headers={"X-User-Email": "admin@example.com"},
        json={"server_name": server_name},
    )
    assert add_again.status_code == 200
    assert add_again.json().get("already_active") is True


@pytest.mark.skipif(_IS_PRODUCTION, reason="Requires debug mode for mock admin access")
def test_admin_mcp_remove_server_updates_overrides(monkeypatch, tmp_path):
    _configure_test_overrides(tmp_path, monkeypatch)

    client = TestClient(app)

    available = client.get(
        "/admin/mcp/available-servers",
        headers={"X-User-Email": "admin@example.com"},
    ).json()["available_servers"]

    server_name = next(iter(available.keys()))

    # Add then remove.
    add_response = client.post(
        "/admin/mcp/add-server",
        headers={"X-User-Email": "admin@example.com"},
        json={"server_name": server_name},
    )
    assert add_response.status_code == 200

    remove_response = client.post(
        "/admin/mcp/remove-server",
        headers={"X-User-Email": "admin@example.com"},
        json={"server_name": server_name},
    )
    assert remove_response.status_code == 200
    remove_data = remove_response.json()
    assert remove_data["server_name"] == server_name
    assert "removed_config" in remove_data

    active = client.get(
        "/admin/mcp/active-servers",
        headers={"X-User-Email": "admin@example.com"},
    ).json()["active_servers"]
    assert server_name not in active

    persisted = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
    assert server_name not in persisted


@pytest.mark.skipif(not _IS_PRODUCTION, reason="Verifies production-mode admin denial")
def test_admin_mcp_routes_reject_mock_users_in_production():
    """In production mode, mock admin users must be denied access."""
    client = TestClient(app)
    for path in ["/admin/mcp/available-servers", "/admin/mcp/active-servers"]:
        response = client.get(path, headers={"X-User-Email": "admin@example.com"})
        # 403 = admin auth correctly denied; 503 = proxy secret also blocks (both are correct)
        assert response.status_code in (403, 503), (
            f"{path} should deny access in production mode, got {response.status_code}"
        )
