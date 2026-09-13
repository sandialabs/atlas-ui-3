"""Tests for 401-driven recovery in MCP tool execution (issue #935).

Covers three behaviors:
- a cached per-user client is rebuilt when the stored OAuth token is rotated
  by a silent refresh from another cache entry (fingerprint check);
- ``refresh_stored_token`` invalidates every cached client for the
  (user, server) pair across all conversations after rotating the token;
- ``call_tool`` performs a single refresh-and-retry on HTTP 401 and raises
  ``AuthenticationRequiredException`` (friendly message, no raw upstream
  URL) when the retry is refused too.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from atlas.modules.mcp_tools.client import MCPToolManager
from atlas.modules.mcp_tools.mcp_errors import _is_unauthorized_error
from atlas.modules.mcp_tools.session_manager import MCPSessionManager
from atlas.modules.mcp_tools.token_storage import AuthenticationRequiredException

SERVER = "oauth-server"
USER = "user@example.com"
CONV = "conv-1"


def _fake_client(call_side_effect=None):
    """A FastMCP-client-shaped mock: async context manager, no task support."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.is_connected = MagicMock(return_value=True)
    client.initialize_result.capabilities.tasks = None
    client.call_tool = AsyncMock(side_effect=call_side_effect)
    return client


def _http_401(url: str = "https://upstream.example/mcp") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", url)
    response = httpx.Response(401, request=request)
    return httpx.HTTPStatusError(
        f"Client error '401 Unauthorized' for url '{url}'",
        request=request,
        response=response,
    )


@pytest.fixture
def manager():
    with patch("atlas.modules.mcp_tools.client.config_manager") as mock_cm:
        mock_cm.app_settings.mcp_call_timeout = 30
        mgr = MCPToolManager.__new__(MCPToolManager)
        mgr.servers_config = {SERVER: {"auth_type": "oauth", "url": "https://upstream.example/mcp"}}
        mgr.clients = {}
        mgr._user_clients = {}
        mgr._user_clients_lock = __import__("asyncio").Lock()
        mgr._session_manager = MCPSessionManager()
        mgr._server_task_support = {}
        mgr._tool_task_forbidden = set()
        mgr._task_timeout = 1
        mgr._elicitation_routing = {}
        mgr._sampling_routing = {}
        mgr._default_log_callback = None
        mgr._min_log_level = 20
        return mgr


