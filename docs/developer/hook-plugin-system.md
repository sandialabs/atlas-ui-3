# Lifecycle Hook / Plugin System

Last updated: 2026-08-09

Atlas exposes a set of **lifecycle hook points** where operator-installed,
server-side plugins can observe and intervene in a chat turn. A plugin receives a
typed event and returns a `HookResult` whose value can **modify, block, annotate,
or escalate** the behavior at that point.

The hook bus is the inbound mirror of `EventPublisher`: the publisher fans events
*out* to the UI with no return influence; the hook registry runs a blocking
interceptor chain whose return values the core respects.

Use hooks for cross-cutting governance -- audit every tool call, redact PII from
prompts, force approval for a class of tools, rewrite a RAG query, drop retrieved
chunks above a risk threshold -- instead of editing `orchestrator.py`,
`tool_executor.py`, or `unified_rag_service.py`.

## Enabling the system

```bash
FEATURE_HOOKS_ENABLED=true
HOOK_PLUGINS=my_org.atlas_plugins.audit,my_org.atlas_plugins.pii:register
HOOK_TIMEOUT_SECONDS=5.0        # default per-handler timeout
```

`HOOK_PLUGINS` is an explicit allow-list -- nothing is auto-discovered. Entries are
separated by commas or whitespace (so a multi-line value works). Each entry is
`module` (whose `register` attribute is used) or `module:attribute`, and must
resolve to a callable taking the registry. Loading is idempotent: an entry point
already registered against the registry is skipped, so the two `AppFactory`
constructions in a live process cannot double-register a handler.

Loading is **fail-fast**: a plugin that cannot be imported, or that raises while
registering, aborts startup. A governance control that silently fails to load is
indistinguishable from one that was never configured.

When `FEATURE_HOOKS_ENABLED` is false the bus is inert and every call site
short-circuits on a `has_hooks()` check, so there is no cost in deployments that
do not use it.

## Writing a plugin

```python
from atlas.core.hooks import HookPoint, HookRegistry, HookResult

DENIED_PATHS = ("/etc/", "/root/")


async def block_sensitive_paths(event):
    path = event.arguments.get("path", "")
    if path.startswith(DENIED_PATHS):
        return HookResult.deny(
            reason=f"path {path} is outside the allowed roots",
            user_message="That file is not available.",
        )
    return None  # equivalent to HookResult.continue_()


def register(registry: HookRegistry) -> None:
    registry.register(
        HookPoint.PRE_TOOL_USE,
        block_sensitive_paths,
        name="block-sensitive-paths",
        priority=10,
    )
```

Handlers may be sync or async. Async is the documented norm: a sync handler is
run on a worker thread so it neither blocks the event loop nor escapes the
timeout, but a thread that overruns its budget cannot be killed -- the chain
moves on while the thread runs to completion.

`@hook(HookPoint.PRE_TOOL_USE)` is available as a decorator shorthand that
registers on the process-wide registry.

## Hook points

| Hook | Fires at | Event | What a return value can do |
|------|----------|-------|----------------------------|
| `SESSION_START` | `ChatService.create_session`, before the session is persisted | `SessionStartEvent` | Seed session-scoped policy state (`context`); reject the session |
| `USER_PROMPT_SUBMIT` | `ChatOrchestrator.execute`, before the message is written to history | `UserPromptSubmitEvent` | Rewrite/redact `prompt`; narrow `selected_tools` / `selected_data_sources`; disable `agent_mode`; block the turn |
| `PRE_LLM_CALL` | `LiteLLMCaller`, before every provider round-trip | `PreLlmCallEvent` | Rewrite `messages`; repoint `model`; pin `temperature`; block the call |
| `PRE_TOOL_USE` | `execute_single_tool`, before the tool is invoked | `PreToolUseEvent` | Mutate `arguments`; deny the call; force approval |
| `POST_TOOL_USE` | `execute_single_tool`, after the tool returns | `PostToolUseEvent` | Transform/redact `content`; withhold the output |
| `PERMISSION_REQUEST` | `execute_single_tool`, once the approval requirement is computed | `PermissionRequestEvent` | Auto-approve or escalate via `needs_approval`; deny |
| `RAG_CALL` | `UnifiedRAGService.query_rag` / `query_rag_batch`, before retrieval | `RagCallEvent` | Rewrite `query`; narrow `data_sources`; block retrieval |
| `RAG_RESPONSE` | `UnifiedRAGService.query_rag` / `query_rag_batch`, after retrieval | `RagResponseEvent` | Filter/redact `content`; withhold it |

