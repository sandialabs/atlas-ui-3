# Elicitation Dialog Not Showing - Issue Analysis

**Date**: 2025-01-07
**Branch**: copilot/allow-mcp-servers-tool-elicitation
**Status**: IDENTIFIED - Root cause found

## Problem

MCP tool elicitation requests (ctx.elicit()) are not displaying the dialog in the UI. Tools complete immediately with empty responses instead of pausing for user input.

## Symptoms

- User calls elicitation demo tools (get_user_name, pick_a_number)
- Tool shows "Success" but returns empty response
- No elicitation dialog appears in frontend
- Tool completes immediately instead of waiting for input

## Root Cause

**Location**: backend/modules/mcp_tools/client.py:247-249

```python
routing = _ACTIVE_ELICITATION_CONTEXT.get()
if routing is None or routing.update_cb is None:
    return ElicitResult(action="cancel", content=None)
```

When `routing.update_cb` is **None**, elicitation is silently cancelled without notifying the frontend.

## How It Should Work

1. Tool calls `ctx.elicit()`
2. MCP client's `_global_elicitation_handler` is invoked
3. Handler checks `_ACTIVE_ELICITATION_CONTEXT` for routing info
4. If `routing.update_cb` exists, sends elicitation_request to frontend via WebSocket
5. Waits for user response
6. Returns user's input to tool

## What's Actually Happening

1. Tool calls `ctx.elicit()`
2. Handler checks routing context
3. **`routing.update_cb` is None**
4. Returns immediate cancel
5. Tool gets empty/cancelled response
6. No UI dialog ever appears

## The Update Callback Flow

```
main.py:335 → lambda websocket callback
  ↓ handle_chat_message (update_callback param)
  ↓ orchestrator.execute (kwargs.get("update_callback"))
  ↓ tools_mode.run (update_callback param, fallback to _get_send_json())
  ↓ execute_tools_workflow (update_callback param)
  ↓ execute_single_tool (update_callback param)
  ↓ tool_manager.execute_tool (context["update_callback"])
  ↓ _use_elicitation_context (update_cb from context)
```

## The Bug

The `update_callback` becomes **None** somewhere in this chain, causing elicitation to fail silently. The fallback mechanism (`self._get_send_json()`) should provide `event_publisher.send_json` but either:
- It's not being invoked correctly
- It's returning None
- The callback isn't being passed through all layers properly

## Fix Strategy

Ensure `update_callback` is always valid when tools execute:
1. Validate that event_publisher.send_json is available as fallback
2. Add logging when update_callback is None
3. Ensure the callback chain is unbroken from WebSocket to MCP tool execution

## The Actual Fix (RESOLVED)

**Root Cause**: The `_ACTIVE_ELICITATION_CONTEXT` was a `contextvars.ContextVar`, but the MCP client's receive loop runs in a **separate asyncio task** from the tool execution. Context variables are task-local and don't cross task boundaries, so the receive loop couldn't access the routing context.

**Solution**: Switched from `contextvars.ContextVar` to **dictionary-based routing**:

1. Created `_ELICITATION_ROUTING: Dict[str, _ElicitationRoutingContext] = {}` keyed by `server_name`
2. Changed `_create_elicitation_handler(server_name)` to return a **closure** that captures `server_name`
3. Each MCP client gets its own handler that knows which server it belongs to
4. The handler looks up routing from the dictionary using the captured `server_name`
5. Updated `_use_elicitation_context` to use dictionary instead of context variable

**Files Modified**:
- `backend/modules/mcp_tools/client.py`: Core elicitation routing fix
- `backend/application/chat/modes/tools.py`: Added logging and validation
- `backend/application/chat/orchestrator.py`: Added logging
- `CHANGELOG.md`: Documented the fix

**Result**: Elicitation requests now properly route from the MCP receive loop task to the correct WebSocket connection, allowing the dialog to appear in the UI.
