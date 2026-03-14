# FastMCP 3.x Comprehensive Upgrade Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt six FastMCP 3.x features — session persistence, meta routing fix (#295), session state, structured output, background tasks, and multi-prompt support — in a single branch.

**Architecture:** New `MCPSessionManager` holds live sessions per `(conversation_id, server_name)` instead of opening/closing per tool call. `meta` parameter threads `tool_call_id` through to elicitation/sampling handlers for correct routing. Background tasks use `ToolTask` with adaptive wait. Prompt override supports multi-select and passes `meta` context.

**Tech Stack:** Python 3.14, FastMCP 3.1.0, asyncio, Pydantic, pytest, pykeyvalue (Redis optional)

**Spec:** `docs/superpowers/specs/2026-03-13-fastmcp-3x-comprehensive-upgrade-design.md`

---

## File Structure

| File | Type | Responsibility |
|------|------|----------------|
| `atlas/modules/mcp_tools/session_manager.py` | **New** | `MCPSessionManager`, `ManagedSession`, `SessionStore` protocol |
| `atlas/modules/mcp_tools/client.py` | Modify | Use session manager, fix routing, add meta/task/structured output |
| `atlas/modules/mcp_tools/__init__.py` | Modify | Export `MCPSessionManager` |
| `atlas/application/chat/utilities/tool_executor.py` | Modify | Thread `conversation_id` into tool context |
| `atlas/application/chat/preprocessors/prompt_override_service.py` | Modify | Multi-prompt, meta, multi-content extraction |
| `atlas/main.py` | Modify | Session cleanup on disconnect/restore |
| `atlas/mcp/common/__init__.py` | **New** | Package init |
| `atlas/mcp/common/state.py` | **New** | `get_state_store()` utility |
| `.env.example` | Modify | Add new env vars |
| `atlas/tests/test_session_manager.py` | **New** | Session lifecycle tests |
| `atlas/tests/test_elicitation_routing.py` | Modify | Concurrent same-server routing tests |
| `atlas/tests/test_adaptive_task_polling.py` | **New** | ToolTask adaptive wait tests |
| `atlas/tests/test_multi_prompt.py` | **New** | Multi-prompt stacking tests |
| `atlas/tests/test_structured_output.py` | **New** | Structured output priority tests |

---

## Chunk 1: Session Manager and Meta Routing Fix

### Task 1: MCPSessionManager — Tests

**Files:**
- Create: `atlas/tests/test_session_manager.py`

- [ ] **Step 1: Write failing tests for session manager**

```python
"""Tests for MCPSessionManager."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from atlas.modules.mcp_tools.session_manager import (
    MCPSessionManager,
    ManagedSession,
)


@pytest.fixture
def session_manager():
    return MCPSessionManager()


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.call_tool = AsyncMock(return_value=MagicMock())
    client.is_connected = MagicMock(return_value=True)
    return client


class TestMCPSessionManager:
    @pytest.mark.asyncio
    async def test_acquire_creates_new_session(self, session_manager, mock_client):
        session = await session_manager.acquire("conv-1", "server-a", mock_client)
        assert session is not None
        assert isinstance(session, ManagedSession)
        mock_client.__aenter__.assert_called_once()

    @pytest.mark.asyncio
    async def test_acquire_reuses_existing_session(self, session_manager, mock_client):
        session1 = await session_manager.acquire("conv-1", "server-a", mock_client)
        session2 = await session_manager.acquire("conv-1", "server-a", mock_client)
        assert session1 is session2
        # __aenter__ only called once (reuse)
        mock_client.__aenter__.assert_called_once()

    @pytest.mark.asyncio
    async def test_acquire_different_conversations_get_different_sessions(
        self, session_manager
    ):
        client1 = AsyncMock()
        client1.__aenter__ = AsyncMock(return_value=client1)
        client1.__aexit__ = AsyncMock(return_value=False)

        client2 = AsyncMock()
        client2.__aenter__ = AsyncMock(return_value=client2)
        client2.__aexit__ = AsyncMock(return_value=False)

        s1 = await session_manager.acquire("conv-1", "server-a", client1)
        s2 = await session_manager.acquire("conv-2", "server-a", client2)
        assert s1 is not s2

    @pytest.mark.asyncio
    async def test_release_closes_session(self, session_manager, mock_client):
        await session_manager.acquire("conv-1", "server-a", mock_client)
        await session_manager.release("conv-1", "server-a")
        mock_client.__aexit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_release_all_closes_all_sessions_for_conversation(
        self, session_manager
    ):
        clients = []
        for name in ["server-a", "server-b", "server-c"]:
            c = AsyncMock()
            c.__aenter__ = AsyncMock(return_value=c)
            c.__aexit__ = AsyncMock(return_value=False)
            clients.append(c)
            await session_manager.acquire("conv-1", name, c)

        await session_manager.release_all("conv-1")
        for c in clients:
            c.__aexit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_release_all_does_not_affect_other_conversations(
        self, session_manager
    ):
        client_conv1 = AsyncMock()
        client_conv1.__aenter__ = AsyncMock(return_value=client_conv1)
        client_conv1.__aexit__ = AsyncMock(return_value=False)

        client_conv2 = AsyncMock()
        client_conv2.__aenter__ = AsyncMock(return_value=client_conv2)
        client_conv2.__aexit__ = AsyncMock(return_value=False)

        await session_manager.acquire("conv-1", "server-a", client_conv1)
        await session_manager.acquire("conv-2", "server-a", client_conv2)

        await session_manager.release_all("conv-1")
        client_conv1.__aexit__.assert_called_once()
        client_conv2.__aexit__.assert_not_called()

    @pytest.mark.asyncio
    async def test_release_nonexistent_is_noop(self, session_manager):
        # Should not raise
        await session_manager.release("nonexistent", "server-a")
        await session_manager.release_all("nonexistent")

    @pytest.mark.asyncio
    async def test_acquire_after_release_creates_new_session(
        self, session_manager, mock_client
    ):
        s1 = await session_manager.acquire("conv-1", "server-a", mock_client)
        await session_manager.release("conv-1", "server-a")

        # Reset mock for second acquire
        mock_client.__aenter__.reset_mock()
        s2 = await session_manager.acquire("conv-1", "server-a", mock_client)
        assert s2 is not s1
        mock_client.__aenter__.assert_called_once()


class TestManagedSession:
    @pytest.mark.asyncio
    async def test_client_property(self, mock_client):
        session = ManagedSession(mock_client)
        await session.open()
        assert session.client is mock_client

    @pytest.mark.asyncio
    async def test_close_calls_aexit(self, mock_client):
        session = ManagedSession(mock_client)
        await session.open()
        await session.close()
        mock_client.__aexit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_idempotent(self, mock_client):
        session = ManagedSession(mock_client)
        await session.open()
        await session.close()
        await session.close()
        # Only called once
        assert mock_client.__aexit__.call_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/garlan/git/atlas/atlas-ui-3 && PYTHONPATH=. python -m pytest atlas/tests/test_session_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atlas.modules.mcp_tools.session_manager'`

