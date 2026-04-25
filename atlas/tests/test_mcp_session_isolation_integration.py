"""Integration-style tests for cross-conversation MCP session isolation.

These tests use a FakeFastMCPClient that mimics the real ``fastmcp.Client``
shape — supporting ``__aenter__`` / ``__aexit__`` and tracking a nesting
counter the way FastMCP does internally — to exercise the actual
``MCPSessionManager.acquire`` path that the production code goes through.

The earlier mocked tests (``test_mcp_per_user_http_clients.py``) only assert
that the cache key shape returns distinct ``MagicMock`` objects per
conversation. They don't catch the original failure mode: that two
conversations sharing one Client accumulate FastMCP's reentrant nesting
counter, so when one conversation's session task dies the other can no
longer reconnect.
"""
import asyncio

import pytest

from atlas.modules.mcp_tools.client import MCPToolManager
from atlas.modules.mcp_tools.session_manager import MCPSessionManager


class _NestingCounterError(RuntimeError):
    """Mirrors the real FastMCP error on stale reentry."""


class FakeFastMCPClient:
    """A test double that approximates the lifecycle of fastmcp.Client.

    Tracks an internal ``nesting`` counter that increments on
    ``__aenter__`` and decrements on ``__aexit__``. If the underlying
    transport has been killed (``simulate_disconnect``) and a caller
    enters again without first exiting cleanly, we raise the same
    error class FastMCP raises — that's the failure mode this PR fixes.
    """

    def __init__(self, label: str):
        self.label = label
        self.nesting = 0
        self.max_nesting = 0
        self._connected = True
        self._disconnected_inside_session = False

    async def __aenter__(self):
        if self._disconnected_inside_session and self.nesting > 0:
            raise _NestingCounterError(
                f"nesting counter should be 0 when starting new session, "
                f"got {self.nesting}"
            )
        self.nesting += 1
        self.max_nesting = max(self.max_nesting, self.nesting)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.nesting = max(0, self.nesting - 1)
        if self.nesting == 0:
            # A clean full exit clears the simulated stale state.
            self._disconnected_inside_session = False
        return False

    def is_connected(self) -> bool:
        return self._connected

    def simulate_disconnect(self) -> None:
        """Simulate the server-side session task dying mid-flight.

        Clears the connection flag but leaves the nesting counter at
        whatever its current value is — FastMCP's real behavior.
        """
        if self.nesting > 0:
            self._disconnected_inside_session = True
        self._connected = False

    def simulate_recover(self) -> None:
        """After the dead session is closed and a fresh one opens, the
        underlying transport reconnects. Our fake just re-flips the flag."""
        self._connected = True


def _make_manager(client_factory) -> MCPToolManager:
    """Build a minimal MCPToolManager wired to inject the given fake clients.

    Patches _get_or_create_user_http_client to return clients produced by
    the supplied factory, while leaving the real MCPSessionManager and the
    real per-user-clients dict in place so we exercise actual behavior.
    """
    mgr = MCPToolManager.__new__(MCPToolManager)
    mgr.servers_config = {
        "state_server": {
            "url": "http://state-server.local/mcp",
            "transport": "http",
        }
    }
    mgr.clients = {}
    mgr._user_clients = {}
    mgr._user_clients_lock = asyncio.Lock()
    mgr._session_manager = MCPSessionManager()

    async def fake_get_or_create(server_name, user_email, conversation_id):
        if not conversation_id:
            raise ValueError("conversation_id required")
        cache_key = (user_email.lower(), server_name, conversation_id)
        async with mgr._user_clients_lock:
            existing = mgr._user_clients.get(cache_key)
            if existing is not None:
                return existing
            new_client = client_factory(cache_key)
            mgr._user_clients[cache_key] = new_client
            return new_client

    mgr._get_or_create_user_http_client = fake_get_or_create
    return mgr


@pytest.mark.asyncio
async def test_two_conversations_get_independent_session_lifecycles():
    """Each conversation acquires its own Client + ManagedSession; opening
    a session for conv-2 must not bump conv-1's nesting counter (the bug
    pre-fix was that a single shared Client's counter accumulated across
    conversations).
    """
    created: dict = {}

    def factory(key):
        client = FakeFastMCPClient(label=str(key))
        created[key] = client
        return client

    mgr = _make_manager(factory)

    client_a = await mgr._get_or_create_user_http_client(
        "state_server", "alice@test.com", "conv-1"
    )
    client_b = await mgr._get_or_create_user_http_client(
        "state_server", "alice@test.com", "conv-2"
    )

    # Distinct Client instances per conversation.
    assert client_a is not client_b

    sess_a = await mgr._session_manager.acquire(
        "conv-1", "state_server", client_a, user_email="alice@test.com"
    )
    sess_b = await mgr._session_manager.acquire(
        "conv-2", "state_server", client_b, user_email="alice@test.com"
    )

    # Each Client's nesting counter is its own — neither exceeds 1.
    assert client_a.nesting == 1
    assert client_b.nesting == 1
    assert client_a.max_nesting == 1
    assert client_b.max_nesting == 1
    assert sess_a is not sess_b


