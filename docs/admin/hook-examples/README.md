# Hook examples: one per lifecycle event

Runnable, deliberately small example hooks — one for each of the nine events in
the [lifecycle hook system](../hooks.md). Each script's docstring/header states
the event, the matcher it expects, the exact stdin payload shape, which
decisions the event honors, and its `on_error` default, so a script can be read
on its own without cross-referencing the reference doc.

These are **teaching examples**, not a policy baseline. Read one, copy it, and
replace the rule with yours.

| Event | Example | Demonstrates |
| ----- | ------- | ------------ |
| `SessionStart` | [session_start.py](session_start.py) | `modify` to attach session metadata; `deny` to reject a session before it is persisted |
| `UserPromptSubmit` | [user_prompt_submit.py](user_prompt_submit.py) | Redact secrets out of the prompt and **narrow** the turn's tool selection |
| `PreLlmCall` | [pre_llm_call.py](pre_llm_call.py) | Swap the model for restricted turns (re-authorized server-side) |
| `PermissionRequest` | [permission_request.py](permission_request.py) | Auto-approve read-only tools; escalate outbound ones |
| `PreToolUse` | [pre_tool_use.sh](pre_tool_use.sh) | The three-line exit-code contract: exit `2` blocks with stderr as the reason |
| `PostToolUse` | [post_tool_use.py](post_tool_use.py) | Redact secrets out of a tool result before the model sees it |
| `RagCall` | [rag_call.py](rag_call.py) | Narrow the source list before retrieval (never widen); `deny` when nothing is left |
| `RagResponse` | [rag_response.py](rag_response.py) | Rewrite retrieved `content` before prompt injection |
| `SessionEnd` | [session_end.sh](session_end.sh) | Fire-and-forget audit record; cannot block |

[hooks.json](hooks.json) registers all nine at once — useful as a shape
reference. In practice, enable one event at a time.

## Trying them out

```bash
# 1. Install the scripts where the config interpolation points.
cp docs/admin/hook-examples/*.py docs/admin/hook-examples/*.sh config/hooks/
chmod +x config/hooks/*
cp docs/admin/hook-examples/hooks.json config/hooks.json

# 2. Restart Atlas (hooks load through ConfigManager).
bash agent_start.sh
```

Because every hook is a plain stdin→stdout program, you can also exercise one
without starting Atlas at all:

```bash
echo '{"hook_event_name":"PreToolUse","user_email":"a@b.gov","payload":
  {"tool_name":"filesystem__write_file","tool_args":{"path":"/etc/passwd"}}}' \
  | docs/admin/hook-examples/pre_tool_use.sh; echo "exit=$?"
# -> Policy: file tools may only touch /workspace (got: /etc/passwd)
# -> exit=2
```

## Reminders that bite

- **Hooks are operator-installed code with server privileges.** Write access to
  `config/hooks.json` is equivalent to code execution as the server user.
- **`PreToolUse`, `PermissionRequest`, `PreLlmCall`, and `RagCall` fail closed.**
  A script that is missing, non-executable, or slow denies the operation. That
  is the point — but it means a typo is an outage, not a warning.
- **A hook can tighten a boundary, never widen one.** Identity, compliance
  level, and security-critical tool arguments are re-asserted after any
  `modify`; tool/source lists are intersected with the user's own selection.
- **Envelopes are secret-bearing.** Log projections (names, keys, lengths), not
  raw payloads.

For a policy that lives outside the scripts, see the
[zero-trust mock policy server](../../../mocks/zero-trust-mock/README.md): a
single hook forwards each envelope to an HTTP service that decides allow /
require approval / deny.
