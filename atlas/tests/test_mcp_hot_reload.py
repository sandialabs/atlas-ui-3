"""Tests for MCP hot reload and auto-reconnect functionality."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from atlas.modules.mcp_tools.client import MCPToolManager


class TestMCPAdminEndpoints:
    """Integration tests for MCP admin endpoints."""

    # Admin group membership is mocked only in debug mode (see core.auth), so
    # these tests must request it explicitly to pass in both CI legs
    # (DEBUG_MODE true and false). See conftest.mock_admin_authorization.
    pytestmark = pytest.mark.usefixtures("mock_admin_authorization")

    def test_mcp_status_endpoint_requires_admin(self):
        """Test that MCP status endpoint requires admin access."""
        from main import app

        client = TestClient(app)

        # Non-admin user should be denied
        r = client.get("/admin/mcp/status", headers={"X-User-Email": "user@example.com"})
        assert r.status_code in (302, 403)

    def test_mcp_status_endpoint_returns_data(self):
        """Test that MCP status endpoint returns expected data structure."""
        from main import app

        from atlas.modules.config import config_manager
        client = TestClient(app)

        # Admin user should get response
        r = client.get("/admin/mcp/status", headers={"X-User-Email": config_manager.app_settings.admin_test_user})
        assert r.status_code == 200

        data = r.json()
        assert "connected_servers" in data
        assert "configured_servers" in data
        assert "failed_servers" in data
        assert "auto_reconnect" in data
        assert "tool_counts" in data
        assert "prompt_counts" in data

        # Check auto_reconnect structure
        auto_reconnect = data["auto_reconnect"]
        assert "enabled" in auto_reconnect
        assert "base_interval" in auto_reconnect
        assert "max_interval" in auto_reconnect
        assert "backoff_multiplier" in auto_reconnect
        assert "running" in auto_reconnect

    def test_mcp_status_marks_failed_servers_not_connected(self):
        """Servers with recorded failures should not appear as connected."""
        from main import app

        from atlas.modules.config import config_manager
        client = TestClient(app)

        # Seed a fake failure in the MCP manager
        from atlas.infrastructure.app_factory import app_factory
        mcp = app_factory.get_mcp_manager()
        mcp._failed_servers["failing-server"] = {
            "last_attempt": time.time(),
            "attempt_count": 1,
            "error": "Simulated failure",
        }
        mcp.clients["failing-server"] = AsyncMock()
        mcp.available_tools["failing-server"] = {"tools": [], "config": {}}
        mcp.available_prompts["failing-server"] = {"prompts": [], "config": {}}

        r = client.get("/admin/mcp/status", headers={"X-User-Email": config_manager.app_settings.admin_test_user})
        assert r.status_code == 200

        data = r.json()
        assert "failing-server" not in data["connected_servers"]

    def test_mcp_status_marks_unauthenticated_oauth_as_auth_required(self):
        from main import app

        from atlas.infrastructure.app_factory import app_factory
        from atlas.modules.config import config_manager

        client = TestClient(app)
        mcp = app_factory.get_mcp_manager()
        server_name = "oauth-auth-required"
        mcp.servers_config[server_name] = {
            "url": "https://mcp.example.com/mcp",
            "auth_type": "oauth",
        }
        mcp._failed_servers[server_name] = {
            "last_attempt": time.time(),
            "attempt_count": 1,
            "error": "RuntimeError: 401 Unauthorized",
        }
        try:
            response = client.get(
                "/admin/mcp/status",
                headers={"X-User-Email": config_manager.app_settings.admin_test_user},
            )
            assert response.status_code == 200
            assert response.json()["failed_servers"][server_name]["auth_required"] is True
        finally:
            mcp.servers_config.pop(server_name, None)
            mcp._failed_servers.pop(server_name, None)

    def test_mcp_status_keeps_unreachable_oauth_as_failed(self):
        from main import app

        from atlas.infrastructure.app_factory import app_factory
        from atlas.modules.config import config_manager

        client = TestClient(app)
        mcp = app_factory.get_mcp_manager()
        server_name = "oauth-unreachable"
        mcp.servers_config[server_name] = {
            "url": "https://mcp.example.com/mcp",
            "auth_type": "oauth",
        }
        mcp._failed_servers[server_name] = {
            "last_attempt": time.time(),
            "attempt_count": 1,
            "error": "ConnectError: connection refused",
        }
        try:
            response = client.get(
                "/admin/mcp/status",
                headers={"X-User-Email": config_manager.app_settings.admin_test_user},
            )
            assert response.status_code == 200
            assert response.json()["failed_servers"][server_name]["auth_required"] is False
        finally:
            mcp.servers_config.pop(server_name, None)
            mcp._failed_servers.pop(server_name, None)

    def test_mcp_reload_endpoint_requires_admin(self):
        """Test that MCP reload endpoint requires admin access."""
        from main import app

        client = TestClient(app)

        # Non-admin user should be denied
        r = client.post("/admin/mcp/reload", headers={"X-User-Email": "user@example.com"})
        assert r.status_code in (302, 403)

    def test_mcp_reconnect_endpoint_requires_admin(self):
        """Test that MCP reconnect endpoint requires admin access."""
        from main import app

        client = TestClient(app)

        # Non-admin user should be denied
        r = client.post("/admin/mcp/reconnect", headers={"X-User-Email": "user@example.com"})
        assert r.status_code in (302, 403)

    def test_mcp_reconnect_endpoint_returns_data(self):
        """Test that MCP reconnect endpoint returns expected data structure."""
        from main import app

        from atlas.modules.config import config_manager
        client = TestClient(app)

        # Admin user should get response
        r = client.post("/admin/mcp/reconnect", headers={"X-User-Email": config_manager.app_settings.admin_test_user})
        assert r.status_code == 200

        data = r.json()
        assert "message" in data
        assert "result" in data
        assert "current_servers" in data
        assert "failed_servers" in data
        assert "triggered_by" in data

    def test_mcp_refresh_endpoint_requires_admin(self):
        """Test that MCP refresh endpoint requires admin access."""
        from main import app

        client = TestClient(app)

        # Non-admin user should be denied
        r = client.post(
            "/admin/mcp/refresh",
            headers={"X-User-Email": "user@example.com"},
            json={"server_name": "test-server"},
        )
        assert r.status_code in (302, 403)

    def test_mcp_refresh_endpoint_returns_data(self):
        """Test that MCP refresh endpoint returns expected data structure."""
        from main import app

        from atlas.infrastructure.app_factory import app_factory
        from atlas.modules.config import config_manager

        client = TestClient(app)

        fake_manager = MagicMock()
        fake_manager.refresh_server = AsyncMock(
            return_value={
                "server": "test-server",
                "status": "connected",
                "tools": 2,
                "prompts": 1,
                "error": None,
                "config_changed": False,
                "evicted_clients": 0,
                "config_reload_error": None,
            }
        )
        fake_manager.clients = {"test-server": MagicMock()}
        fake_manager.servers_config = {"test-server": {"url": "https://mcp.example.com"}}
        fake_manager.get_failed_servers.return_value = {}

        with patch.object(app_factory, "get_mcp_manager", return_value=fake_manager):
            r = client.post(
                "/admin/mcp/refresh",
                headers={"X-User-Email": config_manager.app_settings.admin_test_user},
                json={"server_name": "test-server"},
            )
        assert r.status_code == 200

        data = r.json()
        assert "message" in data
        assert data["result"]["status"] == "connected"
        assert data["result"]["server"] == "test-server"
        assert "servers" in data
        assert "failed_servers" in data
        assert "triggered_by" in data
        fake_manager.refresh_server.assert_awaited_once_with("test-server")

    def test_mcp_refresh_endpoint_unknown_server_returns_404(self):
        """Refreshing a server that is not configured should 404."""
        from main import app

        from atlas.infrastructure.app_factory import app_factory
        from atlas.modules.config import config_manager

        client = TestClient(app)

        fake_manager = MagicMock()
        fake_manager.refresh_server = AsyncMock(
            return_value={
                "server": "nope",
                "status": "unknown",
                "tools": 0,
                "prompts": 0,
                "error": "Server 'nope' is not configured",
                "config_changed": False,
                "evicted_clients": 0,
                "config_reload_error": None,
            }
        )
        fake_manager.clients = {}
        fake_manager.servers_config = {}
        fake_manager.get_failed_servers.return_value = {}

        with patch.object(app_factory, "get_mcp_manager", return_value=fake_manager):
            r = client.post(
                "/admin/mcp/refresh",
                headers={"X-User-Email": config_manager.app_settings.admin_test_user},
                json={"server_name": "nope"},
            )
        assert r.status_code == 404

    def test_admin_dashboard_includes_mcp_endpoints(self):
        """Test that admin dashboard lists MCP endpoints."""
        from main import app

        from atlas.modules.config import config_manager
        client = TestClient(app)

        r = client.get("/admin/", headers={"X-User-Email": config_manager.app_settings.admin_test_user})
        assert r.status_code == 200

        data = r.json()
        endpoints = data.get("available_endpoints", [])
        assert "/admin/mcp/reload" in endpoints
        assert "/admin/mcp/reconnect" in endpoints
        assert "/admin/mcp/refresh" in endpoints
        assert "/admin/mcp/status" in endpoints


class TestMCPFailedServerTracking:
    """Tests for tracking failed MCP server connections."""

    def test_record_server_failure_new_server(self):
        """Test recording first failure for a server."""
        manager = MCPToolManager.__new__(MCPToolManager)
        manager._failed_servers = {}

        manager._record_server_failure("test-server", "Connection refused")

        assert "test-server" in manager._failed_servers
        assert manager._failed_servers["test-server"]["attempt_count"] == 1
        assert manager._failed_servers["test-server"]["error"] == "Connection refused"
        assert "last_attempt" in manager._failed_servers["test-server"]

    def test_record_server_failure_existing_server(self):
        """Test recording additional failures for an already-failed server."""
        manager = MCPToolManager.__new__(MCPToolManager)
        initial_time = time.time() - 100
        manager._failed_servers = {
            "test-server": {
                "last_attempt": initial_time,
                "attempt_count": 2,
                "error": "Old error"
            }
        }

        manager._record_server_failure("test-server", "New error")

        assert manager._failed_servers["test-server"]["attempt_count"] == 3
        assert manager._failed_servers["test-server"]["error"] == "New error"
        assert manager._failed_servers["test-server"]["last_attempt"] > initial_time

    def test_clear_server_failure(self):
        """Test clearing failure tracking after successful connection."""
        manager = MCPToolManager.__new__(MCPToolManager)
        manager._failed_servers = {
            "test-server": {
                "last_attempt": time.time(),
                "attempt_count": 3,
                "error": "Some error"
            }
        }

        manager._clear_server_failure("test-server")

        assert "test-server" not in manager._failed_servers

    def test_clear_server_failure_nonexistent(self):
        """Test clearing a server that wasn't tracked (should not error)."""
        manager = MCPToolManager.__new__(MCPToolManager)
        manager._failed_servers = {}

        # Should not raise any exception
        manager._clear_server_failure("nonexistent-server")

        assert "nonexistent-server" not in manager._failed_servers

    def test_get_failed_servers(self):
        """Test getting failed servers info."""
        manager = MCPToolManager.__new__(MCPToolManager)
        manager._failed_servers = {
            "server1": {"attempt_count": 1, "error": "Error 1"},
            "server2": {"attempt_count": 3, "error": "Error 2"}
        }

        result = manager.get_failed_servers()

        assert result == manager._failed_servers
        # Verify it returns a copy, not the original dict
        assert result is not manager._failed_servers