`PRE_TOOL_USE` is the most powerful control point: every tool call in the system
-- tools mode *and* the agentic loop -- funnels through `execute_single_tool`, so
one handler uniformly governs arguments, denial, and approval escalation across
all tool usage.

`PRE_LLM_CALL` is its counterpart for the model. Where `USER_PROMPT_SUBMIT` fires
once per turn on the raw user text, `PRE_LLM_CALL` fires on **every** provider
round-trip -- each agentic-loop iteration, each tool-synthesis call, streaming and
non-streaming alike -- and sees the fully assembled request: system prompt,
history, injected RAG context, and tool results. It is wired into the four leaf
entry points (`call_plain`, `call_with_tools`, `stream_plain`,
`stream_with_tools`) that every other public LLM method funnels through, so it
fires exactly once per request. It runs *before* the model name, API key, and
per-model kwargs are resolved, so a plugin that repoints `model` gets that
model's real credentials.

Every event carries the turn's trusted context (`session_id`, `user_email`,
`conversation_id`, `compliance_level`). RAG retrieval is reached through the LLM
caller, which holds no session, so `ChatService` publishes that context for the
turn via a `ContextVar` (`atlas.core.hooks.hook_turn`) and the RAG events read it
from there. A RAG query issued outside a chat turn carries `None` for the
session-scoped fields.

## Return-value contract

`HookResult` carries one of four decisions:

| Decision | Constructor | Meaning |
|----------|-------------|---------|
| `CONTINUE` | `HookResult.continue_(**metadata)` or returning `None` | No change; optionally attach audit metadata |
| `MODIFY` | `HookResult.modify({field: value}, reason=...)` | Structured patch over the event's mutable fields |
| `DENY` | `HookResult.deny(reason, user_message=...)` | Short-circuit the prompt, tool, or retrieval |
| `REQUIRE_APPROVAL` | `HookResult.require_approval(reason)` | Force the runtime approval gate |

`MODIFY` is a **structured patch**, not a full replacement. Each event declares
`MUTABLE_FIELDS`; a patch touching anything else is rejected. The trusted context
on every event -- `session_id`, `user_email`, `conversation_id`,
`compliance_level` -- is never patchable.

`MODIFY` is also the *only* way to change an event. The registry applies patches
to the event in place after validating them, and call sites read the patched
values only when `HookChainResult.modified` is set. Mutating `event.<field>`
directly and returning `CONTINUE` skips `validate_patch()`, so the edit is
discarded rather than applied. This holds for nested values too: events are built
from deep copies of the mutable payload (tool arguments, LLM messages, session
context), so an in-place edit of `event.arguments["options"]["path"]` cannot
reach the call site behind a `CONTINUE`.

`reason` is **operator-facing**: it goes to logs and spans, and it typically
names the handler, the rule, or the offending value. It is never shown to the end
user -- the chain substitutes `DEFAULT_DENY_USER_MESSAGE` whenever a `DENY`
arrives without a `user_message`, including for a `HookResult(...)` built
directly rather than through `HookResult.deny()`. A denial the user should see an
explanation for must pass `user_message`; otherwise they get a generic
"This request was blocked by policy."

## Composition and execution

Handlers run sequentially, ordered by `(priority, registration order)` with lower
priority first. The rules are deterministic and most-restrictive-wins:

1. `MODIFY` patches are **piped** -- each handler sees the previous handler's edits.
2. The first `DENY` **short-circuits** the chain; nothing after it runs.
3. `REQUIRE_APPROVAL` is **sticky** but does not short-circuit, so a later `DENY`
   still wins.
4. `PERMISSION_REQUEST` auto-approval cannot undo a `PRE_TOOL_USE` escalation, nor
   an escalation an earlier handler in the same chain applied. Whether a tool
   needs approval never depends on plugin ordering.
5. `HookChainResult.modified` records that a patch was applied and is independent
   of `.decision`: a chain that both patches and escalates composes to
   `REQUIRE_APPROVAL` while still reporting `modified`, so the call site picks up
   the patch.

### Timeouts and error isolation

Every async handler runs under a timeout (`HOOK_TIMEOUT_SECONDS`, overridable per
registration). A handler that raises, times out, returns a non-`HookResult`, or
returns an invalid patch is handled per its **failure mode**:

