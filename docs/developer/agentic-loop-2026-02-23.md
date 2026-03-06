# Agentic Loop Strategy

Last updated: 2026-02-23

## Overview

The `agentic` agent loop strategy mirrors how Claude Code and Claude Desktop drive tool-use loops. It uses zero control tools and `tool_choice="auto"`, trusting the model to manage its own control flow. When the model responds with text only (no tool calls), the loop is done.

This is the simplest and most token-efficient strategy. It produces 1 LLM call per step, compared to 3 for the ReAct strategy (Reason + Act + Observe).

## Configuration

```bash
# In .env or environment
APP_AGENT_LOOP_STRATEGY=agentic
```

Or via the `AGENT_LOOP_STRATEGY` alias (both are accepted by Pydantic's `AliasChoices`).

## How It Works

```
while steps < max_steps:
    response = llm.call_with_tools(messages, tools, tool_choice="auto")

    if no tool_calls in response:
        return response.content  # Done

    execute all tool_calls in parallel
    append assistant + tool result messages
    loop back

# Max steps exhausted: call llm.call_plain for synthesis
```

Key behaviors:

- **No control tools**: Unlike `react` (`agent_decide_next`, `agent_observe_decide`), `think-act` (`agent_think`), and `act` (`finished`), the agentic loop injects no scaffolding tools into the schema. The model sees only the real user tools.
- **`tool_choice="auto"`**: The model naturally decides between calling tools and responding with text. Other strategies use `tool_choice="required"` which forces a tool call even when the model wants to answer directly.
- **Text-only response = done**: The simplest possible completion signal. No JSON parsing, no control tool extraction, no fallback heuristics.
- **Parallel tool execution**: When the model returns multiple tool calls in one response, all execute concurrently via `asyncio.gather` (shared `execute_multiple_tools` from PR #358).
- **Streaming support**: When streaming is enabled, text tokens are published to the UI as they arrive. Tool call responses are handled non-streaming (same as other loops).

## When to Use Each Strategy

| Strategy | Best For | LLM Calls/Step | Control Tools |
|----------|----------|----------------|---------------|
| `agentic` | Anthropic models (Claude), simple tool workflows | 1 | None |
| `react` | OpenAI models, complex multi-step reasoning with explicit structure | 3 | `agent_decide_next`, `agent_observe_decide` |
| `think-act` | Deep reasoning tasks, complex problem solving | 2 | `agent_think` |
| `act` | Fast tool execution with minimal overhead | 1 | `finished` |

**Use `agentic` when:**
- You are using Anthropic models (Claude 3.5/4/4.5) where native tool-use training makes external scaffolding counterproductive
- You want the lowest latency and cost per agent step
- You want the model to naturally integrate reasoning into its tool-use flow

**Use other strategies when:**
- You need explicit structured reasoning visible in the UI (ReAct's Reason/Observe phases)
- You are using models that benefit from forced tool calling (`tool_choice="required"`)
- You need the model to call a specific control tool to signal completion (Act's `finished`)

## Architecture

The implementation lives in `atlas/application/chat/agent/agentic_loop.py` and follows the same patterns as the other loop strategies:

- Implements `AgentLoopProtocol` from `atlas/application/chat/agent/protocols.py`
- Registered in `AgentLoopFactory` at `atlas/application/chat/agent/factory.py`
- Uses `tool_executor.execute_multiple_tools` for parallel tool execution
- Uses `stream_final_answer` for streaming the max-steps fallback
- Emits standard `AgentEvent`s (`agent_start`, `agent_turn_start`, `agent_tool_results`, `agent_completion`)

## File Reference

- Implementation: `atlas/application/chat/agent/agentic_loop.py`
- Tests: `atlas/tests/test_agentic_loop.py` (14 tests)
- Factory: `atlas/application/chat/agent/factory.py`
- Config: `atlas/modules/config/config_manager.py` (`agent_loop_strategy` field)
