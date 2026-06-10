"""Unit tests for MCP client authentication methods.

Tests the per-user authentication functionality in MCPToolManager:
- _requires_user_auth: Check if server requires user authentication
- _get_user_client: Get or create user-specific client with token
- Cache validation and invalidation

Updated: 2025-01-23
"""

from unittest.mock import MagicMock, patch

import pytest


class TestRequiresUserAuth:
    """Test _requires_user_auth method."""

    def test_requires_user_auth_for_jwt(self):
        """JWT auth_type should require user auth."""
        from atlas.modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"test-server": {"auth_type": "jwt"}}

        assert manager._requires_user_auth("test-server") is True

    def test_requires_user_auth_for_oauth(self):
        """OAuth auth_type should require user auth."""
        from atlas.modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"test-server": {"auth_type": "oauth"}}

        assert manager._requires_user_auth("test-server") is True

    def test_requires_user_auth_for_bearer(self):
        """Bearer auth_type should require user auth."""
        from atlas.modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"test-server": {"auth_type": "bearer"}}

        assert manager._requires_user_auth("test-server") is True

    def test_requires_user_auth_for_api_key(self):
        """API key auth_type should require user auth."""
        from atlas.modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"test-server": {"auth_type": "api_key"}}

        assert manager._requires_user_auth("test-server") is True

    def test_no_user_auth_for_none(self):
        """None auth_type should not require user auth."""
        from atlas.modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"test-server": {"auth_type": "none"}}

        assert manager._requires_user_auth("test-server") is False

    def test_no_user_auth_when_missing(self):
        """Missing auth_type should not require user auth (defaults to none)."""
        from atlas.modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {"test-server": {}}

        assert manager._requires_user_auth("test-server") is False

    def test_no_user_auth_for_unknown_server(self):
        """Unknown server should not require user auth."""
        from atlas.modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {}

        assert manager._requires_user_auth("unknown-server") is False


