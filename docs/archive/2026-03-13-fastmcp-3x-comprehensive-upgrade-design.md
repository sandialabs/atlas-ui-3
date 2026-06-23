# FastMCP 3.x Comprehensive Upgrade

**Date:** 2026-03-13
**Status:** Draft
**Branch:** TBD (single branch, all features)
**Fixes:** #295 (MCP elicitation/sampling routing mis-routes concurrent calls)
**Relates to:** #219 (UI hangs on long MCP calls), #297 (RAG pollutes tool inventory)

## Motivation

Atlas upgraded to FastMCP >= 3.0 but still uses 2.x patterns. This upgrade adopts six FastMCP 3.x features in a single pass:

1. **Meta routing fix** — fixes #295 concurrent call mis-routing
2. **Session persistence per conversation** — hold MCP sessions open across tool calls
3. **Session state for MCP servers** — `ctx.get_state()` / `ctx.set_state()` with pluggable storage
4. **Structured output** — prefer `structuredContent` over JSON-in-text parsing
5. **Background tasks with adaptive polling** — async tool execution for long-running calls
6. **Prompt improvements** — multi-prompt support and `meta` on prompt resolution

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Session scope | Per conversation | Balances state reuse with lifecycle simplicity |
| State storage | In-memory, feature-flagged Redis | Start simple, scale to multi-pod later |
| Structured output | Prefer structured, fallback to legacy | Backward compatibility with third-party servers |
| Background tasks | Adaptive polling (sync first, escalate after threshold) | Users expect instant results for fast tools |
| Task timeout | 10s default, configurable via `.env` | `MCP_TASK_TIMEOUT` env var |
| Prompt selection | Multi-prompt stacking | All selected prompts applied, not just first |
| Approach | Single branch, all features | Manageable scope for one pass |

## Future Direction

The session persistence architecture is designed with an abstract storage interface to support **Redis-backed durable sessions** in a future phase. With Redis:
- Sessions survive server restarts and pod migrations
- Users reconnect to existing session state after page refresh
- MCP tools continue running asynchronously when user disconnects
- Enables Atlas as a **long-lived async agent** that works independently of the user's browser session

---

## 1. Session Persistence & Meta Routing

### 1.1 MCPSessionManager

New class in `atlas/modules/mcp_tools/session_manager.py`.

**Responsibilities:**
- Hold live MCP client sessions keyed by `(conversation_id, server_name)`
- Open session on first use, reuse on subsequent tool calls within the same conversation
- Clean up all sessions when a conversation ends (WebSocket disconnect or explicit close)
- Handle session failures (reconnect on error)

```
MCPSessionManager
  _sessions: Dict[(conversation_id, server_name), ManagedSession]
  _lock: asyncio.Lock

  acquire(conversation_id, server_name, client) -> ManagedSession
  release(conversation_id, server_name)
  release_all(conversation_id)
```

`ManagedSession` wraps an open client context (`client.__aenter__()` called once) and exposes the client for tool calls. Reference counting or explicit lifecycle management ensures cleanup.

**Storage interface:** The session manager uses an abstract `SessionStore` protocol so the backing storage can be swapped from in-memory dict to Redis without changing the manager logic. In-memory is the default.

**Per-user auth sessions:** For servers requiring per-user authentication, sessions are keyed by `(user_email, conversation_id, server_name)`. Token validation still happens on each call; if the token expires, the session is closed and a new one opened on re-auth.

### 1.2 Meta Routing Fix (Issue #295)

**Root cause:** Elicitation and sampling handlers search `_ELICITATION_ROUTING` / `_SAMPLING_ROUTING` by `server_name` only, ignoring the `tool_call_id` component of the composite key. Concurrent calls to the same server get the first match.

**Fix:** Pass `tool_call_id` via FastMCP 3.x `meta` parameter and extract it in the handler from `RequestContext`.

**call_tool changes:**
```python
result = await client.call_tool(
    tool_name,
    arguments,
    meta={"tool_call_id": tool_call.id},
    **kwargs,
)
```

**Handler lookup changes (both elicitation and sampling):**

The `_context` parameter is a `RequestContext` from FastMCP 3.x. Its `.meta` field is a Pydantic model (`RequestParams.Meta`) with `extra='allow'`, so custom fields passed via `meta={"tool_call_id": ...}` are stored in `model_extra` and accessible directly.

