# MCP Session Isolation

**Date**: 2026-03-15
**PR**: #431

## Overview

MCP session isolation prevents cross-user state leakage in multi-user deployments. STDIO servers share a single process across all users, so session state operations are blocked. HTTP servers get per-user client routing for session isolation.

## Architecture

### Transport-Based Isolation

| Transport | State Strategy | Why |
|-----------|---------------|-----|
| STDIO | `BlockedStateStore` — reads return empty, writes raise `RuntimeError` | Single process shared across all users; any stored state would be visible to everyone |
| HTTP | Per-user/per-conversation `Client` instances keyed by `(user_email, server_name, conversation_id)` | Each client gets its own MCP session ID, isolating state per conversation. Per-conversation keying (not per-user) is required because `MCPSessionManager` opens persistent sessions per conversation and each open increments FastMCP's reentrant nesting counter on the underlying `Client`. Sharing one client across conversations accumulates the counter; if the underlying session task dies, FastMCP refuses to reconnect ("nesting counter should be 0 when starting new session, got N"). |

### BlockedStateStore (`atlas/mcp_shared/blocked_state.py`)

A fail-safe store injected into STDIO servers via `create_stdio_server()`:

- **Read operations** (`get`, `get_many`, `ttl`): Return empty values (nothing to leak)
- **Delete operations**: No-op (nothing to delete)
- **Write operations** (`put`, `put_many`): Raise `RuntimeError` with guidance to use HTTP transport

### Server Factory (`atlas/mcp_shared/server_factory.py`)

All 27 STDIO MCP servers use `create_stdio_server(name)` instead of `FastMCP(name)` directly:

```python
from atlas.mcp_shared.server_factory import create_stdio_server

mcp = create_stdio_server("My Server")
```

This injects `BlockedStateStore` automatically. For stateful servers, use HTTP transport and optionally `get_state_store()`:

```python
from fastmcp import FastMCP
from atlas.mcp.common.state import get_state_store

mcp = FastMCP("Stateful Server", session_state_store=get_state_store())
```

## MCPSessionManager (`atlas/modules/mcp_tools/session_manager.py`)

Holds live MCP sessions keyed by `(conversation_id, server_name)`:

- **Lazy creation**: Sessions open on first tool call via `acquire()`
- **Reuse**: Subsequent calls in the same conversation reuse the open session
- **Per-key locks**: Opening a session for one server doesn't block others
- **Cleanup**: `release_all(conversation_id)` on WebSocket disconnect or conversation restore

### Lifecycle

1. User calls a tool → `MCPSessionManager.acquire()` opens session if needed
2. Subsequent tool calls reuse the open session
3. WebSocket disconnect → `release_sessions(conversation_id, user_email)` closes all sessions and evicts the conversation's HTTP clients
4. Server shutdown → `cleanup()` closes all sessions and clears client caches

### Dead Session Recovery (PR #461, 2026-03-21)

When a STDIO server process crashes between tool calls, the persistent session becomes stale. Without detection, the next tool call would hit FastMCP's `ClosedResourceError` with no recovery path.

**Detection**: `ManagedSession.is_open` checks `client.is_connected()` in addition to Python-side `_opened`/`_closed` flags. This detects server-side disconnects that the flags alone miss.

**Eviction**: `MCPSessionManager.acquire()` detects dead sessions on the re-check path, removes them from the session map, and calls `close()` outside the global lock. The `close()` call invokes `client.__aexit__()` which resets FastMCP's internal nesting counter — required before `__aenter__` can start a fresh connection.

**Reconnection**: After eviction, a new `ManagedSession` is created and opened normally. The caller sees a transparent reconnect with no error.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_STATE_BACKEND` | `memory` | State storage backend: `memory` (FastMCP default) or `redis` |
| `MCP_REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL (only when `MCP_STATE_BACKEND=redis`) |
| `MCP_TASK_TIMEOUT` | `10` | Seconds to wait synchronously before switching to background task polling |

## Elicitation/Sampling Routing

Concurrent tool calls are routed via composite `(server_name, tool_call_id)` keys on the `MCPToolManager` instance. When a server sends an elicitation or sampling request:

1. **O(1) lookup**: Check `(server_name, tool_call_id)` via `context.meta`
2. **Fallback**: If meta unavailable, scan for single match on `server_name`
3. **Ambiguous**: If multiple matches without meta, cancel (elicitation) or raise (sampling)

## Writing New MCP Servers

### Stateless (STDIO)

```python
from atlas.mcp_shared.server_factory import create_stdio_server

mcp = create_stdio_server("My Stateless Server")

@mcp.tool
async def my_tool(input: str) -> str:
    return f"Processed: {input}"

if __name__ == "__main__":
    mcp.run()
```

### Stateful (HTTP)

```python
from fastmcp import Context, FastMCP
from atlas.mcp.common.state import get_state_store

mcp = FastMCP("My Stateful Server", session_state_store=get_state_store())

@mcp.tool
async def remember(key: str, value: str, ctx: Context) -> str:
    await ctx.set_state(key, value)
    return f"Stored {key}"

@mcp.tool
async def recall(key: str, ctx: Context) -> str:
    value = await ctx.get_state(key)
    return f"{key} = {value}"

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8010)
```
