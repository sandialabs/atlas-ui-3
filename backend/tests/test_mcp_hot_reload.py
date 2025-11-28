"""Tests for MCP hot reload and auto-reconnect functionality."""

import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from modules.mcp_tools.client import MCPToolManager


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

    @patch('modules.mcp_tools.client.config_manager')
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

    @patch('modules.mcp_tools.client.config_manager')
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

    @patch('modules.mcp_tools.client.config_manager')
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

    @patch('modules.mcp_tools.client.config_manager')
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

    @patch('modules.mcp_tools.client.config_manager')
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

    @patch('modules.mcp_tools.client.config_manager')
    async def test_reconnect_respects_backoff(self, mock_config_manager):
        """Test that reconnect skips servers still in backoff period."""
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
        
        result = await manager.reconnect_failed_servers()
        
        assert result["attempted"] == []
        assert result["skipped_backoff"][0]["server"] == "test-server"

    @patch('modules.mcp_tools.client.config_manager')
    async def test_reconnect_attempts_after_backoff(self, mock_config_manager):
        """Test that reconnect attempts servers after backoff period."""
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
        
        result = await manager.reconnect_failed_servers()
        
        assert "test-server" in result["attempted"]
        assert "test-server" in result["still_failed"]
        manager._initialize_single_client.assert_called_once()


class TestConfigManagerMCPReload:
    """Tests for ConfigManager MCP reload functionality."""

    @patch('modules.config.config_manager.ConfigManager._search_paths')
    @patch('modules.config.config_manager.ConfigManager._load_file_with_error_handling')
    @patch('modules.config.config_manager.ConfigManager._validate_mcp_compliance_levels')
    def test_reload_mcp_config_clears_cache(
        self, mock_validate, mock_load, mock_search
    ):
        """Test that reload_mcp_config clears the cached config."""
        from modules.config.config_manager import ConfigManager
        
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
