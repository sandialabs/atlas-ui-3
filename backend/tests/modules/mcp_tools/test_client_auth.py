import os
import pytest
from unittest.mock import Mock, patch, AsyncMock
from backend.modules.mcp_tools.client import MCPToolManager
from backend.modules.config.config_manager import resolve_env_var


class TestMCPClientAuthentication:
    """Test MCP client initialization with authentication."""

    @pytest.mark.asyncio
    @patch('backend.modules.mcp_tools.client.Client')
    async def test_http_client_with_env_var_token(self, mock_client_class, monkeypatch):
        """Should resolve env var and pass token to HTTP client."""
        monkeypatch.setenv("MCP_AUTH_TOKEN", "secret-token-123")

        server_config = {
            "url": "http://localhost:8000/mcp",
            "transport": "http",
            "auth_token": "${MCP_AUTH_TOKEN}"
        }

        # Create a dummy MCPToolManager instance to call _initialize_single_client
        # We need to mock the config_manager.mcp_config.servers to return our server_config
        with patch('backend.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config
            
            manager = MCPToolManager()
            # Manually set servers_config for the manager
            manager.servers_config = {"test-server": server_config}
            
            await manager._initialize_single_client("test-server", server_config)

        mock_client_class.assert_called_once_with(
            "http://localhost:8000/mcp",
            auth="secret-token-123"
        )

    @pytest.mark.asyncio
    @patch('backend.modules.mcp_tools.client.Client')
    async def test_http_client_with_literal_token(self, mock_client_class):
        """Should pass literal token string to HTTP client."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        server_config = {
            "url": "http://localhost:8000/mcp",
            "transport": "http",
            "auth_token": "direct-token-456"
        }

        with patch('backend.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config
            
            manager = MCPToolManager()
            manager.servers_config = {"test-server": server_config}
            
            await manager._initialize_single_client("test-server", server_config)

        mock_client_class.assert_called_once_with(
            "http://localhost:8000/mcp",
            auth="direct-token-456"
        )

    @pytest.mark.asyncio
    @patch('backend.modules.mcp_tools.client.Client')
    async def test_http_client_without_token(self, mock_client_class):
        """Should pass None when no auth_token specified."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        server_config = {
            "url": "http://localhost:8000/mcp",
            "transport": "http"
        }

        with patch('backend.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config
            
            manager = MCPToolManager()
            manager.servers_config = {"test-server": server_config}
            
            await manager._initialize_single_client("test-server", server_config)

        mock_client_class.assert_called_once_with(
            "http://localhost:8000/mcp",
            auth=None
        )

    @pytest.mark.asyncio
    @patch('backend.modules.mcp_tools.client.Client')
    async def test_sse_client_with_token(self, mock_client_class):
        """Should pass auth token to SSE client."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        server_config = {
            "url": "http://localhost:8000/sse",
            "transport": "sse",
            "auth_token": "sse-token-789"
        }

        with patch('backend.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config
            
            manager = MCPToolManager()
            manager.servers_config = {"test-server": server_config}
            
            await manager._initialize_single_client("test-server", server_config)

        mock_client_class.assert_called_once_with(
            "http://localhost:8000/sse",
            auth="sse-token-789"
        )

    @pytest.mark.asyncio
    async def test_missing_env_var_raises_error(self, caplog):
        """Should fail gracefully and log error when env var is missing."""
        server_config = {
            "url": "http://localhost:8000/mcp",
            "transport": "http",
            "auth_token": "${MISSING_TOKEN_VAR}"
        }

        with patch('backend.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config

            manager = MCPToolManager()
            manager.servers_config = {"test-server": server_config}

            # Client initialization should return None when env var is missing
            result = await manager._initialize_single_client("test-server", server_config)
            assert result is None
            # Should log the error about missing environment variable
            assert "Environment variable 'MISSING_TOKEN_VAR' is not set" in caplog.text

    @pytest.mark.asyncio
    @patch('backend.modules.mcp_tools.client.Client')
    async def test_stdio_client_ignores_token(self, mock_client_class):
        """stdio clients should ignore auth_token (no auth mechanism)."""
        server_config = {
            "command": ["python", "server.py"],
            "auth_token": "ignored-token"
        }

        with patch('backend.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config
            
            manager = MCPToolManager()
            manager.servers_config = {"test-server": server_config}
            
            await manager._initialize_single_client("test-server", server_config)

        # For stdio, the Client is called with the command, not URL and auth
        mock_client_class.assert_called_once_with(["python", "server.py"])

    @pytest.mark.asyncio
    async def test_malformed_env_var_pattern(self, caplog):
        """Should handle malformed env var patterns gracefully."""
        server_config = {
            "url": "http://localhost:8000/mcp",
            "transport": "http",
            "auth_token": "${MISSING_CLOSING_BRACE"
        }

        with patch('backend.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config

            manager = MCPToolManager()
            manager.servers_config = {"test-server": server_config}

            # Should succeed and pass the malformed pattern as literal string
            result = await manager._initialize_single_client("test-server", server_config)
            assert result is not None  # Client should be created with malformed string as auth token

    @pytest.mark.asyncio
    @patch('backend.modules.mcp_tools.client.Client')
    async def test_empty_auth_token_string(self, mock_client_class):
        """Should pass empty string as auth token."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        server_config = {
            "url": "http://localhost:8000/mcp",
            "transport": "http",
            "auth_token": ""
        }

        with patch('backend.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config
            
            manager = MCPToolManager()
            manager.servers_config = {"test-server": server_config}
            
            await manager._initialize_single_client("test-server", server_config)

        mock_client_class.assert_called_once_with(
            "http://localhost:8000/mcp",
            auth=""
        )
