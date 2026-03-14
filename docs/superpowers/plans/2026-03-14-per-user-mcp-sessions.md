# Per-User MCP Session Isolation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate shared MCP session state between users by making HTTP servers use per-user clients and blocking state operations on STDIO servers.

**Architecture:** HTTP MCP servers get per-user `Client` instances (keyed by `(user_email, server_name)`) so each user gets their own MCP session ID and isolated state. STDIO servers get a `BlockedStateStore` passed to `FastMCP()` that raises `RuntimeError` on any state operation. The `session_state_demo` server moves from STDIO to HTTP transport.

**Tech Stack:** FastMCP 3.1.0, `AsyncKeyValue` protocol from `key_value.aio`, Python asyncio

---

## Chunk 1: BlockedStateStore + STDIO Server Migration

### Task 1: Create BlockedStateStore

**Files:**
- Create: `atlas/mcp/__init__.py` (empty, makes `atlas.mcp` a proper package)
- Create: `atlas/mcp/shared/__init__.py`
- Create: `atlas/mcp/shared/blocked_state.py`
- Test: `atlas/tests/test_blocked_state_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# atlas/tests/test_blocked_state_store.py
"""Tests for BlockedStateStore — ensures STDIO servers cannot use session state."""

import pytest

from atlas.mcp.shared.blocked_state import BlockedStateStore

MSG_FRAGMENT = "not supported for STDIO"


@pytest.fixture
def store():
    return BlockedStateStore()


@pytest.mark.asyncio
async def test_get_raises(store):
    with pytest.raises(RuntimeError, match=MSG_FRAGMENT):
        await store.get("key")


@pytest.mark.asyncio
async def test_put_raises(store):
    with pytest.raises(RuntimeError, match=MSG_FRAGMENT):
        await store.put("key", {"value": 1})


@pytest.mark.asyncio
async def test_delete_raises(store):
    with pytest.raises(RuntimeError, match=MSG_FRAGMENT):
        await store.delete("key")


@pytest.mark.asyncio
async def test_ttl_raises(store):
    with pytest.raises(RuntimeError, match=MSG_FRAGMENT):
        await store.ttl("key")


@pytest.mark.asyncio
async def test_get_many_raises(store):
    with pytest.raises(RuntimeError, match=MSG_FRAGMENT):
        await store.get_many(["a", "b"])


@pytest.mark.asyncio
async def test_put_many_raises(store):
    with pytest.raises(RuntimeError, match=MSG_FRAGMENT):
        await store.put_many(["a"], [{"v": 1}])


@pytest.mark.asyncio
async def test_delete_many_raises(store):
    with pytest.raises(RuntimeError, match=MSG_FRAGMENT):
        await store.delete_many(["a"])


@pytest.mark.asyncio
async def test_ttl_many_raises(store):
    with pytest.raises(RuntimeError, match=MSG_FRAGMENT):
        await store.ttl_many(["a"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd atlas && PYTHONPATH=.. pytest tests/test_blocked_state_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atlas.mcp.shared.blocked_state'`

- [ ] **Step 3: Create the shared module and BlockedStateStore**

```python
# atlas/mcp/__init__.py
# (empty — makes atlas.mcp a proper Python package for imports)
```

```python
# atlas/mcp/shared/__init__.py
```

```python
# atlas/mcp/shared/blocked_state.py
"""BlockedStateStore — prevents STDIO MCP servers from using session state.

STDIO servers share a single process across all users. Any state stored via
ctx.get_state/ctx.set_state would be visible to every user, which is a
security issue. This store raises RuntimeError on all operations to enforce
that stateful MCP servers must use HTTP transport with per-user sessions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, SupportsFloat

_ERROR = (
    "Session state is not supported for STDIO MCP servers. "
    "Stateful servers must use HTTP transport for per-user session isolation. "
    "See docs or atlas/mcp/session_state_demo for an HTTP example."
)


class BlockedStateStore:
    """AsyncKeyValue-compatible store that raises on every operation."""

    async def get(self, key: str, *, collection: str | None = None) -> dict[str, Any] | None:
        raise RuntimeError(_ERROR)

    async def put(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        raise RuntimeError(_ERROR)

    async def delete(self, key: str, *, collection: str | None = None) -> bool:
        raise RuntimeError(_ERROR)

    async def ttl(
        self, key: str, *, collection: str | None = None
    ) -> tuple[dict[str, Any] | None, float | None]:
        raise RuntimeError(_ERROR)

    async def get_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[dict[str, Any] | None]:
        raise RuntimeError(_ERROR)

    async def put_many(
        self,
        keys: Sequence[str],
        values: Sequence[Mapping[str, Any]],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        raise RuntimeError(_ERROR)

    async def delete_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> int:
        raise RuntimeError(_ERROR)

    async def ttl_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[tuple[dict[str, Any] | None, float | None]]:
        raise RuntimeError(_ERROR)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd atlas && PYTHONPATH=.. pytest tests/test_blocked_state_store.py -v`
