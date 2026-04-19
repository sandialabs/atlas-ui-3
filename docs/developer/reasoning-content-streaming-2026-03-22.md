# Reasoning Content Streaming

Last updated: 2026-03-22

Some LLM models (e.g. OpenAI o-series, Qwen3, GPT-OSS via vLLM) produce chain-of-thought "reasoning" before their final answer. This document describes how Atlas captures and displays reasoning content in both streaming and non-streaming modes.

## Architecture

```
LLM Provider (vLLM, OpenRouter, OpenAI)
    |  streaming SSE chunks with delta.reasoning / delta.reasoning_content
    v
LiteLLM monkey-patch (litellm_caller.py)
    |  maps delta.reasoning → delta.reasoning_content (see Known Issues below)
    v
LiteLLMStreamingMixin (litellm_streaming.py)
    |  yields ReasoningToken per chunk, then ReasoningBlock with full text
    v
Mode Runner (plain.py / tools.py / agentic_loop.py)
    |  stream_and_accumulate() or direct iteration
    |  publishes reasoning_token and reasoning_content WebSocket events
    v
Frontend websocketHandlers.js
    |  buffers reasoning tokens, flushes via setTimeout(30ms)
    |  dispatches STREAM_REASONING_TOKEN / STREAM_REASONING_END actions
    v
useMessages reducer
    |  accumulates reasoning_content on message, sets _reasoningStreaming flag
    v
Message.jsx
    |  renders collapsible "Reasoning..." section
    |  auto-expands during streaming, auto-scrolls
```

## Data Flow

### Backend Models

- **`ReasoningToken`** (`atlas/modules/llm/models.py`): Emitted during streaming for each reasoning chunk. Enables real-time display.
- **`ReasoningBlock`** (`atlas/modules/llm/models.py`): Emitted once with the full accumulated reasoning text, before content tokens begin.

### Streaming Pipeline

1. `litellm_streaming.py` iterates over LiteLLM's async stream
2. For each chunk with `delta.reasoning_content`: yields `ReasoningToken(token=...)`
3. When the first content token arrives (or stream ends): yields `ReasoningBlock(content=full_reasoning)`
4. Then yields content string tokens as before

### WebSocket Events

| Event Type | Payload | Description |
|---|---|---|
| `reasoning_token` | `{ type, token }` | Individual reasoning chunk for real-time display |
| `reasoning_content` | `{ type, content }` | Final complete reasoning text |
| `token_stream` | `{ type, token, is_first, is_last }` | Regular content tokens (unchanged) |

### Frontend Handling

- `websocketHandlers.js`: Buffers reasoning tokens (30ms flush interval), clears `isSynthesizing`/`isThinking` state when reasoning arrives
- `useMessages.js`: `STREAM_REASONING_TOKEN` action accumulates reasoning on the message; `STREAM_REASONING_END` clears the streaming flag
- `Message.jsx`: Collapsible reasoning section auto-expands during streaming with a blinking cursor, collapses when done
- `ChatContext.jsx`: Persists `reasoning_content` in message metadata for local save/reload

### Mode-Specific Handling

- **Plain/RAG mode**: `stream_and_accumulate()` handles reasoning tokens and blocks generically
- **Tools mode** (`tools.py`): `run_streaming()` handles reasoning in the initial LLM stream and in the post-tool synthesis stream; both paths save reasoning to message metadata
- **Agent mode** (`agentic_loop.py`): Handles reasoning tokens in the per-step stream loop; `agent.py` persists reasoning in the final message metadata

## Known Issues

### LiteLLM Streaming Reasoning Patch

**File**: `atlas/modules/llm/litellm_caller.py`

LiteLLM (as of v1.81.x) does not correctly pass through the `reasoning` field from vLLM/SGLang streaming deltas. The upstream issue is:

- https://github.com/BerriAI/litellm/issues/20246

**Root cause**: vLLM sends `delta.reasoning` in SSE chunks, but LiteLLM's `Delta` Pydantic model only recognizes `reasoning_content`. The OpenAI SDK preserves `reasoning` as a Pydantic extra field, but LiteLLM's `CustomStreamWrapper` drops it during chunk conversion.

**Our workaround**: A monkey-patch in `litellm_caller.py` wraps `CustomStreamWrapper.__init__` to intercept the `completion_stream` and remap `delta.reasoning` → `delta.reasoning_content` on each chunk before LiteLLM processes it.

**When to remove**: Once LiteLLM merges a fix for issue #20246 and we upgrade. Search for `# Monkey-patch` in `litellm_caller.py`. The patch is self-contained and clearly delimited with comment blocks. To verify the upstream fix works, run:

```python
from litellm import acompletion
resp = await acompletion(
    model='openai/your-vllm-model',
    messages=[...],
    stream=True,
    api_base='http://localhost:8005/v1',
)
async for chunk in resp:
    delta = chunk.choices[0].delta
    rc = getattr(delta, 'reasoning_content', None)
    if rc:
        print(f'reasoning_content: {rc}')  # Should print reasoning tokens
```

If reasoning tokens appear without the patch applied, the upstream fix has landed and the patch can be removed.
