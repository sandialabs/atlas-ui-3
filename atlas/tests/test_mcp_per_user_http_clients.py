"""Tests for per-user HTTP client isolation in MCPToolManager."""

import asyncio

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from atlas.modules.mcp_tools.client import MCPToolManager


@pytest.fixture
def manager():
    """Create an MCPToolManager with test config."""
    with patch("atlas.modules.mcp_tools.client.config_manager") as mock_cm:
        mock_cm.app_settings.app_config_dir = "/tmp/nonexistent"
        mock_cm.app_settings.mcp_call_timeout = 30
        mock_cm.app_settings.log_level = "INFO"
        mock_cm.mcp_config.servers = {}
        mgr = MCPToolManager.__new__(MCPToolManager)
        mgr.config_path = "/tmp/test"
        mgr.servers_config = {}
        mgr.clients = {}
        mgr.available_tools = {}
        mgr.available_prompts = {}
        mgr._failed_servers = {}
        mgr._reconnect_task = None
        mgr._reconnect_running = False
        mgr._default_log_callback = None
        mgr._min_log_level = 20
        mgr._user_clients = {}
        mgr._user_clients_lock = asyncio.Lock()
        return mgr


# --- _is_http_server tests ---

def test_is_http_server_true_for_http(manager):
    manager.servers_config["my_server"] = {"url": "http://localhost:8010/mcp", "transport": "http"}
    assert manager._is_http_server("my_server") is True


def test_is_http_server_false_for_stdio(manager):
    manager.servers_config["my_server"] = {"command": ["python", "main.py"]}
    assert manager._is_http_server("my_server") is False


def test_is_http_server_false_for_missing(manager):
    assert manager._is_http_server("nonexistent") is False


# --- _get_or_create_user_http_client tests ---

@pytest.mark.asyncio
async def test_get_or_create_user_http_client_creates_client(manager):
    manager.servers_config["state_server"] = {
        "url": "http://127.0.0.1:8010/mcp",
        "transport": "http",
    }
    with patch("atlas.modules.mcp_tools.client.Client") as MockClient:
        mock_instance = MagicMock()
        MockClient.return_value = mock_instance
        client = await manager._get_or_create_user_http_client("state_server", "alice@test.com")
        assert client is mock_instance


@pytest.mark.asyncio
async def test_get_or_create_user_http_client_caches(manager):
    manager.servers_config["state_server"] = {
        "url": "http://127.0.0.1:8010/mcp",
        "transport": "http",
    }
    with patch("atlas.modules.mcp_tools.client.Client") as MockClient:
        mock_instance = MagicMock()
        MockClient.return_value = mock_instance
        c1 = await manager._get_or_create_user_http_client("state_server", "alice@test.com")
        c2 = await manager._get_or_create_user_http_client("state_server", "alice@test.com")
        assert c1 is c2
        assert MockClient.call_count == 1  # Only created once


@pytest.mark.asyncio
async def test_get_or_create_user_http_client_isolates_users(manager):
    manager.servers_config["state_server"] = {
        "url": "http://127.0.0.1:8010/mcp",
        "transport": "http",
    }
    with patch("atlas.modules.mcp_tools.client.Client") as MockClient:
        MockClient.side_effect = [MagicMock(), MagicMock()]
        c1 = await manager._get_or_create_user_http_client("state_server", "alice@test.com")
        c2 = await manager._get_or_create_user_http_client("state_server", "bob@test.com")
        assert c1 is not c2
        assert MockClient.call_count == 2