- [ ] **Step 3: Commit test file**

```bash
git add atlas/tests/test_session_manager.py
git commit -m "test: add session manager tests (red phase)"
```

---

### Task 2: MCPSessionManager — Implementation

**Files:**
- Create: `atlas/modules/mcp_tools/session_manager.py`
- Modify: `atlas/modules/mcp_tools/__init__.py`

- [ ] **Step 1: Write session_manager.py**

```python
"""MCP Session Manager — holds live sessions per (conversation_id, server_name).

Sessions are opened lazily on first tool call and reused across subsequent calls
within the same conversation. Cleanup happens on conversation end (WebSocket
disconnect) or when a conversation is restored/reset.
"""
import asyncio
import logging
from typing import Any, Dict, Optional, Protocol, Tuple

from fastmcp import Client

logger = logging.getLogger(__name__)


class SessionStore(Protocol):
    """Abstract storage interface for session metadata.

    In-memory dict is the default. Swap to Redis for durable sessions
    that survive restarts (future phase).
    """

    def get(self, key: Tuple[str, str]) -> Optional[Any]: ...
    def set(self, key: Tuple[str, str], value: Any) -> None: ...
    def delete(self, key: Tuple[str, str]) -> None: ...
    def keys_by_prefix(self, prefix: str) -> list: ...


class ManagedSession:
    """Wraps an open FastMCP client context for reuse across tool calls."""

    def __init__(self, client: Client):
        self._client = client
        self._opened = False
        self._closed = False

    @property
    def client(self) -> Client:
        return self._client

    @property
    def is_open(self) -> bool:
        return self._opened and not self._closed

    async def open(self) -> None:
        if not self._opened:
            await self._client.__aenter__()
            self._opened = True

    async def close(self) -> None:
        if self._opened and not self._closed:
            self._closed = True
            try:
                await self._client.__aexit__(None, None, None)
            except Exception as e:
                logger.debug("Error closing MCP session: %s", e)


class MCPSessionManager:
    """Manages live MCP sessions keyed by (conversation_id, server_name).

    Sessions are created lazily and reused. Call release_all() on
    conversation end to clean up.
    """

    def __init__(self) -> None:
        self._sessions: Dict[Tuple[str, str], ManagedSession] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        conversation_id: str,
        server_name: str,
        client: Client,
    ) -> ManagedSession:
        """Get or create a session for (conversation_id, server_name).

        If a session already exists and is open, returns it.
        Otherwise opens a new one.
        """
        key = (conversation_id, server_name)
        async with self._lock:
            existing = self._sessions.get(key)
            if existing is not None and existing.is_open:
                return existing

            session = ManagedSession(client)
            await session.open()
            self._sessions[key] = session
            logger.debug(
                "Opened MCP session for conversation=%s server=%s",
                conversation_id,
                server_name,
            )
            return session

    async def release(self, conversation_id: str, server_name: str) -> None:
        """Close and remove a specific session."""
        key = (conversation_id, server_name)
        async with self._lock:
            session = self._sessions.pop(key, None)
        if session is not None:
            await session.close()
            logger.debug(
                "Released MCP session for conversation=%s server=%s",
                conversation_id,
                server_name,
            )

    async def release_all(self, conversation_id: str) -> None:
        """Close all sessions for a conversation."""
        to_close: list[ManagedSession] = []
        async with self._lock:
            keys_to_remove = [
                k for k in self._sessions if k[0] == conversation_id
            ]
            for k in keys_to_remove:
                session = self._sessions.pop(k)
                to_close.append(session)

        for session in to_close:
            await session.close()

        if to_close:
            logger.info(
                "Released %d MCP session(s) for conversation=%s",
                len(to_close),
                conversation_id,
            )
```

- [ ] **Step 2: Update `__init__.py` to export**

Add to `atlas/modules/mcp_tools/__init__.py`:
```python
from .session_manager import MCPSessionManager
```

And add `"MCPSessionManager"` to the `__all__` list.

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd /home/garlan/git/atlas/atlas-ui-3 && PYTHONPATH=. python -m pytest atlas/tests/test_session_manager.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add atlas/modules/mcp_tools/session_manager.py atlas/modules/mcp_tools/__init__.py
git commit -m "feat: add MCPSessionManager for per-conversation session persistence"
```

---

### Task 3: Meta Routing Fix — Tests

**Files:**
- Modify: `atlas/tests/test_elicitation_routing.py`

- [ ] **Step 1: Add concurrent same-server routing test**

Add to `TestElicitationRouting` class in `atlas/tests/test_elicitation_routing.py`:

```python
@pytest.mark.asyncio
async def test_concurrent_same_server_routing(self, manager):
    """Two concurrent tool calls to the same server route correctly."""
    tool_call_1 = ToolCall(id="call_1", name="tool_a", arguments={})
    tool_call_2 = ToolCall(id="call_2", name="tool_b", arguments={})
    cb1 = AsyncMock()
    cb2 = AsyncMock()

    async with manager._use_elicitation_context("server-x", tool_call_1, cb1):
        async with manager._use_elicitation_context("server-x", tool_call_2, cb2):
            # Both entries exist with distinct keys
            assert ("server-x", "call_1") in _ELICITATION_ROUTING
            assert ("server-x", "call_2") in _ELICITATION_ROUTING
            # They point to different callbacks
            assert _ELICITATION_ROUTING[("server-x", "call_1")].update_cb is cb1
            assert _ELICITATION_ROUTING[("server-x", "call_2")].update_cb is cb2
```

- [ ] **Step 2: Add handler test that extracts tool_call_id from meta**

Add to `TestElicitationHandler` class:

```python
@pytest.mark.asyncio
async def test_handler_routes_via_meta_tool_call_id(self, manager):
    """Handler uses _context.meta.model_extra to find correct routing."""
    from unittest.mock import MagicMock

    tool_call_1 = ToolCall(id="call_1", name="tool_a", arguments={})
    tool_call_2 = ToolCall(id="call_2", name="tool_b", arguments={})
    cb1 = AsyncMock()
    cb2 = AsyncMock()

    handler = manager._create_elicitation_handler("server-x")

    async with manager._use_elicitation_context("server-x", tool_call_1, cb1):
        async with manager._use_elicitation_context("server-x", tool_call_2, cb2):
            # Create a mock RequestContext with meta containing tool_call_id
            mock_context = MagicMock()
            mock_meta = MagicMock()
            mock_meta.model_extra = {"tool_call_id": "call_2"}
            mock_context.meta = mock_meta

            # Handler should route to cb2, not cb1
            # (Before fix: would route to cb1 as first match)
            result = await handler("Pick a color", str, None, mock_context)
            # Verify it routed to the right callback
            assert _ELICITATION_ROUTING[("server-x", "call_2")].update_cb is cb2