@pytest.mark.asyncio
async def test_dead_session_in_one_conversation_does_not_break_another():
    """Killing conv-1's session task must leave conv-2 healthy.

    Pre-fix this was the symptom: conv-1's dead session left FastMCP's
    counter > 0 on the *shared* client, so conv-2's next acquire on the
    same shared client raised the nesting-counter error.

    Post-fix conv-1 and conv-2 hold distinct Clients, so conv-1's dead
    state is contained and conv-2 keeps working.
    """
    def factory(key):
        return FakeFastMCPClient(label=str(key))

    mgr = _make_manager(factory)

    client_a = await mgr._get_or_create_user_http_client(
        "state_server", "alice@test.com", "conv-1"
    )
    client_b = await mgr._get_or_create_user_http_client(
        "state_server", "alice@test.com", "conv-2"
    )

    await mgr._session_manager.acquire(
        "conv-1", "state_server", client_a, user_email="alice@test.com"
    )
    await mgr._session_manager.acquire(
        "conv-2", "state_server", client_b, user_email="alice@test.com"
    )

    # Simulate conv-1's session dying server-side.
    client_a.simulate_disconnect()

    # Conv-2 must remain usable: it can still be acquired (cache hit on
    # the live session) and a tool call against client_b would not raise
    # the nesting-counter error because client_b is independent.
    sess_b_again = await mgr._session_manager.acquire(
        "conv-2", "state_server", client_b, user_email="alice@test.com"
    )
    assert sess_b_again.is_open is True
    assert client_b.nesting == 1  # unchanged by conv-1's disaster

    # Conv-1 can recover by closing the dead session and opening fresh.
    # The session manager detects is_connected() == False, evicts, and
    # we open a new one. Allow recovery on the underlying client.
    client_a.simulate_recover()
    sess_a_new = await mgr._session_manager.acquire(
        "conv-1", "state_server", client_a, user_email="alice@test.com"
    )
    assert sess_a_new.is_open is True


@pytest.mark.asyncio
async def test_release_sessions_only_closes_target_conversation():
    """release_sessions("conv-1", user_email=...) must close conv-1's
    ManagedSession AND evict conv-1's cached Client without touching
    conv-2's session or cache.
    """
    def factory(key):
        return FakeFastMCPClient(label=str(key))

    mgr = _make_manager(factory)

    client_a = await mgr._get_or_create_user_http_client(
        "state_server", "alice@test.com", "conv-1"
    )
    client_b = await mgr._get_or_create_user_http_client(
        "state_server", "alice@test.com", "conv-2"
    )
    await mgr._session_manager.acquire(
        "conv-1", "state_server", client_a, user_email="alice@test.com"
    )
    await mgr._session_manager.acquire(
        "conv-2", "state_server", client_b, user_email="alice@test.com"
    )

    await mgr.release_sessions("conv-1", user_email="alice@test.com")

    # Conv-1's Client cache entry is gone.
    assert ("alice@test.com", "state_server", "conv-1") not in mgr._user_clients
    # Conv-2's cache entry survives.
    assert ("alice@test.com", "state_server", "conv-2") in mgr._user_clients

    # Conv-1's session is closed (counter back to 0); conv-2's still open.
    assert client_a.nesting == 0
    assert client_b.nesting == 1

    # MCPSessionManager state mirrors the cache.
    assert (
        "alice@test.com", "conv-1", "state_server"
    ) not in mgr._session_manager._sessions
    assert (
        "alice@test.com", "conv-2", "state_server"
    ) in mgr._session_manager._sessions


@pytest.mark.asyncio
async def test_pre_fix_failure_mode_documented():
    """If two conversations had been forced to share one Client (the
    pre-fix bug), simulate_disconnect on that shared Client + retry
    raises ``_NestingCounterError`` — proving our fake reproduces the
    real failure shape FastMCP exhibits.
    """
    shared = FakeFastMCPClient(label="shared")

    # First conversation opens a session.
    await shared.__aenter__()
    assert shared.nesting == 1

    # Second conversation opens a session on the same Client (the bug).
    await shared.__aenter__()
    assert shared.nesting == 2

    # First conversation's session dies server-side without a clean exit.
    shared.simulate_disconnect()

    # The first conversation tries to reconnect — re-entry fails because
    # nesting is still > 0. This is the failure mode the PR's
    # per-conversation cache prevents from ever happening.
    with pytest.raises(_NestingCounterError):
        await shared.__aenter__()
