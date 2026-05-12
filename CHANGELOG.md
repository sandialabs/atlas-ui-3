# Changelog

All notable changes to Atlas UI 3 will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### PR #596 - 2026-05-11
- Added `AGENT_PORTAL_ALLOWED_ORIGINS` to let the Agent Portal WebSocket stream accept Origin headers beyond loopback when the deployment is fronted by an authenticating reverse proxy (e.g. Cloudflare Access). Loopback hosts remain allowed by default; the env var is a comma-separated hostname allowlist and is empty by default, so the gate is unchanged for stock installs.
- Renamed `_origin_is_loopback` to `_origin_is_allowed` in `atlas/routes/agent_portal_routes.py` and updated the rejection log message; updated `docs/agentportal/threat-model.md` to describe the expanded allowlist and its residual risks.

### PR #565 - 2026-04-25
- MCP sessions are now keyed by `(user, conversation, server)` and client-supplied `conversation_id` values owned by another user are rejected on chat and restore.
- Hardened the new ownership boundary after multi-agent review:
  - Per-turn ownership check now fails closed when the configured `conversation_repository` does not implement `get_conversation_owner` (previously fell through to "always allow" with only a startup warning).
  - `handle_restore_conversation` returns the canonical message list from the DB instead of replaying the client-supplied payload, so a tampered client cannot inject forged history into the LLM context and have it re-persisted.
  - `_save_conversation` honours the repository's `None` return (TOCTOU rejection): the frontend now receives a `conversation_save_rejected` error frame instead of a false `conversation_saved` notification.
  - `save_conversation` and `get_conversation` normalize `user_email` so mixed-case identities (proxy / OAuth normalization differences) cannot silently drop saves.
  - Whitespace-only / non-string `conversation_id` is treated as "not provided" and falls back to the session-id default.
  - WebSocket dispatch now has explicit `AuthorizationError` arms for both chat and restore so a denied request returns a structured `error_type: "authorization"` frame instead of falling through to the generic domain-error arm or, in the restore case, tearing down the connection.
- Post-merge review fixes:
  - `_close_user_client_entry` now passes `user_email` when releasing the underlying MCP session. Without it the cache evicted the per-user HTTP client but called `MCPSessionManager.release` with an empty user scope, leaving the original `(user, conversation, server)` `ManagedSession` orphaned in `_sessions` — defeating the bound the cache exists to enforce. Tests previously mocked the session manager and missed it; a regression test now exercises the real session manager.
  - Email normalization is now applied at every public `ConversationRepository` entry (list / search / export / delete / delete_conversations / delete_all / add_tag / remove_tag / list_tags / update_title) and `get_conversation_owner` returns its result normalized. Earlier the chokepoint was partial — only `save_conversation` and `get_conversation` normalized — so a deployment with mixed-case historical rows would see inconsistent results across operations.

### PR #564 - 2026-04-28
- Bounded the per-user MCP HTTP client cache with LRU/idle eviction, explicit FastMCP client close on cleanup, and enabled Uvicorn WebSocket ping keepalives so dropped connections are detected and MCP session cleanup runs sooner.
- Hardened cache lifecycle after multi-agent review:
  - LRU eviction now skips cached clients touched within `MCP_USER_CLIENT_CACHE_IN_USE_WINDOW_SECONDS` (default 60s) so an in-flight tool call cannot have its connection torn down; cache temporarily exceeds bound rather than evict an active client.
  - `client.__aexit__` is bounded by `MCP_USER_CLIENT_CLOSE_TIMEOUT_SECONDS` (default 5s) so a stuck upstream cannot hang the sweeper or shutdown.
  - Cache sweeper now starts even if MCP discovery fails during lifespan, so the leak guard is not silently disabled in degraded startup.
  - Sweeper close batches are tracked and drained on shutdown, eliminating a cancellation race that could orphan FastMCP clients between pop and close.

### PR #559 - 2026-04-25
- MCP cross-conversation isolation: cache FastMCP HTTP `Client` instances by
  `(user_email, server_name, conversation_id)` so each conversation gets its
  own MCP session ID and FastMCP nesting counter. Fixes the
  "nesting counter should be 0" reconnect failure that surfaced after a
  shared client's session task died, and isolates stateful HTTP servers
  (e.g. the per-session `PrinterService`) across the same user's conversations.
- `handle_reset_session` now releases the previous conversation's MCP
  sessions and per-conversation HTTP clients before generating a new
  `conversation_id` — previously each "New chat" click orphaned the old
  `(user, server, old_conv_id)` cache entries and `MCPSessionManager` sessions.
- `get_prompt` now mirrors `call_tool`'s auth routing on HTTP MCP servers
  with `auth_type` of oauth/jwt/bearer/api_key: requests go through the
  user's stored token instead of the admin/server-default token, and missing
  tokens raise `AuthenticationRequiredException` with the OAuth start URL.
- `_get_or_create_user_http_client` now requires a non-empty
  `conversation_id` (a `None` value would alias every caller into one
  shared cache slot, recreating the bug this cache exists to prevent).
- Added integration-style tests using a faithful `FakeFastMCPClient` that
  drives the real `MCPSessionManager` and reproduces the pre-fix nesting
  counter failure mode, plus unit coverage for `release_sessions`'s
  per-conversation eviction.

### PR #558 - 2026-04-24
- Fix: nvm/venv/uv-installed CLIs (e.g. `cline`) no longer fail with
  a misleading exit 127. The launched binary's own directory is now
  prepended to the child `PATH` so the shebang interpreter
  (`/usr/bin/env node`, `/usr/bin/env python`, …) can be resolved
  alongside the binary. Smallest path extension that fixes the
  common shebang-interpreter case without re-introducing the full
  server `PATH`.
- Fix: PTY-mode race where `output_raw` chunks arrived during
  history replay before XtermView mounted, dropping early stdout/
  stderr silently. The WS handler now buffers raw chunks in a ref
  and XtermView flushes them on mount.
- Non-zero process exits now surface as a toast with the exit code,
  with a hint pointing at the PATH issue when the code is 127.
- Agent Portal UX refresh: launch form moves from the cramped left
  panel into a roomy modal popup opened by a "New launch" button.
  Left panel now shows only active sessions and the presets library,
  plus a Recent launches section collapsed by default. Replace every
  `window.prompt` / `window.alert` / `window.confirm` in the portal
  with a toast system and a custom prompt/confirm dialog component;
  preset save/update/delete and launch all emit a toast instead of
  silent state updates or inline banners.
- New `atlas-portal` CLI (`atlas.portal_cli`) lets developers launch,
  list, get, cancel, inspect processes and manage presets from the
  terminal — useful for debugging launch failures that are awkward to
  reproduce through the UI, and for e2e automation.
- Eleven integration tests walk the full launch → list → get → cancel
  flow through the real FastAPI router plus the CLI parser, covering
  env isolation, bare-command resolution, preset round-trip, and the
  feature-flag kill switch.
- Fix: bare command names like `claude` or `uvx` installed under
  `~/.local/bin`, a venv, or a Nix profile no longer fail to launch
  with `[Errno 2] No such file or directory`. `ProcessManager.launch`
  resolves non-absolute commands against the server's own `PATH` via
  `shutil.which()` before spawning (a one-shot parent-side lookup that
  does not leak the server's search path into the child) and raises a
  clear `FileNotFoundError` naming the command when the lookup fails.
- Server-side preset library at `/api/agent-portal/presets` (CRUD)
  with atomic writes + `fcntl.flock`; filtered by `user_email` at the
  storage layer. Frontend migrates legacy `localStorage` entries on
  first mount and adds an **Update** button for round-trip preset edits.
- Env isolation: child processes no longer inherit `os.environ.copy()`.
  Allow-list of benign keys + pinned `PATH` + deny-list for secret-
  shaped keys prevents backend secrets leaking to launched commands.
- Dev-only hardening: startup guard refuses to enable the feature
  unless `DEBUG_MODE=true`; WebSocket stream endpoint rejects non-
  loopback Origin headers to block drive-by CSRF from untrusted tabs.

### Agent Portal preset library - 2026-04-24
- Server-side preset CRUD at `/api/agent-portal/presets` (list/create/get/
  update/delete). Each preset captures the full launch-form payload
  (command, args, cwd, sandbox settings, resource limits) plus a name and
  optional description. Stored at `<APP_CONFIG_DIR>/agent_portal_presets.json`
  with atomic writes and a `fcntl.flock`-backed lock file; filtered by
  `user_email` at the storage layer on every read and write.
- Frontend migrates any legacy `localStorage`-backed launch configs to the
  server on first mount, renames the "Saved configs" panel to "Presets
  library", and adds an **Update** button that appears when a preset is
  loaded so the form can be saved back in place instead of spawning a
  duplicate. **Save as…** now also prompts for an optional description.
- Docs: `docs/agentportal/presets.md` covers the storage layout, HTTP API,
  and migration behavior.

### Agent Portal (initial) - 2026-04-23
- New `/agent-portal` page (behind `FEATURE_AGENT_PORTAL_ENABLED`, off by default)
  lets a user launch a host subprocess (command + args + optional cwd), view the
  list of their running / finished processes, stream stdout/stderr live over a
  dedicated WebSocket, and cancel a running process (SIGTERM, SIGKILL after 3s).
  Backend: `atlas/modules/process_manager/` + `atlas/routes/agent_portal_routes.py`.
  Dev preview only — no allow-list, quotas, or audit trail yet; governance layer
  will be added in follow-up work.
- Optional Landlock sandbox: a "Restrict to working directory" checkbox confines
  the child's filesystem writes to cwd via Linux Landlock (set up from
  `preexec_fn` between fork and exec, with `PR_SET_NO_NEW_PRIVS`). Capability
  probed via `GET /api/agent-portal/capabilities` so the checkbox is disabled
  when the kernel lacks support. Writes outside cwd return `EACCES`; reads and
  `exec` on system roots (`/usr`, `/lib`, `/etc`, ...) are still permitted so
  normal binaries run.