@pytest.mark.asyncio
async def test_handler_fallback_single_match_without_meta(self, manager):
    """When meta is unavailable but only one routing entry exists, use it."""
    tool_call = ToolCall(id="call_1", name="tool_a", arguments={})
    cb = AsyncMock()

    handler = manager._create_elicitation_handler("server-x")

    async with manager._use_elicitation_context("server-x", tool_call, cb):
        mock_context = MagicMock()
        mock_context.meta = None  # No meta available

        # Should still find the single match
        # (handler will go to fallback path)
        result = await handler("Pick a color", str, None, mock_context)
        # Should not cancel — single match is unambiguous
        assert _ELICITATION_ROUTING[("server-x", "call_1")].update_cb is cb

@pytest.mark.asyncio
async def test_handler_cancels_on_ambiguous_routing_without_meta(self, manager):
    """When meta unavailable and multiple entries exist, cancel."""
    from fastmcp.client.elicitation import ElicitResult

    tool_call_1 = ToolCall(id="call_1", name="tool_a", arguments={})
    tool_call_2 = ToolCall(id="call_2", name="tool_b", arguments={})
    cb1 = AsyncMock()
    cb2 = AsyncMock()

    handler = manager._create_elicitation_handler("server-x")

    async with manager._use_elicitation_context("server-x", tool_call_1, cb1):
        async with manager._use_elicitation_context("server-x", tool_call_2, cb2):
            mock_context = MagicMock()
            mock_context.meta = None

            result = await handler("Pick a color", str, None, mock_context)
            assert result.action == "cancel"
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `cd /home/garlan/git/atlas/atlas-ui-3 && PYTHONPATH=. python -m pytest atlas/tests/test_elicitation_routing.py -v -k "concurrent or meta or ambiguous"`
Expected: FAIL — handler still uses old loop-based lookup

- [ ] **Step 3: Commit test additions**

```bash
git add atlas/tests/test_elicitation_routing.py
git commit -m "test: add concurrent same-server routing tests (red phase, #295)"
```

---

### Task 4: Meta Routing Fix — Implementation

**Files:**
- Modify: `atlas/modules/mcp_tools/client.py` (lines 276-286 and 399-408)

**Note:** The existing `_use_elicitation_context` context manager (line 263) already constructs the routing key as `(server_name, tool_call.id)` — no change needed there. The fix is only in the handler lookup.

- [ ] **Step 1: Fix elicitation handler (lines 281-286)**

Replace the broken loop in `_create_elicitation_handler` (the `handler` inner function, lines 281-286):

```python
# OLD (broken):
# routing = None
# for (srv, _tcid), ctx in _ELICITATION_ROUTING.items():
#     if srv == server_name:
#         routing = ctx
#         break

# NEW (correct):
# Extract tool_call_id from RequestContext meta (Pydantic extra='allow')
tcid = None
if _context and hasattr(_context, 'meta') and _context.meta is not None:
    tcid = getattr(_context.meta, 'model_extra', {}).get("tool_call_id")

# Direct O(1) lookup
routing = _ELICITATION_ROUTING.get((server_name, tcid))

# Fallback: when meta unavailable, use single-match heuristic
if routing is None:
    matches = [v for (srv, _), v in _ELICITATION_ROUTING.items() if srv == server_name]
    if len(matches) == 1:
        routing = matches[0]
    elif len(matches) > 1:
        logger.warning(
            "Ambiguous elicitation routing for server '%s' with %d entries — cancelling",
            server_name, len(matches),
        )
        return ElicitResult(action="cancel", content=None)
```

- [ ] **Step 2: Fix sampling handler (lines 403-408)**

Apply the same pattern in `_create_sampling_handler` (the `handler` inner function, lines 403-408):

```python
# OLD (broken):
# routing = None
# for (srv, _tcid), ctx in _SAMPLING_ROUTING.items():
#     if srv == server_name:
#         routing = ctx
#         break

# NEW (correct):
tcid = None
if context and hasattr(context, 'meta') and context.meta is not None:
    tcid = getattr(context.meta, 'model_extra', {}).get("tool_call_id")

routing = _SAMPLING_ROUTING.get((server_name, tcid))

if routing is None:
    matches = [v for (srv, _), v in _SAMPLING_ROUTING.items() if srv == server_name]
    if len(matches) == 1:
        routing = matches[0]
    elif len(matches) > 1:
        logger.warning(
            "Ambiguous sampling routing for server '%s' with %d entries — cancelling",
            server_name, len(matches),
        )
        raise Exception(f"Ambiguous sampling routing for server '{server_name}'")
```

- [ ] **Step 3: Pass meta in call_tool (line 1585)**

In `call_tool()`, add `meta` parameter and pass it through. Change the method signature (line 1512):

```python
async def call_tool(
    self,
    server_name: str,
    tool_name: str,
    arguments: Dict[str, Any],
    *,
    progress_handler: Optional[Any] = None,
    elicitation_handler: Optional[Any] = None,
    user_email: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,  # NEW
) -> Any:
```

And the call site (line 1585):

```python
kwargs = {}
if progress_handler is not None:
    kwargs["progress_handler"] = progress_handler
if meta is not None:
    kwargs["meta"] = meta

result = await asyncio.wait_for(
    client.call_tool(tool_name, arguments, **kwargs),
    timeout=call_timeout,
)
```

- [ ] **Step 4: Pass tool_call_id in execute_tool (around line 1960)**

In `execute_tool()`, where `call_tool` is invoked, add `meta`:

```python
raw_result = await self.call_tool(
    server_name,
    actual_tool_name,
    tool_call.arguments,
    progress_handler=_progress_handler,
    user_email=user_email,
    meta={"tool_call_id": tool_call.id},  # NEW
)
```

Apply to both call sites (with and without update_cb, around lines 1960 and 1970).

- [ ] **Step 5: Run routing tests**

Run: `cd /home/garlan/git/atlas/atlas-ui-3 && PYTHONPATH=. python -m pytest atlas/tests/test_elicitation_routing.py -v`
Expected: All PASS

