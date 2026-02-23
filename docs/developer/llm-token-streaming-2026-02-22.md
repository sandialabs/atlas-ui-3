# LLM Token Streaming

Last updated: 2026-02-22

Token streaming delivers LLM responses progressively to the user, showing text as it is generated rather than waiting for the full response.

## Architecture

```
LLM Provider (OpenAI/Anthropic/Gemini)
    |  async generator yields str chunks
    v
LiteLLMStreamingMixin (litellm_streaming.py)
    |  stream_plain / stream_with_tools / stream_with_rag / stream_with_rag_and_tools
    v
Mode Runner (plain.py / rag.py / tools.py)
    |  calls stream_and_accumulate() or iterates generator directly
    v
EventPublisher.publish_token_stream(token, is_first, is_last)
    |  sends WebSocket JSON: { type: "token_stream", token, is_first, is_last }
    v
Frontend websocketHandlers.js
    |  buffers tokens in _tokenBuffer, flushes via setTimeout(30ms)
    v
useMessages reducer: STREAM_TOKEN / STREAM_END actions
    |  appends to message with _streaming flag
    v
Message.jsx renders progressive text with blinking cursor
```

## Backend

### Streaming Mixin

`atlas/modules/llm/litellm_streaming.py` provides `LiteLLMStreamingMixin`, mixed into `LiteLLMCaller`. Four async generators:

- **`stream_plain`** - Text-only streaming. Yields `str` chunks from `acompletion(stream=True)`.
- **`stream_with_tools`** - Yields `str` chunks for text, accumulates tool call fragments across chunks, then yields a final `LLMResponse` with the complete tool calls.
- **`stream_with_rag`** - Runs RAG query (non-streaming), then delegates to `stream_plain` with augmented messages.
- **`stream_with_rag_and_tools`** - Runs RAG query (non-streaming), then delegates to `stream_with_tools`.

Backpressure: `asyncio.sleep(0)` every 50 chunks yields control to prevent event loop starvation.

### Mode Runners

Each mode runner has a `run_streaming()` method:

- **PlainModeRunner** (`plain.py`): Uses `stream_and_accumulate()` helper.
- **RAGModeRunner** (`rag.py`): Uses `stream_and_accumulate()` helper.
- **ToolsModeRunner** (`tools.py`): Streams initial LLM call; if tool calls are returned, executes tools then streams a synthesis response.

### Shared Helpers

- **`stream_and_accumulate()`** in `streaming_helpers.py`: Iterates a token generator, publishes each token via `EventPublisher`, accumulates content, and falls back to a non-streaming call on error.
- **`stream_final_answer()`** in `streaming_final_answer.py`: Used by agent loops (ReAct, Think-Act, Act) to stream the final answer after tool execution.

### Error Handling

Streaming errors are classified by `error_handler.classify_llm_error()` into user-friendly messages (rate limit, auth, timeout, generic). On failure:
1. `is_last=True` is always sent to prevent stuck UI cursors.
2. Falls back to non-streaming call if possible.
3. Error message is sent via `{ type: "error" }` WebSocket event.

## Frontend

### Token Buffering (websocketHandlers.js)

Module-level state manages the stream lifecycle:

```
_tokenBuffer: string   - accumulated tokens not yet flushed
_tokenFlushTimer: id   - setTimeout handle for next flush
_streamActive: boolean - whether a stream is in progress
```

The `token_stream` handler:
1. On `is_first`: resets buffer and timer, sets `_streamActive = true`, clears thinking state.
2. Appends `token` to `_tokenBuffer`.
3. Schedules a flush via `setTimeout(FLUSH_INTERVAL_MS)` (30ms = ~33fps).
4. On `is_last`: flushes immediately and calls `streamEnd()`.

The 30ms interval batches rapid token arrivals into fewer React re-renders for smooth display.

**Important**: Do NOT use `requestAnimationFrame` for token flushing. It was tested and breaks progressive rendering because rAF callbacks are suppressed when the tab is inactive or during heavy DOM updates.

### Message Reducer (useMessages.js)

- **`STREAM_TOKEN`**: Uses `findLastIndex(m => m._streaming)` to find the correct message to append to, handling cases where tool messages interleave with streaming.
- **`STREAM_END`**: Removes the `_streaming` flag from the last streaming message.

### Cleanup

`cleanupStreamState()` clears the buffer and timer. It is exported for use during unmount scenarios but must NOT be called from React effect cleanups that run on dependency changes (only on true unmount), as it would kill active streams.

## WebSocket Protocol

### `token_stream` Event

```json
{
  "type": "token_stream",
  "token": "Hello",
  "is_first": true,
  "is_last": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| `token` | string | Text chunk (may be empty on `is_last`) |
| `is_first` | boolean | First token in a new stream |
| `is_last` | boolean | Final event; triggers stream cleanup |

### Lifecycle

1. Backend sends `is_first: true` with the first token.
2. Backend sends subsequent tokens with both flags false.
3. Backend sends `is_last: true` (token may be empty) to signal completion.
4. Backend sends `response_complete` after all processing is done.

If an error occurs mid-stream, `is_last: true` is always sent before the error event to prevent the UI from getting stuck with a blinking cursor.

## Configuration

Token streaming is enabled by default in `llmconfig.yml` via the `streaming` flag on model entries:

```yaml
models:
  - name: gpt-4o
    streaming: true   # enables token streaming (default: true)
```

When `streaming: false`, the mode runner falls back to `run()` (non-streaming) instead of `run_streaming()`.

## Testing

Backend tests: `atlas/tests/test_streaming_token_flow.py` (15+ tests covering helpers, error propagation, publisher contracts, tool call dict conversion).

Frontend tests:
- `frontend/src/handlers/chat/websocketHandlers.test.js` (7 streaming tests with fake timers)
- `frontend/src/test/streaming-reducer.test.js` (interleaving and stream-end tests)