class TestMCPBackoffCalculation:
    """Tests for exponential backoff calculation."""

    @patch('atlas.modules.mcp_tools.client.config_manager')
    def test_calculate_backoff_first_attempt(self, mock_config_manager):
        """Test backoff calculation for first retry attempt."""
        mock_settings = MagicMock()
        mock_settings.mcp_reconnect_interval = 60
        mock_settings.mcp_reconnect_max_interval = 300
        mock_settings.mcp_reconnect_backoff_multiplier = 2.0
        mock_config_manager.app_settings = mock_settings

        manager = MCPToolManager.__new__(MCPToolManager)

        delay = manager._calculate_backoff_delay(1)

        assert delay == 60  # Base interval for first attempt

    @patch('atlas.modules.mcp_tools.client.config_manager')
    def test_calculate_backoff_exponential(self, mock_config_manager):
        """Test exponential backoff for subsequent attempts."""
        mock_settings = MagicMock()
        mock_settings.mcp_reconnect_interval = 60
        mock_settings.mcp_reconnect_max_interval = 300
        mock_settings.mcp_reconnect_backoff_multiplier = 2.0
        mock_config_manager.app_settings = mock_settings

        manager = MCPToolManager.__new__(MCPToolManager)

        # Second attempt: 60 * 2^1 = 120
        assert manager._calculate_backoff_delay(2) == 120
        # Third attempt: 60 * 2^2 = 240
        assert manager._calculate_backoff_delay(3) == 240
        # Fourth attempt: 60 * 2^3 = 480, but capped at 300
        assert manager._calculate_backoff_delay(4) == 300

    @patch('atlas.modules.mcp_tools.client.config_manager')
    def test_calculate_backoff_max_cap(self, mock_config_manager):
        """Test that backoff is capped at max_interval."""
        mock_settings = MagicMock()
        mock_settings.mcp_reconnect_interval = 60
        mock_settings.mcp_reconnect_max_interval = 300
        mock_settings.mcp_reconnect_backoff_multiplier = 2.0
        mock_config_manager.app_settings = mock_settings

        manager = MCPToolManager.__new__(MCPToolManager)

        # Very high attempt count should still be capped
        delay = manager._calculate_backoff_delay(10)

        assert delay == 300