- [ ] **Step 6: Run full test suite to check for regressions**

Run: `cd /home/garlan/git/atlas/atlas-ui-3 && PYTHONPATH=. python -m pytest atlas/tests/ -x -q --timeout=60`
Expected: No regressions

- [ ] **Step 7: Commit**

```bash
git add atlas/modules/mcp_tools/client.py
git commit -m "fix: route elicitation/sampling via meta tool_call_id (#295)

Pass tool_call_id through FastMCP meta parameter and extract it from
RequestContext.meta.model_extra in handlers. Falls back to single-match
heuristic when meta is unavailable (old servers)."
```

---

### Task 5: Integrate Session Manager into Client

**Files:**
- Modify: `atlas/modules/mcp_tools/client.py`
- Modify: `atlas/application/chat/utilities/tool_executor.py`
- Modify: `atlas/main.py`

- [ ] **Step 1: Add session_manager to MCPToolManager.__init__**

In `client.py`, in `__init__` (after line ~153, after `self._user_clients_lock`):

```python
# Session manager for per-conversation session persistence
from atlas.modules.mcp_tools.session_manager import MCPSessionManager
self._session_manager = MCPSessionManager()
```

- [ ] **Step 2: Add conversation_id to call_tool signature**

Add `conversation_id: Optional[str] = None` parameter to `call_tool()`:

```python
async def call_tool(
    self,
    server_name: str,
    tool_name: str,
    arguments: Dict[str, Any],
    *,
    progress_handler: Optional[Any] = None,
    elicitation_handler: Optional[Any] = None,
    user_email: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,  # NEW
) -> Any:
```

- [ ] **Step 3: Replace `async with client:` with session manager**

Replace the `async with client:` block in `call_tool()` (lines 1578-1589):

```python
# OLD:
# async with client:
#     kwargs = {}
#     ...
#     result = await asyncio.wait_for(...)

# NEW:
kwargs = {}
if progress_handler is not None:
    kwargs["progress_handler"] = progress_handler
if meta is not None:
    kwargs["meta"] = meta

if conversation_id:
    # Use persistent session
    session = await self._session_manager.acquire(
        conversation_id, server_name, client
    )
    result = await asyncio.wait_for(
        session.client.call_tool(tool_name, arguments, **kwargs),
        timeout=call_timeout,
    )
else:
    # Fallback: no conversation context, use per-call session
    async with client:
        result = await asyncio.wait_for(
            client.call_tool(tool_name, arguments, **kwargs),
            timeout=call_timeout,
        )
```

- [ ] **Step 4: Thread conversation_id through execute_tool**

In `execute_tool()` (around line 1920), extract `conversation_id` from context:

```python
update_cb = None
user_email = None
conversation_id = None
if isinstance(context, dict):
    update_cb = context.get("update_callback")
    user_email = context.get("user_email")
    conversation_id = context.get("conversation_id")  # NEW
```

Then pass it in both `call_tool` invocations (~lines 1960 and 1970):

```python
raw_result = await self.call_tool(
    server_name,
    actual_tool_name,
    tool_call.arguments,
    progress_handler=_progress_handler,
    user_email=user_email,
    meta={"tool_call_id": tool_call.id},
    conversation_id=conversation_id,  # NEW
)
```

- [ ] **Step 5: Pass conversation_id in tool_executor.py**

In `atlas/application/chat/utilities/tool_executor.py`, at line 461 where `execute_tool` is called, add `conversation_id` to the context dict:

```python
result = await tool_manager.execute_tool(
    tool_call_obj,
    context={
        "session_id": session_context.get("session_id"),
        "user_email": session_context.get("user_email"),
        "conversation_id": session_context.get("conversation_id"),  # NEW
        "update_callback": update_callback,
    }
)
```

- [ ] **Step 6: Add release_all on WebSocket disconnect in main.py**

In `atlas/main.py`, in the `except WebSocketDisconnect:` handler (around line 625), add session cleanup:

```python
except WebSocketDisconnect:
    # Release MCP sessions for this conversation
    session = chat_service.sessions.get(session_id)
    if session:
        conv_id = session.context.get("conversation_id", str(session_id))
        tool_manager = getattr(chat_service, 'tool_manager', None)
        if tool_manager and hasattr(tool_manager, '_session_manager'):
            await tool_manager._session_manager.release_all(conv_id)
    chat_service.end_session(session_id)
    logger.info(f"WebSocket connection closed for session {session_id}")
```

- [ ] **Step 7: Add release on restore_conversation**

In `atlas/main.py`, in the `restore_conversation` message handler, release old sessions before restoring:

```python
elif message_type == "restore_conversation":
    # Release MCP sessions for the current conversation before restoring
    session = chat_service.sessions.get(session_id)
    if session:
        old_conv_id = session.context.get("conversation_id")
        if old_conv_id:
            tool_manager = getattr(chat_service, 'tool_manager', None)
            if tool_manager and hasattr(tool_manager, '_session_manager'):
                await tool_manager._session_manager.release_all(old_conv_id)

    response = await chat_service.handle_restore_conversation(
        session_id=session_id,
        conversation_id=data.get("conversation_id", ""),
        messages=data.get("messages", []),
        user_email=user_email
    )
    await websocket.send_json(response)
```

- [ ] **Step 8: Ensure conversation_id is in session_context in chat service**

Check that `session.context["conversation_id"]` is set before tool execution. It should already be set by `handle_chat_message` (line 258 of service.py). If the chat service builds `session_context` before passing to tool execution, ensure `conversation_id` is included. Search for where `session_context` is constructed and verify it includes `conversation_id`.

- [ ] **Step 9: Run tests**

Run: `cd /home/garlan/git/atlas/atlas-ui-3 && PYTHONPATH=. python -m pytest atlas/tests/test_session_manager.py atlas/tests/test_elicitation_routing.py -v`
Expected: All PASS

- [ ] **Step 10: Commit**

```bash
git add atlas/modules/mcp_tools/client.py atlas/application/chat/utilities/tool_executor.py atlas/main.py
git commit -m "feat: integrate MCPSessionManager for per-conversation session persistence

Sessions are held open across tool calls within a conversation.
Cleanup on WebSocket disconnect and conversation restore."
```

---

## Chunk 2: Structured Output, Session State, and Environment Config

### Task 6: Structured Output Priority — Tests

**Files:**
- Create: `atlas/tests/test_structured_output.py`

- [ ] **Step 1: Write tests for new parsing priority**