class TestCallTool401Retry:
    """call_tool: single refresh-and-retry on HTTP 401 from a per-user server."""

    @pytest.mark.asyncio
    async def test_retries_once_after_401_and_succeeds(self, manager):
        failing = _fake_client(call_side_effect=_http_401())
        healed = _fake_client()
        healed.call_tool = AsyncMock(return_value={"ok": True})

        key = (USER, SERVER, CONV)
        manager._ensure_user_client_cache_state()
        manager._user_clients[key] = failing
        manager._touch_user_client_locked(key)
        # Register the failing conversation's session like a live call would.
        from atlas.modules.mcp_tools.session_manager import ManagedSession

        manager._session_manager._sessions[(USER, CONV, SERVER)] = ManagedSession(failing)

        manager._get_user_client = AsyncMock(side_effect=[failing, healed])
        manager._refresh_oauth_token = AsyncMock(return_value=MagicMock())

        result = await manager.call_tool(
            SERVER, "my_tool", {}, user_email=USER, conversation_id=CONV
        )

        assert result == {"ok": True}
        assert manager._get_user_client.await_count == 2
        manager._refresh_oauth_token.assert_awaited_once_with(
            USER, SERVER, manager.servers_config[SERVER],
            force=True, expected_previous_fingerprint=None,
        )
        # The dead session was released so the retry could not reuse it: the
        # surviving session is a new one bound to the healed client, not the
        # failing one.
        live = manager._session_manager._sessions[(USER, CONV, SERVER)]
        assert live.client is healed
        assert live.client is not failing

    @pytest.mark.asyncio
    async def test_second_401_raises_friendly_auth_required(self, manager):
        failing = _fake_client(call_side_effect=_http_401())
        still_failing = _fake_client(call_side_effect=_http_401())

        manager._get_user_client = AsyncMock(side_effect=[failing, still_failing])
        manager._refresh_oauth_token = AsyncMock(return_value=MagicMock())

        with pytest.raises(AuthenticationRequiredException) as exc_info:
            await manager.call_tool(
                SERVER, "my_tool", {}, user_email=USER, conversation_id=CONV
            )

        message = str(exc_info.value)
        assert "401" in message
        assert "re-authorized" in message or "reconnected" in message
        # The raw upstream endpoint must not leak into the model-facing error.
        assert "upstream.example" not in message
        assert exc_info.value.oauth_start_url == f"/api/mcp/auth/{SERVER}/oauth/start"
        assert manager._get_user_client.await_count == 2

    @pytest.mark.asyncio
    async def test_refresh_failure_still_retries_then_reports(self, manager):
        """A refused refresh falls through to the retry and the friendly error."""
        failing = _fake_client(call_side_effect=_http_401())
        still_failing = _fake_client(call_side_effect=_http_401())

        manager._get_user_client = AsyncMock(side_effect=[failing, still_failing])
        manager._refresh_oauth_token = AsyncMock(return_value=None)

        with pytest.raises(AuthenticationRequiredException):
            await manager.call_tool(SERVER, "my_tool", {}, user_email=USER, conversation_id=CONV)

        manager._refresh_oauth_token.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_retry_for_non_401_errors(self, manager):
        failing = _fake_client(call_side_effect=RuntimeError("boom"))

        manager._get_user_client = AsyncMock(return_value=failing)
        manager._refresh_oauth_token = AsyncMock(return_value=MagicMock())

        with pytest.raises(RuntimeError, match="boom"):
            await manager.call_tool(SERVER, "my_tool", {}, user_email=USER, conversation_id=CONV)

        assert manager._get_user_client.await_count == 1
        manager._refresh_oauth_token.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_retry_without_user_context(self, manager):
        """No user context: the pre-call auth error fires before any call."""
        manager._get_user_client = AsyncMock(return_value=_fake_client())

        with pytest.raises(AuthenticationRequiredException):
            await manager.call_tool(SERVER, "my_tool", {}, user_email=None, conversation_id=CONV)

        assert manager._get_user_client.await_count == 0

    @pytest.mark.asyncio
    async def test_no_retry_when_client_could_not_be_built(self, manager):
        """The pre-call 'no token' signal is not a 401 and is never retried."""
        manager._get_user_client = AsyncMock(return_value=None)

        with pytest.raises(AuthenticationRequiredException):
            await manager.call_tool(SERVER, "my_tool", {}, user_email=USER, conversation_id=CONV)

        assert manager._get_user_client.await_count == 1

    @pytest.mark.asyncio
    async def test_retry_works_without_conversation_session(self, manager):
        """Per-call (sessionless) clients get the same recovery."""
        failing = _fake_client(call_side_effect=_http_401())
        healed = _fake_client()
        healed.call_tool = AsyncMock(return_value={"ok": True})

        manager._get_user_client = AsyncMock(side_effect=[failing, healed])
        manager._refresh_oauth_token = AsyncMock(return_value=MagicMock())

        result = await manager.call_tool(SERVER, "my_tool", {}, user_email=USER)

        assert result == {"ok": True}
        assert manager._get_user_client.await_count == 2

    @pytest.mark.asyncio
    async def test_non_oauth_auth_type_gets_honest_message(self, manager):
        """bearer/api_key servers have no refresh flow: say so, offer no URL."""
        manager.servers_config[SERVER]["auth_type"] = "bearer"
        failing = _fake_client(call_side_effect=_http_401())
        still_failing = _fake_client(call_side_effect=_http_401())

        manager._get_user_client = AsyncMock(side_effect=[failing, still_failing])
        manager._refresh_oauth_token = AsyncMock(return_value=None)

        with pytest.raises(AuthenticationRequiredException) as exc_info:
            await manager.call_tool(SERVER, "my_tool", {}, user_email=USER, conversation_id=CONV)

        message = str(exc_info.value)
        assert "401" in message
        # No refresh happened for a bearer server; the message must not claim one.
        assert "token refresh" not in message
        assert exc_info.value.oauth_start_url is None


