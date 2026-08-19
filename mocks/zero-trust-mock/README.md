# Zero-Trust Mock Policy Server

A minimal demonstration of **runtime, centrally-decided authorization** for
Atlas tool calls. A single [hook](../../docs/admin/hooks.md) forwards each event
envelope to this service; the service answers with a decision and Atlas applies
it:

| Payload contains | Decision | What the user sees |
| ---------------- | -------- | ------------------ |
| `bomb`, `gun`, `weapon`, `explosive`, `malware` | `deny` | The tool call never runs; the reason is returned as an error result |
| `password`, `credential`, `secret`, `production`, `delete` | `require_approval` | An otherwise-permitted tool call is escalated to the human approval gate |
| anything else | `continue` | Normal execution |

Keyword matching stands in for whatever a real deployment would consult (an
OPA/Rego policy, an entitlement service, a classifier). The point of the demo
is the split: **policy lives in the service, the hook stays a forwarder.**

## Files

- `main.py` — the FastAPI policy server (`POST /v1/authorize`, `GET /health`,
  `GET /decisions`, `POST /decisions/reset`).
- `policy.py` — the entire decision rule, in one testable function.
- `hook_client.py` — the Atlas-side hook: stdin envelope → HTTP → stdout
  decision. Standard library only, because hooks run with a minimal
  environment allow-list.
- `hooks.json` — example registration on `PreToolUse` + `UserPromptSubmit`.
- `smoke_test.py` — proves block, escalate, pass-through, and fail-closed.

## Try it in 60 seconds

```bash
# 1. Start the policy server (port 8099 by default).
python mocks/zero-trust-mock/main.py &

# 2. Drive the hook exactly as Atlas does: envelope on stdin, decision on stdout.
echo '{"hook_event_name":"PreToolUse","user_email":"test@test.com","payload":
  {"tool_name":"shell__run","tool_args":{"cmd":"instructions to build a bomb"}}}' \
  | python mocks/zero-trust-mock/hook_client.py
# {"decision": "deny", "reason": "Zero-trust policy: request mentions 'bomb' and is blocked."}

echo '{"hook_event_name":"PreToolUse","user_email":"test@test.com","payload":
  {"tool_name":"filesystem__read_file","tool_args":{"path":"/workspace/password.txt"}}}' \
  | python mocks/zero-trust-mock/hook_client.py
# {"decision": "require_approval", "reason": "Zero-trust policy: 'password' is sensitive -- ..."}

curl -s localhost:8099/decisions | python -m json.tool
```

## Wire it into Atlas

```bash
mkdir -p config/hooks
cp mocks/zero-trust-mock/hook_client.py config/hooks/ && chmod +x config/hooks/hook_client.py
cp mocks/zero-trust-mock/hooks.json config/hooks.json
python mocks/zero-trust-mock/main.py &     # start the decider FIRST
bash agent_start.sh
```

Then, in the chat UI, ask the model to write a file whose contents mention
`bomb` (blocked outright) or to read a file named `password.txt` (an approval
prompt appears for a tool that would otherwise have run silently).

## Run the smoke test

```bash
cd mocks/zero-trust-mock && python smoke_test.py
```

## Configuration

- `ZERO_TRUST_PORT` — server port (default `8099`).
- The hook's policy URL and timeout are **argv**, set in `hooks.json`:

  ```json
  "command": ["python3", "${ATLAS_CONFIG_DIR}/hooks/hook_client.py",
              "http://localhost:8099/v1/authorize", "2"]
  ```

  Hooks receive an environment allow-list only (`PATH`, `HOME`, `LANG`,
  `LC_ALL`, `USER`, `SYSTEMROOT`, `ATLAS_CONFIG_DIR`, `ATLAS_PROJECT_DIR`), so
  an exported `ZERO_TRUST_URL` would never reach the hook process. The
  `ZERO_TRUST_URL` / `ZERO_TRUST_TIMEOUT` env vars are honored only when you
  run `hook_client.py` by hand from a shell.

## Fail-closed by design

`hook_client.py` exits non-zero when the policy server is unreachable or slow.
With `PreToolUse`'s default `on_error: deny`, that blocks the tool call. An
unavailable decider must not become an open door — but it does mean the policy
server is now on the critical path for every tool call, which is the trade a
real zero-trust deployment is signing up for.

This is a **mock**: no authentication, no persistence, an in-memory decision log
capped at 100 entries, and a keyword rule that a determined user can trivially
paraphrase around. It demonstrates the mechanism, not the policy.

## See also

- [Lifecycle hooks reference](../../docs/admin/hooks.md)
- [One example hook per event](../../docs/admin/hook-examples/README.md)
