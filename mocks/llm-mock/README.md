# Mock LLM Server

An OpenAI-compatible mock LLM service for local testing. It implements
`/v1/chat/completions` (streaming and non-streaming), `/v1/models`, a health
check, and forced-error / per-user-auth helpers.

## Running

```bash
# Plain (no auth)
python mocks/llm-mock/main.py            # listens on :8002

# Require a Bearer token (any non-empty token is accepted)
bash mocks/llm-mock/run.sh               # MOCK_LLM_REQUIRE_AUTH=true
```

Environment:

- `MOCK_LLM_PORT` (default `8002`)
- `MOCK_LLM_REQUIRE_AUTH` (default `false`) — when true, requests must carry a
  Bearer token, and known test keys map to user names.

## Test-introspection endpoints

These exist purely so tests can verify what reached the endpoint:

- `GET  /test/last-request` — the most recent chat completion the mock received,
  including the exact `authorization_token` and `model` name. This is the
  **source of truth** for credential/routing E2E assertions.
- `POST /test/reset-log` — clear the recorded request log.
- `POST /test/force-error` — force the next N completions to fail with a given
  error type (`rate_limit`, `timeout`, `auth`, `server_error`,
  `context_window_exceeded`).

## E2E: configured API key wins over a conflicting `OPENAI_API_KEY` (PR #678)

`e2e_llm_api_key_test.py` drives the **real** Atlas LLM path
(`LiteLLMCaller.call_plain` → `litellm.acompletion`) against this mock to prove
the scenario from PR #678: a model whose name *looks* like OpenAI
(`openai/gpt5.4`) but is served by a private gateway must use its explicitly
configured key, and a conflicting `OPENAI_API_KEY` in the environment must be
ignored and left untouched.

```bash
bash mocks/llm-mock/run_e2e.sh
# or via the PR-validation harness:
bash test/run_pr_validation.sh 678
```

The driver sets `OPENAI_API_KEY` to a poisoned value, configures the model with
`api_key="${GATEWAY_API_KEY}"` and `model_url` pointing at this mock, makes a
real request, then reads `/test/last-request` and asserts:

- the mock received the **configured gateway key**, not `OPENAI_API_KEY`;
- the request reached the mock (so it went to the gateway URL, not real OpenAI);
- the OpenAI-looking model name (`gpt5.4`) arrived; and
- `OPENAI_API_KEY` in the environment was never mutated.

### Why no UI screenshots

PR #678 is a backend credential-plumbing change with no user-visible UI surface.
A chat screenshot would only show a reply from the mock — it could not show
*which* API key was used or *which* base URL was contacted. The mock's
`/test/last-request` log is the actual on-the-wire evidence and is deterministic
and machine-verifiable, so it is used in place of screenshots here.