class TestMCPConfigReload:
    """Tests for MCP configuration hot-reload."""

    @patch('atlas.modules.mcp_tools.client.config_manager')
    def test_reload_config_updates_servers(self, mock_config_manager):
        """Test that reload_config updates server configuration."""
        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"old-server": {"description": "Old"}}
        manager._failed_servers = {"old-server": {"attempt_count": 1}}

        # Mock new config
        mock_new_config = MagicMock()
        mock_server = MagicMock()
        mock_server.model_dump.return_value = {"description": "New"}
        mock_new_config.servers = {"new-server": mock_server}
        mock_config_manager.reload_mcp_config.return_value = mock_new_config

        result = manager.reload_config()

        assert "old-server" in result["removed"]
        assert "new-server" in result["added"]
        assert manager.servers_config == {"new-server": {"description": "New"}}
        # Old failed server tracking should be cleared
        assert "old-server" not in manager._failed_servers

    @patch('atlas.modules.mcp_tools.client.config_manager')
    def test_reload_config_preserves_unchanged(self, mock_config_manager):
        """Test that reload_config identifies unchanged servers."""
        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"existing-server": {"description": "Existing"}}
        manager._failed_servers = {}

        # Mock config with same server
        mock_new_config = MagicMock()
        mock_server = MagicMock()
        mock_server.model_dump.return_value = {"description": "Updated"}
        mock_new_config.servers = {"existing-server": mock_server}
        mock_config_manager.reload_mcp_config.return_value = mock_new_config

        result = manager.reload_config()

        assert "existing-server" in result["unchanged"]
        assert result["added"] == []
        assert result["removed"] == []


