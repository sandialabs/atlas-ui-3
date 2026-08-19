# Hook system (config-driven lifecycle hooks)

Atlas supports an **opt-in, config-driven hook system** modeled on the
lifecycle-hook pattern common to agent CLIs: an operator writes a bash or
Python script, registers it in
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
           "command": ["${ATLAS_CONFIG_DIR}/hooks/block_destructive.sh"],
           "timeout_ms": 2000
         }
       ]
     }
   }
   ```

2. Put the script at `config/hooks/block_destructive.sh` and make it executable.

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
| `matcher`    | string (regex)      | `"*"`   | Regex over the event's matcher value (tool name / RAG source). `*`/omit = all. Compiled at load time (an invalid regex is a config error). `SessionStart`/`SessionEnd`/`UserPromptSubmit` supply no matcher value, so setting one there means the hook **never fires** — a warning is logged at load. |
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
| `PreToolUse` (*)   | `execute_single_tool`, before invoke              | Mutate tool args (re-injected with security params after); deny; force approval. | `deny`     |
| `PostToolUse`       | `execute_single_tool`, after invoke              | Transform/redact the result; annotate; deny (replace with error).       | `allow`          |
| `PermissionRequest` | `execute_single_tool`, at the approval gate      | Auto-approve / auto-deny / escalate to a human; add audit reason.       | `deny`           |
| `RagCall`          | `UnifiedRAGService.query_rag(_batch)` + agentic   | Rewrite the query; narrow sources (batch, cannot widen); block retrieval. | `deny`        |
| `RagResponse`      | after retrieval, before prompt injection         | Redact/replace the synthesized `content`; block (empty result). Only `payload["content"]` is read back -- returned metadata is ignored. | `allow`        |
| `SessionEnd`        | `ChatService.end_session`                         | Flush audit records; notify. (Cannot block; deny is treated as continue.) | `allow`       |

(*) **PreToolUse** is the most powerful control point: every tool call — tools
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
  never add a source outside the compliance-filtered allow-list. The same rule
  applies to `UserPromptSubmit`: `selected_tools` / `selected_data_sources` are
  intersected with what the user actually selected, and an explicitly empty
  list means *none* (it is never re-widened to the original selection).
- `PermissionRequest` may auto-approve (`modify` with `needs_approval: false`)
  or escalate (`require_approval`). `PreToolUse` may escalate but **cannot
  auto-approve** — a hook may never lower a boundary another hook raised. If a
  `PreToolUse` hook rewrites the arguments after a `PermissionRequest`
  auto-approval, that approval is **revoked**: it covered the arguments the
  first hook inspected, not the ones that would now run.
- `require_approval` forces the approval gate but does **not** make the request
  admin-only; the tool's own `admin_required` setting stays authoritative, so a
  hook cannot produce a request the requesting user is unable to satisfy.
- A `PreLlmCall` hook that swaps `model` has the replacement re-checked against
  the same per-model group ACL the orchestrator applied to the original
  selection; an unauthorized swap is refused and the original model is kept.
- Every hook invocation emits an OpenTelemetry span (`hook.event`) with the
  event name, verdict, exit code, and duration (no raw prompts/args/outputs —
  see the telemetry sensitive-data policy). The audit trail flows through the
  existing OTel pipeline.
- **Environment allow-list**: hooks get `PATH`, `HOME`, `LANG`, `SYSTEMROOT`,
  `USER`, plus `ATLAS_CONFIG_DIR` and `ATLAS_PROJECT_DIR`. They do **not**
  inherit the server's full environment (which holds provider API keys).
- **Bounded output**: stdout/stderr are capped at 1 MB *at read time* — the
  child is killed as soon as its combined output crosses the cap, so a runaway
  hook cannot be buffered into server memory first. Overflow is treated as a
  hook error (handled per `on_error`); the truncated prefix is never parsed as
  a decision.
- A `SessionStart` `deny` runs **before** the session is persisted, so a
  rejected session leaves no row behind for a retry to pick up.
- A malformed `hooks.json` disables every hook. `ConfigManager.validate_config`
  reports `hooks_config: false` in that case — treat it as a failed security
  control, not a warning.

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

> **Envelopes are secret-bearing.** `tool_args` can contain tokenized download
> URLs (HMAC capability tokens), file paths, and prompt text; `result_content`
> carries retrieved document text. Log a projection, not the raw envelope — or
> treat the audit log itself as a secret store.

```bash
#!/usr/bin/env bash
# config/hooks/audit.sh  (PostToolUse, on_error: allow)
envelope="$(cat)"
python3 -c '
import json, sys
env = json.load(sys.stdin); p = env.get("payload") or {}
args = p.get("tool_args") or {}
json.dump({"user_email": env.get("user_email"), "tool_name": p.get("tool_name"),
           "arg_keys": sorted(args) if isinstance(args, dict) else None,
           "result_success": p.get("result_success")}, sys.stdout)
