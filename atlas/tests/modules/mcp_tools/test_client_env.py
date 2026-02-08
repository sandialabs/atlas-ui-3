
from unittest.mock import Mock, patch

import pytest

from atlas.modules.mcp_tools.client import MCPToolManager


class TestMCPClientEnvironmentVariables:
    """Test MCP client initialization with environment variables."""

    @pytest.mark.asyncio
    @patch('atlas.modules.mcp_tools.client.Client')
    @patch('fastmcp.client.transports.StdioTransport')
    async def test_stdio_client_with_env_vars(self, mock_transport_class, mock_client_class, monkeypatch):
        """Should pass environment variables to StdioTransport."""
        # Set up environment variables for resolution
        monkeypatch.setenv("MY_ENV_VAR", "resolved-value")

        server_config = {
            "command": ["python", "server.py"],
            "cwd": "backend",
            "env": {
                "VAR1": "literal-value",
                "VAR2": "another-literal"
            }
        }

        with patch('atlas.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config

            # Mock os.path.exists to return True for cwd
            with patch('os.path.exists', return_value=True):
                manager = MCPToolManager()
                manager.servers_config = {"test-server": server_config}

                await manager._initialize_single_client("test-server", server_config)

        # Verify StdioTransport was called with env dict
        assert mock_transport_class.called
        call_kwargs = mock_transport_class.call_args[1]
        assert "env" in call_kwargs
        assert call_kwargs["env"] == {
            "VAR1": "literal-value",
            "VAR2": "another-literal"
        }

    @pytest.mark.asyncio
    @patch('atlas.modules.mcp_tools.client.Client')
    @patch('fastmcp.client.transports.StdioTransport')
    async def test_stdio_client_with_env_var_resolution(self, mock_transport_class, mock_client_class, monkeypatch):
        """Should resolve ${ENV_VAR} patterns in env values."""
        # Set up environment variables
        monkeypatch.setenv("CLOUD_PROFILE", "my-profile-9")
        monkeypatch.setenv("CLOUD_REGION", "us-east-7")

        server_config = {
            "command": ["python", "server.py"],
            "cwd": "backend",
            "env": {
                "PROFILE": "${CLOUD_PROFILE}",
                "REGION": "${CLOUD_REGION}",
                "LITERAL": "not-a-var"
            }
        }

        with patch('atlas.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config

            # Mock os.path.exists to return True for cwd
            with patch('os.path.exists', return_value=True):
                manager = MCPToolManager()
                manager.servers_config = {"test-server": server_config}

                await manager._initialize_single_client("test-server", server_config)

        # Verify env vars were resolved
        assert mock_transport_class.called
        call_kwargs = mock_transport_class.call_args[1]
        assert call_kwargs["env"] == {
            "PROFILE": "my-profile-9",
            "REGION": "us-east-7",
            "LITERAL": "not-a-var"
        }

    @pytest.mark.asyncio
    @patch('atlas.modules.mcp_tools.client.Client')
    @patch('fastmcp.client.transports.StdioTransport')
    async def test_stdio_client_without_env(self, mock_transport_class, mock_client_class):
        """Should pass None when no env specified."""
        server_config = {
            "command": ["python", "server.py"],
            "cwd": "backend"
        }

        with patch('atlas.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config

            # Mock os.path.exists to return True for cwd
            with patch('os.path.exists', return_value=True):
                manager = MCPToolManager()
                manager.servers_config = {"test-server": server_config}

                await manager._initialize_single_client("test-server", server_config)

        # Verify env is None
        assert mock_transport_class.called
        call_kwargs = mock_transport_class.call_args[1]
        assert call_kwargs["env"] is None

    @pytest.mark.asyncio
    async def test_stdio_client_missing_env_var_fails(self, caplog):
        """Should fail when env var resolution fails."""
        server_config = {
            "command": ["python", "server.py"],
            "cwd": "backend",
            "env": {
                "PROFILE": "${MISSING_VAR}"
            }
        }

        with patch('atlas.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config

            # Mock os.path.exists to return True for cwd
            with patch('os.path.exists', return_value=True):
                manager = MCPToolManager()
                manager.servers_config = {"test-server": server_config}

                result = await manager._initialize_single_client("test-server", server_config)

        # Should return None and log error
        assert result is None
        assert "Failed to resolve env var" in caplog.text
        assert "MISSING_VAR" in caplog.text

    @pytest.mark.asyncio
    @patch('atlas.modules.mcp_tools.client.Client')
    @patch('fastmcp.client.transports.StdioTransport')
    async def test_stdio_client_with_env_no_cwd(self, mock_transport_class, mock_client_class, monkeypatch):
        """Should pass env vars even when no cwd specified."""
        monkeypatch.setenv("MY_VAR", "my-value")

        server_config = {
            "command": ["python", "server.py"],
            "env": {
                "TEST_VAR": "${MY_VAR}"
            }
        }

        with patch('atlas.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config

            manager = MCPToolManager()
            manager.servers_config = {"test-server": server_config}

            await manager._initialize_single_client("test-server", server_config)

        # Verify env was passed
        assert mock_transport_class.called
        call_kwargs = mock_transport_class.call_args[1]
        assert call_kwargs["env"] == {"TEST_VAR": "my-value"}

    @pytest.mark.asyncio
    @patch('atlas.modules.mcp_tools.client.Client')
    @patch('fastmcp.client.transports.StdioTransport')
    async def test_stdio_client_empty_env_dict(self, mock_transport_class, mock_client_class):
        """Should handle empty env dict."""
        server_config = {
            "command": ["python", "server.py"],
            "env": {}
        }

        with patch('atlas.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config

            manager = MCPToolManager()
            manager.servers_config = {"test-server": server_config}

            await manager._initialize_single_client("test-server", server_config)

        # Empty dict should become empty dict (not None)
        assert mock_transport_class.called
        call_kwargs = mock_transport_class.call_args[1]
        assert call_kwargs["env"] == {}