@pytest.mark.asyncio
class TestMCPDiscoveryResilience:
    """Tests to ensure tool/prompt discovery tolerates removed servers."""

    async def test_discover_tools_skips_removed_server(self):
        """If a client exists without config, discovery should not crash."""
        manager = MCPToolManager.__new__(MCPToolManager)
        # Simulate one valid server and one removed server
        manager.servers_config = {"server-a": {"description": "A"}}
        manager._failed_servers = {}
        manager.clients = {
            "server-a": AsyncMock(),
            "removed-server": AsyncMock(),
        }

        async def fake_discover(server_name, client):  # noqa: ARG001
            if server_name == "server-a":
                return {"tools": [MagicMock(name="t1")], "config": {"description": "A"}}
            # Simulate that removed-server had a client but config was deleted
            raise RuntimeError("Simulated failure for removed-server")

        manager._discover_tools_for_server = fake_discover  # type: ignore[assignment]

        # Should complete without raising, and only include server-a in available_tools
        await manager.discover_tools()
        assert "server-a" in manager.available_tools
        assert "removed-server" not in manager.available_tools

    @pytest.mark.asyncio
    async def test_discover_prompts_skips_removed_server(self):
        """Prompt discovery should also skip servers missing from config."""
        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"server-a": {"description": "A"}}
        manager._failed_servers = {}
        manager.clients = {
            "server-a": AsyncMock(),
            "removed-server": AsyncMock(),
        }

        async def fake_discover_prompts(server_name, client):  # noqa: ARG001
            if server_name == "server-a":
                return {"prompts": [MagicMock(name="p1")], "config": {"description": "A"}}
            raise RuntimeError("Simulated failure for removed-server")

        manager._discover_prompts_for_server = fake_discover_prompts  # type: ignore[assignment]

        await manager.discover_prompts()
        assert "server-a" in manager.available_prompts
        assert "removed-server" not in manager.available_prompts


@pytest.mark.asyncio
class TestMCPDiscoveryFailureTracking:
    """Tests for tracking discovery failures so admin status can reflect them."""

    async def test_tool_discovery_failure_records_failed_server(self):
        """Tool discovery exception should record server in _failed_servers."""
        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"bad-server": {"description": "Bad"}}
        manager.clients = {"bad-server": AsyncMock()}
        manager._failed_servers = {}

        async def failing_discover(server_name, client):  # noqa: ARG001
            raise RuntimeError("Simulated tool discovery failure")

        manager._discover_tools_for_server = failing_discover  # type: ignore[assignment]

        await manager.discover_tools()

        assert "bad-server" in manager._failed_servers
        error = manager._failed_servers["bad-server"]["error"]
        assert "Simulated tool discovery failure" in error

    async def test_prompt_discovery_failure_records_failed_server(self):
        """Prompt discovery exception should record server in _failed_servers."""
        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"bad-server": {"description": "Bad"}}
        manager.clients = {"bad-server": AsyncMock()}
        manager._failed_servers = {}

        async def failing_discover_prompts(server_name, client):  # noqa: ARG001
            raise RuntimeError("Simulated prompt discovery failure")

        manager._discover_prompts_for_server = failing_discover_prompts  # type: ignore[assignment]

        await manager.discover_prompts()

        assert "bad-server" in manager._failed_servers
        error = manager._failed_servers["bad-server"]["error"]
        assert "Simulated prompt discovery failure" in error