```python
# Extract tool_call_id from RequestContext.meta.model_extra
# Meta is a Pydantic model with extra='allow', so custom fields
# passed via call_tool(meta={...}) are in meta.model_extra
tcid = None
if _context and _context.meta:
    tcid = getattr(_context.meta, 'model_extra', {}).get("tool_call_id")

# Direct O(1) lookup instead of broken loop
routing = _ELICITATION_ROUTING.get((server_name, tcid))

# Fallback: if meta not available (old servers), use single-match heuristic
if routing is None:
    matches = [(k, v) for k, v in _ELICITATION_ROUTING.items() if k[0] == server_name]
    if len(matches) == 1:
        routing = matches[0][1]
    elif len(matches) > 1:
        logger.warning("Ambiguous routing for server '%s' - cancelling", server_name)
        return ElicitResult(action="cancel", content=None)
```

**Verification note:** Unit tests must assert that `_context.meta.model_extra` contains `tool_call_id` when passed via `call_tool(meta={"tool_call_id": ...})` with FastMCP 3.1.0.

### 1.3 Client Lifecycle Change

```
Before:  call_tool() -> async with client: -> call -> close session
After:   call_tool() -> session_manager.acquire() -> call -> session stays open
                                                          -> release on conversation end
```

**Plumbing `conversation_id`:** Currently `execute_tool()` receives a `context` dict with `update_callback` and `user_email` but not `conversation_id`. The `conversation_id` lives in the chat service layer (`session.context["conversation_id"]`). Changes needed:
- `tool_executor.py` passes `conversation_id` in the context dict
- `execute_tool()` extracts it and passes to the session manager
- `call_tool()` accepts `conversation_id` parameter

**Session lifecycle and cleanup:** Sessions are keyed by `conversation_id`, not `session_id` (WebSocket session). A single WebSocket session can handle multiple conversations via `restore_conversation`. When `restore_conversation` is called, the previous conversation's MCP sessions must be released before acquiring sessions for the new conversation. On WebSocket disconnect (`main.py` line 625), `release_all(conversation_id)` closes all sessions for the active conversation.

**Lazy session creation:** Sessions are only opened for servers that are actually called during the conversation, not all 26 servers eagerly. Combined with an idle timeout (configurable, default 5 min), this bounds memory pressure from long-lived STDIO subprocess sessions.

| File | Plumbing Change |
|------|----------------|
| `atlas/application/chat/utilities/tool_executor.py` | Pass `conversation_id` in context dict |
| `atlas/modules/mcp_tools/client.py` | `execute_tool()` and `call_tool()` accept `conversation_id` |
| `atlas/main.py` | Call `release_all()` on WebSocket disconnect and on `restore_conversation` |

---

## 2. Session State for MCP Servers

### 2.1 Storage Backend Utility

New shared utility at `atlas/mcp/common/state.py`:

```python
def get_state_store():
    backend = os.getenv("MCP_STATE_BACKEND", "memory")
    if backend == "redis":
        from key_value.aio.stores.redis import RedisStore
        return RedisStore(url=os.getenv("MCP_REDIS_URL", "redis://localhost:6379/0"))
    return None  # FastMCP default in-memory store
```

MCP servers that want state use:
```python
from atlas.mcp.common.state import get_state_store

mcp = FastMCP("my-server", session_state_store=get_state_store())
```

### 2.2 Server Adoption

Session state is opt-in per server. Good initial candidates:

- **RAG servers** — track retrieved documents to avoid redundant fetches within a conversation
- **Order database** — remember query context, pagination cursors
- **Tool planner** — maintain plan state across steps
- **Code executor** — preserve execution context/variables

Stateless servers (calculator, csv_reporter, etc.) remain unchanged.

### 2.3 State API Usage

```python
@mcp.tool
async def search_documents(query: str, ctx: Context) -> dict:
    previous_docs = await ctx.get_state("retrieved_doc_ids") or []
    # ... search excluding previous_docs ...
    await ctx.set_state("retrieved_doc_ids", previous_docs + new_doc_ids)
    return results
```

For non-serializable request-scoped state (large objects within a single call):
```python
await ctx.set_state("temp_data", large_object, serializable=False)
```

**Note:** `serializable=False` state is stored in a request-scoped in-memory dict regardless of the configured backend (memory or Redis). It does not survive across requests and cannot be distributed. This is by design for large transient objects that shouldn't be serialized to JSON.

---

## 3. Structured Output

### 3.1 Result Normalization Priority

Updated `_normalize_mcp_tool_result()` parsing priority:

1. **`raw_result.data`** — FastMCP 3.x parsed/validated structured content. Preferred because FastMCP validates the result against the tool's output schema and deserializes it into the declared Python type. This is higher fidelity than the raw dict. Current code checks `structured_content` first, then `data` — this reverses that order.
2. **`raw_result.structured_content`** — raw structured content dict (already partially handled). Unvalidated but structured.
3. **`content[0].text`** JSON parse — legacy fallback
4. **Legacy keys** (`result`, `meta-data`, `metadata`) — backward compat

