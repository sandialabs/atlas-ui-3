# Mock Security Check Service

A minimal FastAPI app that implements the security check API contract used by `SecurityCheckService` for local testing.

## API

### `POST /check`

#### Request body

```json
{
  "content": "...",
  "check_type": "input" | "output" | "tool_rag_tool" | "tool_rag_rag",
  "username": "user@example.com",
  "message_history": [{"role": "user", "content": "..."}]
}
```

**Check Types:**
- `input`: User input before sending to LLM
- `output`: LLM response before showing to user  
- `tool_rag_tool`: Tool output before sending to LLM
- `tool_rag_rag`: RAG retrieval results before sending to LLM

#### Response body

The mock returns deterministic responses based on `content`:

- Contains `"block-me"` or `"bomb"` →
  - `status`: `"blocked"`
  - `message`: explains it was blocked
  - Prints to console for visibility
- Contains `"warn-me"` →
  - `status`: `"allowed-with-warnings"`
  - `message`: explains it was warned
  - Prints to console for visibility
- Otherwise →
  - `status`: `"good"`

If the `Authorization` header is missing or not a `Bearer` token, the mock still returns `allowed-with-warnings` so you can test auth handling without breaking flows.

### `POST /check2` (probabilistic)

This endpoint behaves like `/check` for auth handling, but the decision to flag content is probabilistic so you can simulate occasional moderation hits.

By default:
- With probability `0.2` (1 in 5 requests), the content is *flagged*.
- Of flagged requests, `0.5` are `blocked` and `0.5` are `allowed-with-warnings`.
- Otherwise, the response is `good`.

You can tune this via environment variables:

```bash
# Overall chance that a request is flagged (0.0–1.0)
SECURITY_MOCK_FLAG_PROB=0.2

# Fraction of flagged requests that are blocked (0.0–1.0)
SECURITY_MOCK_BLOCK_FRACTION=0.5
```

Example call:

```bash
curl -X POST "http://localhost:8089/check2" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-key" \
  -d '{"content": "some text", "check_type": "input", "username": "me@example.com", "message_history": []}'
```

## Running locally

From the repo root (after creating/activating the uv venv):

```bash
cd mocks/security_check_mock
chmod +x run.sh
./run.sh
```

Then configure the backend to use it, for example in `.env`:

```bash
FEATURE_SECURITY_CHECK_INPUT_ENABLED=true
FEATURE_SECURITY_CHECK_OUTPUT_ENABLED=true
FEATURE_SECURITY_CHECK_TOOL_RAG_ENABLED=true
SECURITY_CHECK_API_URL=http://localhost:8089/check
SECURITY_CHECK_API_KEY=test-key
```

You can exercise it directly with curl:

```bash
# Test input blocking
curl -X POST "http://localhost:8089/check" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-key" \
  -d '{"content": "please block-me", "check_type": "input", "username": "me@example.com", "message_history": []}'

# Test tool output warning
curl -X POST "http://localhost:8089/check" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-key" \
  -d '{"content": "warn-me about this tool output", "check_type": "tool_rag_tool", "username": "me@example.com", "message_history": []}'

# Test RAG output blocking  
curl -X POST "http://localhost:8089/check" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-key" \
  -d '{"content": "Retrieved document contains bomb instructions", "check_type": "tool_rag_rag", "username": "me@example.com", "message_history": []}'
```