sys.stdout.write("\n")
' <<<"$envelope" >> "${ATLAS_PROJECT_DIR}/logs/tool-audit.jsonl"
exit 0
```

See `atlas/config/hooks-example/audit_tool.sh` for the full version.

### Scan retrieved RAG content for prompt injection (python)

```python
#!/usr/bin/env python3
# config/hooks/rag_injection_scan.py  (RagResponse)
import json, re, sys
env = json.load(sys.stdin)
content = env["payload"].get("content") or ""
if re.search(r"ignore\s+(previous|all|prior)\s+instructions", content, re.I):
    # observe: record and let it through. To enforce instead, emit
    # {"decision": "deny"} or a "modify" that strips the offending text.
    print("possible injection in retrieved content", file=sys.stderr)
sys.exit(0)
```

`atlas/config/hooks-example/rag_injection_scan.py` is the full version: it
scores content against a set of injection heuristics (instruction-override
phrasing, encoded blobs, fake conversation turns, invisible characters) and
appends medium/high hits as JSONL. **Pass the destination as an argv element**
— `["python3", ".../rag_injection_scan.py", "${ATLAS_PROJECT_DIR}/logs/rag_injection_scan.jsonl"]`.
Argv is interpolated; a custom environment variable would not work, because
`_build_env` gives hooks only a fixed allow-list (see the environment
allow-list under "Security & governance" above).

> **These records are secret-bearing**, the same caution `audit_tool.sh`
> carries: each one holds the requesting username, the data sources queried,
> and a 240-character verbatim excerpt of retrieved document text. Treat the
> destination as a secret store, and keep it out of the config directory.

This replaces a check that used to run unconditionally inside the MCP RAG path
and write `logs/security_high_risk.jsonl`. It only ever logged, and nothing
consumed the file, so it now lives here as opt-in operator policy instead of
core behavior.

Two calibration notes, because `RagResponse` hands over the whole synthesized
answer rather than one chunk:

- The structural signals (entropy, delimiters, formatting, length) fire on
  ordinary long markdown — a benign 900-byte answer scores 60 on them alone —
  so they cannot escalate by themselves. `medium`/`high` requires at least one
  *content* trigger. Re-tune if you score per chunk instead.
- The heuristics are cheap pattern matching that both misses paraphrased
  attacks and fires on innocent text quoting an instruction. Treat a hit as a
  lead rather than a verdict, and tune before wiring it to `deny`.

## Relationship to existing extension points

- **MCP tools** are how the model invokes external capabilities. Hooks are how
  an operator governs those invocations at lifecycle boundaries. They
  complement, not replace, MCP.
- The **`EventPublisher`** is the outbound UI event sink; hooks are the
  *inbound/interceptor* counterpart.
- Hooks are **not** a sandbox and **not** a marketplace. A hook is
  operator-installed code with server privileges.