class TestGetUserClient:
    """Test _get_user_client method."""

    @pytest.fixture
    def manager(self):
        """Create a mock MCPToolManager for testing."""
        import asyncio

        from atlas.modules.mcp_tools.client import MCPToolManager

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
        with patch("atlas.modules.mcp_tools.token_storage.get_token_storage") as mock_storage:
            mock_token_storage = MagicMock()
            mock_token_storage.get_valid_token.return_value = None
            mock_storage.return_value = mock_token_storage

            result = await manager._get_user_client("test-server", "user@example.com")

            assert result is None

    @pytest.mark.asyncio
    async def test_creates_client_with_api_key_header(self, manager):
        """Should create client with custom header for API key auth type."""
        with patch("atlas.modules.mcp_tools.token_storage.get_token_storage") as mock_storage, \
             patch("atlas.modules.mcp_tools.client.Client") as mock_client_class, \
             patch("atlas.modules.mcp_tools.client.StreamableHttpTransport") as mock_transport_class:

            # Mock token storage
            mock_token = MagicMock()
            mock_token.token_value = "test-api-key-123"
            mock_token_storage = MagicMock()
            mock_token_storage.get_valid_token.return_value = mock_token
            mock_storage.return_value = mock_token_storage

            # Mock transport and Client constructor
            mock_transport = MagicMock()
            mock_transport_class.return_value = mock_transport
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            result = await manager._get_user_client("test-server", "user@example.com")

            assert result is mock_client
            # Verify StreamableHttpTransport was created with custom header
            mock_transport_class.assert_called_once()
            transport_call_kwargs = mock_transport_class.call_args
            assert transport_call_kwargs[1]["headers"] == {"X-API-Key": "test-api-key-123"}
            # Verify Client was created with transport
            mock_client_class.assert_called_once()
            client_call_kwargs = mock_client_class.call_args
            assert client_call_kwargs[1]["transport"] is mock_transport

    @pytest.mark.asyncio
    async def test_creates_client_with_bearer_token(self):
        """Should create client with auth parameter for bearer auth type."""
        import asyncio

        from atlas.modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {
            "bearer-server": {
                "auth_type": "bearer",
                "url": "http://localhost:8080"
            }
        }
        manager._user_clients = {}
        manager._user_clients_lock = asyncio.Lock()
        manager._create_log_handler = MagicMock(return_value=None)
        manager._create_elicitation_handler = MagicMock(return_value=None)
        manager._create_sampling_handler = MagicMock(return_value=None)

        with patch("atlas.modules.mcp_tools.token_storage.get_token_storage") as mock_storage, \
             patch("atlas.modules.mcp_tools.client.Client") as mock_client_class:

            # Mock token storage
            mock_token = MagicMock()
            mock_token.token_value = "bearer-token-123"
            mock_token_storage = MagicMock()
            mock_token_storage.get_valid_token.return_value = mock_token
            mock_storage.return_value = mock_token_storage

            # Mock Client constructor
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            result = await manager._get_user_client("bearer-server", "user@example.com")

            assert result is mock_client
            mock_client_class.assert_called_once()
            # Verify token was passed as auth parameter (not via transport)
            call_args = mock_client_class.call_args
            assert call_args[0][0] == "http://localhost:8080"  # URL as first positional arg
            assert call_args[1]["auth"] == "bearer-token-123"

    @pytest.mark.asyncio
    async def test_uses_custom_auth_header_name(self):
        """Should use custom auth_header from config for API key auth."""
        import asyncio

        from atlas.modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {
            "custom-header-server": {
                "auth_type": "api_key",
                "auth_header": "X-Custom-Auth",
                "url": "http://localhost:8080"
            }
        }
        manager._user_clients = {}
        manager._user_clients_lock = asyncio.Lock()
        manager._create_log_handler = MagicMock(return_value=None)
        manager._create_elicitation_handler = MagicMock(return_value=None)
        manager._create_sampling_handler = MagicMock(return_value=None)

        with patch("atlas.modules.mcp_tools.token_storage.get_token_storage") as mock_storage, \
             patch("atlas.modules.mcp_tools.client.Client") as mock_client_class, \
             patch("atlas.modules.mcp_tools.client.StreamableHttpTransport") as mock_transport_class:

            mock_token = MagicMock()
            mock_token.token_value = "custom-key-456"
            mock_token_storage = MagicMock()
            mock_token_storage.get_valid_token.return_value = mock_token
            mock_storage.return_value = mock_token_storage

            mock_transport = MagicMock()
            mock_transport_class.return_value = mock_transport
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            result = await manager._get_user_client("custom-header-server", "user@example.com")

            assert result is mock_client
            # Verify custom header name was used
            transport_call_kwargs = mock_transport_class.call_args
            assert transport_call_kwargs[1]["headers"] == {"X-Custom-Auth": "custom-key-456"}

    @pytest.mark.asyncio
    async def test_caches_client(self, manager):
        """Should cache client for subsequent calls."""
        with patch("atlas.modules.mcp_tools.token_storage.get_token_storage") as mock_storage, \
             patch("atlas.modules.mcp_tools.client.Client") as mock_client_class, \
             patch("atlas.modules.mcp_tools.client.StreamableHttpTransport") as mock_transport_class:

            mock_token = MagicMock()
            mock_token.token_value = "test-api-key"
            mock_token_storage = MagicMock()
            mock_token_storage.get_valid_token.return_value = mock_token
            mock_storage.return_value = mock_token_storage

            mock_transport = MagicMock()
            mock_transport_class.return_value = mock_transport
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
        with patch("atlas.modules.mcp_tools.token_storage.get_token_storage") as mock_storage, \
             patch("atlas.modules.mcp_tools.client.Client") as mock_client_class, \
             patch("atlas.modules.mcp_tools.client.StreamableHttpTransport") as mock_transport_class:

            mock_token = MagicMock()
            mock_token.token_value = "test-api-key"
            mock_token_storage = MagicMock()
            mock_storage.return_value = mock_token_storage

            mock_transport = MagicMock()
            mock_transport_class.return_value = mock_transport
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            # First call - token valid
            mock_token_storage.get_valid_token.return_value = mock_token
            result1 = await manager._get_user_client("test-server", "user@example.com", "conv-1")
            assert result1 is mock_client

            # Second call - token expired (returns None)
            mock_token_storage.get_valid_token.return_value = None
            result2 = await manager._get_user_client("test-server", "user@example.com", "conv-1")

            # Should return None and cache should be invalidated
            assert result2 is None
            cache_key = ("user@example.com", "test-server", "conv-1")
            assert cache_key not in manager._user_clients