class TestExecuteToolSurfacesAuthRequired:
    """execute_tool forwards AuthenticationRequiredException to the executor."""

    @pytest.fixture
    def exec_manager(self, manager):

        manager.available_tools = {
            SERVER: [MagicMock(name="my_tool", spec=[])]
        }
        manager._tool_index = {
            "my_tool": {"server": SERVER, "tool": MagicMock(name="tool_obj")}
        }
        return manager

    @pytest.mark.asyncio
    async def test_auth_required_is_reraised_not_stringified(self, exec_manager):
        auth_err = AuthenticationRequiredException(
            server_name=SERVER,
            auth_type="oauth",
            message="Server 'oauth-server' rejected the stored credential (401 Unauthorized).",
            oauth_start_url=f"/api/mcp/auth/{SERVER}/oauth/start",
        )
        with patch.object(exec_manager, "call_tool", side_effect=auth_err):
            tool_call = MagicMock(id="tc-1", arguments={})
            tool_call.name = "my_tool"
            with pytest.raises(AuthenticationRequiredException) as exc_info:
                await exec_manager.execute_tool(
                    tool_call,
                    context={"user_email": USER, "conversation_id": CONV},
                )

        assert exc_info.value is auth_err

    @pytest.mark.asyncio
    async def test_execute_tool_calls_keeps_list_contract(self, exec_manager):
        """Batch callers get a failed ToolResult, not a raised exception."""
        auth_err = AuthenticationRequiredException(
            server_name=SERVER,
            auth_type="oauth",
            message="rejected credential",
            oauth_start_url=None,
        )
        with patch.object(exec_manager, "call_tool", side_effect=auth_err):
            tool_call = MagicMock(id="tc-1", arguments={})
            tool_call.name = "my_tool"
            results = await exec_manager.execute_tool_calls(
                [tool_call],
                context={"user_email": USER, "conversation_id": CONV},
            )

        assert len(results) == 1
        assert results[0].success is False
        assert "Authentication required" in results[0].content
        assert results[0].meta_data["auth_required"] is True


class TestUnauthorizedErrorDetection:
    """_is_unauthorized_error walks chains and matches the canonical text."""

    def test_direct_401(self):
        assert _is_unauthorized_error(_http_401()) is True

    def test_wrapped_401_via_cause(self):
        wrapped = RuntimeError("call failed")
        wrapped.__cause__ = _http_401()
        assert _is_unauthorized_error(wrapped) is True

    def test_non_httpx_wrapper_with_401_text(self):
        wrapped = RuntimeError(
            "Client error '401 Unauthorized' for url 'https://upstream.example/mcp'"
        )
        assert _is_unauthorized_error(wrapped) is True

    def test_non_401_status(self):
        request = httpx.Request("POST", "https://upstream.example/mcp")
        err = httpx.HTTPStatusError(
            "Client error '403 Forbidden' for url 'https://upstream.example/mcp'",
            request=request,
            response=httpx.Response(403, request=request),
        )
        assert _is_unauthorized_error(err) is False

    def test_unrelated_error(self):
        assert _is_unauthorized_error(RuntimeError("boom")) is False


