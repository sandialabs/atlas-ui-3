# Multi-Tool Calling (Parallel Execution)

Last updated: 2026-02-22

## Overview

Atlas supports parallel execution of multiple tool calls when an LLM returns
more than one tool call in a single response. This is common with models like
GPT-4, Claude, and Gemini that support "parallel function calling".

## How It Works

When the LLM response contains multiple `tool_calls`, all calls are dispatched
concurrently using `asyncio.gather`. Each tool runs as an independent coroutine
so that IO-bound MCP operations (HTTP, subprocess, etc.) overlap rather than
serialize.

```
LLM Response
  tool_calls: [toolA, toolB, toolC]
       |
       v
asyncio.gather(
    execute_single_tool(toolA),
    execute_single_tool(toolB),
    execute_single_tool(toolC),
)
       |
       v
[ToolResult-A, ToolResult-B, ToolResult-C]
```

Results are returned in the same order as the input tool calls. If one tool
fails, its result is converted to an error `ToolResult` while the other tools
still succeed.

## Key Function

`atlas/application/chat/utilities/tool_executor.py:execute_multiple_tools`

```python
async def execute_multiple_tools(
    tool_calls: list,
    session_context: Dict[str, Any],
    tool_manager,
    update_callback=None,
    config_manager=None,
    skip_approval: bool = False,
) -> List[ToolResult]:
```

- Single tool call: delegates directly to `execute_single_tool` (no overhead).
- Multiple tool calls: runs all via `asyncio.gather(return_exceptions=True)`.
- Exceptions are caught and converted to error `ToolResult` objects.

## Where It Is Used

| Component | File | Before | After |
|-----------|------|--------|-------|
| ReAct loop | `agent/react_loop.py` | First tool only | All tools in parallel |
| Think-Act loop | `agent/think_act_loop.py` | First tool only | All tools in parallel |
| Act loop | `agent/act_loop.py` | First non-finished tool only | All non-finished tools in parallel |
| Tools mode (non-streaming) | `utilities/tool_executor.py` | Sequential for loop | Parallel via `execute_multiple_tools` |
| Tools mode (streaming) | `modes/tools.py` | Sequential for loop | Parallel via `execute_multiple_tools` |

## Error Handling

If a tool execution raises an exception during parallel execution, the
exception is caught by `asyncio.gather(return_exceptions=True)` and converted
into an error `ToolResult`:

```python
ToolResult(
    tool_call_id=tc_id,
    content=f"Tool execution failed: {error}",
    success=False,
    error=str(error),
)
```

This ensures that:
1. Other tools still complete successfully.
2. The LLM receives error information and can decide how to proceed.
3. No unhandled exceptions propagate up to the agent loop.

## Message Format

After parallel execution, each tool result is appended as a separate `tool`
message in the conversation, matching the OpenAI/Anthropic API contract:

```python
# Assistant message references all tool calls
messages.append({
    "role": "assistant",
    "content": llm_response.content,
    "tool_calls": [tc1, tc2, tc3],
})
# Each tool result is a separate message
messages.append({"role": "tool", "content": result1.content, "tool_call_id": "tc1"})
messages.append({"role": "tool", "content": result2.content, "tool_call_id": "tc2"})
messages.append({"role": "tool", "content": result3.content, "tool_call_id": "tc3"})
```