- **fail closed** -- the failure becomes a `DENY`. The turn/tool/retrieval is
  blocked rather than proceeding unchecked.
- **fail open** -- the handler is skipped and the chain continues.

Defaults per hook point:

| Hook point | Default |
|------------|---------|
| `SESSION_START` | fail open |
| `POST_TOOL_USE` | fail open |
| `USER_PROMPT_SUBMIT`, `PRE_LLM_CALL`, `PRE_TOOL_USE`, `PERMISSION_REQUEST`, `RAG_CALL`, `RAG_RESPONSE` | fail closed |

Pass `fail_open=True/False` to `register()` to override. Task cancellation
(client disconnect, shutdown) is never treated as a plugin fault -- it propagates
so the surrounding turn unwinds normally.

## Security model

Plugins are trusted, operator-installed, in-process code, but the core still
treats their **output** defensively. A plugin can tighten a boundary; it can
never widen one.

- **Identity cannot be forged.** After a `PRE_TOOL_USE` patch, `tool_executor`
  re-runs `inject_context_into_args()` and `_filter_args_to_schema()`, so
  `_atlas_user` is re-stamped server-side and undeclared parameters are dropped.
- **Compliance cannot be relaxed.** `compliance_level` is read-only on every
  event and continues to reach `execute_tool()` straight from the session.
- **RAG sources are narrow-only.** `RagCallEvent` rejects any `data_sources`
  patch that adds a source to the already compliance-filtered list, and refuses
  to let the list be emptied (return `DENY` to block retrieval instead).
- **Admin-mandated approval is absolute.** `PermissionRequestEvent` rejects a
  patch setting `needs_approval=False` when `admin_required` is true (set by
  `FORCE_TOOL_APPROVAL_GLOBALLY` or a per-tool `require_approval` in `mcp.json`).
- **Prompt hooks are narrow-only.** `UserPromptSubmitEvent` rejects patches that
  add tools or data sources the user did not select, or that enable agent mode.
- **Session context is the plugin's own space.** `SessionStartEvent` rejects a
  `context` patch that adds, alters, or drops a runtime-owned key
  (`conversation_id`, `compliance_level`, `files`, `selected_data_sources`,
  `agent_mode`, ... or anything starting with `_`). Those keys reach
  `session.context` only through the writers that validate them; a plugin seeds
  its own policy state alongside them.
- **A model swap is re-authorized.** A `PRE_LLM_CALL` patch may repoint `model`,
  but the call site re-runs `check_model_access()` against the turn's user and
  refuses the call unless that user is authorized for the new model -- the same
  per-model group check the orchestrator applies to the model the client asked
  for. An unconfigured model name is refused too. `messages` patches must keep
  the list non-empty and every entry a dict with a known role (`system`, `user`,
  `assistant`, `tool`, `function`); `temperature` must land in `[0.0, 2.0]`.
- **Withheld output stays withheld.** A `POST_TOOL_USE` denial clears the
  result's `artifacts` and `display_config` as well as its text, so files the
  tool produced are never published to the UI behind a download URL.

A rejected patch is treated exactly like a handler failure: denied under
fail-closed, skipped under fail-open.

Every non-`CONTINUE` decision is logged with the handler name and reason, and
`HookChainResult.contributors` / `.metadata` carry the same information to the
call site for auditing.

## Relationship to other extension points

- **MCP tools** add *capability* the model can choose to invoke. Hooks add
  *policy* over capability that already exists; they are not model-visible.
- **`EventPublisher`** is the outbound UI sink. Hooks do not replace it -- a
  plugin that wants to surface a warning should deny with a `user_message`.
- **`ToolAuthorizationService` and the approval broker** remain the authoritative
  authorization and consent chokepoints. `PERMISSION_REQUEST` composes with them;
  it does not bypass them.

## Testing plugins

`reset_hook_registry()` drops the process-wide singleton, and `HookRegistry` can
be instantiated directly for unit tests:

```python
from atlas.core.hooks import HookPoint, HookRegistry, PreToolUseEvent

registry = HookRegistry()
registry.register(HookPoint.PRE_TOOL_USE, my_handler, name="mine")
chain = await registry.dispatch(PreToolUseEvent(tool_name="files_read", arguments={...}))
assert chain.denied
```

See `atlas/tests/test_hook_registry.py` and `atlas/tests/test_hook_integration.py`.
