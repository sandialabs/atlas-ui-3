# Mock Security Check Service

A minimal FastAPI app that implements the security check API contract used by `SecurityCheckService` for local testing.

## API

### `POST /check`

#### Request body

```json
{
  "content": "...",
  "check_type": "input" | "output",
  "username": "user@example.com",
  "message_history": [{"role": "user", "content": "..."}]
}
```

#### Response body

The mock returns deterministic responses based on `content`:

- Contains `"block-me"` →
  - `status`: `"blocked"`
  - `message`: explains it was blocked
- Contains `"warn-me"` →
  - `status`: `"allowed-with-warnings"`
  - `message`: explains it was warned
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
uvicorn app:app --host 0.0.0.0 --port 8089
```

Then configure the backend to use it, for example in `.env`:

```bash
FEATURE_SECURITY_CHECK_INPUT_ENABLED=true
FEATURE_SECURITY_CHECK_OUTPUT_ENABLED=true
SECURITY_CHECK_API_URL=http://localhost:8089/check
SECURITY_CHECK_API_KEY=test-key
```

You can exercise it directly with curl:

```bash
curl -X POST "http://localhost:8089/check" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-key" \
  -d '{"content": "please block-me", "check_type": "input", "username": "me@example.com", "message_history": []}'
```