```python
"""Tests for structured output parsing priority."""
import json
import pytest
from unittest.mock import MagicMock

from atlas.modules.mcp_tools.client import MCPToolManager


@pytest.fixture
def manager():
    """Create a MCPToolManager for testing normalization."""
    return MCPToolManager(config_path="/tmp/nonexistent_mcp_test.json")


class TestStructuredOutputPriority:
    def test_data_preferred_over_structured_content(self, manager):
        """raw_result.data (validated) takes priority over structured_content (raw)."""
        raw = MagicMock()
        raw.data = {"results": "from-data", "meta_data": {"source": "validated"}}
        raw.structured_content = {"results": "from-structured", "meta_data": {"source": "raw"}}
        raw.content = []

        result = manager._normalize_mcp_tool_result(raw)
        assert result["results"] == "from-data"
        assert result["meta_data"]["source"] == "validated"

    def test_structured_content_when_no_data(self, manager):
        """Falls back to structured_content when data is None."""
        raw = MagicMock()
        raw.data = None
        raw.structured_content = {"results": "from-structured"}
        raw.content = []

        result = manager._normalize_mcp_tool_result(raw)
        assert result["results"] == "from-structured"

    def test_text_fallback_when_no_structured(self, manager):
        """Falls back to content[0].text JSON when neither data nor structured_content."""
        raw = MagicMock()
        raw.data = None
        raw.structured_content = None

        text_item = MagicMock()
        text_item.type = "text"
        text_item.text = json.dumps({"results": "from-text"})
        raw.content = [text_item]

        result = manager._normalize_mcp_tool_result(raw)
        assert result["results"] == "from-text"

    def test_legacy_keys_still_work(self, manager):
        """Legacy keys (result, meta-data) are still recognized."""
        raw = MagicMock()
        raw.data = None
        raw.structured_content = {"result": "legacy-val", "meta-data": {"k": "v"}}
        raw.content = []

        result = manager._normalize_mcp_tool_result(raw)
        assert result["results"] == "legacy-val"
        assert result["meta_data"]["k"] == "v"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/garlan/git/atlas/atlas-ui-3 && PYTHONPATH=. python -m pytest atlas/tests/test_structured_output.py -v`
Expected: `test_data_preferred_over_structured_content` FAILS (current code checks `structured_content` first)

- [ ] **Step 3: Commit**

```bash
git add atlas/tests/test_structured_output.py
git commit -m "test: add structured output priority tests (red phase)"
```

---

### Task 7: Structured Output Priority — Implementation

**Files:**
- Modify: `atlas/modules/mcp_tools/client.py` (lines 1768-1775)

- [ ] **Step 1: Reverse priority in `_normalize_mcp_tool_result`**

Change lines 1768-1775 from:

```python
if hasattr(raw_result, "structured_content") and raw_result.structured_content:
    structured = raw_result.structured_content
elif hasattr(raw_result, "data") and raw_result.data:
    structured = raw_result.data
```

To:

```python
if hasattr(raw_result, "data") and raw_result.data:
    # FastMCP 3.x validated/deserialized structured content (highest fidelity)
    structured = raw_result.data if isinstance(raw_result.data, dict) else {"results": raw_result.data}
elif hasattr(raw_result, "structured_content") and raw_result.structured_content:
    structured = raw_result.structured_content
```

- [ ] **Step 2: Fix the second priority instance in execute_tool V2 extraction (lines 1990-1997)**

There is a second `structured_content`-before-`data` check in `execute_tool()` (the V2 component extraction block). Apply the same reversal:

```python
# OLD (lines 1990-1997):
# if hasattr(raw_result, "structured_content") and raw_result.structured_content:
#     sc = raw_result.structured_content
#     if isinstance(sc, dict):
#         structured = sc
# elif hasattr(raw_result, "data") and raw_result.data:
#     dt = raw_result.data
#     if isinstance(dt, dict):
#         structured = dt

# NEW:
if hasattr(raw_result, "data") and raw_result.data:
    dt = raw_result.data
    if isinstance(dt, dict):
        structured = dt
elif hasattr(raw_result, "structured_content") and raw_result.structured_content:
    sc = raw_result.structured_content
    if isinstance(sc, dict):
        structured = sc
```

- [ ] **Step 3: Run structured output tests**

Run: `cd /home/garlan/git/atlas/atlas-ui-3 && PYTHONPATH=. python -m pytest atlas/tests/test_structured_output.py -v`
Expected: All PASS

- [ ] **Step 4: Run full test suite**

Run: `cd /home/garlan/git/atlas/atlas-ui-3 && PYTHONPATH=. python -m pytest atlas/tests/ -x -q --timeout=60`
Expected: No regressions

- [ ] **Step 5: Commit**

```bash
git add atlas/modules/mcp_tools/client.py
git commit -m "feat: prefer raw_result.data over structured_content in normalization

FastMCP 3.x data is validated against output schema — higher fidelity
than raw structured_content. Legacy fallback preserved."
```

---

### Task 8: Session State Utility

**Files:**
- Create: `atlas/mcp/common/__init__.py`
- Create: `atlas/mcp/common/state.py`
- Modify: `.env.example`

- [ ] **Step 1: Create common package**

```python
# atlas/mcp/common/__init__.py
"""Common utilities for Atlas MCP servers."""
```

- [ ] **Step 2: Create state.py**

```python
"""Pluggable session state store for MCP servers.

Usage in an MCP server:
    from atlas.mcp.common.state import get_state_store
    mcp = FastMCP("my-server", session_state_store=get_state_store())

Environment variables:
    MCP_STATE_BACKEND: "memory" (default) or "redis"
    MCP_REDIS_URL: Redis connection URL (only when backend=redis)
"""
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


def get_state_store() -> Optional[Any]:
    """Return a session state store based on MCP_STATE_BACKEND env var.

    Returns None for in-memory (FastMCP default), or a RedisStore for
    distributed deployments.
    """
    backend = os.getenv("MCP_STATE_BACKEND", "memory")

    if backend == "redis":
        redis_url = os.getenv("MCP_REDIS_URL", "redis://localhost:6379/0")
        try:
            from key_value.aio.stores.redis import RedisStore
            logger.info("Using Redis session state store: %s", redis_url)
            return RedisStore(url=redis_url)
        except ImportError:
            logger.error(
                "MCP_STATE_BACKEND=redis but pykeyvalue[redis] not installed. "
                "Falling back to in-memory state."
            )
            return None
        except Exception as e:
            logger.error("Failed to connect to Redis (%s): %s. Falling back to in-memory.", redis_url, e)
            return None

    # "memory" or unrecognized → FastMCP default in-memory store
    return None
```

- [ ] **Step 3: Add env vars to .env.example**

