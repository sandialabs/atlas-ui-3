"""Unit tests for MCP client authentication methods.

Tests the per-user authentication functionality in MCPToolManager:
- _requires_user_auth: Check if server requires user authentication
- _get_user_client: Get or create user-specific client with token
- Cache validation and invalidation

Updated: 2025-01-23
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestRequiresUserAuth:
    """Test _requires_user_auth method."""

    def test_requires_user_auth_for_jwt(self):
        """JWT auth_type should require user auth."""
        from modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"test-server": {"auth_type": "jwt"}}

        assert manager._requires_user_auth("test-server") is True

    def test_requires_user_auth_for_oauth(self):
        """OAuth auth_type should require user auth."""
        from modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"test-server": {"auth_type": "oauth"}}

        assert manager._requires_user_auth("test-server") is True

    def test_requires_user_auth_for_bearer(self):
        """Bearer auth_type should require user auth."""
        from modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"test-server": {"auth_type": "bearer"}}

        assert manager._requires_user_auth("test-server") is True

    def test_requires_user_auth_for_api_key(self):
        """API key auth_type should require user auth."""
        from modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"test-server": {"auth_type": "api_key"}}

        assert manager._requires_user_auth("test-server") is True

    def test_no_user_auth_for_none(self):
        """None auth_type should not require user auth."""
        from modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"test-server": {"auth_type": "none"}}

        assert manager._requires_user_auth("test-server") is False

    def test_no_user_auth_when_missing(self):
        """Missing auth_type should not require user auth (defaults to none)."""
        from modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"test-server": {}}

        assert manager._requires_user_auth("test-server") is False

    def test_no_user_auth_for_unknown_server(self):
        """Unknown server should not require user auth."""
        from modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {}

        assert manager._requires_user_auth("unknown-server") is False


class TestGetUserClient:
    """Test _get_user_client method."""

    @pytest.fixture
    def manager(self):
        """Create a mock MCPToolManager for testing."""
        import asyncio
        from modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {
            "test-server": {
                "auth_type": "api_key",
                "url": "http://localhost:8080"
            }
        }
        manager._user_clients = {}
        manager._user_clients_lock = asyncio.Lock()
        manager._create_log_handler = MagicMock(return_value=None)
        manager._create_elicitation_handler = MagicMock(return_value=None)
        manager._create_sampling_handler = MagicMock(return_value=None)
        return manager

    @pytest.mark.asyncio
    async def test_returns_none_without_token(self, manager):
        """Should return None when user has no token stored."""
        with patch("modules.mcp_tools.token_storage.get_token_storage") as mock_storage:
            mock_token_storage = MagicMock()
            mock_token_storage.get_valid_token.return_value = None
            mock_storage.return_value = mock_token_storage

            result = await manager._get_user_client("test-server", "user@example.com")

            assert result is None

    @pytest.mark.asyncio
    async def test_creates_client_with_token(self, manager):
        """Should create client when user has valid token."""
        with patch("modules.mcp_tools.token_storage.get_token_storage") as mock_storage, \
             patch("modules.mcp_tools.client.Client") as mock_client_class:

            # Mock token storage
            mock_token = MagicMock()
            mock_token.token_value = "test-api-key-123"
            mock_token_storage = MagicMock()
            mock_token_storage.get_valid_token.return_value = mock_token
            mock_storage.return_value = mock_token_storage

            # Mock Client constructor
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            result = await manager._get_user_client("test-server", "user@example.com")

            assert result is mock_client
            mock_client_class.assert_called_once()
            # Verify token was passed to client
            call_kwargs = mock_client_class.call_args
            assert call_kwargs[1]["auth"] == "test-api-key-123"

    @pytest.mark.asyncio
    async def test_caches_client(self, manager):
        """Should cache client for subsequent calls."""
        with patch("modules.mcp_tools.token_storage.get_token_storage") as mock_storage, \
             patch("modules.mcp_tools.client.Client") as mock_client_class:

            mock_token = MagicMock()
            mock_token.token_value = "test-api-key"
            mock_token_storage = MagicMock()
            mock_token_storage.get_valid_token.return_value = mock_token
            mock_storage.return_value = mock_token_storage

            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            # First call creates client
            result1 = await manager._get_user_client("test-server", "user@example.com")
            # Second call should use cache
            result2 = await manager._get_user_client("test-server", "user@example.com")

            assert result1 is result2
            # Client constructor only called once
            assert mock_client_class.call_count == 1

    @pytest.mark.asyncio
    async def test_invalidates_cache_on_expired_token(self, manager):
        """Should invalidate cached client when token expires."""
        with patch("modules.mcp_tools.token_storage.get_token_storage") as mock_storage, \
             patch("modules.mcp_tools.client.Client") as mock_client_class:

            mock_token = MagicMock()
            mock_token.token_value = "test-api-key"
            mock_token_storage = MagicMock()
            mock_storage.return_value = mock_token_storage

            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            # First call - token valid
            mock_token_storage.get_valid_token.return_value = mock_token
            result1 = await manager._get_user_client("test-server", "user@example.com")
            assert result1 is mock_client

            # Second call - token expired (returns None)
            mock_token_storage.get_valid_token.return_value = None
            result2 = await manager._get_user_client("test-server", "user@example.com")

            # Should return None and cache should be invalidated
            assert result2 is None
            cache_key = ("user@example.com", "test-server")
            assert cache_key not in manager._user_clients


class TestInvalidateUserClient:
    """Test _invalidate_user_client method."""

    @pytest.mark.asyncio
    async def test_removes_cached_client(self):
        """Should remove client from cache."""
        import asyncio
        from modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager._user_clients = {
            ("user@example.com", "test-server"): MagicMock(),
            ("other@example.com", "test-server"): MagicMock(),
        }
        manager._user_clients_lock = asyncio.Lock()

        await manager._invalidate_user_client("user@example.com", "test-server")

        assert ("user@example.com", "test-server") not in manager._user_clients
        # Other user's client should remain
        assert ("other@example.com", "test-server") in manager._user_clients

    @pytest.mark.asyncio
    async def test_handles_missing_cache_entry(self):
        """Should not error when cache entry doesn't exist."""
        import asyncio
        from modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager._user_clients = {}
        manager._user_clients_lock = asyncio.Lock()

        # Should not raise
        await manager._invalidate_user_client("user@example.com", "test-server")
