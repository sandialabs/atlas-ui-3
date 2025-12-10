"""Tests for MCP OAuth configuration and authentication."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from backend.modules.config.config_manager import OAuthConfig, MCPServerConfig


class TestOAuthConfig:
    """Test OAuth configuration model."""

    def test_oauth_config_defaults(self):
        """Test OAuth config with default values."""
        config = OAuthConfig()
        assert config.enabled is False
        assert config.scopes is None
        assert config.client_name == "Atlas UI 3"
        assert config.callback_port is None
        assert config.token_storage_path is None
        assert config.additional_metadata is None

    def test_oauth_config_with_values(self):
        """Test OAuth config with custom values."""
        config = OAuthConfig(
            enabled=True,
            scopes="read write admin",
            client_name="My Custom Client",
            callback_port=8080,
            token_storage_path="~/.my-tokens",
            additional_metadata={"key": "value"}
        )
        assert config.enabled is True
        assert config.scopes == "read write admin"
        assert config.client_name == "My Custom Client"
        assert config.callback_port == 8080
        assert config.token_storage_path == "~/.my-tokens"
        assert config.additional_metadata == {"key": "value"}


class TestMCPServerConfigOAuth:
    """Test MCP server config with OAuth fields."""

    def test_server_config_with_oauth(self):
        """Test server config with OAuth configuration."""
        config = MCPServerConfig(
            url="https://api.example.com/mcp",
            oauth_config=OAuthConfig(
                enabled=True,
                scopes="read write"
            )
        )
        assert config.oauth_config is not None
        assert config.oauth_config.enabled is True
        assert config.oauth_config.scopes == "read write"

    def test_server_config_without_oauth(self):
        """Test server config without OAuth configuration."""
        config = MCPServerConfig(
            url="https://api.example.com/mcp"
        )
        assert config.oauth_config is None

    def test_server_config_with_jwt_file(self):
        """Test server config with JWT file path."""
        config = MCPServerConfig(
            url="https://api.example.com/mcp",
            jwt_file="/path/to/jwt.txt"
        )
        assert config.jwt_file == "/path/to/jwt.txt"

    def test_auth_priority_fields(self):
        """Test that all auth fields can coexist in config."""
        config = MCPServerConfig(
            url="https://api.example.com/mcp",
            auth_token="${MY_TOKEN}",
            oauth_config=OAuthConfig(enabled=True),
            jwt_file="/path/to/jwt.txt"
        )
        # All fields should be present
        assert config.auth_token == "${MY_TOKEN}"
        assert config.oauth_config.enabled is True
        assert config.jwt_file == "/path/to/jwt.txt"


class TestMCPClientOAuthIntegration:
    """Test MCP client OAuth authentication integration."""

    @pytest.fixture
    def mock_oauth_class(self):
        """Mock the OAuth class from FastMCP."""
        with patch('backend.modules.mcp_tools.client.OAuth') as mock:
            yield mock

    @pytest.fixture
    def mock_client_class(self):
        """Mock the FastMCP Client class."""
        with patch('backend.modules.mcp_tools.client.Client') as mock:
            mock_instance = AsyncMock()
            mock.return_value = mock_instance
            yield mock

    @pytest.fixture
    def mcp_tool_manager(self):
        """Create MCPToolManager instance for testing."""
        from backend.modules.mcp_tools.client import MCPToolManager
        # Use a test config path that doesn't exist
        manager = MCPToolManager(config_path="/nonexistent/test.json")
        manager.servers_config = {}
        return manager

    @pytest.mark.asyncio
    async def test_get_auth_oauth_enabled(self, mcp_tool_manager, mock_oauth_class):
        """Test _get_auth_for_server with OAuth enabled."""
        config = {
            "url": "https://api.example.com/mcp",
            "oauth_config": {
                "enabled": True,
                "scopes": "read write",
                "client_name": "Test Client"
            }
        }

        # Call the method
        auth = mcp_tool_manager._get_auth_for_server(
            "test-server",
            config,
            url="https://api.example.com/mcp"
        )

        # Should have called OAuth constructor
        mock_oauth_class.assert_called_once()
        call_kwargs = mock_oauth_class.call_args[1]
        assert call_kwargs["mcp_url"] == "https://api.example.com/mcp"
        assert call_kwargs["scopes"] == "read write"
        assert call_kwargs["client_name"] == "Test Client"

    @pytest.mark.asyncio
    async def test_get_auth_bearer_token(self, mcp_tool_manager):
        """Test _get_auth_for_server with bearer token."""
        config = {
            "auth_token": "my-secret-token"
        }

        auth = mcp_tool_manager._get_auth_for_server("test-server", config)

        # Should return the token string
        assert auth == "my-secret-token"

    @pytest.mark.asyncio
    async def test_get_auth_jwt_storage(self, mcp_tool_manager):
        """Test _get_auth_for_server with stored JWT."""
        with patch('backend.modules.mcp_tools.client.get_jwt_storage') as mock_storage:
            mock_jwt_storage = Mock()
            mock_jwt_storage.has_jwt.return_value = True
            mock_jwt_storage.get_jwt.return_value = "stored-jwt-token"
            mock_storage.return_value = mock_jwt_storage

            config = {}
            auth = mcp_tool_manager._get_auth_for_server("test-server", config)

            # Should retrieve JWT from storage
            assert auth == "stored-jwt-token"
            mock_jwt_storage.has_jwt.assert_called_once_with("test-server")
            mock_jwt_storage.get_jwt.assert_called_once_with("test-server")

    @pytest.mark.asyncio
    async def test_get_auth_priority_oauth_over_jwt(self, mcp_tool_manager, mock_oauth_class):
        """Test that OAuth takes priority over stored JWT."""
        with patch('backend.modules.mcp_tools.client.get_jwt_storage') as mock_storage:
            mock_jwt_storage = Mock()
            mock_jwt_storage.has_jwt.return_value = True
            mock_jwt_storage.get_jwt.return_value = "stored-jwt-token"
            mock_storage.return_value = mock_jwt_storage

            config = {
                "url": "https://api.example.com/mcp",
                "oauth_config": {
                    "enabled": True,
                    "scopes": "read"
                }
            }

            auth = mcp_tool_manager._get_auth_for_server(
                "test-server",
                config,
                url="https://api.example.com/mcp"
            )

            # Should use OAuth, not JWT
            mock_oauth_class.assert_called_once()
            # JWT storage should not be checked
            mock_jwt_storage.has_jwt.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_auth_priority_jwt_over_bearer(self, mcp_tool_manager):
        """Test that stored JWT takes priority over auth_token."""
        with patch('backend.modules.mcp_tools.client.get_jwt_storage') as mock_storage:
            mock_jwt_storage = Mock()
            mock_jwt_storage.has_jwt.return_value = True
            mock_jwt_storage.get_jwt.return_value = "stored-jwt-token"
            mock_storage.return_value = mock_jwt_storage

            config = {
                "auth_token": "bearer-token"
            }

            auth = mcp_tool_manager._get_auth_for_server("test-server", config)

            # Should use JWT, not bearer token
            assert auth == "stored-jwt-token"

    @pytest.mark.asyncio
    async def test_get_auth_no_auth(self, mcp_tool_manager):
        """Test _get_auth_for_server with no authentication configured."""
        with patch('backend.modules.mcp_tools.client.get_jwt_storage') as mock_storage:
            mock_jwt_storage = Mock()
            mock_jwt_storage.has_jwt.return_value = False
            mock_storage.return_value = mock_jwt_storage

            config = {}
            auth = mcp_tool_manager._get_auth_for_server("test-server", config)

            # Should return None
            assert auth is None

    @pytest.mark.asyncio
    async def test_get_auth_oauth_with_storage_path(self, mcp_tool_manager, mock_oauth_class, tmp_path):
        """Test OAuth with custom token storage path."""
        storage_path = tmp_path / "oauth-tokens"

        config = {
            "url": "https://api.example.com/mcp",
            "oauth_config": {
                "enabled": True,
                "token_storage_path": str(storage_path)
            }
        }

        # Mock the encryption wrapper imports
        with patch('backend.modules.mcp_tools.client.DiskStore') as mock_disk, \
             patch('backend.modules.mcp_tools.client.FernetEncryptionWrapper') as mock_wrapper, \
             patch('backend.modules.mcp_tools.client.Fernet') as mock_fernet:
            
            auth = mcp_tool_manager._get_auth_for_server(
                "test-server",
                config,
                url="https://api.example.com/mcp"
            )

            # Should have created encrypted storage
            mock_disk.assert_called_once()
            mock_wrapper.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_auth_env_var_resolution(self, mcp_tool_manager, monkeypatch):
        """Test that auth_token environment variables are resolved."""
        monkeypatch.setenv("TEST_TOKEN", "secret-123")

        config = {
            "auth_token": "${TEST_TOKEN}"
        }

        auth = mcp_tool_manager._get_auth_for_server("test-server", config)

        # Should resolve to actual value
        assert auth == "secret-123"

    @pytest.mark.asyncio
    async def test_get_auth_env_var_missing(self, mcp_tool_manager):
        """Test that missing environment variable returns None."""
        config = {
            "auth_token": "${MISSING_TOKEN}"
        }

        auth = mcp_tool_manager._get_auth_for_server("test-server", config)

        # Should return None when env var resolution fails
        assert auth is None