class TestInvalidateUserClient:
    """Test _invalidate_user_client method."""

    def _make_manager(self, clients: dict):
        """Build a minimal MCPToolManager suitable for invalidation tests."""
        import asyncio

        from atlas.modules.mcp_tools.client import MCPToolManager
        from atlas.modules.mcp_tools.session_manager import MCPSessionManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager._user_clients = clients
        manager._user_clients_lock = asyncio.Lock()
        manager._session_manager = MCPSessionManager()
        return manager

    @pytest.mark.asyncio
    async def test_removes_cached_client(self):
        """Should remove client from cache."""
        manager = self._make_manager({
            ("user@example.com", "test-server", "conv-1"): MagicMock(),
            ("user@example.com", "test-server", "conv-2"): MagicMock(),
            ("other@example.com", "test-server", "conv-1"): MagicMock(),
        })

        await manager._invalidate_user_client("user@example.com", "test-server")

        # All conversation entries for the target user/server are removed
        assert ("user@example.com", "test-server", "conv-1") not in manager._user_clients
        assert ("user@example.com", "test-server", "conv-2") not in manager._user_clients
        # Other user's client should remain
        assert ("other@example.com", "test-server", "conv-1") in manager._user_clients

    @pytest.mark.asyncio
    async def test_handles_missing_cache_entry(self):
        """Should not error when cache entry doesn't exist."""
        manager = self._make_manager({})
        # Should not raise
        await manager._invalidate_user_client("user@example.com", "test-server")

    @pytest.mark.asyncio
    async def test_releases_orphaned_sessions_on_revocation(self):
        """Sessions that outlived their cache entry must be closed on token revocation.

        Scenario: the LRU sweeper already evicted the _user_clients cache entry
        but its async close-task has not yet run, so MCPSessionManager still
        holds a live session.  _invalidate_user_client must close it via
        release_sessions_for_user_server even when _user_clients is empty.
        """
        from unittest.mock import AsyncMock

        from atlas.modules.mcp_tools.session_manager import ManagedSession

        manager = self._make_manager({})

        # Manually inject a live session into the session manager to simulate
        # the "orphaned session" scenario (cache entry already gone).
        fake_client = MagicMock()
        fake_client.is_connected = MagicMock(return_value=True)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=None)
        session = ManagedSession(fake_client)
        await session.open()

        key = ("user@example.com", "conv-1", "test-server")
        manager._session_manager._sessions[key] = session
        manager._session_manager._conv_index.setdefault("conv-1", set()).add(key)

        assert session.is_open

        await manager._invalidate_user_client("user@example.com", "test-server")

        # The orphaned session must have been closed.
        assert session._closed
        # Session index must be cleaned up.
        assert key not in manager._session_manager._sessions
        assert "conv-1" not in manager._session_manager._conv_index