class TestRefreshInvalidatesCachedClients:
    """refresh_stored_token must evict clients built with the previous token."""

    @pytest.fixture
    def cache_manager(self, manager):
        """A manager with cached clients for two conversations.

        c1 was touched moments ago (treated as possibly in use); c2 was last
        used long ago (idle, evictable) -- mirroring a rotation observed while
        another conversation is streaming.
        """

        manager._ensure_user_client_cache_state()
        fresh_key = (USER, SERVER, CONV)
        manager._user_clients[fresh_key] = MagicMock()
        manager._touch_user_client_locked(fresh_key)
        idle_key = (USER, SERVER, "conv-2")
        manager._user_clients[idle_key] = MagicMock()
        manager._touch_user_client_locked(idle_key)
        manager._user_client_last_used[idle_key] = (
            time.monotonic() - manager._user_client_cache_in_use_window_seconds - 1
        )
        # An unrelated user's entry must survive.
        other_key = ("other@example.com", SERVER, CONV)
        manager._user_clients[other_key] = MagicMock()
        manager._touch_user_client_locked(other_key)
        return manager

    @pytest.mark.asyncio
    async def test_refresh_rotates_token_and_invalidates_all_conversations(
        self, cache_manager
    ):
        refreshed = MagicMock()
        refreshed.token_value = "token-v2"
        storage = MagicMock()
        existing = MagicMock()
        existing.refresh_token = "refresh-token"
        storage.get_token.return_value = existing
        storage.get_valid_token.return_value = None  # expired: refresh proceeds
        storage.update_oauth_tokens.return_value = refreshed

        oauth_client = MagicMock()
        oauth_client.redirect_uri = "redirect-uri"

        async def _metadata(url):
            return MagicMock()

        with patch("atlas.modules.mcp_tools.mcp_oauth_service.get_token_storage",
                   return_value=storage), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service.get_server_oauth_metadata",
                   new=AsyncMock(return_value=MagicMock())), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service._base_url_from_settings",
                   return_value="http://atlas.example"), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service.redirect_uri_for",
                   return_value="redirect-uri"), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service._existing_client",
                   return_value=oauth_client), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service.refresh_access_token",
                   new=AsyncMock(return_value=MagicMock(
                       access_token="token-v2",
                       expires_at=1234.0,
                       refresh_token="refresh-token-v2",
                       scopes=None,
                   ))), \
             patch("atlas.infrastructure.app_factory.app_factory") as mock_factory:
            mock_factory.get_mcp_manager.return_value = cache_manager

            from atlas.modules.mcp_tools import mcp_oauth_service

            result = await mcp_oauth_service.refresh_stored_token(
                USER, SERVER, cache_manager.servers_config[SERVER]
            )

        assert result is refreshed
        # Idle entries for the (user, server) pair are evicted...
        assert (USER, SERVER, "conv-2") not in cache_manager._user_clients
        # ...entries possibly in use are left for the fingerprint check to
        # rebuild (never torn down mid-call)...
        assert (USER, SERVER, CONV) in cache_manager._user_clients
        # ...and other users are untouched.
        assert ("other@example.com", SERVER, CONV) in cache_manager._user_clients

    @pytest.mark.asyncio
    async def test_forced_refresh_contacts_provider_despite_valid_token(self):
        """force=True refreshes even when the stored token still looks valid.

        After the provider retires a credential server-side, expires_at is a
        lie; the 401 recovery relies on the forced path.
        """
        storage = MagicMock()
        existing = MagicMock()
        existing.refresh_token = "refresh-token"
        storage.get_token.return_value = existing
        storage.get_valid_token.return_value = MagicMock(token_value="still-valid")
        refreshed = MagicMock()
        storage.update_oauth_tokens.return_value = refreshed

        oauth_client = MagicMock()
        oauth_client.redirect_uri = "redirect-uri"

        with patch("atlas.modules.mcp_tools.mcp_oauth_service.get_token_storage",
                   return_value=storage), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service.get_server_oauth_metadata",
                   new=AsyncMock(return_value=MagicMock())), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service._base_url_from_settings",
                   return_value="http://atlas.example"), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service.redirect_uri_for",
                   return_value="redirect-uri"), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service._existing_client",
                   return_value=oauth_client), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service.refresh_access_token",
                   new=AsyncMock(return_value=MagicMock(
                       access_token="token-v2",
                       expires_at=1234.0,
                       refresh_token="refresh-token-v2",
                       scopes=None,
                   ))), \
             patch("atlas.infrastructure.app_factory.app_factory") as mock_factory:
            mock_factory.get_mcp_manager.return_value = None

            from atlas.modules.mcp_tools import mcp_oauth_service

            result = await mcp_oauth_service.refresh_stored_token(
                USER, SERVER, {"auth_type": "oauth", "url": "https://upstream.example/mcp"}, force=True
            )

        assert result is refreshed
        storage.update_oauth_tokens.assert_called_once()

    @pytest.mark.asyncio
    async def test_unforced_refresh_still_short_circuits_on_valid_token(self):
        """Without force, a still-valid token is returned untouched."""
        storage = MagicMock()
        valid = MagicMock(token_value="still-valid")
        storage.get_valid_token.return_value = valid

        with patch("atlas.modules.mcp_tools.mcp_oauth_service.get_token_storage",
                   return_value=storage):
            from atlas.modules.mcp_tools import mcp_oauth_service

            result = await mcp_oauth_service.refresh_stored_token(
                USER, SERVER, {"auth_type": "oauth", "url": "https://upstream.example/mcp"}
            )

        assert result is valid
        storage.update_oauth_tokens.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalidation_failure_does_not_fail_the_refresh(self):
        """A broken manager must not turn a successful refresh into an error."""
        storage = MagicMock()
        existing = MagicMock()
        existing.refresh_token = "refresh-token"
        storage.get_token.return_value = existing
        storage.get_valid_token.return_value = None
        storage.update_oauth_tokens.return_value = MagicMock()

        oauth_client = MagicMock()
        oauth_client.redirect_uri = "redirect-uri"

        with patch("atlas.modules.mcp_tools.mcp_oauth_service.get_token_storage",
                   return_value=storage), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service.get_server_oauth_metadata",
                   new=AsyncMock(return_value=MagicMock())), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service._base_url_from_settings",
                   return_value="http://atlas.example"), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service.redirect_uri_for",
                   return_value="redirect-uri"), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service._existing_client",
                   return_value=oauth_client), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service.refresh_access_token",
                   new=AsyncMock(return_value=MagicMock(
                       access_token="token-v2",
                       expires_at=1234.0,
                       refresh_token="refresh-token-v2",
                       scopes=None,
                   ))), \
             patch("atlas.infrastructure.app_factory.app_factory") as mock_factory:
            broken = MagicMock()
            broken._invalidate_user_client = AsyncMock(side_effect=RuntimeError("no"))
            mock_factory.get_mcp_manager.return_value = broken

            from atlas.modules.mcp_tools import mcp_oauth_service

            result = await mcp_oauth_service.refresh_stored_token(
                USER, SERVER, {"auth_type": "oauth", "url": "https://upstream.example/mcp"}
            )

        assert result is not None