Expected: 8 PASSED

- [ ] **Step 5: Commit**

```bash
git add atlas/mcp/__init__.py atlas/mcp/shared/__init__.py atlas/mcp/shared/blocked_state.py atlas/tests/test_blocked_state_store.py
git commit -m "feat: add BlockedStateStore to prevent STDIO servers from using session state"
```

---

### Task 2: Create `create_stdio_server()` helper

**Files:**
- Create: `atlas/mcp/shared/server_factory.py`
- Test: `atlas/tests/test_stdio_server_factory.py`

- [ ] **Step 1: Write the failing test**

```python
# atlas/tests/test_stdio_server_factory.py
"""Tests for create_stdio_server helper."""

import pytest

from atlas.mcp.shared.server_factory import create_stdio_server


def test_creates_fastmcp_instance():
    mcp = create_stdio_server("TestServer")
    assert mcp.name == "TestServer"


def test_state_store_is_blocked():
    """The returned FastMCP instance must use BlockedStateStore."""
    mcp = create_stdio_server("TestServer")
    from atlas.mcp.shared.blocked_state import BlockedStateStore
    # FastMCP stores the raw storage in _state_storage
    assert isinstance(mcp._state_storage, BlockedStateStore)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd atlas && PYTHONPATH=.. pytest tests/test_stdio_server_factory.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement the helper**

```python
# atlas/mcp/shared/server_factory.py
"""Factory for creating MCP server instances with appropriate state stores."""

from __future__ import annotations

from fastmcp import FastMCP

from atlas.mcp.shared.blocked_state import BlockedStateStore


