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

## Manual Testing

Parallel tool calling is model-dependent -- the LLM decides whether to return
multiple tool calls in one response. GPT-4o and Claude are the most likely to
do so. Below are prompts and tool combinations that reliably trigger it.

### Calculator (default config, easiest)

The `calculator` server is in the default `mcp.json`. Select it, enable agent
mode (any strategy; `act` is fastest), and send:

> "Calculate these three things: (1) 355/113, (2) sqrt(2), (3) e^pi"

The LLM should return three `calculator_evaluate` calls in one response.

### DuckDuckGo search (add to config)

Merge `atlas/config/mcp-example-configs/mcp-duckduckgo.json` into your
`config/mcp.json`. Web searches are inherently independent, so prompts like
this reliably trigger parallel calls:

> "Search for Python 3.13 new features, Rust 2024 edition changes, and Go 1.22
> release notes"

### Calculator + PPTX generator (default config)

Both are in the default config. This tests parallel calls across different
servers:

> "I need two things at once: calculate 2^64 and create a one-slide PowerPoint
> about parallel computing"

### Order database + CSV reporter

Add both from `mcp-example-configs/`. A realistic data workflow:

> "Look up all orders for customer 'Acme Corp' and also get the total revenue
> summary"

### Verifying parallelism

Check the backend logs for:

```
INFO atlas...tool_executor - Executing 3 tool calls in parallel
```

If you only see individual `execute_single_tool` entries without that log line,
the LLM returned one tool call per response. Try a different model or a more
explicit prompt like "do all of these at the same time".

### Model behavior notes

| Model | Parallel tool calling |
|-------|----------------------|
| GPT-4o | Frequently, especially with independent tasks |
| Claude 3.5/4 | Frequently with explicit multi-part requests |
| Gemini 1.5/2 | Sometimes, less aggressive than GPT-4o |
| Smaller/local models | Rarely -- most return one tool at a time |
