# Reasoning Content Streaming

Some LLMs (OpenAI o-series, Qwen3, GPT-OSS via vLLM) emit chain-of-thought
"reasoning" before their final answer. This document describes how Atlas
captures, streams, and displays that reasoning.

## Architecture

```
LLM provider (vLLM, OpenRouter, OpenAI)
    |  streaming SSE chunks with delta.reasoning / delta.reasoning_content
    v
LiteLLM monkey-patch (litellm_caller.py)
    |  maps delta.reasoning -> delta.reasoning_content (see Known Issues)
    v
LiteLLMStreamingMixin (litellm_streaming.py)
    |  yields ReasoningToken per chunk, then ReasoningBlock with the full text
    v
Mode runner (plain.py / rag.py / tools.py / agentic_loop.py)
    |  stream_and_accumulate() or direct iteration over the stream
    |  publishes reasoning_token and reasoning_content events
    v
Frontend websocketHandlers.js
    |  buffers reasoning tokens, flushes every 30ms
    |  dispatches STREAM_REASONING_TOKEN / STREAM_REASONING_END
    v
useMessages reducer
    |  accumulates reasoning_content, tracks the _reasoningStreaming flag
    v
Message.jsx
    |  collapsible "Reasoning" section, auto-expanded while streaming
```

## Backend models

Both live in `atlas/modules/llm/models.py`:

- **`ReasoningToken`** — one reasoning chunk, emitted per delta so the UI can
  render reasoning live.
- **`ReasoningBlock`** — emitted once with the full accumulated reasoning text,
  as soon as the first content token arrives (or at end of stream, if the model
  produced no content at all).

`LLMResponse.reasoning_content` carries the same text on non-streaming calls and
on the final `LLMResponse` yielded by `stream_with_tools`.

These markers are yielded *into the same stream* as content strings, so every
consumer of `stream_plain` / `stream_with_tools` must type-check items rather
than assume `str`. Concatenating a marker onto accumulated text raises.

## WebSocket events

| Event type | Payload | Description |
|---|---|---|
| `reasoning_token` | `{ type, token }` | One reasoning chunk, for live display |
| `reasoning_content` | `{ type, content }` | Final, authoritative full reasoning text |
| `chat_response` | `{ ..., reasoning_content? }` | Non-streamed replies carry reasoning inline |

`reasoning_content` is sent *in addition to* the individual tokens. The frontend
uses it to reconcile its incrementally-accumulated text, so tokens coalesced or
dropped during buffering can't leave the panel out of sync with the backend.

## Mode-specific handling

- **Plain / RAG** — `stream_and_accumulate()` handles the markers generically and
  returns `(content, reasoning_content)`.
- **Tools** — `run_streaming()` handles reasoning both in the initial tool-selection
  stream and in the post-tool synthesis stream; both persist to message metadata.
- **Agent** — `agentic_loop.py` forwards reasoning per step; `agent.py` persists the
  final step's reasoning into the assistant message metadata.

### Closing the stream after reasoning-only turns

When a model reasons and then goes straight to tool calls (no content tokens),
the mode runners still send a final `token_stream` with `is_last=True`. Without
it the reasoning message stays flagged `_streaming`, and the post-tool synthesis
appends to that stale bubble instead of starting a fresh one. Both `tools.py` and
`agentic_loop.py` track whether reasoning was sent for exactly this reason.

## Persistence

Reasoning is stored in assistant message metadata under `reasoning_content`.
On load, metadata is spread onto the message object; on local save,
`buildPersistedMessage()` in `frontend/src/utils/chatExport.js` folds it back
into metadata. Reasoning therefore survives a page reload.

## Known issues

### LiteLLM streaming reasoning patch

**File**: `atlas/modules/llm/litellm_caller.py`

LiteLLM does not pass through the `reasoning` field from vLLM/SGLang streaming
deltas. Upstream issue: https://github.com/BerriAI/litellm/issues/20246

**Root cause**: vLLM sends `delta.reasoning` in SSE chunks, but LiteLLM's `Delta`
model only recognizes `reasoning_content`. The OpenAI SDK preserves `reasoning`
as a Pydantic extra field, but LiteLLM's `CustomStreamWrapper` drops it during
chunk conversion, so reasoning-only chunks look empty.

**Workaround**: a monkey-patch wraps `CustomStreamWrapper.__init__` to intercept
`completion_stream` and remap `delta.reasoning` -> `delta.reasoning_content` on
each chunk before LiteLLM processes it. It only wraps async streams, reads the
field cheaply (no `model_dump()` per token), and is a no-op for providers that
already populate `reasoning_content`.

**When to remove**: once the upstream fix lands and we upgrade LiteLLM. Search
for `Monkey-patch` in `litellm_caller.py`; the block is self-delimited. To check
whether the fix has landed:

```python
from litellm import acompletion
resp = await acompletion(
    model='openai/your-vllm-model',
    messages=[...],
    stream=True,
    api_base='http://localhost:8005/v1',
)
async for chunk in resp:
    rc = getattr(chunk.choices[0].delta, 'reasoning_content', None)
    if rc:
        print(f'reasoning_content: {rc}')
```

If reasoning tokens appear with the patch removed, it can be deleted.