- Frontend persists recent launches (command, args, cwd, sandbox mode) to
  `localStorage` (`atlas.agentPortal.launchHistory.v1`, up to 15 entries) and
  prepopulates the form from the most recent entry on load. A "Recent launches"
  list lets the user click to reapply or remove past entries.
- Third sandbox mode `workspace-write`: reads are allowed across the entire
  filesystem (so tools like `cline` can find `node` / configs / caches under
  `~/.local`, `~/.nvm`, `/nix`, etc.) but writes are still confined to cwd.
  The `strict` mode remains for tighter isolation. Both modes allow read +
  write on `/dev` so `/dev/null`, `/dev/tty`, and shell redirections keep
  working. The UI exposes the choice as a dropdown; the request body now
  carries `sandbox_mode` (`off` | `strict` | `workspace-write`) with backward
  compatibility for the earlier `restrict_to_cwd` flag.
- Extra writable paths: a new textarea lets the user whitelist additional
  directories for write access alongside cwd (e.g. `~/.cline`,
  `~/.cache/<tool>`). Backend field `extra_writable_paths` is passed to the
  Landlock wrapper via the `ATLAS_SANDBOX_EXTRA_WRITE_PATHS` env var; each
  directory gets the same access set as the workspace and is created on
  demand.
- Named launch configs: the user can save the current form (command, args,
  cwd, sandbox mode, extra writable paths) as a named preset. Presets are
  stored in `localStorage` under `atlas.agentPortal.launchConfigs.v1` and
  shown in a "Saved configs" panel separate from the auto-history; each
  config can be reapplied to the form with one click or deleted.

### PR #557 - 2026-04-22
- MCP task-augmented execution fixes: discovery-time seeding of task-forbidden
  cache from per-tool execution.taskSupport metadata (SEP-1686), and runtime
  fallback detection for immediate error results that don't raise exceptions.

### PR #555 - 2026-04-23
- Monthly release process + cron automation: `docs/developer/release-process.md`
  runbook, `.github/workflows/release-cut.yml` scheduled cut (day 22, 14:00
  UTC) that creates `release/YYYY.MM`, bumps versions, reshapes CHANGELOG,
  and opens a draft release PR from `.github/release-checklist.md`. Workflow
  uses an optional `RELEASE_PAT` secret so the PR triggers `CI/CD Pipeline`
  and `Security Checks`, falls back to `GITHUB_TOKEN` with a visible
  kick-CI banner, and includes a recovery path that opens a PR when a prior
  run stranded a pushed branch without one. No publish paths change.

### PR #552 - 2026-04-20
- New Chat stops in-flight generation: clicking "New Chat" while a reply is
  streaming no longer lets orphaned tokens bleed into the fresh session.
  `clearChat` now cancels the active task (`stop_streaming` +
  `agent_control: stop` when in agent mode) before requesting a new session,
  fully resets local thinking / synthesizing / agent-step state, and asks
  for confirmation before discarding an existing conversation or
  interrupting generation. Backend `reset_session` also cancels any running
  chat task as defense-in-depth, and a new `agent_control` server handler
  replaces the prior "Unknown message type" echo.
- Sidebar "Delete Conversation" (of the active conversation) now bypasses the
  New-Chat confirm prompt via `clearChat({ skipConfirm: true })` so users
  don't see a second "Start a new chat?" dialog right after deleting.
- Header "New Chat" button and `Ctrl+Alt+N` hotkey now gate their follow-up
  side-effects (close canvas, focus input) on the confirm result — if the
  user clicks Cancel, the chat stays intact.

### PR #551 - 2026-04-20 - Pause banner/config polling when user is idle
- Added `useUserActivity` hook that tracks mouse/keyboard/touch/scroll activity.
- `BannerPanel` now pauses `/api/config` and `/api/banners` polling after 5
  minutes of no user activity and resumes automatically (with an immediate
  refresh) on the next user event.