class TestGetPromptAuthRouting:
    """Tests that get_prompt mirrors call_tool's auth routing.

    Pre-fix, get_prompt only checked _is_http_server and routed every HTTP
    server through _get_or_create_user_http_client (admin/server-default
    token). For servers with auth_type=oauth/jwt/bearer/api_key this caused
    prompt fetches to run unauthenticated even though tool calls on the
    same server ran as the user.
    """

    def _make_manager_for_auth_server(self, auth_type: str = "bearer"):
        import asyncio

        from atlas.modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {
            "auth-server": {
                "auth_type": auth_type,
                "url": "http://auth-server.local",
            },
        }
        manager.clients = {}
        manager._user_clients = {}
        manager._user_clients_lock = asyncio.Lock()
        manager._create_log_handler = MagicMock(return_value=None)
        manager._create_elicitation_handler = MagicMock(return_value=None)
        manager._create_sampling_handler = MagicMock(return_value=None)
        return manager

    @pytest.mark.asyncio
    async def test_get_prompt_uses_user_client_for_auth_server(self):
        """get_prompt on an auth-required HTTP server must go through
        _get_user_client, not _get_or_create_user_http_client.
        """
        from unittest.mock import AsyncMock

        manager = self._make_manager_for_auth_server("bearer")

        user_client = MagicMock()
        user_client.__aenter__ = AsyncMock(return_value=user_client)
        user_client.__aexit__ = AsyncMock(return_value=None)
        user_client.get_prompt = AsyncMock(return_value="prompt-text")

        manager._get_user_client = AsyncMock(return_value=user_client)
        manager._get_or_create_user_http_client = AsyncMock(
            side_effect=AssertionError(
                "auth-required server must not fall through to admin-token client"
            )
        )

        result = await manager.get_prompt(
            "auth-server",
            "my_prompt",
            user_email="alice@example.com",
            conversation_id="conv-1",
        )

        assert result == "prompt-text"
        manager._get_user_client.assert_awaited_once_with(
            "auth-server", "alice@example.com", "conv-1",
        )
        manager._get_or_create_user_http_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_prompt_raises_when_no_user_token(self):
        """Auth-required server with no stored token must raise
        AuthenticationRequiredException, not silently fall back to admin auth.
        """
        from unittest.mock import AsyncMock

        from atlas.modules.mcp_tools.token_storage import (
            AuthenticationRequiredException,
        )

        manager = self._make_manager_for_auth_server("oauth")
        manager._get_user_client = AsyncMock(return_value=None)
        manager._get_or_create_user_http_client = AsyncMock(
            side_effect=AssertionError("must not fall through on missing token")
        )

        with pytest.raises(AuthenticationRequiredException) as exc_info:
            await manager.get_prompt(
                "auth-server",
                "my_prompt",
                user_email="bob@example.com",
                conversation_id="conv-2",
            )

        assert exc_info.value.server_name == "auth-server"
        assert exc_info.value.auth_type == "oauth"
        assert "/api/mcp/auth/auth-server/oauth/start" in (
            exc_info.value.oauth_start_url or ""
        )

    @pytest.mark.asyncio
    async def test_get_prompt_raises_when_no_user_email(self):
        """Auth-required server invoked without user_email must raise."""
        from atlas.modules.mcp_tools.token_storage import (
            AuthenticationRequiredException,
        )

        manager = self._make_manager_for_auth_server("jwt")

        with pytest.raises(AuthenticationRequiredException):
            await manager.get_prompt(
                "auth-server",
                "my_prompt",
                user_email=None,
                conversation_id="conv-3",
            )

    @pytest.mark.asyncio
    async def test_get_prompt_uses_http_client_for_unauthed_server(self):
        """Plain HTTP server (no auth_type) with a user_email still routes
        through the per-conversation HTTP client — unchanged behavior.
        """
        import asyncio
        from unittest.mock import AsyncMock

        from atlas.modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {
            "plain-http": {"url": "http://plain.local"},
        }
        manager.clients = {}
        manager._user_clients = {}
        manager._user_clients_lock = asyncio.Lock()
        manager._create_log_handler = MagicMock(return_value=None)
        manager._create_elicitation_handler = MagicMock(return_value=None)
        manager._create_sampling_handler = MagicMock(return_value=None)

        plain_client = MagicMock()
        plain_client.__aenter__ = AsyncMock(return_value=plain_client)
        plain_client.__aexit__ = AsyncMock(return_value=None)
        plain_client.get_prompt = AsyncMock(return_value="ok")

        manager._get_or_create_user_http_client = AsyncMock(return_value=plain_client)
        manager._get_user_client = AsyncMock(
            side_effect=AssertionError("non-auth server must not call _get_user_client")
        )

        result = await manager.get_prompt(
            "plain-http",
            "p",
            user_email="alice@example.com",
            conversation_id="conv-x",
        )
        assert result == "ok"
        manager._get_or_create_user_http_client.assert_awaited_once()