def create_stdio_server(name: str, **kwargs) -> FastMCP:
    """Create a FastMCP instance for STDIO transport with state blocked.

    STDIO servers share a single process across all users, so session state
    is not isolated. This factory injects a BlockedStateStore that raises
    RuntimeError if any tool attempts to use ctx.get_state/ctx.set_state.

    For stateful servers, use HTTP transport instead.

    Args:
        name: Server display name
        **kwargs: Additional arguments passed to FastMCP()

    Returns:
        FastMCP instance with BlockedStateStore
    """
    return FastMCP(name, session_state_store=BlockedStateStore(), **kwargs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd atlas && PYTHONPATH=.. pytest tests/test_stdio_server_factory.py -v`
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add atlas/mcp/shared/server_factory.py atlas/tests/test_stdio_server_factory.py
git commit -m "feat: add create_stdio_server helper with BlockedStateStore"
```

---

### Task 3: Migrate existing STDIO servers to use `create_stdio_server()`

**Files to modify** (all STDIO servers — exclude `api_key_demo` which uses HTTP):
- Modify: `atlas/mcp/basictable/main.py`
- Modify: `atlas/mcp/calculator/main.py`
- Modify: `atlas/mcp/code-executor/main.py`
- Modify: `atlas/mcp/corporate_cars/main.py`
- Modify: `atlas/mcp/csv_reporter/main.py`
- Modify: `atlas/mcp/duckduckgo/main.py`
- Modify: `atlas/mcp/elicitation_demo/main.py`
- Modify: `atlas/mcp/env-demo/main.py`
- Modify: `atlas/mcp/file_size_test/main.py`
- Modify: `atlas/mcp/filesystem/main.py`
- Modify: `atlas/mcp/image_demo/main.py`
- Modify: `atlas/mcp/logging_demo/main.py`
- Modify: `atlas/mcp/many_tools_demo/main.py`
- Modify: `atlas/mcp/order_database/main.py`
- Modify: `atlas/mcp/pdfbasic/main.py`
- Modify: `atlas/mcp/pptx_generator/main.py`
- Modify: `atlas/mcp/progress_demo/main.py`
- Modify: `atlas/mcp/progress_updates_demo/main.py`
- Modify: `atlas/mcp/prompts/main.py`
- Modify: `atlas/mcp/public_demo/main.py`
- Modify: `atlas/mcp/sampling_demo/main.py`
- Modify: `atlas/mcp/structured_output_demo/main.py`
- Modify: `atlas/mcp/task_demo/main.py`
- Modify: `atlas/mcp/thinking/main.py`
- Modify: `atlas/mcp/tool_planner/main.py`
- Modify: `atlas/mcp/ui-demo/main.py`
- Modify: `atlas/mcp/username-override-demo/main.py`
- Modify: `atlas/modules/mcp_tools/client.py` (STDIO launch section — add project root to PYTHONPATH)

**Do NOT modify:** `atlas/mcp/api_key_demo/main.py` (HTTP transport, needs default MemoryStore)
**Do NOT modify:** `atlas/mcp/session_state_demo/main.py` (converted to HTTP in Task 4)

**Approach:** Rather than adding `sys.path.insert` to every server file, add the project root to `PYTHONPATH` in the STDIO launch code in `client.py` (around line 720-730 in `_initialize_single_client` where `resolved_env` is built). This way the import `from atlas.mcp.shared.server_factory import create_stdio_server` works without modifying `sys.path` in each server.

The change in each server file is then just:

**Before:**
```python
from fastmcp import FastMCP
mcp = FastMCP("ServerName")
```

**After:**
```python
from atlas.mcp.shared.server_factory import create_stdio_server
mcp = create_stdio_server("ServerName")
```

Keep all other imports (especially `from fastmcp import Context` if used).

- [ ] **Step 1: Add project root to PYTHONPATH in STDIO launch code**

In `atlas/modules/mcp_tools/client.py`, in the STDIO section of `_initialize_single_client` (around line 720), ensure the project root is added to the subprocess environment:

```python
# After resolving env vars, add project root to PYTHONPATH for atlas.mcp imports
if resolved_env is None:
    resolved_env = dict(os.environ)
if "PYTHONPATH" in resolved_env:
    resolved_env["PYTHONPATH"] = f"{project_root}:{resolved_env['PYTHONPATH']}"
else:
    resolved_env["PYTHONPATH"] = str(project_root)
```

- [ ] **Step 2: List all STDIO servers to migrate (verify list)**

Run: `grep -rl "FastMCP(" atlas/mcp/*/main.py | xargs grep -L 'streamable-http'`

Verify the output matches the list above.

- [ ] **Step 3: Migrate each STDIO server**

For each file, replace the `FastMCP` import and instantiation. Keep all other imports (especially `from fastmcp import Context` if used).

- [ ] **Step 4: Run existing tests to verify nothing broke**

Run: `cd atlas && PYTHONPATH=.. pytest tests/ -x -q --timeout=30`
Expected: All existing tests pass

- [ ] **Step 5: Commit**

```bash
git add atlas/mcp/*/main.py atlas/modules/mcp_tools/client.py
git commit -m "refactor: migrate all STDIO MCP servers to create_stdio_server with BlockedStateStore"
```

---

### Task 4: Move `session_state_demo` to HTTP transport

**Files:**
- Modify: `atlas/mcp/session_state_demo/main.py`
- Modify: `atlas/config/mcp.json` (change from STDIO command to HTTP url)
- Modify: `atlas/config/mcp-example-configs/mcp-session_state_demo.json`

- [ ] **Step 1: Update `session_state_demo/main.py` to run as HTTP**

Change the server to use HTTP transport. It should NOT use `create_stdio_server` — it needs the default `MemoryStore` for legitimate state.

At the bottom of the file, replace:
```python
if __name__ == "__main__":
    mcp.run(show_banner=False)
```

With:
```python
if __name__ == "__main__":
    import os
    port = int(os.environ.get("MCP_SESSION_STATE_PORT", "8010"))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port, show_banner=False)
```

Keep the `from fastmcp import Context, FastMCP` import and `mcp = FastMCP("Session State Demo")` — do NOT use `create_stdio_server` here.

- [ ] **Step 2: Update `atlas/config/mcp.json`**

Replace the STDIO entry:
```json
"session_state_demo": {
    "command": ["python", "mcp/session_state_demo/main.py"],
    "cwd": "atlas",
    ...
}
```

With HTTP entry:
```json
"session_state_demo": {
    "url": "http://127.0.0.1:8010/mcp",
    "transport": "http",
    "groups": ["users"],
    "description": "Shopping cart demo using FastMCP 3.x session state (ctx.get_state/ctx.set_state) — state persists across tool calls within a conversation, isolated per user via HTTP sessions",
    "author": "Chat UI Team",
    "short_description": "Session state demo (HTTP)",
    "compliance_level": "Public"
}
```

- [ ] **Step 3: Update example config**

Update `atlas/config/mcp-example-configs/mcp-session_state_demo.json` to match the HTTP config.

- [ ] **Step 4: Commit**

```bash
git add atlas/mcp/session_state_demo/main.py atlas/config/mcp.json atlas/config/mcp-example-configs/mcp-session_state_demo.json
git commit -m "feat: move session_state_demo to HTTP transport for per-user state isolation"
```

---

## Chunk 2: Per-User HTTP Clients in MCPToolManager

### Task 5: Extend `call_tool` to use per-user clients for all HTTP servers

**Files:**
- Modify: `atlas/modules/mcp_tools/client.py` (lines ~1533-1569 in `call_tool`)
- Test: `atlas/tests/test_mcp_per_user_http_clients.py`

The key change: in `call_tool()`, HTTP servers should ALWAYS create per-user clients, not just auth servers. STDIO servers continue using shared clients (they have BlockedStateStore so state is safe).

- [ ] **Step 1: Write the failing tests**

```python
# atlas/tests/test_mcp_per_user_http_clients.py
"""Tests for per-user HTTP client isolation in MCPToolManager."""

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
        mgr._user_clients_lock = __import__("asyncio").Lock()
        return mgr


def test_is_http_server_true_for_http(manager):
    manager.servers_config["my_server"] = {"url": "http://localhost:8010/mcp", "transport": "http"}
    assert manager._is_http_server("my_server") is True


def test_is_http_server_false_for_stdio(manager):
    manager.servers_config["my_server"] = {"command": ["python", "main.py"]}
    assert manager._is_http_server("my_server") is False


def test_is_http_server_false_for_missing(manager):
    assert manager._is_http_server("nonexistent") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd atlas && PYTHONPATH=.. pytest tests/test_mcp_per_user_http_clients.py -v`
Expected: FAIL — `AttributeError: 'MCPToolManager' object has no attribute '_is_http_server'`

- [ ] **Step 3: Add `_is_http_server()` method to MCPToolManager**

Add after `_requires_user_auth()` (around line 1398):

```python
def _is_http_server(self, server_name: str) -> bool:
    """Check if a server uses HTTP/SSE transport (not STDIO)."""
    config = self.servers_config.get(server_name, {})
    transport = self._determine_transport_type(config)
    return transport in ("http", "sse")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd atlas && PYTHONPATH=.. pytest tests/test_mcp_per_user_http_clients.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add atlas/modules/mcp_tools/client.py atlas/tests/test_mcp_per_user_http_clients.py
git commit -m "feat: add _is_http_server helper to MCPToolManager"
```

---

### Task 6: Add `_get_or_create_user_http_client()` method

**Files:**
- Modify: `atlas/modules/mcp_tools/client.py`
- Modify: `atlas/tests/test_mcp_per_user_http_clients.py`

This is similar to `_get_user_client` but without token/auth requirements — it creates a plain HTTP client per user for servers that don't have auth_type set.

- [ ] **Step 1: Write the failing test**

Add to `atlas/tests/test_mcp_per_user_http_clients.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd atlas && PYTHONPATH=.. pytest tests/test_mcp_per_user_http_clients.py -v`
Expected: FAIL — `AttributeError: '_get_or_create_user_http_client'`

- [ ] **Step 3: Implement `_get_or_create_user_http_client()`**

Add after `_is_http_server()` in `client.py`:

```python
async def _get_or_create_user_http_client(
    self,
    server_name: str,
    user_email: str,
) -> Client:
    """Get or create a per-user HTTP client for session isolation.

    Unlike _get_user_client (which requires auth tokens), this creates
    plain HTTP clients keyed by (user_email, server_name). Each user gets
    their own MCP session ID, ensuring state isolation.

    Args:
        server_name: Name of the MCP server
        user_email: User's email address

    Returns:
        FastMCP Client instance for this user+server pair
    """
    cache_key = (user_email.lower(), server_name)

    async with self._user_clients_lock:
        if cache_key in self._user_clients:
            return self._user_clients[cache_key]

        config = self.servers_config.get(server_name, {})
        url = config.get("url", "")
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"

        # Resolve admin auth token if configured (not per-user, just server-level)
        raw_token = config.get("auth_token")
        try:
            token = resolve_env_var(raw_token)
        except ValueError:
            token = None

        log_handler = self._create_log_handler(server_name)
        client = Client(
            url,
            auth=token,
            log_handler=log_handler,
            elicitation_handler=self._create_elicitation_handler(server_name),
            sampling_handler=self._create_sampling_handler(server_name),
        )

        self._user_clients[cache_key] = client

    logger.info(f"Created per-user HTTP client for server '{server_name}' user='{user_email}'")
    return client
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd atlas && PYTHONPATH=.. pytest tests/test_mcp_per_user_http_clients.py -v`
Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add atlas/modules/mcp_tools/client.py atlas/tests/test_mcp_per_user_http_clients.py
git commit -m "feat: add _get_or_create_user_http_client for per-user HTTP session isolation"
```

---

### Task 7: Modify `call_tool()` to route HTTP servers through per-user clients

**Files:**
- Modify: `atlas/modules/mcp_tools/client.py` (the `call_tool` method, lines ~1533-1569)
- Modify: `atlas/tests/test_mcp_per_user_http_clients.py`

The routing logic becomes:
1. If `_requires_user_auth(server_name)` → existing auth flow (tokens required)
2. Else if `_is_http_server(server_name)` and `user_email` → `_get_or_create_user_http_client`
3. Else → shared `self.clients[server_name]` (STDIO, safe because of BlockedStateStore)

- [ ] **Step 1: Write the failing test**

Add to `atlas/tests/test_mcp_per_user_http_clients.py`:

```python
@pytest.mark.asyncio
async def test_call_tool_uses_per_user_client_for_http(manager):
    """HTTP servers should use per-user clients, not shared ones."""
    manager.servers_config["http_server"] = {
        "url": "http://127.0.0.1:8010/mcp",
        "transport": "http",
    }
    # Put a shared client in — it should NOT be used
    shared_client = MagicMock()
    manager.clients["http_server"] = shared_client

    with patch.object(manager, "_get_or_create_user_http_client", new_callable=AsyncMock) as mock_get:
        per_user_client = AsyncMock()
        per_user_client.__aenter__ = AsyncMock(return_value=per_user_client)
        per_user_client.__aexit__ = AsyncMock(return_value=False)
        per_user_client.call_tool = AsyncMock(return_value="result")
        mock_get.return_value = per_user_client

        with patch.object(manager, "_use_elicitation_context") as mock_elic, \
             patch.object(manager, "_use_sampling_context") as mock_samp:
            mock_elic.return_value.__aenter__ = AsyncMock()
            mock_elic.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_samp.return_value.__aenter__ = AsyncMock()
            mock_samp.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await manager.call_tool(
                "http_server", "my_tool", {"arg": "val"},
                user_email="alice@test.com",
            )

        mock_get.assert_called_once_with("http_server", "alice@test.com")


@pytest.mark.asyncio
async def test_call_tool_uses_shared_client_for_stdio(manager):
    """STDIO servers should still use shared clients."""
    manager.servers_config["stdio_server"] = {
        "command": ["python", "main.py"],
    }
    shared_client = AsyncMock()
    shared_client.__aenter__ = AsyncMock(return_value=shared_client)
    shared_client.__aexit__ = AsyncMock(return_value=False)
    shared_client.call_tool = AsyncMock(return_value="result")
    manager.clients["stdio_server"] = shared_client

    with patch.object(manager, "_use_elicitation_context") as mock_elic, \
         patch.object(manager, "_use_sampling_context") as mock_samp:
        mock_elic.return_value.__aenter__ = AsyncMock()
        mock_elic.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_samp.return_value.__aenter__ = AsyncMock()
        mock_samp.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await manager.call_tool(
            "stdio_server", "my_tool", {"arg": "val"},
            user_email="alice@test.com",
        )

    # Should have used shared client, not per-user
    shared_client.call_tool.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd atlas && PYTHONPATH=.. pytest tests/test_mcp_per_user_http_clients.py::test_call_tool_uses_per_user_client_for_http -v`
Expected: FAIL — shared client used instead of per-user

- [ ] **Step 3: Modify `call_tool()` routing logic**

In `atlas/modules/mcp_tools/client.py`, replace the `else` block at lines 1565-1569:

**Before (lines 1536-1569):**
```python
        # Check if this server requires per-user authentication
        if self._requires_user_auth(server_name):
            ...  # existing auth flow stays the same
        else:
            # Use shared client for servers without per-user auth
            if server_name not in self.clients:
                raise ValueError(f"No client available for server: {server_name}")
            client = self.clients[server_name]
```

**After:**
```python
        # Check if this server requires per-user authentication
        if self._requires_user_auth(server_name):
            ...  # existing auth flow stays the same
        elif self._is_http_server(server_name) and user_email:
            # HTTP servers get per-user clients for session state isolation
            client = await self._get_or_create_user_http_client(server_name, user_email)
        else:
            # STDIO servers use shared client (safe: BlockedStateStore prevents state use)
            if server_name not in self.clients:
                raise ValueError(f"No client available for server: {server_name}")
            client = self.clients[server_name]
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `cd atlas && PYTHONPATH=.. pytest tests/test_mcp_per_user_http_clients.py -v`
Expected: All PASSED

Run: `cd atlas && PYTHONPATH=.. pytest tests/ -x -q --timeout=30`
Expected: All existing tests still pass

- [ ] **Step 5: Commit**

```bash
git add atlas/modules/mcp_tools/client.py atlas/tests/test_mcp_per_user_http_clients.py
git commit -m "feat: route HTTP MCP servers through per-user clients for session isolation

SECURITY: Previously all users shared a single MCP client per server,
meaning ctx.get_state/ctx.set_state was shared across users. Now HTTP
servers create per-user Client instances with isolated session IDs.
STDIO servers use shared clients but have BlockedStateStore to prevent
state operations."
```

---

### Task 8: Update `get_prompt` to also use per-user clients for HTTP

**Files:**
- Modify: `atlas/modules/mcp_tools/client.py` (the `get_prompt` method, lines ~1599-1615)

The `get_prompt` method has the same shared-client pattern. Apply the same fix.

- [ ] **Step 1: Modify `get_prompt()` to route HTTP servers through per-user clients**

**Before:**
```python
async def get_prompt(self, server_name: str, prompt_name: str, arguments: Dict[str, Any] = None) -> Any:
    """Get a specific prompt from an MCP server."""
    if server_name not in self.clients:
        raise ValueError(f"No client available for server: {server_name}")
    client = self.clients[server_name]
```

**After:**
```python
async def get_prompt(
    self,
    server_name: str,
    prompt_name: str,
    arguments: Dict[str, Any] = None,
    *,
    user_email: Optional[str] = None,
) -> Any:
    """Get a specific prompt from an MCP server."""
    if self._is_http_server(server_name) and user_email:
        client = await self._get_or_create_user_http_client(server_name, user_email)
    elif server_name not in self.clients:
        raise ValueError(f"No client available for server: {server_name}")
    else:
        client = self.clients[server_name]
```

- [ ] **Step 2: Find and update callers of `get_prompt` to pass `user_email`**

Run: `grep -rn "get_prompt" atlas/ --include="*.py" | grep -v test | grep -v __pycache__`

Known caller: `atlas/application/chat/preprocessors/prompt_override_service.py` line ~56.
This caller needs `user_email` threaded from its call context. Check the preprocessor pipeline to find where `user_email` is available (likely in `session_context`) and pass it through.

Update each caller to pass `user_email` from their context if available. If a caller doesn't have access to `user_email`, pass `None` — the method falls back to the shared client, which is fine for STDIO prompt servers.

- [ ] **Step 3: Run full test suite**

Run: `cd atlas && PYTHONPATH=.. pytest tests/ -x -q --timeout=30`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add atlas/modules/mcp_tools/client.py
git commit -m "feat: extend per-user HTTP client routing to get_prompt"
```

---

### Task 9: Final verification and cleanup

- [ ] **Step 1: Run the full test suite**

Run: `cd atlas && PYTHONPATH=.. pytest tests/ -v --timeout=30`
Expected: All tests pass

- [ ] **Step 2: Verify STDIO servers block state at runtime**

```bash
cd atlas && PYTHONPATH=.. python -c "
from atlas.mcp.shared.server_factory import create_stdio_server

mcp = create_stdio_server('Test')
print('BlockedStateStore type:', type(mcp._state_storage).__name__)
print('STDIO state blocking works correctly')
"
```

Expected: `BlockedStateStore`

- [ ] **Step 3: Commit all remaining changes and push**

```bash
git push origin feat/per-user-mcp-sessions
```