Append to `.env.example` (at the end, after the existing MCP section):

```env

# ── MCP Session & State (FastMCP 3.x) ──────────────────────────────────
# Seconds to wait before switching long-running tool calls to polling mode
MCP_TASK_TIMEOUT=10

# Session state backend for MCP servers: "memory" (default) or "redis"
MCP_STATE_BACKEND=memory

# Redis URL for distributed session state (only when MCP_STATE_BACKEND=redis)
# MCP_REDIS_URL=redis://localhost:6379/0
```

- [ ] **Step 4: Commit**

```bash
git add atlas/mcp/common/__init__.py atlas/mcp/common/state.py .env.example
git commit -m "feat: add pluggable session state store and MCP env config

get_state_store() reads MCP_STATE_BACKEND to select memory or Redis.
New env vars: MCP_TASK_TIMEOUT, MCP_STATE_BACKEND, MCP_REDIS_URL."
```

---

## Chunk 3: Background Tasks with Adaptive Polling

### Task 9: Adaptive Task Polling — Tests

**Files:**
- Create: `atlas/tests/test_adaptive_task_polling.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for adaptive background task polling in MCPToolManager.call_tool."""
import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from atlas.modules.mcp_tools.client import MCPToolManager
from atlas.modules.mcp_tools.session_manager import ManagedSession


@pytest.fixture
def manager():
    """Create a MCPToolManager with mocked internals for testing call_tool."""
    tm = MCPToolManager(config_path="/tmp/nonexistent_mcp_test.json")
    tm._server_task_support = {}
    return tm


def _make_mock_client(*, task_support=True, immediate=True, wait_timeout=False):
    """Create a mock client with configurable ToolTask behavior."""
    mock_client = AsyncMock()

    mock_task = MagicMock()
    mock_task.returned_immediately = immediate
    mock_result = MagicMock()
    mock_result.content = [MagicMock(type="text", text="done")]
    mock_result.structured_content = None
    mock_result.data = None
    mock_task.result = mock_result
    mock_task.cancel = AsyncMock()
    mock_task.on_status_change = MagicMock()

    if wait_timeout:
        mock_task.wait = AsyncMock(side_effect=asyncio.TimeoutError)
    else:
        mock_wait_result = MagicMock()
        mock_wait_result.state = "completed"
        mock_task.wait = AsyncMock(return_value=mock_wait_result)

    mock_client.call_tool = AsyncMock(return_value=mock_task)

    # Simulate task support via initialize_result
    if task_support:
        caps = MagicMock()
        caps.tasks = MagicMock()
        init_result = MagicMock()
        init_result.capabilities = caps
        mock_client.initialize_result = init_result
    else:
        mock_client.initialize_result = None

    return mock_client, mock_task


class TestAdaptiveTaskPolling:
    @pytest.mark.asyncio
    async def test_immediate_result_returns_without_ui_notification(self, manager):
        """When task.returned_immediately is True, no UI events sent."""
        mock_client, mock_task = _make_mock_client(immediate=True)
        update_cb = AsyncMock()

        # Set up session manager to return our mock
        mock_session = MagicMock(spec=ManagedSession)
        mock_session.client = mock_client
        manager._session_manager.acquire = AsyncMock(return_value=mock_session)

        # Register the server
        manager.clients["test-server"] = mock_client
        manager.servers_config["test-server"] = {}

        result = await manager.call_tool(
            "test-server", "tool_a", {},
            conversation_id="conv-1",
            meta={"tool_call_id": "tc-1"},
            update_cb=update_cb,
        )

        # update_cb should NOT have been called with tool_task_started
        for call in update_cb.call_args_list:
            assert call[0][0].get("type") != "tool_task_started"

    @pytest.mark.asyncio
    async def test_no_task_support_falls_back_to_blocking(self, manager):
        """Servers without task support use blocking call_tool."""
        mock_client, _ = _make_mock_client(task_support=False)

        mock_session = MagicMock(spec=ManagedSession)
        mock_session.client = mock_client
        manager._session_manager.acquire = AsyncMock(return_value=mock_session)

        manager.clients["test-server"] = mock_client
        manager.servers_config["test-server"] = {}

        result = await manager.call_tool(
            "test-server", "tool_a", {},
            conversation_id="conv-1",
        )

        # call_tool called WITHOUT task=True
        call_kwargs = mock_client.call_tool.call_args
        assert call_kwargs.kwargs.get("task") is not True

    @pytest.mark.asyncio
    async def test_cancellation_calls_task_cancel(self, manager):
        """When asyncio cancels the call, ToolTask.cancel() is invoked."""
        mock_client, mock_task = _make_mock_client(immediate=False)
        # Make wait() hang so we can cancel
        mock_task.wait = AsyncMock(side_effect=asyncio.CancelledError)

        mock_session = MagicMock(spec=ManagedSession)
        mock_session.client = mock_client
        manager._session_manager.acquire = AsyncMock(return_value=mock_session)

        manager.clients["test-server"] = mock_client
        manager.servers_config["test-server"] = {}

        with pytest.raises(asyncio.CancelledError):
            await manager.call_tool(
                "test-server", "tool_a", {},
                conversation_id="conv-1",
                meta={"tool_call_id": "tc-1"},
            )

        mock_task.cancel.assert_called_once()
```

- [ ] **Step 2: Run tests**

Run: `cd /home/garlan/git/atlas/atlas-ui-3 && PYTHONPATH=. python -m pytest atlas/tests/test_adaptive_task_polling.py -v`
Expected: PASS (these test the external ToolTask interface via mocks)

- [ ] **Step 3: Commit**

```bash
git add atlas/tests/test_adaptive_task_polling.py
git commit -m "test: add adaptive task polling tests"
```

---

### Task 10: Adaptive Task Polling — Implementation

**Files:**
- Modify: `atlas/modules/mcp_tools/client.py`

- [ ] **Step 1: Add `update_cb` parameter to `call_tool` and task support cache**

In `MCPToolManager.__init__`, after the session manager line:

```python
# Cache of which servers support background tasks
self._server_task_support: Dict[str, bool] = {}
```

In `call_tool()` signature, add `update_cb`:

```python
async def call_tool(
    self,
    server_name: str,
    tool_name: str,
    arguments: Dict[str, Any],
    *,
    progress_handler: Optional[Any] = None,
    elicitation_handler: Optional[Any] = None,
    user_email: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
    update_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,  # NEW
) -> Any:
```

In `execute_tool()`, pass `update_cb` to `call_tool` (both call sites around lines 1960/1970):