### 3.2 Server-Side Output Schemas

Atlas's own MCP servers can declare output types:

```python
from pydantic import BaseModel

class ToolOutput(BaseModel):
    results: list
    meta_data: dict | None = None

@mcp.tool
async def my_tool() -> ToolOutput:
    return ToolOutput(results=[...], meta_data={...})
```

FastMCP automatically generates the output schema and validates return values. The client receives typed, validated results in `structured_content`.

### 3.3 V2 Component Extraction

The existing V2 extraction logic (artifacts, display config, meta_data) reads from the structured dict regardless of which parsing path produced it. No change to the extraction logic itself — only the input source priority changes.

### 3.4 Backward Compatibility

Third-party servers returning JSON strings in `TextContent` continue to work via the fallback chain. No breaking changes.

---

## 4. Background Tasks with Adaptive Polling

### 4.1 Flow

FastMCP 3.x `call_tool(task=True)` returns a `ToolTask` handle directly — there is no way to "re-issue" a blocking call as a task mid-flight. The adaptive approach uses `ToolTask` from the start for servers that support it, then checks if the result came back immediately.

**`ToolTask` API (FastMCP 3.1.0):**
- `task.returned_immediately` — `True` if the server completed synchronously
- `task.wait(state=..., timeout=...)` — blocks until target state or timeout
- `task.on_status_change(callback)` — registers a callback for state changes
- `task.status` — returns current `GetTaskResult`
- `task.result` — returns `CallToolResult` (only when complete)
- `task.cancel()` — cancels the task on the server

**Flow:**
```
call_tool() starts
  |-- Server supports tasks? (cached capability check)
  |   |-- No  -> call normally (blocking), return result
  |   \-- Yes ->
  |         |-- Call with task=True -> get ToolTask handle
  |         |-- task.returned_immediately?
  |         |   \-- Yes -> return task.result (instant, user sees no difference)
  |         \-- No (long-running) ->
  |               |-- Wait MCP_TASK_TIMEOUT seconds via task.wait(timeout=MCP_TASK_TIMEOUT)
  |               |-- Completed within timeout? -> return task.result
  |               \-- Still running ->
  |                     |-- Send UI: {"type": "tool_task_started", ...}
  |                     |-- Register task.on_status_change(progress_callback)
  |                     |   \-- Send UI: {"type": "tool_task_progress", ...}
  |                     |-- task.wait() until completion
  |                     \-- Send UI: {"type": "tool_task_completed", ...}
  |                         Return task.result
```

For servers without task support, behavior is identical to today — blocking `call_tool()`.

### 4.2 Server Capability Detection

After session initialization, check `client.initialize_result` for task capability support. Cache this per server to avoid repeated checks.

### 4.3 UI WebSocket Messages

New message types sent via `update_cb`:

| Type | Fields | Purpose |
|------|--------|---------|
| `tool_task_started` | `tool_call_id`, `tool_name`, `server_name` | Tool exceeded timeout, now polling |
| `tool_task_progress` | `tool_call_id`, `status`, `progress`, `total`, `message` | Status update during polling |
| `tool_task_completed` | `tool_call_id` | Result ready (followed by normal tool result) |

The frontend can show a "still running..." indicator per tool. Multiple tools can be in-flight simultaneously.

### 4.4 Task Cancellation

When the user cancels a chat request (`stop_streaming` → `asyncio.CancelledError`), any in-flight `ToolTask` instances must be cleaned up:

```python
try:
    result = await task.wait()
except asyncio.CancelledError:
    await task.cancel()  # Cancel on the MCP server
    raise
```

The session manager tracks active `ToolTask` instances per conversation so `release_all()` can cancel them on disconnect.

### 4.5 Configuration

```env
# Seconds to wait before switching to polling mode (default: 10)
MCP_TASK_TIMEOUT=10
```

Added to `.env.example` with documentation.

---

## 5. Prompt Improvements

### 5.1 Multi-Prompt Support

Change `apply_prompt_override()` in `atlas/application/chat/preprocessors/prompt_override_service.py`:

**Before:** Break after first valid prompt.
**After:** Iterate all selected prompts, retrieve each, stack as separate system messages in selection order.