@pytest.mark.asyncio
class TestMCPReconnection:
    """Tests for MCP server reconnection functionality."""

    async def test_reconnect_skips_when_no_failed_servers(self):
        """Test that reconnect returns early when no servers have failed."""
        manager = MCPToolManager.__new__(MCPToolManager)
        manager._failed_servers = {}

        result = await manager.reconnect_failed_servers()

        assert result["attempted"] == []
        assert result["reconnected"] == []
        assert result["still_failed"] == []
        assert result["skipped_backoff"] == []

    @patch('atlas.modules.mcp_tools.client.config_manager')
    async def test_reconnect_respects_backoff(self, mock_config_manager):
        """When not forced, reconnect should skip servers still in backoff."""
        mock_settings = MagicMock()
        mock_settings.mcp_reconnect_interval = 60
        mock_settings.mcp_reconnect_max_interval = 300
        mock_settings.mcp_reconnect_backoff_multiplier = 2.0
        mock_config_manager.app_settings = mock_settings

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"test-server": {"description": "Test"}}
        manager.clients = {}
        # Server failed just now, should be in backoff
        manager._failed_servers = {
            "test-server": {
                "last_attempt": time.time(),
                "attempt_count": 1,
                "error": "Connection refused"
            }
        }

        result = await manager.reconnect_failed_servers(force=False)

        assert result["attempted"] == []
        assert result["skipped_backoff"][0]["server"] == "test-server"

    @patch('atlas.modules.mcp_tools.client.config_manager')
    async def test_reconnect_attempts_after_backoff(self, mock_config_manager):
        """When backoff has elapsed, reconnect should attempt server."""
        mock_settings = MagicMock()
        mock_settings.mcp_reconnect_interval = 60
        mock_settings.mcp_reconnect_max_interval = 300
        mock_settings.mcp_reconnect_backoff_multiplier = 2.0
        mock_config_manager.app_settings = mock_settings

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"test-server": {"description": "Test"}}
        manager.clients = {}
        # Server failed long ago, backoff period has passed
        manager._failed_servers = {
            "test-server": {
                "last_attempt": time.time() - 120,  # 2 minutes ago
                "attempt_count": 1,
                "error": "Connection refused"
            }
        }

        # Mock the initialization method to return None (still failing)
        manager._initialize_single_client = AsyncMock(return_value=None)

        result = await manager.reconnect_failed_servers(force=False)

        assert "test-server" in result["attempted"]
        assert "test-server" in result["still_failed"]
        manager._initialize_single_client.assert_called_once()

    @patch('atlas.modules.mcp_tools.client.config_manager')
    async def test_reconnect_force_ignores_backoff(self, mock_config_manager):
        """Forced reconnect should attempt even inside backoff window."""
        mock_settings = MagicMock()
        mock_settings.mcp_reconnect_interval = 60
        mock_settings.mcp_reconnect_max_interval = 300
        mock_settings.mcp_reconnect_backoff_multiplier = 2.0
        mock_config_manager.app_settings = mock_settings

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"test-server": {"description": "Test"}}
        manager.clients = {}
        # Server failed just now, so normally it would be in backoff
        manager._failed_servers = {
            "test-server": {
                "last_attempt": time.time(),
                "attempt_count": 1,
                "error": "Connection refused"
            }
        }

        # Mock the initialization method to return None (still failing)
        manager._initialize_single_client = AsyncMock(return_value=None)

        # With force=True, it should attempt despite backoff
        result = await manager.reconnect_failed_servers(force=True)

        assert "test-server" in result["attempted"]
        manager._initialize_single_client.assert_called_once()


