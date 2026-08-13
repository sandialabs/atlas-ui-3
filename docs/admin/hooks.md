# Hook system (config-driven lifecycle hooks)

Atlas supports an **opt-in, config-driven hook system** modeled on Claude Code
and OpenCode: an operator writes a bash or Python script, registers it in
`config/hooks.json` against a lifecycle event, and Atlas runs it as a
subprocess at that point in the turn. The event payload arrives as **JSON on
stdin**; the script's **JSON on stdout** (plus its exit code) can allow,
modify, block, or escalate the operation.

This is the substrate for governed controls (audit, redaction, approval gates,
policy enforcement) without scattering one-off checks through the core loop.
See [GH #713](https://github.com/sandialabs/atlas-ui-3/issues/713) for the
design proposal.

> Hooks are **operator-installed code running with server privileges** — the
> same trust level as `config/mcp.json` entries. Write access to
> `config/hooks.json` is equivalent to code execution as the server user.
> Untrusted hooks are out of scope.

## Quick start

1. Create `config/hooks.json` (next to your `mcp.json`, in the user config dir
   resolved by `APP_CONFIG_DIR`, default `config/`):

   ```json
   {
     "hooks": {
       "PreToolUse": [
         {
           "name": "block-destructive-fs",
           "matcher": "filesystem__.*",
           "command": ["${ATLAS_CONFIG_DIR}/hooks/block_destructive.py"],
           "timeout_ms": 2000
         }
       ]
     }
   }
   ```

2. Put the script at `config/hooks/block_destructive.py` and make it executable.

3. Restart Atlas (config is loaded through `ConfigManager`, so hook changes
   follow the same reload story as the rest of Atlas config).

With no `config/hooks.json`, the hook system is **completely disabled** — no
subprocess is ever spawned and the hot path pays only a cached-property lookup.
This is asserted by the test suite (`TestNoConfigInvariant`).

## Configuration reference

Each hook entry under `hooks.<EventName>` is:

| Field        | Type                | Default | Description                                                                 |
| ------------ | ------------------- | ------- | -------------------------------------------------------------------------- |
| `name`       | string (required)   | —       | Identifier used in logs/audit spans.                                       |
| `matcher`    | string (regex)      | `"*"`   | Regex over the event's matcher value (tool name / RAG source). `*`/omit = all. |
| `command`    | string[] (required) | —       | argv array, spawned with **no shell**. Supports `${ATLAS_CONFIG_DIR}` and `${ATLAS_PROJECT_DIR}` interpolation. |
| `timeout_ms` | int                 | `2000`  | Wall-clock budget; on expiry the process is killed and `on_error` applies. |
| `on_error`   | `"deny"` / `"allow"` | per-event default | Outcome when the hook crashes, times out, or emits malformed output. |

`on_error` defaults are **per-event** (see table below) and can be overridden
per-hook. Security/interceptor events fail **closed** (`deny`): a crashing hook
must not silently weaken a boundary. Observability/lifecycle events fail
**open** (`allow`): a broken audit hook should not take down the chat turn.

Multiple hooks per event run **sequentially in config order**, each seeing the
previous hook's `modify` output. The first `deny` short-circuits the chain; a
`require_approval` is sticky and cannot be downgraded by a later hook.

## Events

| Event               | Fires at                                          | Hook can                                                                 | on_error default |
| ------------------- | ------------------------------------------------- | ------------------------------------------------------------------------ | ---------------- |
| `SessionStart`      | `ChatService.create_session`                      | Attach session metadata (`modify`); reject the session (`deny`).         | `allow`          |
| `UserPromptSubmit`  | `ChatOrchestrator.execute`, after user msg added  | Rewrite/redact the prompt; narrow tools/sources; block the turn (`deny`). | `allow`       |
| `PreLlmCall`        | `call_plain` / `call_with_tools` (+ streaming)    | Inspect/redact outgoing messages; swap the model; block the call.       | `deny`           |
| `PreToolUse` ⭐     | `execute_single_tool`, before invoke              | Mutate tool args (re-injected with security params after); deny; force approval. | `deny`     |
| `PostToolUse`       | `execute_single_tool`, after invoke              | Transform/redact the result; annotate; deny (replace with error).       | `allow`          |
| `PermissionRequest` | `execute_single_tool`, at the approval gate      | Auto-approve / auto-deny / escalate to a human; add audit reason.       | `deny`           |
| `RagCall`          | `UnifiedRAGService.query_rag(_batch)` + agentic   | Rewrite the query; narrow sources (batch, cannot widen); block retrieval. | `deny`        |
| `RagResponse`      | after retrieval, before prompt injection         | Redact/replace synthesized content; filter chunks; block (empty result). | `allow`        |
| `SessionEnd`        | `ChatService.end_session`                         | Flush audit records; notify. (Cannot block; deny is treated as continue.) | `allow`       |

⭐ **PreToolUse** is the most powerful control point: every tool call — tools
mode *and* the agentic loop — passes through `execute_single_tool`, so one hook
uniformly governs arguments, denial, and approval escalation.

## The contract

### stdin (envelope)

A JSON object with a stable envelope plus an event-specific `payload`:

```json
{
  "hook_event_name": "PreToolUse",
  "session_id": "...",
  "user_email": "user@example.gov",
  "compliance_level": 3,
  "payload": {
    "tool_name": "filesystem__write_file",
    "tool_args": { "path": "/etc/passwd", "content": "..." },
    "tool_call_id": "call-1"
  }
}
```

`user_email` and `compliance_level` are **informational** — they are stamped
server-side and re-asserted after the hook returns regardless of what the hook
says (a hook can never widen or spoof identity/compliance).

### Exit codes (the fast path)

| Exit code | Meaning                                                                 |
| --------- | ----------------------------------------------------------------------- |
| `0`       | Continue. If stdout is non-empty JSON, apply it; if empty, no change.  |
| `2`       | **Block.** stderr is surfaced as the reason.                            |
| other     | Error. Handled per `on_error` (`deny` or `allow`); always logged/audited. |

The simplest useful hook is three lines of bash with no JSON emitted at all.

### stdout (the structured path)

```json
{ "decision": "modify", "payload": { "tool_args": { "path": "/tmp/safe.txt" } } }
{ "decision": "deny", "reason": "Writes outside /workspace are blocked by policy" }
{ "decision": "require_approval", "reason": "Cross-domain network call" }
{ "decision": "continue" }
```

- **`continue`** — no change (also the meaning of exit 0 with empty stdout).
- **`modify`** — replace the mutable part of the payload (prompt text, tool
  args, tool result, LLM messages, RAG query/content). Core re-applies
  security-critical invariants after (e.g. re-injects `_atlas_user` into tool
  args) and validates against the original schema.
- **`deny`** — short-circuit. For tools this becomes an error `ToolResult` so
  the loop continues gracefully; for prompt/LLM events the reason surfaces to
  the user.
- **`require_approval`** — (PreToolUse / PermissionRequest) force the runtime
  approval gate even if it would have passed. Sticky across hooks.

## Security & governance

- A hook can **tighten but never widen** a boundary. Server-side identity
  (`user_email`) and trusted `compliance_level` are re-injected after any
  `modify`. For tool args, `_atlas_user` and other security-critical parameters
  are re-applied — the same rule already used for user-edited args in
  `tool_executor.py`.
- `modify` on `RagCall` (batch) may **narrow** the source list; sources the
  hook lists that were not in the original request are dropped. A hook can
  never add a source outside the compliance-filtered allow-list.
- `PermissionRequest` may auto-approve (`modify` with `needs_approval: false`)
  or escalate (`require_approval`). `PreToolUse` may escalate but **cannot
  auto-approve** — a hook may never lower a boundary another hook raised.
- Every hook invocation emits an OpenTelemetry span (`hook.event`) with the
  event name, verdict, exit code, and duration (no raw prompts/args/outputs —
  see the telemetry sensitive-data policy). The audit trail flows through the
  existing OTel pipeline.
- **Environment allow-list**: hooks get `PATH`, `HOME`, `LANG`, `SYSTEMROOT`,
  `USER`, plus `ATLAS_CONFIG_DIR` and `ATLAS_PROJECT_DIR`. They do **not**
  inherit the server's full environment (which holds provider API keys).
- **Bounded output**: stdout/stderr are capped at 1 MB; overflow is treated as
  a hook error (handled per `on_error`).

## Example hooks

### Block destructive filesystem writes (bash)

```bash
#!/usr/bin/env bash
# config/hooks/block_destructive.sh  (PreToolUse, matcher "filesystem__.*")
read -r envelope
path=$(echo "$envelope" | jq -r '.payload.tool_args.path // empty')
case "$path" in
  /etc/*|/var/*|/usr/*|/root/*)
    echo "Writes to $path are blocked by policy" >&2
    exit 2
    ;;
esac
exit 0
```

### Redact PII from prompts (python)

```python
#!/usr/bin/env python3
# config/hooks/redact_pii.py  (UserPromptSubmit)
import json, re, sys
env = json.load(sys.stdin)
prompt = env["payload"]["prompt"]
redacted = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED-SSN]", prompt)
if redacted != prompt:
    print(json.dumps({"decision": "modify", "payload": {"prompt": redacted}}))
sys.exit(0)
```

### Force approval for cross-domain network tools (python)

```python
#!/usr/bin/env python3
# config/hooks/require_approval_network.py  (PreToolUse)
import json, sys
env = json.load(sys.stdin)
if env["payload"]["tool_name"].startswith("http_") or env["payload"]["tool_name"].startswith("fetch_"):
    print(json.dumps({"decision": "require_approval", "reason": "Cross-domain network call requires approval"}))
sys.exit(0)
```

### Audit every tool call (bash, fire-and-forget style)

```bash
#!/usr/bin/env bash
# config/hooks/audit.sh  (PostToolUse, on_error: allow)
read -r envelope
echo "$envelope" >> "${ATLAS_PROJECT_DIR}/logs/tool-audit.jsonl"
exit 0
```

## Relationship to existing extension points

- **MCP tools** are how the model invokes external capabilities. Hooks are how
  an operator governs those invocations at lifecycle boundaries. They
  complement, not replace, MCP.
- The **`EventPublisher`** is the outbound UI event sink; hooks are the
  *inbound/interceptor* counterpart.
- Hooks are **not** a sandbox and **not** a marketplace. A hook is
  operator-installed code with server privileges.