```python
# Before
for prompt_key in selected_prompts:
    # ... retrieve and inject ...
    break  # only first

# After
system_messages = []
for prompt_key in selected_prompts:
    text = await self._resolve_prompt(prompt_key)
    if text:
        system_messages.append({"role": "system", "content": text})
# Prepend all system messages to conversation
messages = system_messages + messages
```

Prompts are prepended in selection order — first selected prompt is the first system message (farthest from the user's message). Each prompt gets its own system message so the LLM can distinguish them.

**`_extract_prompt_text()` update:** Currently only reads `content_field[0]` (first content item). Update to concatenate all `TextContent` items from the prompt response, since multi-content prompts are valid in FastMCP 3.x.

### 5.2 Meta on Prompt Resolution

`get_prompt()` passes contextual metadata to the server:

```python
result = await client.get_prompt(
    prompt_name,
    arguments,
    meta={"user_email": user_email, "conversation_id": conversation_id},
)
```

Servers that don't use `meta` ignore it. Servers that want personalization read it from context to tailor their response.

### 5.3 Version Support

FastMCP 3.x `get_prompt()` accepts `version`. The plumbing passes it through:
- Prompt discovery stores version info if the server provides it
- `get_prompt()` accepts optional `version` parameter
- No immediate consumer — available for future use

### 5.4 Backward Compatibility

Simple string-returning prompts continue to work. The existing `_extract_prompt_text()` fallback chain is preserved. New features are additive.

---

## 6. Configuration & Environment

### 6.1 New Environment Variables

```env
# Background task polling threshold (seconds before switching to polling)
MCP_TASK_TIMEOUT=10

# Session state backend: "memory" or "redis"
MCP_STATE_BACKEND=memory

# Redis URL (only when MCP_STATE_BACKEND=redis)
MCP_REDIS_URL=redis://localhost:6379/0
```

### 6.2 No Changes to `mcp.json`

All new features are auto-detected or negotiated via MCP capabilities. No per-server config changes required.

---

## 7. Files Changed

| File | Type | Changes |
|------|------|---------|
| `atlas/modules/mcp_tools/session_manager.py` | **New** | `MCPSessionManager`, `ManagedSession`, `SessionStore` protocol |
| `atlas/modules/mcp_tools/client.py` | Modified | Use session manager, meta routing fix, adaptive task polling, structured output priority, prompt meta |
| `atlas/application/chat/utilities/tool_executor.py` | Modified | Pass `conversation_id` in context dict |
| `atlas/main.py` | Modified | Call `release_all()` on WebSocket disconnect and `restore_conversation` |
| `atlas/mcp/common/__init__.py` | **New** | Package init |
| `atlas/mcp/common/state.py` | **New** | `get_state_store()` utility |
| `atlas/application/chat/preprocessors/prompt_override_service.py` | Modified | Multi-prompt support, meta passing, `_extract_prompt_text` multi-content |
| `atlas/mcp/*/main.py` | Modified | Selected servers updated to use session state (opt-in) |
| `.env.example` | Modified | New env vars documented |
| `atlas/tests/test_session_manager.py` | **New** | Session lifecycle tests |
| `atlas/tests/test_elicitation_routing.py` | Modified | Concurrent same-server routing tests |
| `atlas/tests/test_adaptive_task_polling.py` | **New** | Task timeout and polling tests |
| `atlas/tests/test_multi_prompt.py` | **New** | Multi-prompt stacking tests |
| `atlas/tests/test_structured_output.py` | **New** | Structured output priority tests |

---

## 8. Testing Strategy

### Unit Tests
- **Session manager:** acquire/release lifecycle, concurrent access, cleanup on conversation end, reconnect on failure
- **Meta routing:** concurrent calls to same server route correctly, fallback when meta unavailable, ambiguous routing cancelled
- **Structured output:** parsing priority (data > structured_content > text > legacy), backward compat with old servers
- **Adaptive polling:** timeout triggers task mode, capability detection, polling loop, servers without task support fall back
- **Multi-prompt:** multiple prompts stacked in order, meta passed through, backward compat with simple prompts

### Integration Tests
- Full tool call through session manager with elicitation
- Two concurrent tool calls to same server with correct routing
- Background task with polling status updates
- Multi-prompt selection through chat flow

---

## 9. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Session leak (sessions not cleaned up) | `release_all()` on WebSocket disconnect + periodic cleanup sweep |
| Server doesn't support tasks | Capability check before attempting task mode; graceful fallback |
| Redis unavailable when configured | Feature flag check at startup, clear error message, fallback to memory |
| Breaking third-party MCP servers | All changes have fallback paths; legacy parsing preserved |
| Meta not available in handler context | Fallback to single-match heuristic when only one routing entry exists |