class TestConcurrentForcedRefreshesShareRotation:
    """N concurrent 401s must produce one provider rotation, not N.

    Against a rotating provider, refresh k+1 retires the credential caller k
    just retried with. The forced path therefore carries the failing token's
    fingerprint and short-circuits when the stored token no longer matches.
    """

    def _oauth_mocks(self, shared_record):
        storage = MagicMock()
        storage.get_token.return_value = shared_record
        storage.get_valid_token.return_value = shared_record

        def _update(**kwargs):
            shared_record.token_value = kwargs["access_token"]
            shared_record.refresh_token = kwargs["refresh_token"]
            return shared_record

        storage.update_oauth_tokens.side_effect = _update

        oauth_client = MagicMock()
        oauth_client.redirect_uri = "redirect-uri"
        return storage, oauth_client

    @pytest.mark.asyncio
    async def test_two_concurrent_forced_refreshes_contact_provider_once(self):
        from types import SimpleNamespace

        shared_record = SimpleNamespace(
            token_value="token-v1",
            refresh_token="r1",
            token_type="oauth_access",
            expires_at=1234.0,
            scopes=None,
            metadata={},
        )
        storage, oauth_client = self._oauth_mocks(shared_record)

        refresh_mock = AsyncMock(return_value=MagicMock(
            access_token="token-v2",
            expires_at=1234.0,
            refresh_token="r2",
            scopes=None,
        ))

        with patch("atlas.modules.mcp_tools.mcp_oauth_service.get_token_storage",
                   return_value=storage), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service.get_server_oauth_metadata",
                   new=AsyncMock(return_value=MagicMock())), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service._base_url_from_settings",
                   return_value="http://atlas.example"), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service.redirect_uri_for",
                   return_value="redirect-uri"), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service._existing_client",
                   return_value=oauth_client), \
             patch("atlas.modules.mcp_tools.mcp_oauth_service.refresh_access_token",
                   new=refresh_mock), \
             patch("atlas.infrastructure.app_factory.app_factory") as mock_factory:
            mock_factory.get_mcp_manager.return_value = None

            from atlas.modules.mcp_tools import mcp_oauth_service
            from atlas.modules.mcp_tools.token_storage import token_fingerprint

            old_fp = token_fingerprint(shared_record)

            first, second = await asyncio.gather(
                mcp_oauth_service.refresh_stored_token(
                    USER, SERVER, {"auth_type": "oauth", "url": "https://upstream.example/mcp"},
                    force=True, expected_previous_fingerprint=old_fp,
                ),
                mcp_oauth_service.refresh_stored_token(
                    USER, SERVER, {"auth_type": "oauth", "url": "https://upstream.example/mcp"},
                    force=True, expected_previous_fingerprint=old_fp,
                ),
            )

        assert first is not None
        assert second is not None
        # Exactly one provider rotation for two concurrent 401s.
        assert refresh_mock.await_count == 1
        # The second caller got the already-rotated credential back.
        assert second.token_value == "token-v2"