@pytest.mark.asyncio
class TestMCPServerRefresh:
    """Tests for single-server refresh (refresh_server)."""

    @staticmethod
    def _patch_config(mock_config_manager, servers: dict):
        """Make config_manager.reload_mcp_config return the given server dict."""
        mock_new_config = MagicMock()
        mock_servers = {}
        for name, cfg in servers.items():
            server = MagicMock()
            server.model_dump.return_value = cfg
            mock_servers[name] = server
        mock_new_config.servers = mock_servers
        mock_config_manager.reload_mcp_config.return_value = mock_new_config

    def _make_manager(self, servers_config):
        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = dict(servers_config)
        manager.clients = {}
        manager._failed_servers = {}
        manager.available_tools = {}
        manager.available_prompts = {}
        manager._user_clients_lock = asyncio.Lock()
        manager._server_refresh_lock = asyncio.Lock()
        return manager

    @patch('atlas.modules.mcp_tools.client.config_manager')
    async def test_refresh_connects_failed_server(self, mock_config_manager):
        """A failed server refreshed with a reachable config becomes connected."""
        self._patch_config(mock_config_manager, {"srv": {"url": "http://host"}})
        manager = self._make_manager({"srv": {"url": "http://host"}})
        manager._failed_servers = {"srv": {"last_attempt": time.time(), "attempt_count": 2, "error": "down"}}
        new_client = AsyncMock()
        manager._initialize_single_client = AsyncMock(return_value=new_client)
        manager._discover_and_register_server = AsyncMock()
        manager.available_tools = {"srv": {"tools": ["t1", "t2"]}}
        manager.available_prompts = {"srv": {"prompts": ["p1"]}}

        result = await manager.refresh_server("srv")

        assert result["status"] == "connected"
        assert result["tools"] == 2
        assert result["prompts"] == 1
        assert result["error"] is None
        assert manager.clients["srv"] is new_client
        assert "srv" not in manager._failed_servers
        manager._discover_and_register_server.assert_awaited_once_with("srv", new_client)

    @patch('atlas.modules.mcp_tools.client.config_manager')
    async def test_refresh_detects_config_change(self, mock_config_manager):
        """Refreshing after an mcp.json edit reports config_changed."""
        self._patch_config(mock_config_manager, {"srv": {"url": "http://new-url"}})
        manager = self._make_manager({"srv": {"url": "http://old-url"}})
        manager._initialize_single_client = AsyncMock(return_value=AsyncMock())
        manager._discover_and_register_server = AsyncMock()

        result = await manager.refresh_server("srv")

        assert result["config_changed"] is True
        assert manager.servers_config["srv"] == {"url": "http://new-url"}

    @patch('atlas.modules.mcp_tools.client.config_manager')
    async def test_refresh_failure_records_failure(self, mock_config_manager):
        """When the client cannot be rebuilt the server stays tracked as failed."""
        self._patch_config(mock_config_manager, {"srv": {"url": "http://host"}})
        manager = self._make_manager({"srv": {"url": "http://host"}})
        manager._initialize_single_client = AsyncMock(return_value=None)
        manager._discover_and_register_server = AsyncMock()

        result = await manager.refresh_server("srv")

        assert result["status"] == "failed"
        assert "srv" not in manager.clients
        assert "srv" in manager._failed_servers
        manager._discover_and_register_server.assert_not_awaited()

    @patch('atlas.modules.mcp_tools.client.config_manager')
    async def test_refresh_exception_records_failure(self, mock_config_manager):
        """An exception while rebuilding the client becomes a failure record."""
        self._patch_config(mock_config_manager, {"srv": {"url": "http://host"}})
        manager = self._make_manager({"srv": {"url": "http://host"}})
        manager._initialize_single_client = AsyncMock(side_effect=RuntimeError("boom"))
        manager._discover_and_register_server = AsyncMock()

        result = await manager.refresh_server("srv")

        assert result["status"] == "failed"
        assert "RuntimeError" in result["error"]
        assert "srv" in manager._failed_servers

    @patch('atlas.modules.mcp_tools.client.config_manager')
    async def test_refresh_removed_server_cleans_catalogue(self, mock_config_manager):
        """Refreshing a server that was dropped from mcp.json removes its state."""
        self._patch_config(mock_config_manager, {"other": {"url": "http://other"}})
        manager = self._make_manager({"srv": {"url": "http://host"}, "other": {"url": "http://other"}})
        manager.clients = {"srv": AsyncMock(), "other": AsyncMock()}
        manager.available_tools = {"srv": {"tools": ["t1"]}}
        manager.available_prompts = {"srv": {"prompts": []}}
        manager._tool_index = {
            "srv_t1": {"server": "srv", "tool": MagicMock()},
            "other_t2": {"server": "other", "tool": MagicMock()},
        }
        manager._initialize_single_client = AsyncMock()
        manager._discover_and_register_server = AsyncMock()

        result = await manager.refresh_server("srv")

        assert result["status"] == "removed"
        assert "srv" not in manager.clients
        assert "other" in manager.clients
        assert "srv" not in manager.available_tools
        assert "srv" not in manager.available_prompts
        assert "srv_t1" not in manager._tool_index
        assert "other_t2" in manager._tool_index
        manager._initialize_single_client.assert_not_awaited()

    @patch('atlas.modules.mcp_tools.client.config_manager')
    async def test_refresh_unknown_server(self, mock_config_manager):
        """Refreshing a server that was never configured reports unknown."""
        self._patch_config(mock_config_manager, {"other": {"url": "http://other"}})
        manager = self._make_manager({"other": {"url": "http://other"}})
        manager._initialize_single_client = AsyncMock()

        result = await manager.refresh_server("never-configured")

        assert result["status"] == "unknown"
        manager._initialize_single_client.assert_not_awaited()

    @patch('atlas.modules.mcp_tools.client.config_manager')
    async def test_refresh_tolerates_broken_config(self, mock_config_manager):
        """A config parse error should not block reconnection from memory."""
        mock_config_manager.reload_mcp_config.side_effect = ValueError("bad json")
        manager = self._make_manager({"srv": {"url": "http://host"}})
        manager._initialize_single_client = AsyncMock(return_value=AsyncMock())
        manager._discover_and_register_server = AsyncMock()

        result = await manager.refresh_server("srv")

        assert result["status"] == "connected"
        assert "ValueError" in result["config_reload_error"]
        # The in-memory config is kept when the disk read fails
        assert manager.servers_config == {"srv": {"url": "http://host"}}

    @patch('atlas.modules.mcp_tools.client.config_manager')
    async def test_refresh_closes_previous_client_and_stale_index(self, mock_config_manager):
        """The old shared client is closed and its tool routing entries dropped."""
        self._patch_config(mock_config_manager, {"srv": {"url": "http://host"}})
        manager = self._make_manager({"srv": {"url": "http://host"}})
        old_client = AsyncMock()
        manager.clients = {"srv": old_client}
        manager._tool_index = {
            "srv_old_tool": {"server": "srv", "tool": MagicMock()},
            "other_tool": {"server": "other", "tool": MagicMock()},
        }
        manager._initialize_single_client = AsyncMock(return_value=AsyncMock())
        manager._discover_and_register_server = AsyncMock()

        await manager.refresh_server("srv")

        old_client.__aexit__.assert_awaited_once()
        assert "srv_old_tool" not in manager._tool_index
        assert "other_tool" in manager._tool_index

    @patch('atlas.modules.mcp_tools.client.config_manager')
    async def test_refresh_leaves_other_servers_untouched(self, mock_config_manager):
        """Refreshing server A must not install config edits made to server B.

        The targeted config read installs only the named server's entry, so
        an unrelated edit to B in mcp.json stays out of servers_config until
        a real global reload.
        """
        # Disk config now has srv v2 AND a changed other-server, which the
        # refresh of srv must NOT apply.
        self._patch_config(
            mock_config_manager,
            {"srv": {"url": "http://srv-v2"}, "other": {"url": "http://other-v2"}},
        )
        manager = self._make_manager({"srv": {"url": "http://srv-v1"}})
        manager._initialize_single_client = AsyncMock(return_value=AsyncMock())
        manager._discover_and_register_server = AsyncMock()

        result = await manager.refresh_server("srv")

        assert result["status"] == "connected"
        assert manager.servers_config["srv"] == {"url": "http://srv-v2"}
        # The edit to 'other' is not applied by a targeted refresh
        assert "other" not in manager.servers_config

    @patch('atlas.modules.mcp_tools.client.config_manager')
    async def test_refresh_reports_discovery_failure_as_failed(self, mock_config_manager):
        """A client that builds but cannot complete discovery is a failed refresh.

        Discovery helpers swallow connection errors (record the failure,
        return an empty catalogue), so the failure record after discovery is
        the only signal the server is still down.
        """
        self._patch_config(mock_config_manager, {"srv": {"url": "http://host"}})
        manager = self._make_manager({"srv": {"url": "http://host"}})
        manager._initialize_single_client = AsyncMock(return_value=AsyncMock())

        async def failing_discovery(server_name, client):  # noqa: ARG001
            manager._record_server_failure(server_name, "ConnectError: connection refused")

        manager._discover_and_register_server = failing_discovery  # type: ignore[assignment]

        result = await manager.refresh_server("srv")

        assert result["status"] == "failed"
        assert "connection refused" in result["error"]

    @patch('atlas.modules.mcp_tools.client.config_manager')
    async def test_refresh_serializes_concurrent_refreshes(self, mock_config_manager):
        """Two concurrent refreshes of one server run one after the other."""
        self._patch_config(mock_config_manager, {"srv": {"url": "http://host"}})
        manager = self._make_manager({"srv": {"url": "http://host"}})
        manager._initialize_single_client = AsyncMock(return_value=AsyncMock())
        manager._discover_and_register_server = AsyncMock()

        inside_lock = []

        original_locked = manager._refresh_server_locked

        async def tracking_locked(server_name):
            inside_lock.append("enter")
            await asyncio.sleep(0.01)
            result = await original_locked(server_name)
            inside_lock.append("exit")
            return result

        async def slow_discover(server_name, client):  # noqa: ARG001
            # While one refresh is discovering, another must not be inside
            assert inside_lock.count("enter") == inside_lock.count("exit") + 1

        manager._discover_and_register_server = slow_discover  # type: ignore[assignment]
        manager._refresh_server_locked = tracking_locked  # type: ignore[assignment]

        results = await asyncio.gather(
            manager.refresh_server("srv"),
            manager.refresh_server("srv"),
        )
        assert all(r["status"] == "connected" for r in results)
        assert inside_lock == ["enter", "exit", "enter", "exit"]

    @patch('atlas.modules.mcp_tools.client.config_manager')
    async def test_refresh_clears_per_user_tool_cache(self, mock_config_manager):
        """Refresh drops cached per-user tool catalogues for the server."""
        self._patch_config(mock_config_manager, {"srv": {"url": "http://host"}})
        manager = self._make_manager({"srv": {"url": "http://host"}})
        manager._initialize_single_client = AsyncMock(return_value=AsyncMock())
        manager._discover_and_register_server = AsyncMock()
        cleared = []
        manager.clear_user_tool_cache = lambda server_name=None: cleared.append(server_name)  # type: ignore[method-assign]

        await manager.refresh_server("srv")

        assert cleared == ["srv"]

    async def test_invalidate_user_clients_for_server(self):
        """Idle clients are evicted; in-use clients keep their connection but are marked stale."""
        manager = MCPToolManager.__new__(MCPToolManager)
        manager._user_clients_lock = asyncio.Lock()
        manager._ensure_user_client_cache_state()
        idle_client = AsyncMock()
        in_use_client = AsyncMock()
        other_client = AsyncMock()
        manager._user_clients = {
            ("u1", "srv", "c1"): idle_client,
            ("u2", "srv", "c2"): in_use_client,
            ("u1", "other", "c3"): other_client,
        }
        manager._user_client_active_calls = {("u2", "srv", "c2"): 1}
        manager._user_client_token_fingerprints = {
            ("u1", "srv", "c1"): "fp1",
            ("u2", "srv", "c2"): "fp2",
            ("u1", "other", "c3"): "fp3",
        }

        evicted = await manager._invalidate_user_clients_for_server("srv")

        assert evicted == 1
        assert ("u1", "srv", "c1") not in manager._user_clients
        assert ("u2", "srv", "c2") in manager._user_clients
        assert ("u1", "other", "c3") in manager._user_clients
        idle_client.__aexit__.assert_awaited_once()
        in_use_client.__aexit__.assert_not_awaited()
        # In-use entry is marked stale so both acquisition paths rebuild it
        # (the plain-HTTP path ignores token fingerprints, so the marker is
        # the signal it acts on).
        assert ("u2", "srv", "c2") in manager._user_client_refresh_stale_keys
        assert ("u1", "other", "c3") not in manager._user_client_refresh_stale_keys
        assert ("u2", "srv", "c2") not in manager._user_client_token_fingerprints
        assert manager._user_client_token_fingerprints[("u1", "other", "c3")] == "fp3"

    async def test_refresh_stale_marker_forces_plain_http_rebuild(self):
        """The plain-HTTP acquisition path rebuilds an entry the refresh marked stale."""
        from unittest.mock import patch as mock_patch

        manager = MCPToolManager.__new__(MCPToolManager)
        manager._user_clients_lock = asyncio.Lock()
        manager._ensure_user_client_cache_state()
        stale_client = AsyncMock()
        manager._user_clients = {("u1@example.com", "srv", "c1"): stale_client}
        manager._user_client_refresh_stale_keys.add(("u1@example.com", "srv", "c1"))
        manager.servers_config = {"srv": {"url": "http://localhost:1"}}
        new_client = AsyncMock()
        with mock_patch(
            "atlas.modules.mcp_tools.client.Client", return_value=new_client
        ), mock_patch.object(
            manager, "_create_log_handler", return_value=None
        ), mock_patch.object(
            manager, "_create_elicitation_handler", return_value=None
        ), mock_patch.object(
            manager, "_create_sampling_handler", return_value=None
        ), mock_patch.object(
            manager, "_build_wormhole_headers", return_value={}
        ):
            client = await manager._get_or_create_user_http_client("srv", "u1@example.com", "c1")

        assert client is new_client
        # Old client was closed, marker consumed, fresh entry cached
        stale_client.__aexit__.assert_awaited_once()
        assert ("u1@example.com", "srv", "c1") not in manager._user_client_refresh_stale_keys
        assert manager._user_clients[("u1@example.com", "srv", "c1")] is new_client


class TestConfigManagerMCPReload:
    """Tests for ConfigManager MCP reload functionality."""

    @patch('atlas.modules.config.config_manager.ConfigManager._search_paths')
    @patch('atlas.modules.config.config_manager.ConfigManager._load_file_with_error_handling')
    @patch('atlas.modules.config.config_manager.ConfigManager._validate_mcp_compliance_levels')
    def test_reload_mcp_config_clears_cache(
        self, mock_validate, mock_load, mock_search
    ):
        """Test that reload_mcp_config clears the cached config."""
        from atlas.modules.config.config_manager import ConfigManager

        manager = ConfigManager()
        # Pre-populate cache
        manager._mcp_config = MagicMock()
        manager._tool_approvals_config = MagicMock()

        # Mock the config loading
        mock_search.return_value = []
        mock_load.return_value = {"test-server": {"description": "Test"}}

        manager.reload_mcp_config()

        # Cache should have been cleared and reloaded
        assert manager._tool_approvals_config is None or manager._tool_approvals_config != MagicMock()