```python
raw_result = await self.call_tool(
    server_name,
    actual_tool_name,
    tool_call.arguments,
    progress_handler=_progress_handler,
    user_email=user_email,
    meta={"tool_call_id": tool_call.id},
    conversation_id=conversation_id,
    update_cb=update_cb,  # NEW
)
```

- [ ] **Step 2: Add task support detection method**

Add method to `MCPToolManager`:

```python
def _supports_tasks(self, server_name: str, client: Client) -> bool:
    """Check if a server supports background tasks (cached)."""
    if server_name in self._server_task_support:
        return self._server_task_support[server_name]

    supports = False
    try:
        init_result = getattr(client, 'initialize_result', None)
        if init_result and hasattr(init_result, 'capabilities'):
            caps = init_result.capabilities
            # Check for tasks capability
            supports = getattr(caps, 'tasks', None) is not None
    except Exception:
        pass

    self._server_task_support[server_name] = supports
    return supports
```

- [ ] **Step 3: Implement adaptive call in call_tool**

Replace the tool call execution block in `call_tool()` with adaptive logic. After acquiring the session/client, replace the `result = await asyncio.wait_for(...)` with:

```python
task_timeout = float(os.getenv("MCP_TASK_TIMEOUT", "10"))
use_tasks = conversation_id and self._supports_tasks(server_name, active_client)

if use_tasks:
    # Attempt task mode for servers that support it
    tool_task = await active_client.call_tool(tool_name, arguments, task=True, **kwargs)

    if tool_task.returned_immediately:
        result = tool_task.result
    else:
        # Wait up to task_timeout before notifying UI
        try:
            wait_result = await asyncio.wait_for(
                tool_task.wait(timeout=call_timeout),
                timeout=task_timeout,
            )
            result = tool_task.result
        except asyncio.TimeoutError:
            # Exceeded threshold — notify UI and keep waiting
            if update_cb:
                await update_cb({
                    "type": "tool_task_started",
                    "tool_call_id": meta.get("tool_call_id") if meta else None,
                    "tool_name": tool_name,
                    "server_name": server_name,
                })

            # Register progress callback
            if update_cb:
                async def _task_progress(status):
                    await update_cb({
                        "type": "tool_task_progress",
                        "tool_call_id": meta.get("tool_call_id") if meta else None,
                        "status": getattr(status, 'state', 'running'),
                        "progress": getattr(status, 'progress', None),
                        "total": getattr(status, 'total', None),
                        "message": getattr(status, 'message', None),
                    })
                tool_task.on_status_change(_task_progress)

            try:
                await tool_task.wait(timeout=call_timeout - task_timeout)
                result = tool_task.result
            except asyncio.CancelledError:
                await tool_task.cancel()
                raise
            finally:
                if update_cb:
                    await update_cb({
                        "type": "tool_task_completed",
                        "tool_call_id": meta.get("tool_call_id") if meta else None,
                    })
        except asyncio.CancelledError:
            await tool_task.cancel()
            raise
else:
    # Non-task mode: blocking call
    result = await asyncio.wait_for(
        active_client.call_tool(tool_name, arguments, **kwargs),
        timeout=call_timeout,
    )
```

Note: `active_client` is `session.client` when using session manager, or the raw `client` in the fallback `async with client:` path. `update_cb` needs to be threaded into `call_tool`. Add `update_cb: Optional[Callable] = None` to the signature and pass it from `execute_tool`.

- [ ] **Step 4: Run all tests**

Run: `cd /home/garlan/git/atlas/atlas-ui-3 && PYTHONPATH=. python -m pytest atlas/tests/ -x -q --timeout=60`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add atlas/modules/mcp_tools/client.py
git commit -m "feat: add adaptive background task polling for long-running tool calls

Uses ToolTask API for servers that support it. Waits MCP_TASK_TIMEOUT
seconds synchronously, then switches to polling with UI notifications.
Cancels server-side tasks on asyncio cancellation."
```

---

## Chunk 4: Prompt Improvements

### Task 11: Multi-Prompt and Meta — Tests

**Files:**
- Create: `atlas/tests/test_multi_prompt.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for multi-prompt support and meta passing."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from atlas.application.chat.preprocessors.prompt_override_service import (
    PromptOverrideService,
)


@pytest.fixture
def mock_tool_manager():
    tm = AsyncMock()
    return tm


@pytest.fixture
def service(mock_tool_manager):
    return PromptOverrideService(tool_manager=mock_tool_manager)


class TestMultiPromptSupport:
    @pytest.mark.asyncio
    async def test_single_prompt_still_works(self, service, mock_tool_manager):
        """Backward compat: single prompt applied as system message."""
        mock_tool_manager.get_prompt.return_value = "You are a wizard."
        messages = [{"role": "user", "content": "Hello"}]

        result = await service.apply_prompt_override(messages, ["server_wizard"])
        assert result[0] == {"role": "system", "content": "You are a wizard."}
        assert result[1] == {"role": "user", "content": "Hello"}

    @pytest.mark.asyncio
    async def test_multiple_prompts_all_applied(self, service, mock_tool_manager):
        """All selected prompts are applied, not just the first."""
        async def mock_get_prompt(server, name, **kwargs):
            return {"wizard": "You are a wizard.", "analyst": "You are an analyst."}[name]

        mock_tool_manager.get_prompt = mock_get_prompt
        messages = [{"role": "user", "content": "Hello"}]

        result = await service.apply_prompt_override(
            messages, ["server_wizard", "server_analyst"]
        )
        # Both prompts prepended
        assert len(result) == 3
        assert result[0]["content"] == "You are a wizard."
        assert result[1]["content"] == "You are an analyst."
        assert result[2]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_failed_prompt_skipped_others_applied(self, service, mock_tool_manager):
        """If one prompt fails, others still get applied."""
        call_count = 0

        async def mock_get_prompt(server, name, **kwargs):
            nonlocal call_count
            call_count += 1
            if name == "bad":
                raise Exception("Server down")
            return "Good prompt."

        mock_tool_manager.get_prompt = mock_get_prompt
        messages = [{"role": "user", "content": "Hello"}]

        result = await service.apply_prompt_override(
            messages, ["server_bad", "server_good"]
        )
        assert len(result) == 2  # one prompt + original message
        assert result[0]["content"] == "Good prompt."

    @pytest.mark.asyncio
    async def test_no_prompts_returns_unchanged(self, service):
        messages = [{"role": "user", "content": "Hello"}]
        result = await service.apply_prompt_override(messages, None)
        assert result == messages

        result = await service.apply_prompt_override(messages, [])
        assert result == messages