### PR #550 - 2026-04-20
- **Admin telemetry dashboard (issue #546)**: New `/admin/telemetry` page with
  five read-only views backed by the OpenTelemetry span audit trail: Overview
  (turn / tool / LLM / RAG rollups over 1h–30d), Tool health (per-tool call
  count, success rate, p95 duration, click-through to recent failures), LLM
  performance (per-model p50/p95/p99 latency, token totals, retry rate), RAG
  effectiveness (per-source retrieval-to-use ratio, top-score distribution),
  and Session drill-down (span tree waterfall by `session_id` or `turn_id`).
  Data source is pluggable via a `SpanReader` protocol; the default
  `FileSpanReader` streams `logs/spans.jsonl` and an OTLP/Jaeger/Tempo backend
  can be swapped in without UI changes. All endpoints require admin authz and
  defensively whitelist the span attributes they echo — no raw prompts, tool
  outputs, or RAG document text ever reach the dashboard.

### PR #549 - 2026-04-20 - OpenTelemetry spans for S3 / file-storage operations
- Emit `file.upload`, `file.download`, `storage.list`, and `storage.delete`
  spans from `S3StorageClient` and `MockS3StorageClient`. Contract uses
  HMAC-SHA256 hashes for user/key, `safe_label` for filenames, and
  `preview(..., max_chars=300)` for error messages — never raw keys,
  bucket names, filenames (beyond the sanitized label), or user emails.
- Cross-user access attempts set `access_denied=true` before the exception
  is raised so the event survives in `spans.jsonl` even on failure.
- `docs/telemetry/README.md` gains attribute tables for the four new span
  types; `docs/telemetry/analysis_example.py` gains
  `upload_volume_by_user` and `storage_success_rate_by_backend`
  aggregations.

### PR #549 review follow-up - 2026-04-21
- **Security**: `S3StorageClient` no longer embeds the raw boto
  `Error.Message` in the raised `Exception` string. Those messages can
  carry tokens, caller args, or user content and previously leaked up to
  API responses / upstream logs. The sanitized preview still goes on the
  span; the exception message is now generic (`"S3 upload failed"` etc.)
  and the underlying cause is chained via `raise ... from e`.
- **Correctness**: upload-failure spans now populate `file_size`,
  `category`, and `key_hash` from the values computed before the failure
  (when available) instead of writing `0` / `"other"` unconditionally,
  so `upload_volume_by_user` and category breakdowns include failed
  attempts.
- **Contract consistency**: the `NoSuchKey` / 404 branches on download
  and delete now also set `error_type` (`"NoSuchKey"` for real S3,
  `"NotFound"` for the mock) so failure-mode aggregation groups them
  alongside raised errors. `error_message` rows added to the
  `storage.list` / `storage.delete` attribute tables to match emission.
- **Analysis hygiene**: `file_type=None` on `storage.list` now surfaces
  as the string sentinel `"null"` so the attribute is always present
  (OTel drops `None` attrs). Duplicate `attr_num_results` removed from
  `_NUMERIC_ATTRS`.

### PR #544 - 2026-04-19
- **Fix**: MCP client tore down the streamable-HTTP session (POST → DELETE) after every tool call on stateful servers, so state written by one tool was invisible to the next. Root cause: `ChatService.handle_chat_message` only set `session.context["conversation_id"]` when the client sent one, but the frontend doesn't send a conversation id on the first message of a new conversation. That left `conversation_id=None` for tool execution, which forced `MCPToolManager.call_tool` into its per-call `async with client:` fallback instead of reusing the persistent session held by `MCPSessionManager`. Fix: default `session.context["conversation_id"]` to `str(session_id)` when the client doesn't send one (matches the fallback already used by `_save_conversation` and the `conversation_saved` notification, so the stable id round-trips to the client). Stateful MCP servers (e.g. FastMCP 3.x streamable-HTTP servers that key per-tool state on `Context.session_id`) now see a reused `Mcp-Session-Id` across tool calls within a conversation, as required by the MCP spec.

### PR #547 hardening pass - 2026-04-19 (issue #545 follow-up, same PR)
- **Security / privacy**:
  - `tool.call.error_message` is now routed through `preview()` (sanitized,
    CR/LF stripped, capped at 300 chars). Upstream exception strings from
    DB drivers / HTTP clients / MCP tools routinely embed caller args,
    URLs with tokens, and user content; the prior contract allowed those
    to reach span attributes and OTLP exporters verbatim.
  - RAG `doc_ids` now sanitize + length-cap each element (≤200 chars,
    control chars stripped), preferring `chunk_id` and only falling back
    to `title`/`source` after sanitization — external RAG backends can
    return untrusted strings with injection payloads.
  - `hash_short` switched from truncated SHA-256 to HMAC-SHA256 keyed by
    `ATLAS_TELEMETRY_HMAC_SECRET` (falls back to `CAPABILITY_TOKEN_SECRET`,
    then to a per-process random key with a startup warning). Prevents
    rainbow-table reversal of short identifiers in small populations.
    Docs updated to describe this as pseudonymization, not anonymization.
  - `write_tool_output_sidecar` creates files with `0600` and the
    `tool_outputs/` directory with `0700`. `spans.jsonl` and `app.jsonl`
    are likewise tightened to `0600` on POSIX filesystems.
  - `_coerce_attr` gained a 4000-char hard cap on all string attribute
    values (including list elements) as defense-in-depth against a future
    call site forgetting to use `preview()` or `safe_label()`.
- **Reliability**: `JSONLSpanExporter` now holds a long-lived file handle
  guarded by a lock; `force_flush` issues `fsync` and `shutdown` closes
  the handle. `OpenTelemetryConfig.shutdown()` flushes processors and
  tears them down cleanly. Previous behavior returned `True` from
  `force_flush` without touching disk and did nothing on shutdown.
- **OpenShift manifest**: Grafana anonymous-admin is now **off** by
  default — replaced with a `grafana-admin` Secret and
  `GF_SECURITY_ADMIN_*` env wiring. A commented `ANONYMOUS_DEV_ONLY`
  block preserves the laptop-only shortcut. In-cluster
  `tls: insecure: true` is documented as namespace-scoped only.
- **Tests**: Added 9 new test cases covering sanitized `error_message`,
  HMAC keying and secret dependence, sanitized RAG `doc_ids`,
  `_coerce_attr` hard-capping, sidecar/spans file permissions, and
  `JSONLSpanExporter` flush/shutdown semantics. PR-validation script
  extended with a failing-tool negative control and file-mode assertion.

### PR #547 - 2026-04-19 (issue #545)
- **Feature**: OpenTelemetry audit trail. ATLAS now emits structured spans for every high-value event in a chat turn: `chat.turn` (per user message), `llm.call` (per LiteLLM call, including streaming), `tool.call` (per tool invocation), and `rag.query` (per RAG query, including batched multi-source queries). Spans are written as one JSON line per span to `logs/spans.jsonl` via a `BatchSpanProcessor`; optional OTLP export is enabled via `OTEL_EXPORTER_OTLP_ENDPOINT`. Attribute contract is frozen and documented in `docs/telemetry/README.md`: sanitized previews, hashes, sizes, token counts, retry counts, RAG document IDs/scores, and tool success/duration — never raw prompts, raw tool outputs, or raw RAG document text. Full tool outputs are opt-in only via `ATLAS_LOG_TOOL_OUTPUTS=true` (written to `logs/tool_outputs/{span_id}.txt`). A reference pandas analysis script lives at `docs/telemetry/analysis_example.py` and computes per-tool success rates, per-model p95 latency, RAG retrieval/use ratios, and retries per turn. An optional Grafana Tempo / Grafana stack recipe is included in the telemetry README for interactive trace exploration. 19 unit tests cover span emission, sensitive-data containment, and the JSONL exporter contract.
- **Review fixes** (addressed on the same PR):
  - Fixed `tool.call` **output leak** when `args_edited=true`: the LLM-facing edit note containing executed arguments was being captured into `output_preview`/`output_sha256`/`output_size`. Telemetry now reads the pre-edit-note content; a new `args_edited` boolean attribute records whether the edit happened. Regression test added.
  - Fixed `tool_source` attribution for MCP servers whose names contain underscores (e.g. `pptx_generator`). Previously split the tool name on the first `_`, which mis-attributed tools like `pptx_generator_create` to `pptx`. Now uses `MCPToolManager.get_server_for_tool(name)` (authoritative tool index). Falls back to `null` when unavailable so analysis code never sees a fabricated prefix. Regression test added.
  - Fixed `rag.query.content_size` to report UTF-8 byte size (via `telemetry.size_bytes`) instead of character count, matching the documented contract.
  - Replaced `span.record_exception(exc)` with an `error_type` attribute only — avoids forwarding full exception messages (which can contain user/tool content) via OTLP.
  - `set_attrs` now preserves empty lists so list-typed contract fields (`doc_ids`, `doc_scores`, `docs_used_in_context`) appear as explicit `[]` rather than silently vanishing.
  - Renamed streaming `llm.call.output_tokens` to `output_tokens_estimate` (it's computed from `output_chars // 4`, not from real usage metadata) so aggregations don't silently mix estimates with authoritative token counts.
  - Broke the `litellm_streaming` → `litellm_caller` cyclic import: `split_provider` moved to `atlas/modules/llm/models.py`.
  - `set_attrs` debug-log now sanitizes attribute key/exception strings via `sanitize_for_logging`.
  - `docs/telemetry/analysis_example.py::retries_per_turn` uses `df.reindex` so partial span files (e.g. only `tool.call` spans) no longer raise `KeyError`.

### PR #541 - 2026-04-19
- **Fix**: MCP tool calls kept failing with `Session terminated` until a backend restart when a stateful MCP server's backing process invalidated its session ID while the HTTP transport still reported connected. `MCPToolManager.execute_tool` now detects session-termination errors (`"session terminated"`, `"session not found"`, `"invalid session id"`) — including when wrapped via `__cause__` / `__context__` — and calls `_session_manager.release(conversation_id, server_name)` so the next tool call transparently opens a fresh session. Also promoted the on-disconnect `release_sessions` failure log from `debug` to `warning` so silent failures are visible. Added three regression tests covering the direct, chained-exception, and negative (unrelated error) paths.

### Frontend maintainability - 2026-04-18
- **Refactor**: Decomposed `frontend/src/components/Message.jsx` from 1,396 lines to 524 lines by extracting cohesive helpers into sibling modules. New modules: `utils/markdownRenderer.js` (marked + highlight.js + DOMPurify config), `utils/ragCitations.js` (source-label extraction, inline citation badges, collapsible References section), `utils/messageContent.js` (content shaping), `utils/clipboard.js` (code-block and message copy helpers), `utils/toolResultUtils.js` (argument filtering, tool-result sanitization, file download). The `ToolApprovalMessage` and `ToolElapsedTime` sub-components moved to their own files under `components/`. `rag-citation-rendering.test.js` now imports from `utils/ragCitations.js` instead of duplicating the helpers, eliminating drift risk. Addressed review feedback: sanitize hljs language tag before HTML interpolation, null-guard artifact/base64 lengths in `processToolResult`, scope the code-block copy delegator to each message's container ref (was `document`, which multiplied listeners by message count), and render RAG citation chips as real `<button>` elements for native keyboard activation. No behavior changes.

### PR #536 - 2026-04-17
- **Fix**: MCP tool calls using the background-task (`ToolTask`) path now return results correctly instead of `null` (`fastmcp>=3.2.0` changed `ToolTask.result` to an async method).

### PR #534 - 2026-04-16
- **Fix**: Anthropic calls failed with `litellm.UnsupportedParamsError: Anthropic doesn't support tool calling without tools= param specified` whenever the conversation history contained a prior assistant `tool_calls` block but the current call omitted `tools=` (e.g. title generation, plain replies, or follow-ups on a conversation that earlier used tools). Set `litellm.modify_params = True` at module load so litellm injects a benign `dummy_tool` schema for Anthropic in this case, matching litellm's documented workaround. Added a regression test asserting both `drop_params` and `modify_params` stay enabled.

### PR #TBD - 2026-04-17
- **Fix**: Tool calls failed with `McpError: FunctionTool '...' does not support task-augmented execution` when the server advertised task capability but the individual tool declared `tasks.mode="forbidden"`. `MCPToolManager.call_tool` now catches that specific error, falls back to a synchronous (non-task) call, and caches the `(server, tool)` pair so subsequent invocations skip task mode directly. Unrelated errors still propagate unchanged.

### PR #533 - 2026-04-15
- **Fix**: File delete (and download) from the File Library returned 404 in production. `AllFilesView` used `encodeURIComponent` on the full S3 key which encoded `/` to `%2F`, breaking path-based routing through reverse proxies. Now encodes each path segment individually. Also fixed `FilesPage` referencing the non-existent `file.s3_key` property (should be `file.key`). Added backend `unquote()` safety net on all `{file_key:path}` route handlers to handle residual percent-encoding from proxies.

### PR #504 - 2026-04-12
- **Fix**: Light mode white-on-white bug in slash command and `@file` autocomplete dropdowns. Tool and file names now inherit their text color from the parent row instead of using a hardcoded `text-white` class, making them visible in both light and dark themes.

### PR #512 - 2026-04-12
- **Security**: Removed the hardcoded `b"dev-capability-secret"` fallback used by `atlas/core/capabilities.py` when `CAPABILITY_TOKEN_SECRET` was unset. Previously, any attacker who knew this constant could forge HMAC capability tokens for any `{user, file_key}` pair and download any user's files via `/mcp/files/download/` (which intentionally bypasses header-based auth). The fallback now generates a cryptographically random 32-byte per-process secret via `secrets.token_bytes(32)`; tokens signed with it cannot be predicted or forged. A `CRITICAL` log entry is emitted in production (or `WARNING` in debug mode) the first time the ephemeral secret is used, instructing operators to set `CAPABILITY_TOKEN_SECRET` for durable, restart-stable tokens. Fail-closed: no hardcoded value is ever returned from `_get_secret()`.

### PR #511 - 2026-04-12
- **Security**: Tool approval requests are now bound to the authenticated user who created them. Any WebSocket approval response from a different user (or from an empty/missing user identity) is rejected and a security warning is logged. This prevents cross-user approval bypass (F-03) where a user who learned another user's pending `tool_call_id` could approve, reject, or inject edited arguments into that user's tool execution. The ownership check fails closed: once a request is bound to a `user_email`, the response must supply a matching one. Backward compatible: verification is skipped only for legacy requests where the request itself has no `user_email` (single-user deployments).

### PR #510 - 2026-04-12
- **Security**: `get_current_user()` now raises HTTP 401 when `request.state.user_email` is unset, instead of silently falling back to `test@test.com`. Any request that bypasses auth middleware is now rejected rather than granted a default identity.
- **Security**: `is_user_in_group()` mock group memberships (which grant admin access to the test user) are now gated behind `debug_mode=True`. In production mode with no external auth endpoint, users receive only the default `users` group — no admin privileges are granted via mock.
- **Security**: `FEATURE_PROXY_SECRET_ENABLED` now defaults to `true`. In production without a configured `PROXY_SECRET`, the middleware rejects all requests with HTTP 503 (fail-closed) instead of silently passing through. This prevents direct backend access from spoofing the `X-User-Email` header. Deployments that rely on network isolation can explicitly set `FEATURE_PROXY_SECRET_ENABLED=false`.
- **Security**: `GLOBUS_SESSION_SECRET` no longer has a default value. The old placeholder (`atlas-globus-session-change-me`) allowed session cookie forgery. When Globus auth is enabled but the secret is missing or still the placeholder, the feature is automatically disabled at startup and an error is logged.

### PR #503 - 2026-04-10
- **Fix**: `_parse_rag_metadata` in `AtlasRAGClient` now handles `data_sources` entries that are dicts (with `id`/`label` fields) in addition to plain strings, resolving a Pydantic validation error when the ATLAS RAG API returns object-shaped data sources.
- **Fix**: Documents in `documents_found` with a nested `data_source` object (instead of a flat `corpus_id`/`title`) now correctly populate `source` and `title`, so citations show meaningful labels instead of "Document 1, Document 2, …".

### PR #500 - 2026-04-10
- **Chore**: Upgrade fastmcp to `>=3.2.0` in all `pyproject.toml` files (main package and `mocks/mcp-http-mock`).

### PR #498 - 2026-04-04
- **Fix**: `GET /api/files/{file_key}` and `DELETE /api/files/{file_key}` now use the `{file_key:path}` converter, so S3 keys containing `/` (e.g. `users/alice@example.com/generated/foo.txt`) are captured in full instead of returning 404. Route declarations were reordered so the greedy catch-all comes after specific `/files/...` routes (healthz, list, download, stats) to prevent it from shadowing them.

### PR #495 - 2026-04-03
- **Feature**: Help documentation is now authored in Markdown (`help.md`). The help page renders the `.md` file content directly. The header "Help" button now displays a text label alongside the icon. Admins can edit the help content via the admin panel.

### PR #493 - 2026-04-02
- **Feature**: Add `plain_text_types` list to `atlas/config/file-extractors.json`. Files with a matching extension (e.g. `.py`, `.c`, `.txt`, `.md`) are now decoded directly from their base64 content and injected into the LLM context without requiring an external extractor service. Extensions are matched case-insensitively.

### PR #491 - 2026-04-02
- **Feature**: Models that declare `supports_tools: false` in `llmconfig.yml` now have tools and agent mode automatically stripped by the orchestrator, with user-visible warnings sent via a new `warning` WebSocket message type. Frontend shows capability icons (eye/wrench) in the model dropdown and yellow warning banners when incompatible features are selected.

### PR #475 - 2026-03-25
- **Feature**: Add `strict_role_ordering` config flag to `ModelConfig` for Mistral/Devstral models served via vLLM. When enabled, post-tool `system` messages are converted to `user` role and a bridging `assistant` message is inserted so the role sequence satisfies Mistral's strict ordering constraint.
- **Fix**: All LLM call paths (plain, tool-calling, streaming) now use a unified `_prepare_messages()` pipeline that chains existing sanitization with the new role enforcement.

### PR #473 - 2026-03-25
- **Fix**: `ps_agent_start.ps1` now forces UTF-8 encoding for the log file (`logs/app.jsonl`) and sets console output encoding to UTF-8 on Windows. This resolves the issue where Windows users saw Chinese/CJK characters in the Log Viewer — caused by PowerShell 5.1 writing UTF-16 LE by default, which Python's UTF-8 reader misinterpreted as CJK code points.

### PR #468 - 2026-03-25
- **Fix**: Filenames with special characters (`(`, `)`, `!`, `#`, `?`, `&`, etc.) are now properly sanitized to underscores in both the frontend and backend. Previously only whitespace was replaced, causing filenames like `my_cool_idea(!).pdf` to bypass document extraction and tool processing.

### PR #472 - 2026-03-25
- **Chore**: Replace all `requirements.txt` files with `pyproject.toml` in mock services and remove redundant ones from atlas MCP subpackages. Update Dependabot to track mock subdirectories. Dependabot now monitors `mocks/file-extractor-mock`, `mocks/multipart-extractor-mock`, `mocks/banyan-extractor-mock`, and `mocks/mcp-http-mock` for weekly dependency updates.

### PR #467 - 2026-03-24
- **Fix**: CI workflows (quay-publish, ci, build-artifacts) now inject the correct Vite build args: `VITE_APP_NAME=ATLAS`, `VITE_FEATURE_ANIMATED_LOGO=true`, `VITE_FEATURE_POWERED_BY_ATLAS=false`, and pass `GIT_HASH`/`APP_VERSION` to Docker builds.

### PR #466 - 2026-03-23
- **Feature**: Models that declare `supports_vision: true` in `llmconfig.yml` now receive attached image files as inline multimodal content blocks (OpenAI `image_url` format, translated by LiteLLM). The frontend shows image thumbnails with a vision indicator when a vision-capable model is selected.

### PR #461 - 2026-03-21
- **Fix**: MCP sessions now auto-reconnect when the underlying server process dies between tool calls. `ManagedSession.is_open` checks transport liveness via `client.is_connected()`, and `MCPSessionManager.acquire()` evicts dead sessions before opening a fresh connection.

### PR #449 - 2026-03-18
- **Fix**: Chat input search-glass button now clears all selected data sources and disables RAG when clicked while active (green), and opens the Data Sources sidebar when clicked while inactive (gray). Header Sources button only toggles sidebar visibility.

### PR #426 - 2026-03-18
- **Feature**: Add AI-generated follow-up question suggestion buttons after each chat response. Enabled via `FEATURE_FOLLOWUP_SUGGESTIONS_ENABLED=true`. Suggestions appear as clickable pill buttons below the messages and are cleared when a new message is sent.

### PR #420 - 2026-03-16
- **Enhancement**: Users can now paste images or documents directly into the chat input textarea to attach them, using the same flow as drag-and-drop file attachment.

### PR #431 - 2026-03-15
- **Feature**: Per-user MCP session isolation -- STDIO servers use `BlockedStateStore` to prevent cross-user state leakage; HTTP servers get per-user client routing for session isolation.
- **Fix**: Concurrent elicitation/sampling routing (#295) -- O(1) composite key lookup replaces broken server-name-only iteration.
- **Feature**: Session persistence per conversation with `MCPSessionManager`, adaptive background task polling, multi-prompt support with meta forwarding, and pluggable state backend (memory/redis).

### PR #420 - 2026-03-16
- **Enhancement**: Banner Messages admin card now displays the exact config file save path (e.g. `Config: /path/to/messages.txt`), consistent with how MCP Configuration shows its config path.

### PR #418 - 2026-03-13
- **Fix**: Canvas file downloads no longer return 401 errors behind a reverse proxy. Canvas files now use HMAC-tokenized `/mcp/files/download/` URLs (bypassing nginx `auth_request`) instead of hardcoded `/api/files/download/` paths.

### PR #412 - 2026-03-12
- **Fix**: Eliminate UI flash on startup by caching the last `/api/config` response in localStorage for instant hydration on page load, then reconciling with fresh data.
- **Enhancement**: Add `/api/config/shell` fast endpoint that returns feature flags, models, and app metadata without waiting for slow MCP tool/prompt and RAG source discovery.

### PR #409 - 2026-03-12
- **Release**: Bump version from 0.1.4 to 0.1.5.

### PR #407 - 2026-03-12
- **Enhancement**: Split Python dependencies into core vs. `mcp-demos` optional extra. Core install is now lighter; `uv sync --dev` or `pip install atlas-chat[mcp-demos]` pulls in matplotlib, pandas, numpy, and other demo-only packages.
- **Docs**: Added README section for extracting pre-built frontend from PyPI wheel on machines without Node.js.

### PR #403 - 2026-03-11
- **Feature**: Separate MCP and browser file download paths. MCP servers now use `/mcp/files/download/` (HMAC token auth, bypasses nginx `auth_request`) while browsers use `/api/files/download/` (nginx-injected `X-User-Email`). Fixes 401 errors when browser downloads went through the unauthenticated MCP path.

### PR #394 - 2026-03-10
- **Fix**: LLM errors (rate limit, timeout, auth, bad request) now propagate as domain-specific errors through the WebSocket to the frontend instead of causing the chat to hang indefinitely.
- **Fix**: Frontend error handler now resets agent UI state (step counter, pending question) and includes a 5-minute safety timeout that clears the stuck "thinking" indicator.
- **Enhancement**: Transient LLM errors (rate limit, timeout, 5xx) are now auto-retried up to 3 times with exponential backoff; auth errors raise immediately without retry.

### PR #366 - 2026-03-10
- **Upgrade**: Bump minimum FastMCP dependency from `>=2.10.0` to `>=3.0.0`. The codebase already used FastMCP 3.x-compatible APIs (`list_tools()`, `list_prompts()`, `Client` constructor), so no application code changes were needed.

### PR #390 - 2026-03-07
- **Fix**: Admin panel MCP server status now correctly excludes failed servers from connected list, shows per-server tool/prompt counts, and displays the active `mcp.json` file path so admins know which config file is being read and written.
- **Fix**: Add/remove server endpoints now properly reload MCP config instead of calling non-existent `reload_servers()` method; removed servers are cleaned up from clients, tools, and prompts caches.

### PR #389 - 2026-03-06
- **Fix**: RAG `is_completion` responses no longer bypass tools when both RAG and tools are active. The pre-synthesized RAG answer is injected as context so the LLM can still use available tools.

### PR #388 - 2026-03-06
- **Fix**: Remove `auth_request` from `/api/files/download/` nginx location block; the endpoint uses application-layer HMAC capability tokens for auth, and the nginx `auth_request` was causing 302 redirects for MCP servers and other non-browser clients.

### PR #384 - 2026-03-04
- **Fix**: Package install no longer silently ignores user config files. `atlas-server` now auto-detects a `config/` directory next to the loaded `.env` file when neither `--config-folder` nor `APP_CONFIG_DIR` is set. `atlas-init --minimal` now sets `APP_CONFIG_DIR=./config` in the generated `.env` by default.

### PR #373 - 2026-03-06
- **Fix**: Agentic loop strategy now appears in the Settings panel dropdown and the selected strategy is correctly sent to the backend via WebSocket (was previously undefined).
- **Fix**: Strip empty `tool_calls` arrays from messages before sending to LLM providers; OpenAI rejects messages where `tool_calls` is present but empty, which caused the agentic loop to fail when tools were enabled.

### PR #371 - 2026-02-26
- **Feature**: App version and git commit hash logged to browser console on startup (e.g. `Atlas v0.1.3 (a3f8b2c) | Built 2026-02-26T15:30:00Z`). Version injected at build time via Vite `define`, with Docker build-arg support. `/api/health` now includes `git_commit` field.
- **Fix**: Sync `atlas/version.py` to `0.1.3` to match `pyproject.toml`.

### PR #372 - 2026-02-27
- **Feature**: Animated logo on the welcome screen with 3D mouse-tracking tilt, floating bob, ambient glow, and paired energy pulse rings radiating from the thunderbird icon. Controlled by the `VITE_FEATURE_ANIMATED_LOGO` build-time flag (enabled by default).

### PR #367 - 2026-02-25
- **Feature**: 3-state chat save mode (issue #367). Users cycle between Incognito (nothing saved), Saved Locally (IndexedDB in browser), and Saved to Server (backend database). The selected mode persists across page refreshes via `usePersistentState`. New `localConversationDB.js` IndexedDB wrapper and `useLocalConversationHistory` hook provide browser-local conversation storage with the same API shape as the server-backed hook.

### PR #365 - 2026-02-24
- **Feature**: Globus OAuth integration for ALCF inference endpoints (issue #361). Users log in via Globus Auth to automatically obtain access tokens for ALCF and other Globus-scoped services, eliminating manual token copy-paste.
- **Feature**: New `api_key_source: "globus"` option for LLM models with `globus_scope` field to identify which Globus resource server token to use.

### PR #348 - 2026-02-24
- **Feature**: LaTeX rendering in assistant messages using KaTeX. Display math (`\[...\]`, `$$...$$`) and inline math (`\(...\)`, `$...$`) are rendered as formatted equations. LaTeX inside fenced code blocks and inline code spans is left as-is.

### PR #362 - 2026-02-24
- **Fix**: Conversation save/display duplication bug (issue #356). Backend now sends a `conversation_saved` WebSocket event with the `conversation_id` after persisting, so the frontend can track the active conversation and avoid optimistic UI duplicates in the sidebar.
- **Feature**: Download all conversations (issue #354). New "Download All Conversations" button in the sidebar exports all saved conversations with full messages as a JSON file via `GET /api/conversations/export`.

### PR #368 - 2026-02-23
- **Feature**: Update RAG discovery API to v2 format. Data sources now return `id`, `label`, `compliance_level`, and `description` fields. The `label` and `description` are displayed in the data sources panel with a more compact layout.

### PR #363 - 2026-02-23
- **Feature**: New `agentic` agent loop strategy (`APP_AGENT_LOOP_STRATEGY=agentic`) that mirrors the Claude Code / Claude Desktop tool-use pattern. Uses `tool_choice="auto"` with zero control tools (no `finished`, `agent_decide_next`, etc.), resulting in 1 LLM call per step instead of 3 (ReAct). Best suited for Anthropic models but compatible with all providers.

### PR #358 - 2026-02-22
- **Feature**: Parallel multi-tool calling support (issue #353). When an LLM returns multiple tool calls in a single response, all calls now execute concurrently via `asyncio.gather` instead of sequentially or only the first. Applies to all three agent loops (ReAct, Think-Act, Act) and the non-agent tools mode.

### PR #355 - 2026-02-22
- **Feature**: LLM token streaming for progressive response display. Tokens stream from the LLM provider through WebSocket `token_stream` events to the frontend, where they are buffered at 30ms intervals for smooth ~33fps rendering.
- **Refactor**: Extract streaming methods (`stream_plain`, `stream_with_tools`, `stream_with_rag`, `stream_with_rag_and_tools`) from `litellm_caller.py` into `LiteLLMStreamingMixin` in `litellm_streaming.py`, reducing the caller from 1009 to 726 lines.
- **Feature**: Add `stream_and_accumulate` shared helper for mode runners and `stream_final_answer` shared helper for agent loops to eliminate duplicated streaming+fallback logic.
- **Fix**: Handle `STREAM_TOKEN` interleaving with tool messages by using `findLastIndex(m => m._streaming)` instead of assuming the last message is the streaming target.
- **Fix**: Add error classification and propagation to frontend for streaming failures (rate limit, auth, timeout).

### PR #351 - 2026-02-21
- **Performance**: Make `atlas-init` start in <0.5s (down from ~4s) by using lazy `__getattr__` imports in `atlas/__init__.py`. The heavy dependency chain (SQLAlchemy, litellm, FastAPI) is now only loaded when `AtlasClient` or `ChatResult` is actually accessed.

### PR #350 - 2026-02-20
- **Feature**: Add `/api/heartbeat` endpoint for lightweight uptime monitoring. Bypasses authentication but is rate-limited to prevent abuse.

### PR #347 - 2026-02-20
- **Config**: Enable chat history with DuckDB by default in `.env.example` so new setups get conversation persistence out of the box.

### PR #344 - 2026-02-16
- **Feature**: Chat history persistence with DuckDB (local) and PostgreSQL (production) support. Conversations, messages, and tags are saved to a database and can be browsed, searched, loaded, and deleted from the sidebar.
- **Feature**: Incognito mode prevents conversation saving, with a clear visual indicator in the header.
- **Feature**: Alembic migration framework for chat history schema (no FK constraints for DuckDB compatibility).
- **API**: New REST endpoints at `/api/conversations` for listing, searching, CRUD, tagging, and bulk deletion.
- **Frontend**: Rebuilt sidebar with conversation list, search, tag filtering, and delete all. Incognito toggle in header.
- **Config**: New `FEATURE_CHAT_HISTORY_ENABLED` (default: false) and `CHAT_HISTORY_DB_URL` settings.

### PR #337 - 2026-02-13
- **Breaking**: Remove `requirements.txt` and consolidate all Python dependencies into `pyproject.toml` as the single source of truth. Development setup now uses `uv pip install -e ".[dev]"` instead of `uv pip install -r requirements.txt`.
- **Fix**: Remove eager `S3StorageClient()` instantiation from `atlas/modules/file_storage/__init__.py` that created an unnecessary S3 connection at import time regardless of the `USE_MOCK_S3` setting.
- **Fix**: Remove `PYTHONPATH` workaround from `agent_start.sh` and Dockerfiles -- editable install makes it unnecessary.

### PR #335 - 2026-02-14
- **Fix**: RAG no longer triggers automatically when data sources are selected. Selecting data sources now only marks availability; RAG is invoked only when explicitly activated via the search button toggle or the `/search` command.

### PR #334 - 2026-02-13
- **Fix**: Add exponential backoff with jitter to all frontend polling endpoints to prevent accidental backend DOS. Affects WebSocket health checks, log viewer, MCP status polling, and banner panel.
- **New**: Shared `usePollingWithBackoff` hook and `calculateBackoffDelay` utility for consistent backoff behavior across components.

### PR #333 - 2026-02-11
- **CI**: Update GitHub Actions versions in pypi-publish.yml: checkout v4->v6, setup-python v5->v6, setup-node v4->v6, upload-artifact v4->v6, download-artifact v4->v7. Combines Dependabot PRs #328-#332.

### PR #318 - 2026-02-10
- **Feature**: Per-user LLM API keys. Models can be configured with `api_key_source: "user"` in `llmconfig.yml` so users bring their own API keys, stored encrypted via the existing MCP token storage infrastructure.
- **API**: New REST endpoints at `/api/llm/auth/` for uploading, checking, and removing per-user LLM API keys.
- **Frontend**: Key icon in model selector shows authentication status; reuses `TokenInputModal` for key entry.

### PR #323 - 2026-02-09
- **Feature**: Use standard Office slide layouts (Title and Content) for PPTX generation instead of manual textboxes, with three-tier fallback: custom template file -> built-in layouts -> blank layout.
- **Feature**: Add template file discovery via `PPTX_TEMPLATE_PATH` environment variable and standard search paths (script directory, package config, user config).

### PR #324 - 2026-02-08
- **Fix**: `agent_start.sh` now respects the `ATLAS_HOST` environment variable instead of hardcoding host values. Previously, backend-only mode (`-b`) always bound to `0.0.0.0` and full startup always bound to `127.0.0.1`, ignoring the `.env` setting.

### PR #306 - 2026-02-08
- **Feature**: Add spinner animation and elapsed time counter to tool call status badges during active `calling`/`in_progress` states, with a timeout warning after 30 seconds.
- **Feature**: Make the global "Thinking..." indicator context-aware: shows "Processing tool results..." after tool completion and "Running tool..." during tool execution.

### PR #269 - 2026-02-08
- **Fix**: Frontend now validates persisted tool, prompt, and marketplace server selections against the current backend config on every config refresh, removing stale entries that no longer exist (#269).

### PR #317 - 2026-02-08
- **Feature**: Attach conversation history to user feedback by default (issue #307). Users see a checkbox (default on) in the feedback dialog. History is stored inline in the feedback JSON, and admins can view/download it.
- **Fix**: CSP middleware now reads settings dynamically per-request and parses CSP directives robustly instead of brittle string replace.
- **Fix**: FeedbackData model uses `Optional[str]` for `conversation_history` and `Field(default_factory=dict)` for `session`, with a 500K character limit on history.
- **Docs**: Updated feedback documentation in `/docs/admin/feedback.md` to describe the new `conversation_history` field, opt-in UI toggle, admin views, and size limit.

### PR #315 - 2026-02-07
- **Fix**: Bundle frontend into PyPI package so `atlas-server` serves the UI when installed via pip. CI now builds the frontend and copies it to `atlas/static/` before packaging.
- **Fix**: Resolve `runtime_feedback_dir` to an absolute path inside the project root instead of relative to cwd, preventing stray `runtime/` directories when running from arbitrary locations.

### PR #275 - 2026-02-04
- **Feature**: Make atlas installable as a Python package (`pip install atlas-chat`). Provides `AtlasClient` for programmatic use and CLI tools (`atlas-chat`, `atlas-server`) for command-line usage.
- **Refactor**: Rename `backend/` directory to `atlas/` for proper Python package structure with `__init__.py` exports.
- **CLI**: Add `atlas-server` command for starting the server with `--env`, `--config-folder`, `--port` options.
- **CI/CD**: Add GitHub Actions workflow for publishing to PyPI on release.
- **Fix**: Resolve test isolation issue where `test_capability_tokens_and_injection.py` was polluting `sys.modules` with a fake LiteLLMCaller, causing 25 tests to fail when run together.

### PR #TBD - 2026-02-04
- Add banyan-extractor-mock service for PDF and PPTX content extraction using banyan-ingest and Nemotron Parse, with pypdf fallback for PDFs when banyan-ingest is unavailable.
- Add pptx-text extractor configuration to file-extractors.json supporting PowerPoint file extraction.
- Fix f-string log sanitization in chat service file attachment error handling.

### PR #302 - 2026-02-04
- Fix help page width constraint so documentation content fills the full available width (#145)
- Add configurable timeouts (`MCP_DISCOVERY_TIMEOUT`, `MCP_CALL_TIMEOUT`) for MCP discovery and tool calls to prevent indefinite hangs (#298)
- Close #293 (f-string backslash SyntaxError was already resolved on main)

### PR #291 - 2026-02-04
- Fix `FEATURE_RAG_ENABLED` to fully disable RAG on the backend (not just the UI). When disabled, RAG services are not initialized and `rag-sources.json` is not loaded.
- Make RAG discovery and retrieval best-effort: a single failing RAG data source no longer prevents other sources from returning results. HTTP and MCP RAG discovery are independent, per-source errors are isolated, and null content is handled gracefully.

### PR #287 - 2026-02-03
- Add `_mcp_data` special injected argument for MCP tools. Tools that declare `_mcp_data` in their schema automatically receive structured metadata about all available MCP servers and tools, enabling planning/orchestration tools to reason about available capabilities.
- Add `tool_planner` MCP server that uses `_mcp_data` injection and MCP sampling to generate runnable bash scripts from task descriptions. Converts available tool metadata into an LLM-friendly CLI reference and uses `ctx.sample()` to produce multi-step scripts using `atlas_chat_cli.py`.

### PR #285 - 2026-02-02
- Fix document upload failure when filenames contain spaces by sanitizing filenames (replacing whitespace with underscores) in both frontend and backend.
- Fix S3 tag URL-encoding to properly handle special characters in tag values.

### PR #279 - 2026-02-01
- Make backend port configurable via `PORT` in `.env` instead of hardcoding 8000 in `agent_start.sh`, enabling git worktrees to run on different ports.
- Add git-worktree-setup Claude Code agent with automatic port conflict handling.

### PR #278 - 2026-01-30
- Replace boolean file extraction toggle with 3-mode system (`full` | `preview` | `none`) for fine-grained control over how file content is injected into LLM prompts.
- Add backward-compatible normalization of legacy config values (`"extract"` -> `"full"`, `"attach_only"` -> `"none"`).

### PR #276 - 2026-02-01
- RAG endpoints that return chat completions (LLM-interpreted results) are now returned directly without additional LLM processing
- Added `is_completion` flag to `RAGResponse` to detect when content is already interpreted
- UI displays a note when responses come from RAG completions endpoint
- Reduces unnecessary LLM API calls and processing time for RAG completions

### PR #274 - 2026-01-30
- **Feature**: Add multipart form-data upload support for file content extraction. Extractors can now use `request_format: "multipart"` to send files via multipart upload instead of base64 JSON, enabling compatibility with standard file upload APIs.
- **Config**: Add `form_field_name` field to extractor config for controlling the multipart form field name (default: `"file"`).

### PR #264 - 2026-01-28
- **Feature**: Add metrics logging for user activity tracking without capturing sensitive data. Logs LLM calls, tool usage, file uploads, and errors with only metadata (counts, sizes, types).
- **Feature**: Add `FEATURE_METRICS_LOGGING_ENABLED` environment variable to enable/disable metrics logging.
- **Privacy**: Metrics explicitly exclude prompts, tool arguments, file names, and error details - only non-sensitive metadata is logged.
- **Format**: All metrics use consistent `[METRIC] [username] event_type key=value ...` pattern for easy filtering and analysis.
- **Documentation**: Add comprehensive metrics logging documentation in `docs/metrics-logging.md` with examples and query patterns.

### PR #TBD - 2026-01-27
- **Feature**: Add non-interactive CLI (`atlas_chat_cli.py`) and Python API (`atlas_client.py`) for one-shot LLM chat with full MCP tools, RAG, and agent mode support. Enables scripted workflows, E2E testing, and MCP development without the browser UI.
- **Feature**: Add CLI event publisher for headless operation with streaming and collecting modes.
- **Architecture**: Add `initialize()` async method and `create_headless_chat_service()` to `AppFactory` for use outside FastAPI context.

### PR #TBD - 2026-01-26
- **Fix**: Add `:U` suffix to bind mounts in docker-compose.yml to fix permissions issues on some platforms where logs and config directories were owned by root instead of appuser.

### PR #250 - 2026-01-24
- **Feature**: Add support for displaying images returned by MCP tools via ImageContent. When MCP tools return ImageContent objects with base64-encoded images, Atlas now automatically extracts and displays them in the canvas panel.
- **Enhancement**: Images are automatically opened in the canvas panel for easy viewing, supporting PNG, JPEG, GIF, and other image formats.
- **Security**: Validate ImageContent base64 data and mime types against an allowlist of safe image types.
- **Testing**: Add comprehensive unit tests for ImageContent extraction, including single images, multiple images, mixed content, and edge cases.
- **Example**: Add image_demo MCP server demonstrating how to return images from tools.
- **Fix**: Correctly filter tool arguments when schema has empty parameters. Previously, tools with no parameters would incorrectly keep extra arguments instead of filtering them out.

### PR #253 - 2026-01-25
- **Feature**: Add per-user MCP API key, JWT, and bearer token authentication flow. Users can now authenticate with MCP servers that require API keys or tokens through the UI.
- **Feature**: Secure token storage with Fernet encryption. Tokens are encrypted at rest and isolated per-user.
- **Feature**: Add MCP Server Manager search filter on admin page for quickly finding servers by name, description, or author.
- **UI Enhancement**: Token input uses password field with show/hide toggle for security.
- **Fix**: Admin page "reload and reconnect" button now refreshes tools list without requiring F5.
- **Security**: Replace generic 500 error details with safe messages to prevent internal info leakage.

### PR #TBD - 2026-01-23
- **Fix**: Display configured app name instead of hardcoded "Chat UI" in the thinking spinner. Fixes #244.

### PR #245 - 2026-01-23
- **Fix**: Preserve line breaks in user messages by adding `whitespace-pre-wrap` CSS class. Previously, multi-line user input displayed as a wall of text without line breaks.

### PR #243 - 2026-01-23
- **UI Enhancement**: Implement responsive header with mobile hamburger menu for improved usability on small screens and mobile devices. Header controls collapse into a slide-out menu on screens smaller than 1024px, and button text labels are hidden on mobile while maintaining icon visibility.

### PR #237 - 2026-01-22
- **Fix**: Add exponential backoff to admin dashboard MCP status polling to prevent toast notification spam when backend is disconnected. Polling backs off from 1s to 30s max delay between retries, then continues polling at 30s intervals until the backend recovers.

### PR #TBD - 2026-01-23
- **Feature**: Add unified RAG configuration via `rag-sources.json`. Multiple RAG backends (HTTP and MCP) can now be configured in a single file.
- **Feature**: Add ATLAS RAG API integration with `AtlasRAGClient` supporting Bearer token auth with `as_user` impersonation.
- **Feature**: Add RAG feature toggle (`FEATURE_RAG_ENABLED`) and `/search` autocomplete command in chat UI.
- **Architecture**: Add `UnifiedRAGService` in `backend/domain/unified_rag_service.py` for aggregating RAG discovery and queries across multiple backends.
- **Architecture**: Add `RAGSourceConfig` and `RAGSourcesConfig` Pydantic models for type-safe configuration.
- **Architecture**: `LiteLLMCaller` now uses `UnifiedRAGService` for all RAG queries instead of a separate RAG client injection.
- **UI**: Integrate RAG feature toggle and search command handling in ChatArea and RagPanel components.
- **Fix**: Fix 404 error when querying ATLAS RAG API - server prefix is now properly stripped before calling the RAG API.
- **Config**: Support environment variable substitution (`${ENV_VAR}`) in `rag-sources.json` for secrets like bearer tokens.
- **Testing**: Add `mocks/atlas-rag-api-mock/` mock service with grep-based search for testing RAG integration.
- **Docs**: Update RAG documentation to reflect the new unified configuration approach.

### PR #234 - 2026-01-20
- **UI Enhancement**: Renamed "Chat UI Admin Dashboard" to "ATLAS Admin Dashboard" for consistency with branding.
- **UI Fix**: Moved toast notifications from top-right to top-center to prevent covering the "Back to Chat" button.

### PR #T231 - 2026-01-20
- **Fix**: Merged duplicate GEMINI.md and gemini.md files into a single GEMINI.md file to resolve case-insensitive filesystem conflicts on macOS.

### PR #225 - 2026-01-19
- **Feature**: Implement automatic file content extraction for uploaded PDFs and images. When enabled, files are processed by configurable HTTP extractor services and their content is included in the LLM context.
- **Feature**: Add mock file extractor service (`mocks/file-extractor-mock/`) supporting PDF text extraction, image analysis, and OCR endpoints for development and testing.
- **Feature**: Add API key and custom headers support to file extractor configuration for authenticating with external extraction services.
- **Feature**: Support `${ENV_VAR}` syntax in file extractor configuration for `api_key`, `headers`, and `url` fields, matching the pattern used by LLM and MCP configs.
- **Feature**: Add per-file extraction toggle in the UI, allowing users to control which files are extracted.
- **Config**: Add `file-extractors.json` configuration with extension-to-extractor mapping and service definitions.
- **Tests**: Add comprehensive tests for file extraction routes, content extractor, and API key/headers functionality.

### PR #215 - 2026-01-18
- **Fix**: Restored MCP sampling implementation, re-adding per-server sampling handlers and routing context so sampling tests can import `_SamplingRoutingContext` again.
- **Fix**: Re-enabled backend sampling workflows, ensuring the restored sampling handler uses LiteLLM preferences and the MCP client initializes with sampling support.
### PR #217 - 2026-01-15
- **Feature**: Add info icon (i) to prompts in the Tools & Integrations panel, matching the existing tool info icon behavior. Users can now click the icon to view prompt descriptions instead of relying on hover tooltips.
- **UX Enhancement**: Long prompt descriptions (>500 characters) are automatically truncated, showing the first 200 and last 200 characters with "..." in between, making very long prompts (100s of pages) more manageable.
- **UI Consistency**: Prompts now have the same expandable description UI as tools, improving discoverability and user experience.
- **Tests**: Add 6 comprehensive unit tests for prompt info icon functionality including expansion, truncation, and edge cases.
- **Demo/Test Data**: Add a super-long prompt description to the prompts MCP server to validate truncation behavior in the UI.

### PR #195 - 2026-01-13
- **Fix**: Fix file upload registration issue where files attached in one WebSocket connection were not visible in subsequent chat messages. The issue was caused by each ChatService instance creating its own session repository, preventing session sharing across connections.
- **Architecture**: Created a shared InMemorySessionRepository in AppFactory that is passed to all ChatService instances, ensuring sessions and attached files are properly shared across WebSocket connections.

### PR #211 - 2026-01-11
- **Feature**: Add drag and drop file attachment support to the chat area. Users can now drag files directly onto the chat interface to attach them to messages.
- **UI**: Visual overlay with dashed border appears when dragging files over the chat area, providing clear feedback.
- **Tests**: Add comprehensive frontend tests for drag and drop functionality (8 tests).

### PR #210 - 2026-01-12
- **Fix**: Treat approval-only elicitation (`response_type=None`) as expecting an empty response object on accept, preventing `approve_deletion` from failing when the UI returns placeholder data.
- **Tests**: Add backend regression coverage for approval-only elicitation accept payload normalization.

### PR #192 - 2026-01-10
- **File Access**: Add `BACKEND_PUBLIC_URL` configuration so remote MCP servers (HTTP/SSE) can download attached files via absolute URLs.
- **File Access**: Add optional `INCLUDE_FILE_CONTENT_BASE64` fallback to include base64 file content in tool arguments (disabled by default).
- **Docs**: Add troubleshooting and developer documentation for remote MCP file access configuration.
- **Tests**: Add coverage for absolute/relative download URL generation.
### PR #206 - 2026-01-11
- **Tools & Integrations Panel**: Display custom MCP server metadata (author, short_description, help_email) in the Tools & Integrations panel. Previously these fields from mcp.json were returned by the backend but not displayed in the UI.
- **UI Enhancement**: Add expandable description with "Show more details..." / "Show less" toggle to keep the UI compact while making full descriptions available on demand.
- **Tests**: Add 8 comprehensive unit tests for custom information display and description expansion functionality.
### PR #207 - 2026-01-11
- **Fix**: Keep loaded custom prompts available when switching back to the default prompt by separating loaded prompts from the active prompt selection.
- **Tests**: Add frontend regression coverage for prompt persistence when clearing the active prompt.

### PR #203 - 2026-01-10
- **Admin Panel**: Add User Feedback viewer card to admin dashboard with statistics display (positive/neutral/negative counts)
- **Admin Panel**: Add feedback download functionality supporting CSV and JSON export formats
- **Backend**: Add `/api/feedback/download` endpoint for exporting feedback data

### PR #201 - 2026-01-10
- **Fix**: Include feedback_router in main.py to fix 404 on /api/feedback endpoint. The feedback routes were defined but never registered with the FastAPI app.
- **Tests**: Add comprehensive test suite for feedback routes (13 tests) to prevent regression. Tests cover route registration, feedback submission, admin-only access controls, and deletion.

### PR #197 - 2026-01-08
- **Configuration**: Synchronized docker-compose.yml environment variables with .env.example. Added all missing feature flags, API keys, agent configuration, and other application settings to ensure Docker deployments have the same configuration options as local development.
- **CI**: Updated test container build to include `.env.example` and `docker-compose.yml` so docker env sync tests can run.

### 2026-01-07 - Elicitation Routing Fix and Testing
- **Fix**: Resolve elicitation dialog not appearing by switching from `contextvars.ContextVar` to dictionary-based routing. The MCP receive loop runs in a separate asyncio task that cannot access context variables set in the tool execution task. Now uses per-server routing with proper cross-task visibility.
- **Fix**: Add `setPendingElicitation` to WebSocket handler destructuring so dialog state updates work correctly.
- **Fix**: Add `sendMessage` to ChatContext exports so ElicitationDialog can send responses.
- **Fix**: Close elicitation dialog after user responds (accept/decline/cancel).
- Add comprehensive logging to trace `update_callback` flow from WebSocket to MCP tool execution.
- Add validation and fallback mechanism in `ToolsModeRunner` to ensure update_callback is never None during tool execution.
- Create per-server elicitation handlers using closures to capture server_name for proper routing.
- **Tests**: Add comprehensive unit tests for elicitation routing (8 backend tests, 7 frontend tests) to prevent regression.

### PR #191 - 2026-01-06
- **MCP Tool Elicitation Support**: Implemented full support for MCP tool elicitation (FastMCP 2.10.0+), allowing tools to request structured user input during execution via `ctx.elicit()`. Includes backend elicitation manager, WebSocket message handling, and a modal dialog UI supporting string, number, boolean, enum, and structured multi-field forms.
- **Elicitation Demo Server**: Added `elicitation_demo` MCP server showcasing all elicitation types including scalar inputs, enum selections, structured forms, multi-turn flows, and approval-only requests.
- Fix elicitation handler integration to use `client.set_elicitation_callback()` instead of passing as kwarg (resolves FastMCP API compatibility).
- Admin UI: Fix duplicate "MCP Configuration & Controls" card rendering.
- Admin UI: Clarify MCP Server Manager note that available configs are loaded from `atlas/config/mcp-example-configs/`.

### PR #190 - 2026-01-05
- Add a "Back to Admin Dashboard" navigation button to the admin LogViewer.

### PR #184 - 2025-12-19
- Add configurable log levels for controlling sensitive data logging. Set `LOG_LEVEL=INFO` in production to prevent logging user input/output content, or `LOG_LEVEL=DEBUG` for development/testing with verbose logging.
- Fix logging in error_utils.py to prevent full LLM response objects from being logged at INFO level.
- Redact tool approval response logging so tool arguments are never logged at INFO.
- Remove unused local variables in test_log_level_sensitive_data.py (code quality improvement).

### PR #180 - 2025-12-17
- Add MCP Server Management admin panel and update Admin Dashboard panel layout.

### PR #181 - 2025-12-17
- Add unsaved changes confirmation dialog to tools panel

### PR 177 Security Fixes - 2025-12-13
- **SECURITY FIX**: Fixed MD5 hash usage in S3 client by adding `usedforsecurity=False` parameter to address cryptographic security warnings while maintaining S3 ETag compatibility
- **SECURITY FIX**: Enhanced network binding security by making host binding configurable via `ATLAS_HOST` environment variable, defaulting to localhost (127.0.0.1) for secure development while allowing 0.0.0.0 for production deployments
- Updated Docker configuration to properly handle new host binding environment variable
### PR #176 - 2025-12-15
- Add Quay.io container registry CI/CD workflow for automated container publishing from main and develop branches
- Update README and Getting Started guide with Quay.io pre-built image information

### PR #173 - 2025-12-13
- Increase unit test coverage across backend and frontend; add useSettings localStorage error-handling tests and harden the hook against localStorage failures.
### PR #169 - 2025-12-11
- Implement MCP server logging infrastructure with FastMCP log_handler
- Add log level filtering based on environment LOG_LEVEL configuration
- Forward MCP server logs to chat UI via intermediate_update websocket messages
- Add visual indicators (badges and colors) for different log levels (debug, info, warning, error, alert)
- Create comprehensive test suite for MCP logging functionality
- Add demo MCP server (logging_demo) for testing log output

### PR #172 - 2025-12-13
- Resolve all frontend ESLint errors and warnings; update ESLint config and tests for consistency.
### PR #170 - 2025-12-12
- Improve LogViewer performance by memoizing expensive computations with `useMemo`


### PR  163 - 2024-12-09
- **SECURITY FIX**: Fixed edit args security bug that allowed bypassing username override security through approval argument editing
- Added username-override-demo MCP server to demonstrate the username security feature
- Server includes tools showing how Atlas UI prevents LLM user impersonation
- Added comprehensive documentation and example configuration

### PR #158 - 2025-12-10
- Add explicit "Save Changes" and "Cancel" buttons to Tools & Integration Panel (ToolsPanel)
- Add explicit "Save Changes" and "Cancel" buttons to Data Sources Panel (RagPanel)
- Implement pending state pattern to track unsaved changes
- Save button is disabled when no changes are made, enabled when changes are pending
- Changes only persist to localStorage when user clicks "Save Changes"
- Cancel button reverts all pending changes and closes panel
- Updated tests to verify save/cancel functionality


### PR #156 - 2024-12-07
- Add CHANGELOG.md to track changes across PRs
- Update agent instructions to require changelog entries for each PR

## Recent Changes

### PR #157 - 2024-12-07
- Enhanced ToolsPanel UI with improved visual separation between tools and prompts
- Added section headers with icons for tools and prompts
- Updated color scheme to use consistent green styling for both tools and prompts
- Added horizontal divider between tools and prompts sections
- Increased font size and weight for section headers
- Improved vertical spacing between UI sections

### PR #155 - 2024-12-06
- Add automated documentation bundling for CI/CD artifacts