class TestPromptTextExtraction:
    def test_extract_string(self, service):
        assert service._extract_prompt_text("hello") == "hello"

    def test_extract_multi_content(self, service):
        """Concatenates all TextContent items, not just first."""
        from types import SimpleNamespace

        item1 = SimpleNamespace(text="Part 1.")
        item2 = SimpleNamespace(text=" Part 2.")

        prompt_obj = SimpleNamespace(content=[item1, item2])

        result = service._extract_prompt_text(prompt_obj)
        assert "Part 1." in result
        assert "Part 2." in result

    def test_extract_single_content(self, service):
        """Single content item still works."""
        from types import SimpleNamespace

        item = SimpleNamespace(text="Only part.")
        prompt_obj = SimpleNamespace(content=[item])

        result = service._extract_prompt_text(prompt_obj)
        assert result == "Only part."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/garlan/git/atlas/atlas-ui-3 && PYTHONPATH=. python -m pytest atlas/tests/test_multi_prompt.py -v`
Expected: `test_multiple_prompts_all_applied` FAILS (current code breaks after first)

- [ ] **Step 3: Commit**

```bash
git add atlas/tests/test_multi_prompt.py
git commit -m "test: add multi-prompt and meta prompt tests (red phase)"
```

---

### Task 12: Multi-Prompt and Meta — Implementation

**Files:**
- Modify: `atlas/application/chat/preprocessors/prompt_override_service.py`

- [ ] **Step 1: Rewrite apply_prompt_override for multi-prompt**

Replace the entire `apply_prompt_override` method:

```python
async def apply_prompt_override(
    self,
    messages: List[Dict[str, Any]],
    selected_prompts: Optional[List[str]] = None,
    *,
    user_email: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Apply MCP prompt overrides for all selected prompts.

    All valid prompts are applied in selection order, each as a
    separate system message prepended to the conversation. First
    selected prompt is farthest from the user message.

    Args:
        messages: Current message history
        selected_prompts: List of prompt keys (format: "server_promptname")
        user_email: Optional user email for prompt personalization via meta
        conversation_id: Optional conversation ID for meta

    Returns:
        Messages with prompt overrides prepended (if applicable)
    """
    if not selected_prompts or not self.tool_manager:
        return messages

    system_messages: List[Dict[str, Any]] = []

    for key in selected_prompts:
        if not isinstance(key, str) or "_" not in key:
            continue

        server, prompt_name = key.split("_", 1)

        try:
            # Build meta for prompt personalization
            meta = {}
            if user_email:
                meta["user_email"] = user_email
            if conversation_id:
                meta["conversation_id"] = conversation_id

            prompt_obj = await self.tool_manager.get_prompt(
                server, prompt_name, meta=meta if meta else None
            )
            prompt_text = self._extract_prompt_text(prompt_obj)

            if prompt_text:
                system_messages.append({"role": "system", "content": prompt_text})
                logger.info(
                    "Applied MCP prompt '%s' (len=%d)", key, len(prompt_text)
                )

        except Exception:
            logger.debug("Failed retrieving MCP prompt %s", key, exc_info=True)

    if system_messages:
        messages = system_messages + messages

    return messages
```

- [ ] **Step 2: Update _extract_prompt_text for multi-content**

Replace the `_extract_prompt_text` method:

```python
def _extract_prompt_text(self, prompt_obj: Any) -> Optional[str]:
    """
    Extract text content from various MCP prompt object formats.

    Concatenates all text content items (not just the first).

    Args:
        prompt_obj: Prompt object from MCP (could be string or structured object)

    Returns:
        Extracted prompt text, or None if extraction failed
    """
    if isinstance(prompt_obj, str):
        return prompt_obj

    if hasattr(prompt_obj, "content"):
        content_field = getattr(prompt_obj, "content")

        if isinstance(content_field, list) and content_field:
            texts = []
            for item in content_field:
                if hasattr(item, "text") and isinstance(item.text, str):
                    texts.append(item.text)
            if texts:
                return "\n".join(texts)

    return str(prompt_obj)
```

- [ ] **Step 3: Update get_prompt in client.py to accept meta**

In `atlas/modules/mcp_tools/client.py`, update the `get_prompt` method (around line 1599):

```python
async def get_prompt(self, server_name: str, prompt_name: str, arguments: Dict[str, Any] = None, *, meta: Optional[Dict[str, Any]] = None) -> Any:
    """Get a specific prompt from an MCP server."""
    if server_name not in self.clients:
        raise ValueError(f"No prompt client available for server: {server_name}")
    client = self.clients[server_name]
    async with client:
        kwargs = {}
        if meta is not None:
            kwargs["meta"] = meta
        result = await client.get_prompt(prompt_name, arguments, **kwargs)
    return result
```

- [ ] **Step 4: Run tests**

Run: `cd /home/garlan/git/atlas/atlas-ui-3 && PYTHONPATH=. python -m pytest atlas/tests/test_multi_prompt.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /home/garlan/git/atlas/atlas-ui-3 && PYTHONPATH=. python -m pytest atlas/tests/ -x -q --timeout=60`
Expected: No regressions

- [ ] **Step 6: Commit**

```bash
git add atlas/application/chat/preprocessors/prompt_override_service.py atlas/modules/mcp_tools/client.py
git commit -m "feat: multi-prompt support with meta and multi-content extraction

All selected prompts are applied (not just first). Meta (user_email,
conversation_id) passed to prompt resolution for personalization.
_extract_prompt_text concatenates all text content items."
```

---

## Chunk 5: Final Integration and Cleanup

### Task 13: Verify All Tests Pass

- [ ] **Step 1: Run complete test suite**

Run: `cd /home/garlan/git/atlas/atlas-ui-3 && PYTHONPATH=. python -m pytest atlas/tests/ -v --timeout=120`
Expected: All PASS

- [ ] **Step 2: Check for import errors**

Run: `cd /home/garlan/git/atlas/atlas-ui-3 && python -c "from atlas.modules.mcp_tools import MCPToolManager, MCPSessionManager; from atlas.mcp.common.state import get_state_store; print('All imports OK')"`
Expected: `All imports OK`

### Task 14: Commit All and Verify

- [ ] **Step 1: Review all changes**

Run: `git diff --stat main`

Verify the file list matches the plan.

- [ ] **Step 2: Final commit if any uncommitted changes**

```bash
git status
# If any remaining changes:
git add -u
git commit -m "chore: final cleanup for FastMCP 3.x upgrade"
```

- [ ] **Step 3: Run tests one final time**

Run: `cd /home/garlan/git/atlas/atlas-ui-3 && PYTHONPATH=. python -m pytest atlas/tests/ -x -q --timeout=120`
Expected: All PASS
