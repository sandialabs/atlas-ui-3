# Changelog

All notable changes to Atlas UI 3 will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### PR #868 - 2026-08-29
- **Typing in the composer no longer shifts previously rendered messages** (#866): two separate per-keystroke problems made the previous response jump down and snap back, most visibly once the composer had grown to 2-3 lines. First, `Message` is wrapped in `memo`, but `ChatArea` handed it a freshly allocated `onCorrect` arrow on every render, so the memo never hit and every keystroke re-rendered and re-reconciled the entire transcript -- markdown, KaTeX and highlight subtrees included -- which the `MutationObserver` then answered with a *smooth* (animated) scroll-to-bottom. The per-index handlers are now memoized on the message list, taking a 3-line message from 58 `Message` renders to 2. Second, auto-sizing the composer collapsed the textarea to `height: auto` to read `scrollHeight`; because the composer is a flex sibling of the scrollable message list, that collapse momentarily grew the list's viewport, shrinking its scrollable range so the browser clamped `scrollTop` — and the clamp survived the re-expansion. Composer sizing now lives in `utils/composerAutoResize`, which snapshots the transcript's offset (or its pinned-to-bottom state) and restores it around every height mutation, including the reset on send.

### PR #867 - 2026-08-28
- **Links in LLM chat responses now open in a new tab instead of navigating away from ATLAS** (closes #859): the marked link renderer already emitted `target="_blank" rel="noopener noreferrer"` on external links, but the rendered HTML is sanitized with `DOMPurify.sanitize(html, DOMPURIFY_CONFIG)` before being injected into assistant messages, and DOMPurify 3.x strips the `target` attribute by default (it is no longer in the built-in allowlist). Only `rel="noopener noreferrer"` survived, so every link opened in the same tab. `target` is now explicitly added to `DOMPURIFY_CONFIG.ADD_ATTR` in `frontend/src/utils/markdownRenderer.js`, so the sanitizer preserves the `target="_blank"` the renderer emits. External links and bare/autolinked URLs open in a new tab; in-app fragment links (`#section`, used by citation-badge / references scrolling) are unchanged because the renderer already omits `target` for those, keeping them same-tab.

### PR #865 - 2026-08-28
- **`USE_NEW_FRONTEND` is removed and the old-frontend code path behind it is deleted**: the flag was always `true` and the backward-compatibility branch it guarded (skip the frontend build in `agent_start.sh` / `ps_agent_start.ps1`) referenced a frontend that no longer exists. The env var is dropped from `.env.example`, `docker-compose.yml`, and `Dockerfile-test`, and the skip-build `if` block is removed from both startup scripts so the frontend is always built. **Operators:** remove any `USE_NEW_FRONTEND` line from your existing `.env`, `docker-compose.yml` environment block, and `-e`/`--env-file` overrides — it is now ignored and the frontend will always be built. The historical CHANGELOG entry for PR #863 (which documented adding the flag to the PowerShell script) is left intact as a record.
- **`build_frontend` / `Build-Frontend` now check the `npm run build` exit code before replacing `atlas/static`**: previously the scripts ran `rm -rf atlas/static` unconditionally after `npm run build`, so a failed build wiped the served assets. Now the build must succeed before the copy proceeds; on failure the existing assets are preserved and the function returns an error. This pre-existing issue was exposed by removing the flag's skip-build escape hatch.

### PR #862 - 2026-08-28
- **`atlas_search` is a tool the model has to call, not something RAG does by itself**: selecting data sources used to route the turn through `call_with_rag_and_tools` / `stream_with_rag_and_tools`, which queried every selected source *before* the model was asked anything and inserted the passages as a `Retrieved context from ...` system message. The search was invisible in the UI, the query came from the conversation rather than from a decision, every turn paid for a retrieval round trip, and the model could never read a result and search again. Agent mode and tools mode now always take the plain `call_with_tools` / `stream_with_tools` path; retrieval happens only when the model calls `atlas_search({query, max_results?, depth?})`, and the passages come back as that call's tool result -- rendered in the chat like any other tool call. The two RAG-injecting caller methods, their `LLMProtocol` declarations and the RAG branch in `safe_call_llm_with_tools` are deleted rather than left unused, so the silent path cannot quietly come back. RAG mode (no tools) is unchanged: with no tools schema and no model turn to spend, pre-injection is the design there.
- **A data source selection now scopes the search and offers the tool, instead of triggering one**: the selection is still the ceiling on what `atlas_search` may read (intersected with the user's authorized sources in `mcp_execution`, unchanged), and it additionally makes `atlas_search` available as if the user had ticked it -- gated on `FEATURE_RAG_ENABLED` and `FEATURE_ATLAS_RAG_TOOLS_ENABLED`, and never duplicated when the tool is already selected under its current or pre-#855 name. Without this a user who picked a source but not the tool would get a selection that silently did nothing, which is the same failure mode in the other direction. Legacy names (`atlas_rag_query`) and saved conversations are unaffected.
- **The implied search tool is resolved before the agent-mode guard, not after** (review feedback): agent mode falls back to a plain chat turn when no tools are selected, and the frontend blocks the send outright -- so "sources selected, nothing else ticked" was downgraded before the tool those sources imply ever existed. `ChatOrchestrator._resolve_search_tool` now runs ahead of that guard (and the frontend guard counts selected sources), which is what makes the source-only agent workflow actually reachable.
- **`FEATURE_RAG_ENABLED=true` with `FEATURE_ATLAS_RAG_TOOLS_ENABLED=false` no longer loses retrieval silently** (review feedback): that combination is supported, and a turn that has both MCP tools and data sources selected runs in tools mode, where nothing reads those sources once the pre-injection path is gone. The user is now told their sources were not searched and what to do about it, rather than handed an answer that quietly skipped their evidence. A turn with sources and no tools still routes to RAG mode, which reads them itself, and a turn that already names the search tool -- including under its pre-#855 spelling `atlas_rag_query` -- is never warned about.
- **PR-validation scripts that exercised the deleted methods are retired or rewritten** (review feedback): `test_pr389`, `test_pr276`, `test_pr844` and `test_pr850` each kept the checks that still describe live behaviour and replaced the RAG+tools ones with a note explaining what superseded them; `test_pr389` now guards that the pre-injection path stays deleted. New `test_pr862_search_explicit_tool_call.sh` covers the explicit-call behaviour end to end.

### PR #863 - 2026-08-28
- **`ps_agent_start.ps1` reaches feature parity with `agent_start.sh`**: added `Initialize-ChatHistoryDb` (DuckDB + PostgreSQL), `-e`/`-h`/`--env-file`/`--help` flags and `ATLAS_ENV_FILE` support, `USE_NEW_FRONTEND` gating (now respected by both scripts), a quote- and comment-aware `.env` loader, `ATLAS_ENV_FILE` export to children, a loud error for a missing explicit env file, and the correct init order (env → runtime → MinIO → chat history). Added `mocks/mcp-http-mock/run.bat`; the MCP mock is now started directly (python PID tracked for clean shutdown) and neither runner echoes token values.

### Fix - atlas_sleep heartbeat - 2026-08-27
- **The `atlas_sleep` tool no longer appears to hang on long waits**: a 900-second sleep could show 79 minutes of elapsed time because the tool did a single `asyncio.sleep(seconds)` with zero `tool_progress` events — if the `tool_complete` frame was lost (WebSocket drop, backend error), the frontend row stayed in `calling` status forever with the clock ticking and the generic "taking longer than expected" warning. The wait is now broken into heartbeat intervals (capped at 30s) that emit `tool_progress` frames, so the frontend knows the sleep is alive, transitions to `in_progress`, and shows the actual (clamped) duration. The frontend `ToolElapsedTime` clock uses the heartbeat's `total` field (which reflects per-call/turn clamping) instead of the raw requested `seconds`, and escalates from "completing..." to "connection may be lost" (red) when the clock runs more than 60s past the wait without a `tool_complete` — so a genuinely stuck sleep is visually distinct from one that is wrapping up.

### Repo cleanup - 2026-08-27
- **Removed 11 PR-evidence screenshots that no doc or code references** (~1.1 MB): `docs/developer/images/pr500-citations-screenshot.png`, `pr637-tiff-vision-proof.png`, `pr650-chat-export-active-prompt-ui.png`, `pr650-chat-export-prompt-e2e.png`, `pr664-agent-multi-tool-chain-2.png`, `rag-snippets-collapsed.png`, `rag-snippets-expanded.png`, `rag-snippets-live-pipeline.png`, `wormhole-e2e-console-2026-06-09.png`, `wormhole-e2e-dashboard-2026-06-09.png`, and `docs/readme_img/screenshot-10-24-2025.png`. These were review evidence attached to past PRs that landed in the tree rather than staying on the PR thread; every image still referenced by a doc or test is untouched. Same cleanup as #853.

### PR #857 - 2026-08-27
- **MCP tools that return a typed object no longer crash with `Object of type Root is not JSON serializable`**: FastMCP 3.x advertises an auto-generated `output_schema` for every tool, and for a typed-object return (typed dict / pydantic model / dataclass) the schema has `properties` but no `title` (titles are pruned). On the client side, `_parse_call_tool_result` validates the response `structuredContent` against that schema and rebuilds a pydantic model/dataclass named `Root`, so `CallToolResult.data` arrived as a model instance, not a plain dict. `_normalize_mcp_tool_result` only handled the dict case, wrapped the instance as `{"results": <Root>}`, and `execute_tool`'s `json.dumps` then raised, failing the whole tool call — any MCP tool returning a typed object (including third-party servers) hit this. `atlas/modules/mcp_tools/mcp_result_processor.py` now coerces `data` and `structured_content` to plain JSON-able Python via `pydantic_core.to_jsonable_python` on both extraction paths (so a `Root` instance round-trips back to its field dict), and the final tool-result `json.dumps` carries a `default=str` backstop. Scalar (`str`) and `Dict[str, Any]` returns are unaffected — their schemas map to `str` / `dict[str, Any]` and never build a `Root` model, which is why the in-repo demo servers that return `str` never surfaced it.

### PR #856 - 2026-08-27
- **`canvas`, `sleep` and `search` are now one built-in `atlas` server** (closes #855): the three non-MCP pseudo-servers (`canvas`/`canvas_canvas`, `atlas_agent`/`atlas_agent_sleep`, `atlas_rag`/`atlas_rag_query` + `atlas_rag_discover_data_sources`) showed up in the tools panel as three separate servers for four tools, and each carried its own copy of the "special-case this pseudo-server" branch through discovery, schema building, the tool index, execution dispatch, the authorization allow-list and `/api/config`. They are now one server, `atlas`, exposing `atlas_canvas`, `atlas_sleep` and `atlas_search`, with `atlas/modules/mcp_tools/atlas_server.py` (mirrored by `frontend/src/constants/atlasTools.js`) as the single source of truth for the names, schemas and gating. The old fully-qualified names are still accepted and normalized at the edges, so a browser holding `canvas_canvas` in `localStorage` and a saved conversation replaying it both keep working with no reset.
- **`atlas_search` takes a single `query` argument and reads the selected data sources**: `data_sources` and `mode` are gone from the model-facing schema — sources come from the user's RAG panel selection (falling back to every source the user is authorized for when nothing is selected) and the mode is always `raw`, because a tool call should hand the model evidence to reason over rather than a backend-written answer. Model-supplied `data_sources`/`mode` on an `atlas_search` call are ignored, so a model cannot reach past the user's selection to another authorized source; the existing authorization intersection against the user's discovered set still applies underneath. `atlas_rag_discover_data_sources` is no longer advertised (search reads the UI selection, so there is nothing for a discover step to feed) but still executes for old conversations.
- **The magnifying-glass button is gone from the chat input**: it toggled RAG and opened the data source panel in one control, duplicating a decision that belongs in the tools panel. Selecting `atlas_search` is now how a user turns search on, and the sources panel is still reachable from the header. A turn counts as RAG-activated whenever `atlas_search` is selected, so "search on, no sources picked" means "everything I can reach" rather than "nothing".
- **Two quiet-breakage paths the rename opened are closed** (review feedback): `matcher` in `config/hooks.json` is an operator-written regex over the tool name, and a `PreToolUse`/`PermissionRequest` hook may be a deny or approval policy — renaming `canvas_canvas` to `atlas_canvas` would have silently retired it. `HookConfig.matches` now tests the pre-#855 spelling as well, one way only (a matcher on the new name does not start matching the old one, so no hook's reach widens). Separately, an `mcp.json` server named `atlas` — or `canvas`/`atlas_agent`/`atlas_rag` — would have connected and been discovered but then been unreachable, because the built-in names short-circuit the tool index, `/api/config` and execution dispatch; those names are now reserved and dropped at config load with an error naming the collision.
- **A disabled built-in is refused at execution, not only omitted from the schema** (review feedback): omitting `atlas_search` from the schema stops the model being *offered* it, but a replayed conversation or a non-UI client can still name it, so execution now checks the RAG flags and refuses with a clear error — matching how `atlas_sleep` has always been gated.
- **`atlas_discover_sources` is a first-class tool again** (follow-up on #856): it was dropped from the advertised set on the grounds that search reads the UI selection, so nothing needed to feed it -- too narrow a reading of what it is for. It answers two questions the model cannot otherwise answer: *which corpus did this come from*, and *is what the user is asking about indexed anywhere they can reach* -- the difference between "I found nothing" and "you have no source that would contain this". It takes no arguments (the authenticated user decides the result, and the user is never a model input), is gated by the same RAG flags as `atlas_search`, and the pre-#855 name `atlas_rag_discover_data_sources` now normalizes onto it rather than onto search.
- **`atlas_search` gains two optional retrieval knobs, `max_results` and `depth`**: `query` is still the only *required* argument. `max_results` caps passages per source (clamped to 50); `depth` is `quick` | `standard` | `deep` -- words rather than numbers, so the model says how hard to look and ATLAS decides what that costs. The line matters: these change *how much* comes back, never *which sources are reachable*, which is why `data_sources` stays out of the schema and these two are safe to take from the model. Both are coerced and clamped in `search_kwargs_for` before they leave the process, so a nonsense value degrades to the source default instead of reaching a backend. They map onto the ATLAS RAG **v2** `search_kwargs` block (`top_k_final`, `rerank`, `top_k_vector`, `expanded_window`); **v1 has no equivalent on the wire and ignores both**, since its request body is the conversation plus a model name with no retrieval knobs at all. Because the v2 client treats an explicit `search_kwargs` as the whole block, `_query_http_client` folds the source's configured `top_k` in as a default first -- otherwise a `depth`-only call would quietly drop it.
- **The "Server:" label under a tool call is asked for rather than guessed**: it was derived by splitting the fully-qualified name on its last underscore, which reads `atlas_discover_sources` as the server `atlas_discover` -- and, long before this change, `pptx_generator_markdown_to_pptx` as `pptx_generator_markdown_to`. Both halves of a `server_toolName` can contain underscores, so the split can never be right in general; `notify_tool_start` now asks the tool manager which server owns the tool and keeps the split only as a fallback for when the index is not built. It is a display label, so a lookup failure degrades to the old guess instead of failing the call.
- **The built-in `atlas` server is pinned to the top of the tools panel and the marketplace**: `sortAtlasFirst` lifts it out of the server list and leaves the order of everything else untouched, so the built-ins are in the same place whether a user has one MCP server selected or twenty.
- **A disabled built-in drops out of the tool list instead of taking its server with it**: `atlas_sleep` still requires `AGENT_SLEEP_MAX_SECONDS > 0` and `atlas_search` still requires `feature_rag_enabled` *and* `feature_atlas_rag_tools_enabled`, but the `atlas` server itself stays visible because `atlas_canvas` does not depend on either. Disabled built-ins are omitted from the schema rather than advertised and then refused — agent mode reaches the loop without ACL filtering, so a tool in the schema costs a step before execution can reject the call.

### PR #854 - 2026-08-27
- **A message sent while the agent loop is running now steers it instead of racing it** (closes #824): in agent mode, a second chat used to overwrite the in-flight task pointer and start a *concurrent* `handle_chat` coroutine appending to the same `session.history` — a race that corrupts the transcript. It is now pushed onto a `SteeringChannel` and injected as a **normal user turn** at the next iteration boundary, so an in-flight tool call finishes first, the steering text reaches the LLM on the next step, and the loop is neither broken nor stopped. The agent runner activates the channel only while a loop is genuinely consuming, so a turn that requested agent mode but fell back to a non-agent turn never swallows the message undrained. A steer that arrives while the model is producing its would-be final answer folds that answer in as display-only `agent_intermediate` narration and continues rather than ignoring it. The injected message is a plain USER message in history (no display-only `message_type`), so it counts toward rewind ordinals and is visible to later turns — “land as a normal user turn”, per the issue.

### PR #853 - 2026-08-26
- **The top bar no longer overlaps itself when the sidebar is open**: at a 1280px viewport with the conversations sidebar open, the header's trailing four buttons (Agent Portal, Tools, File Manager, Canvas) were laid out from x=1279 to x=1491 -- entirely past the header's right edge at x=1280, so they were unreachable -- while the model selector painted over the save-mode button. The cause was a unit mismatch: the desktop button cluster was gated on the **viewport** media query `min-[1280px]:flex`, but the header is laid out inside `flex-1` beside a 256px sidebar, so at a 1280px viewport the header itself is only 1024px wide. The cluster switched on a full sidebar-width before there was room for it, and since the fixed-size icon buttons cannot shrink, the surplus became overflow and collision rather than a graceful squeeze. A new `useElementWidth` hook measures the header itself and `DESKTOP_ACTIONS_MIN_WIDTH` (1320px of header width, with headroom over the measured 1289px fit point) gates the cluster on that. A CSS `@container` query would be the natural fix but is unusable here: `container-type: inline-size` applies layout containment, which would make the header a containing block for the three `position: fixed` descendants it holds (the menu backdrop, the menu panel, and the API key modal), re-anchoring all three to the header instead of the viewport.
- **The header stays one row tall, and a long username can no longer recreate the collision**: the "Sources", "New Chat", and save-mode labels had no `whitespace-nowrap`, so under flex pressure the text wrapped onto a second line and the bar grew from 69px to 89px -- which also invalidated the mobile menu panel's hardcoded `top-[57px] sm:top-[65px]` offset, since that assumes a single-row header. The username in the desktop cluster is now `truncate max-w-[12rem]` with a `title`: it is the one piece of unbounded content in the bar, and without a bound a long address pushes its neighbours and reproduces the overlap above any threshold. The compact menu's overlay is gated on the render (`mobileMenuOpen && !showDesktopActions`) rather than on an effect: the panel and backdrop used to be hidden by the same CSS breakpoint that hid the hamburger, and since effects run after paint, closing it from an effect would leave the backdrop covering the desktop cluster for a frame -- long enough to swallow a click -- while the header was widened past the threshold. Overlay and hamburger now flip in the same commit; the effect remains only so the menu does not spring back open if the header narrows again. Verified in-browser across 320px-2560px with the sidebar open and closed: zero overlapping control pairs and zero content past the header's right edge at every width.

### PR #850 - 2026-08-26
- **A truncated tool call no longer kills the conversation permanently**: when a model ran out of output tokens partway through a tool call, the streamed `arguments` arrived as an unparseable fragment (e.g. `{"filename": "1787784579_..._topic`). The streaming accumulator concatenated the deltas with no validation and appended the fragment verbatim to the assistant message, and because providers re-parse every tool call in the history on each request, that one fragment made every subsequent turn fail with `OpenAIException - Unterminated string starting at: line 1 column 73` -- a 400 no retry could clear, since the poison was in the history, not the request. Tool calls whose arguments do not parse as JSON are now dropped at the point they are accumulated, in both the streaming and non-streaming paths; well-formed sibling calls in the same turn still run. When nothing usable is left, the turn fails with a new `LLMMalformedToolCallError` (error type `malformed_tool_call`) that -- unlike the provider rejection it replaces -- tells the user the failure is transient and worth retrying, and uses `finish_reason` to distinguish an output-limit truncation from a model that simply emitted bad JSON. `_raise_llm_domain_error` now passes an already-classified `LLMError` through untouched so the precise message is not demoted to a generic service error. Empty arguments still count as valid (models legitimately emit `""` for no-argument tools).
- **Streamed narration no longer hides a dropped tool call** (review feedback on #850): both streaming consumers suppress a mid-stream error once any text has been streamed, which is right for a transport fault but wrong here -- the model announced work, the call was dropped, and the narration was saved as a successful final answer that silently skipped it. `AgenticLoop._call_llm_streaming` now re-raises `LLMMalformedToolCallError` even after partial text (closing the open token stream first so the UI does not leave a live cursor), and `ToolsModeRunner.run_streaming` sends the `malformed_tool_call` error frame regardless of accumulated content. The two `call_with_rag*` domain-error passthrough tuples now catch `LLMError` rather than enumerating its subclasses, so a newly added member is never silently downgraded into the fallback retry that discards the injected RAG context.
- **A call truncated before its first argument is caught too, and the executor no longer guesses at cut-off values** (review feedback on #850): a tool call cut off before any argument delta arrives has `arguments == ""`, which parses fine as "no arguments" and would execute with `{}` -- for a tool whose parameters are all optional, the wrong action performed silently. When `finish_reason == "length"` the last call is now also dropped if its arguments are empty; earlier calls in the same response completed before the limit and are still honoured. Separately, `_try_repair_json` in the tool executor used to close an open string value, which turned the production fragment `{"filename": "1787784579_..._topic` into a *different, valid-looking* filename the tool would have executed against. It now balances missing braces only: a repair may complete the shape of an object, never the content of a value. `docs/developer/error-flow-diagram.md` documents the new error type, its retryable semantics, and the boundary between the two layers.
- **Brace-only damage is repaired rather than rejected, and a partial drop is no longer invisible** (review feedback on #850): some models emit tool arguments without the enclosing braces (`"q": "hi"`), a shape repaired and executed long before the guard existed -- failing the turn over it was a regression. The guard now runs one structural repair (`repair_structural_json`, braces only) before declaring a call malformed and writes the repaired string **back onto the call**, so the history copy is parseable on every later request; the executor's `_try_repair_json` delegates to the same function so the two layers cannot drift apart. When some calls are dropped but others survive, `LLMResponse.dropped_tool_calls` carries the discarded names, both mode runners publish a `warning` frame from it, and a `malformed_tool_call` metric records the drop. Neither mode saves a failed turn empty any more: tools mode persists the narration it already streamed (flagged `incomplete`) instead of losing it on reload, and agent mode closes the turn with a terminal message, matching the interrupted-turn contract. `finish_reason` is allow-listed against the known OpenAI vocabulary before it reaches a log line or span attribute, and the truncation copy now leads with "asking for one thing at a time" rather than starting a new conversation, which shortens the input and cannot relieve an output-token limit.
- **A truncated call is never brace-repaired, and an unparseable one never runs with `{}`** (review feedback on #850): brace-balancing a *mid-object* truncation (`{"path": "/data", "recursive": true`) produces valid JSON whose remaining keys were silently dropped -- it executes and is written to history looking entirely well-formed -- so the structural repair is now skipped for the last call of a truncated response. In the executor, a failed repair raises `ToolError` instead of falling back to `parsed_args = {}`, which for an all-optional-parameter tool succeeded and returned a plausible result for a request the user never made. The guard moved out of `models.py` (data models) into `atlas/modules/llm/tool_call_guard.py`, which is where the dropped-call warning copy now lives too -- one shared implementation for both mode runners, saying which cause applied, pluralizing, and no longer claiming the surviving calls have already run when it is published before they execute. Agent mode now keeps the narration it streamed before a failure, matching tools mode.
- **The dropped-call warning reaches the non-streaming paths, and the persisted copy names the real cause** (review nits on #850): the partial-drop warning was wired into the streaming paths only, so with streaming off a dropped sibling call was still silent; `publish_dropped_call_warning` is now one helper called from every path that produces an `LLMResponse`. Agent mode's turn-closing text always said the call "was cut off" even when the failure was plain invalid JSON, and that text is persisted into history -- `LLMMalformedToolCallError` now carries `truncated` and the persisted copy branches on it. Truncation detection for the *repair policy* widened from `finish_reason == "length"` to any unclean finish (`response_was_cut_off`), so a `content_filter` stop or an unrecognized provider reason no longer takes the brace-repair path; a missing `finish_reason` still counts as clean, since providers routinely omit it. The user-facing "ran out of room" copy stays keyed to the output limit specifically, because a content filter cutting a response off is not the model running out of room.
- **The truncation flag survives reclassification, and the repair keys on shape rather than `finish_reason` alone** (review nits on #850): `safe_call_llm_with_tools` rebuilt `LLMMalformedToolCallError` without forwarding `truncated`, so on that path the turn-closing text persisted into history always reported the wrong cause. The structural repair now splits on shape: text that opens with a brace and never closes it is the cut-off signature, so supplying the closing brace requires positive evidence the response finished, while text missing the *opening* brace is a sloppy envelope and is always repaired -- which is what lets an absent `finish_reason` (routinely omitted on accumulated chunks) keep the envelope repair without letting a truncated object be completed with keys the model never sent. The ~20-line guard block that was duplicated across the streaming and non-streaming callers is now one `_guard_tool_calls()` helper, so the policy and the `finish_reason` rule behind the user-facing copy cannot drift apart.

### PR #849 - 2026-08-26
- **RAG mock v2 and ATLAS client aligned to the v0.8.0 OpenAPI schema** (closes #791 follow-up): the v2 wire contract previously used a bespoke `{query, corpora, mode, top_k, filters, synthesis_params}` → `{query, mode, results: {hits|answer+citations}, metadata: {response_time_ms, corpora_searched}}` shape that did not match the published schema. Both the mock (`mocks/atlas-rag-api-mock/main.py`) and the client (`atlas/modules/rag/atlas_rag_client.py`) now speak the v0.8.0 contract: the request body is `{query, corpora, search_kwargs}` (with a full `SearchKwargs` model — `top_k_final`, `rerank`, `rank_strategy`, `threshold`, etc.) and the response is `{response, metadata: {response_time, references: [{filename, sections: [{text, relevance}], reference}]}}`. `mode` (raw/synthesized) is now a client-side interpretation knob, never sent on the wire: `synthesized` uses the backend's `response` verbatim (`is_completion=true`); `raw` builds an evidence block from `metadata.references` (`is_completion=false`). `top_k` is mapped to `search_kwargs.top_k_final`. The v1 endpoints are unchanged. `Section.section_ref` is now optional so both the v1 and v0.8.0 shapes parse. Updated `test_atlas_rag_v2.py`, the PR validation script, `docs/admin/external-rag-api.md`, the planning doc, and the mock README.

### PR #847 - 2026-08-26
- **AI output now uses full screen width on mobile** (fixes mobile layout): on viewports below the `sm` breakpoint (640px), the message avatar (32px circle + 12px gap), the chat container padding (16px), and the bubble padding (16px) consumed ~60px on the left side, leaving only ~80% of the screen for AI-generated text. On mobile the avatar is now hidden, the gap removed, and the container/bubble padding reduced to 8px/12px respectively, so the assistant bubble spans ~96% of the viewport. User messages get a wider `max-w` (85% vs 70%) on mobile for the same reason. Compact tool-call rows drop their `pl-11` indent. Desktop layout (640px+) is unchanged — avatars, gaps, and padding all restore at the `sm` breakpoint.

### PR #844 - 2026-08-25
- **A RAG query failure is no longer silent** (closes #844): when a selected data source errors (e.g. the RAG service returns a 500), the LLM is now told the query failed and instructed to tell the user, instead of the turn silently falling back to a plain answer as if nothing happened. `_query_all_rag_sources` now returns the failed server batches as a third element (`failures`) alongside the existing `exclusions`; a new `_build_rag_failure_notice` rides into the RAG context message on the partial-failure path, and on the all-sources-failed path a system message is injected that tells the model the retrieval failed and to begin its reply by telling the user. Applies uniformly to `call_with_rag`, `call_with_rag_and_tools`, `stream_with_rag`, and `stream_with_rag_and_tools`. The raw error text is kept out of the model-facing message (only the corpus name is named); permission denials and LLM domain errors still behave exactly as before.

### PR #841 - 2026-08-25
- **`atlas_agent_sleep` no longer shows a misleading "taking longer than expected" warning** (closes #838): the generic 30-second slow-tool threshold is meaningless for a tool whose job is to wait minutes or hours, so an active sleep now shows a progress clock against the requested duration (`MM:SS of MM:SS`, or `HH:MM:SS` for hour-plus waits) and switches to a "completing..." hint once the requested wait has elapsed. Other tools keep the existing timer + threshold behavior. Falls back to the generic timer when the sleep tool's `seconds` argument is missing, non-positive, or non-numeric.
- **`tool_start` now carries the executed arguments after an approval edit** (review feedback on #841): when a user edited tool arguments in the approval dialog, `execute_single_tool` recomputed the args that actually run but left the UI-facing `display_args` stale, so the `tool_start` frame (and any UI element that targets the executed args, e.g. the sleep progress clock) still carried the pre-approval request. The user-edit path now refreshes `display_args`, matching the existing hook-rewrite paths.

### PR #840 - 2026-08-25
- **Session store is now pluggable** (refs #760): `SESSION_REPOSITORY_TYPE` (default `memory`) selects the session repository implementation at startup, so a multi-replica deployment can plug in a distributed store through `atlas.infrastructure.sessions.factory.create_session_repository` instead of inheriting the process-local `InMemorySessionRepository` that forces sticky sessions. Also adds an end-to-end test verifying that a client disconnect mid-turn persists the completed work through `cleanup_disconnected_session`.

### PR #835 - 2026-08-22
- **Reopening a conversation from history re-enables its workspace** (closes #829): the active workspace id is persisted with each conversation and restored on load, so you no longer re-pick the prompt, sources, and tools. A notification names the workspace it switched to, since the switch replaces hand-picked tools, prompt, and RAG selections. Best effort — a workspace that has since been deleted is skipped with a notification saying so, a workspace-less conversation leaves the active one untouched, and a restore that lands before the workspace list (or the app config) has loaded is deferred until it has, with any explicit workspace action you take in the meantime cancelling it. Simply opening a conversation never re-binds it: the stored workspace is only rewritten when you send a turn. Applies to both server- and browser-saved conversations.

### PR #833 - 2026-08-21
- **The system prompt now carries the current date/time** (closes #823): every turn appends a "Current Date & Time" line to the rendered system prompt so the model knows what "now" is, and when the gap between turns meets `SYSTEM_PROMPT_TIME_REFRESH_MINUTES` (default 5), an explicit "approximately N minutes have elapsed since your previous prompt" note is appended so the model can reason about long pauses (a status may have resolved, a deadline may have passed). The current time is always injected; the refresh setting only gates the elapsed-time note (`0` keeps the time, drops the note). The gap is derived from the conversation history's existing message timestamps (preserved across save/reload by the conversation loader), so no new per-session state is required. `SYSTEM_PROMPT_TIMEZONE` (IANA name, default `UTC`; unknown names fall back to UTC) sets the displayed wall-clock; there is no per-user timezone plumbing. The enrichment is runtime addition applied to both the default and a user-supplied custom system prompt (issue #153's "custom replaces default" contract is preserved — the custom text still leads and the default template does not leak), and does not modify the packaged prompt templates.

### PR #832 - 2026-08-21
- **Transfer MCP `read_file_from_disk` no longer injects the entire file into the model context** (closes #831): the tool-result text is now a head+tail preview — the first and last `MCP_TRANSFER_PREVIEW_LINES` lines (default 50 each) of a UTF-8 file, joined by an omission marker when the file exceeds twice the line budget. A byte ceiling (`MCP_TRANSFER_PREVIEW_BYTES`, default 16 KiB) also applies, so a file with few but very long lines (minified bundles, single-line JSON) cannot bypass the line budget. Short files (under both budgets) are returned in full. When the preview is trimmed the text is emitted under `content_preview` and the `content` key is omitted, so a blind forward into `write_file_to_disk(content=...)` fails loudly instead of silently writing the omission marker. The complete file still travels as a base64 artifact so the agent can forward it to a downstream tool without re-reading. Binary files no longer inject a redundant `content_base64` into the tool result — the bytes travel only as the artifact. Structured truncation metadata (`truncated`, `total_lines`, `omitted_lines`, `preview_lines`) is emitted as integers for programmatic consumers. New env vars `MCP_TRANSFER_PREVIEW_LINES` and `MCP_TRANSFER_PREVIEW_BYTES` control the head/tail line budget and byte ceiling.

### PR #828 - 2026-08-21
- **`atlas-chat` now supports `--agent-mode`** (closes #827): the CLI registers the flag and passes `agent_mode=args.agent_mode` to `client.chat()`, enabling headless agent mode when tools are selected. `--agent-mode` and `--only-rag` are now mutually exclusive (argparse rejects the combination) so the orchestrator never receives both — it dispatches agent mode before checking `only_rag`, which would otherwise let tools run despite `--only-rag`.

### PR #830 - 2026-08-21
- **Product direction: the in-app agent loop is now a first-class citizen.** Reverses the prior stance that the in-app agent loop was not the focus and that agent work should route to a separate Agent Portal. The Agent Portal still exists for governed launch/stream of host subprocesses, but the in-chat agent loop (agent mode, multi-step tool use, streaming, surrounding UX) is now a primary surface on equal footing with chat, RAG, and MCP tools. Updated the copy in `AGENTS.md`, the `agentic-loop-2026-02-23` design note, and the RAG API v2 planning doc; no code or UI behavior changes.

### PR #826 - 2026-08-20
- **Conversation saving no longer depends on the browser**: a WebSocket gets a fresh session per connection while the browser keeps the conversation it is displaying, so after a dropped connection the next turn named a conversation the server had no history for — and because each save replaces the conversation's whole message set, that turn's two messages were written over the entire stored conversation, title included. The server now reloads the conversation from the database before running such a turn (skipped for incognito, and for a `conversation_id` the store has not seen), so the model keeps its context and the save that follows is complete with no reload in the browser. As a backstop, `save_conversation` refuses a write carrying fewer messages than are stored, logs at ERROR, and reports a failed save rather than applying it; rewind/edit-and-resubmit is the one turn allowed to shorten a conversation. Deployments that saw conversations lose their history after a disconnect were hitting this.
- **Turning saving back on after an incognito interlude branches the conversation**: messages taken while incognito are never persisted, so the segment after them is a slice of the session rather than a continuation of the conversation it was opened from. Writing that slice back would replace everything before the incognito turn; refusing it (which the no-shrink guard would now do) would reject every remaining turn of the session with no way out. The segment is saved as a new conversation instead, and the client adopts the new id from the `conversation_saved` frame it already handles.
- **A failed rehydration is retried**: a session bound to a conversation but holding no history re-attempts the load on its next turn, so a transient store failure no longer locks that session out of hydration for good while its history grows back toward the stored count. The store read now runs off the event loop, which matters when every client reconnects at once after a restart.
- **The shrink exemption is earned, not requested**: the guard's one exemption (rewind/edit-and-resubmit) now follows an actual truncation recorded by the orchestrator, rather than the presence of the client-supplied `rewind_to_user_index` field — an out-of-range or malformed index truncates nothing and no longer relaxes the guard for the turn. A rehydration that failed to read the store revokes the exemption outright, since a truncation measured against a partial session says nothing about the stored conversation.
- **Restoring a conversation no longer destroys its message timestamps**: the restore path rebuilt each message without its stored timestamp, so every message was restamped at load time — and since a save rewrites the whole row set, that restamped value was persisted. Restore and the new reload path now share one loader that carries the original timestamp through. Already-flattened timestamps in existing databases are not recoverable.

### PR #822 - 2026-08-19
- **Legacy prompt-risk filter removed, superseded by hooks**: `atlas/core/prompt_risk.py` scored RAG chunks against prompt-injection heuristics and appended medium/high hits to `logs/security_high_risk.jsonl`. It never blocked or redacted anything and nothing read the log. Its only call site sat in `RAGMCPService.search_raw`, which runs when an MCP RAG server does not implement `rag_get_synthesized_results` (`synthesize` falls back to it), so deployments with such a server were running the check and now are not — they were getting a log line nobody read, not a control. The `RagCall`/`RagResponse` hooks (#713 / #803) fire around every retrieval and can block, rewrite, narrow, or redact, so the check is gone rather than duplicated. The scoring now ships as an opt-in `RagResponse` hook (`atlas/config/hooks-example/rag_injection_scan.py`) that observes only until an operator wires it to `deny`. `security_high_risk.jsonl` is no longer written, which supersedes the `APP_LOG_DIR` handling added for it in #818 and the resolver/relocation-notice refinements in #820; existing files are left in place. The unused `PI_THRESHOLD_LOW/MEDIUM/HIGH` settings are removed — `AppSettings` ignores extra env vars, so a stale value in a deployment `.env` is harmless, but a deployment that tuned them should know they no longer do anything; the equivalent knob is the `THRESHOLD_*` constants in the copied hook script.

### PR #821 - 2026-08-19
- **A worked example for every hook event**: `docs/admin/hook-examples/` ships one runnable hook per lifecycle event, each stating its matcher, stdin payload shape, honored decisions, and `on_error` default in its own header, plus a combined `hooks.json`. The examples are exercised through the real hook engine in CI, so a stale example fails the build rather than an operator's deployment.
- **Zero-trust mock policy server**: `mocks/zero-trust-mock/` decides per tool call whether to allow, escalate to the approval gate, or block, with a stdlib-only forwarding hook on the Atlas side — a demonstration of runtime authorization where policy lives in a service instead of in each hook script.

### PR #820 - 2026-08-18
- **Lands the review fixes that missed the #818 squash-merge** (the `high_risk_log_path` parts are superseded by #822, which removes that module): `high_risk_log_path()` is a pure resolver again (the one-time relocation notice moved into `log_high_risk_event`), that notice now records a path only once it has actually fired so a stale log appearing later is still announced, `_release()`'s comment no longer claims a closed-loop task ends up cancelled (it raises and stays PENDING), and the ordering plugin raises `pytest.UsageError` so a mistyped `ATLAS_TEST_ORDER` reports one line and exit 4 (usage) instead of a ~50-line `INTERNALERROR` traceback and exit 3.
- **The relocation notice no longer cries wolf** (superseded by #822, which removes the notice along with the log): it compares the old and new locations with `samefile()`, so the migration the #818 upgrade note recommends -- symlinking the old path at the live log -- is recognized as the same file rather than reported as "no longer updated". `ATLAS_TEST_ORDER_SCOPE` is also now validated for every ordering rather than only the seeded shuffle, so a typo fails the same way whichever order is requested (it previously exited 0 with `reverse` and 4 with a seed).

### PR #819 - 2026-08-18
- **Workspace rows name the prompt they carry**: the switcher summarized a saved prompt as the generic "custom prompt", so you could not tell which prompt you were about to switch into. Rows now read `4 tools · 2 sources · Terse Code Reviewer`, resolving user-library prompts by title and MCP prompts by name, and falling back to the generic label only when the prompt no longer exists.
- **Workspace switcher keeps its selection across a page refresh**: the active-workspace pointer persists in `localStorage` while the feature flags and the workspace list both arrive asynchronously, and the stale-pointer cleanup ran against those pre-fetch defaults — so every reload cleared the pointer and the header fell back to "Workspace" even though the selections it applied were still in place. The cleanup now waits for the config payload and the first successful workspace fetch (`isStaleWorkspacePointer`).
- **Workspaces**: the long-dormant `FEATURE_WORKSPACES_ENABLED` flag now does something. A workspace is a named bundle of the active prompt, RAG data sources, and MCP tool selections, so switching between contexts (work / home / project-A) is one click in the new header switcher instead of re-picking every selection. Workspaces persist per user in the chat-history database via `/api/workspaces` (create / rename / update / delete), and the flag is gated on `FEATURE_CHAT_HISTORY_ENABLED` — with nowhere to persist them the switcher stays hidden and the API returns 404. Previously the flag was wired end to end as data but had no consumer anywhere in the UI, so turning it on changed nothing.

### PR #818 - 2026-08-18
- **Test suite no longer shares state between tests or with the developer's checkout**: removed an import-time `sys.modules` fake and a misnamed singleton reset that made the suite order-dependent, redirected every persistent store (chat-history and agent-portal DuckDB files, audit log, feedback, capture, token and log directories) to a per-session temp directory, and scoped the env vars, `sys.path` entries and mock patchers that leaked past their tests. See [docs/developer/test-isolation.md](docs/developer/test-isolation.md).
- **`logs/security_high_risk.jsonl` follows `APP_LOG_DIR`**: the prompt-risk audit log previously always resolved to `<project_root>/logs/`, ignoring the log-directory override every other log honors. Operators with `APP_LOG_DIR` set will find it in that directory now; move or symlink the old file if a collector still tails the repository path. **Superseded by #822 in this same release: the file is no longer written at all, so no migration is needed.**

### PR #817 - 2026-08-18
- **Agents can wait** (closes #779): `atlas_agent_sleep` is a built-in pseudo-tool that pauses the turn for a requested number of seconds so an agent can poll long-running external work. It runs in process next to `canvas_canvas` and the `atlas_rag_*` tools rather than behind an MCP server, because MCP tool calls are bounded by `MCP_CALL_TIMEOUT` (120s) and the useful waits are minutes to hours. `AGENT_SLEEP_MAX_SECONDS` (default 7200) caps one call -- longer requests are clamped and the result says so, so a polling agent keeps going instead of ending on a tool error -- and `0` removes the tool from the tools panel, ACL filtering, and execution. Stopping a run already cancels the turn's asyncio task, so an in-flight sleep aborts with it and no cancellation plumbing was added.

### PR #816 - 2026-08-18
- **DuckDB no longer relies on its secondary indexes**: DuckDB's ART indexes can silently stop matching rows that are present in the file, which surfaced as conversations opening with no messages and custom prompts vanishing from the list; the declared secondary indexes are now dropped at startup on the DuckDB dialect only (chat-history and agent-portal stores), which also repairs an already-affected file. PostgreSQL keeps its indexes and production behavior is unchanged. `scripts/repair_duckdb_indexes.py` inspects (`--check`), drops, or rebuilds (`--rebuild`) a file out of band.

### PR #814 - 2026-08-17
- **Authorization tests keep the developer bypass disabled after application import**: test setup now pins `SKIP_AUTHORIZATION_CHECKS=false` instead of deleting it, preventing `atlas.main` from restoring a true value from the repository `.env` during test collection.

### PR #811 - 2026-08-17
- **Authorization tests ignore a developer-local bypass flag**: `tests/conftest.py` now clears `SKIP_AUTHORIZATION_CHECKS` at session start, matching the existing `.env` and external-authorizer isolation guards. Tests that need the bypass still opt in explicitly through `skip_auth_checks_env`, while admin denial tests remain meaningful even when a contributor has the escape hatch exported locally.

### PR #807 - 2026-08-16
- **The release runbook documents the flow we actually use**: `AGENTS.md` said releases ship only via the `release-cut` cron ("do not push `v*.*.*` tags or create GitHub Releases outside that flow") and the runbook was built around a `release/YYYY.MM` stabilization branch, a draft checklist PR, a cherry-pick window, and a back-merge. v0.5.0 shipped without any of it — a three-line bump PR into `main`, a tag, and a GitHub Release — so the docs described a process nobody follows. Both now document that flow as canonical, with copy-pasteable steps (version derived from the highest *published* release, the one-commit bump of `atlas/version.py` + `pyproject.toml` + `CHANGELOG.md`, tag the squashed commit, `gh release create --verify-tag`) and a note that publishing is one-way. The stabilization branch keeps its own section describing the one problem it solves — freezing a release while unrelated work keeps landing on `main` — along with the details that bite when you take it (version reconciliation, the cut PR doubling as the back-merge, the `GITHUB_TOKEN` CI-kick fallback). The hotfix flow is now "land the fix on `main` and release a PATCH", with branching from the shipped tag reserved for when `main` has moved on. The pre-tag smoke test drops the locally built wheel in favour of dispatching `target: testpypi` and installing the artifact CI actually builds, and the redundant `Guardrails` section was folded into the stabilization-branch section.

### PR #806 - 2026-08-16
- **TestPyPI publishes are authenticated again**: the `target: testpypi` escape hatch in `pypi-publish.yml` passed `password: ${{ secrets.TEST_PYPI_API_TOKEN }}`, but that secret was never created — an empty password makes `gh-action-pypi-publish` fall back to OIDC trusted publishing, which failed with `invalid-publisher` because test.pypi.org had no publisher (nor an `atlas-chat` project) configured. Every dispatch of that target has failed since it was added, leaving the `testpypi` deployment environment permanently red. The job now uses trusted publishing deliberately — the `password:` line is gone and a pending publisher is registered on test.pypi.org for this repo, workflow file, and the `testpypi` environment — so there is no token to store or rotate. `skip-existing: true` keeps a re-dispatch at an already-uploaded version from failing, since this path exists to be retried by hand. Production PyPI is untouched and still uses `PYPI_API_TOKEN`.

## [0.5.0] - 2026-08-16

### PR #802 - 2026-08-15
- **Tools mode now carries a cross-turn tool digest** (closes #798): the digest added in #755 was attached only in agent mode, so a normal turn -- which runs in tools mode by default -- never carried it and tool work was invisible to every later turn. `ToolsModeRunner` now attaches `agent_tool_digest` at all three of its closing sites (`run`, `run_streaming` synthesis, and `_finalize_text_response`) via a new `_close_turn` helper that mirrors `AgentModeRunner._close_turn`. `close_open_turn` in `interrupted_turn.py` also attaches a digest from the flushed `tool_call` rows on the interrupted path, so a stopped tools-mode turn's tool work is visible to the next request too. The regression surface is small: `get_messages_for_llm` already caps folds at 3 digests / 12000 chars and folds into an existing message, so role alternation is unchanged; costs are prompt growth on tool-heavy chats (shared budget with agent digests) and inheriting the digest's untrusted-data quoting as-is. Flipping the `agent_mode` default would be the wrong fix -- it changes latency, cost, step budgets, and the emitted event stream for every user, and it still would not help the turns downgraded back to tools mode by `orchestrator.py` when the model lacks tool calling or no tools are selected.

### PR #793 - 2026-08-13
- **RAG API v2: an explicit query instead of the whole conversation** (Phase 1 of #791): v1 posts the entire `messages` array to `/api/v1/rag/completions` on every turn even though the server only reads the last user message, so conversation history left the process for no reason and "the query" was implicit -- a last message of "thanks" *was* the RAG query. New `POST /api/v2/rag/query` takes `{query, corpora, mode, top_k, filters, synthesis_params}` and nothing else. `mode: "raw"` returns retrieved sections as an evidence block carrying the same `[N]` document markers the UI citation pipeline already parses, with `is_completion=false` so Atlas' own LLM writes the answer; `mode: "synthesized"` returns the backend's answer with `is_completion=true`, which is v1's user-visible behaviour. A source opts in with `"api_version": "v2"` in `rag-sources.json` (v1 stays the default) and picks its fallback shape with `default_mode`; endpoint paths default per version and explicit `query_endpoint`/`discovery_endpoint` overrides still win. `UnifiedRAGService.query_rag`/`query_rag_batch` gained optional `query` and `mode` arguments -- an omitted `query` still derives from the last user message, so every existing caller behaves exactly as before and v1 sources ignore both. The `atlas_rag_query` agent tool passes its already-explicit query straight through and now asks for `mode: "raw"` by default, so on a v2 source the model reasons over the retrieved evidence alongside its other tool results rather than relaying a backend-written answer; the tool schema gains an optional `mode` whose unrecognized values fall back to `raw` rather than failing the call, since the value is model-supplied. Authorization is deliberately identical on both paths -- `_ensure_source_query_allowed`, group and compliance filtering, `as_user` impersonation and `strip_domain` all apply the same way, because the contract version decides the request shape and not who may read what. The `rag.query` span keeps `query_hash`/`query_chars` and adds `mode` and `explicit_query`; the query text is still never logged. The mock serves `GET /api/v2/discover/datasources` (each source declaring `api_version`) and `POST /api/v2/rag/query` alongside the v1 endpoints. Still open, and tracked on #791: discovery-driven version negotiation (config declares the version today), removing the `call_with_rag` / `call_with_rag_and_tools` / `_build_rag_completion_response` special-casing, a v2 MCP transport shape, and retiring v1.
### PR #795 - 2026-08-14
- **Auth tests no longer depend on the ambient environment**: `core.auth.is_user_in_group` prefers a configured external authorization service over its local group logic, so a developer or CI runner with `AUTH_GROUP_CHECK_URL` and `AUTH_GROUP_CHECK_API_KEY` exported turned every membership decision in the suite into an outbound HTTPS POST — 49 tests across 8 files failed with 403s and connection errors for reasons unrelated to the change under test. `conftest.py` now clears both variables for the session alongside the existing `.env` isolation guard; tests that exercise the external path set them via `monkeypatch`, which takes precedence, so that path keeps real coverage. Separately, admin-only test fixtures tagged resources with a literal `"admin"` instead of the configured `ADMIN_GROUP`, and the debug-only mock table in `core/auth.py` did the same even though the `test_user` branch directly above it already honoured `app_settings.admin_group` — on a deployment with a renamed admin group the configured `ADMIN_TEST_USER` was granted a group nothing checks, leaving debug-mode admin routes unreachable for that identity. Runtime authorization is unchanged: the external authorizer remains the sole decision-maker whenever it is configured and the mock table stays debug-only. Adds coverage for the branch production actually runs — delegation to the external authorizer (allow and deny, asserting the POST URL, body, and bearer credential against the configured settings), that `DEBUG_MODE` cannot resurrect the mock table once an authorizer is configured, that a transport failure fails closed, and the `users` group short-circuit. Allow/deny pairs that tag a resource with `ADMIN_GROUP` now skip with a stated reason when that group is one every non-admin identity already holds (`users`, `mcp_basic`), since such a deployment has no working admin gate to test.

### PR #794 - 2026-08-13
- **Test-identity cleanup follow-up (review nits on #789)**: the RAG integration fixture's mock registration now calls `raise_for_status()` and verifies the returned groups are non-empty, so a 4xx/5xx or an older mock without `clone_from` (which would register an empty group list) skips with a clear reason instead of silently proceeding; the configured test user is registered via `clone_from` (cloning the mock's all-corpora identity) rather than hand-copying group data from `mock_data.json`; the mock's `POST /admin/users` enforces an either/or contract for `groups`/`clone_from` via a `model_validator`; the shared `test_user_headers` / `admin_test_user_headers` / `test_user` fixtures are now adopted by the capture/feedback route tests and the AtlasRAGClient unit tests, replacing module-level header constants that snapshotted config at import time; the `POST /admin/users` endpoint is documented in the RAG mock README and covered by unit tests.

### PR #790 - 2026-08-13
- **MCP prompt keys now support underscore server names** (closes #790): prompt and tool key cleanup resolves keys against known full server names before falling back to the legacy first-underscore split, so servers like `file_viewer` keep prompt injection, compliance cleanup, and prompt-selector labels working without changing persisted key format.

### PR #789 - 2026-08-13
- **Tests use configured development identities**: Python tests now reference the configured `test_user` and `admin_test_user` values instead of hardcoded default email strings (including the remaining `admin@test.com` admin-route mocks), with shared pytest fixtures available for future auth-focused tests. The RAG API mock gains an authenticated `POST /admin/users` endpoint so live integration tests can register the configured identity and stay robust to `TEST_USER` overrides.

### PR #787 - 2026-08-13
- **The tool digest's per-call budget counts content, not escaping** (follow-up to #784, issue #755): arguments and results were escaped before being capped, so the 300/400-character budgets were spent on `&lt;`/`&gt;` entities and a fetched HTML page kept well under two thirds of its intended content. The cap is now spent on source characters, with a separate ceiling on the escaped result, so ordinary output gets its documented budget while a value made only of delimiters still cannot crowd later calls out of the digest. The `status` field goes through the same quoting as every other value on the line — it carries only recorder-written literals today, so nothing observable changes.

### PR #784 - 2026-08-12
- **Stopped turns are closed in every mode, and the tool digest is hardened** (follow-up to #776, issue #755): `close_open_turn()` now walks back past display-only rows, so a stopped tools-mode turn — whose flush leaves a `tool_call` row last — is closed instead of reopening the next request as `user -> user`; `ToolsModeRunner.run` keeps artifact processing and its recorder flush inside the unwind guard. Digest arguments are escaped and fenced like results (the escape covers `<` and `>`, so neither field can forge or close a delimiter), and a stopped call now emits `tool_interrupted` so the live row stops spinning instead of contradicting the reloaded transcript. The tool-status ladder names each terminal state, with anything unrecognized rendering neutrally rather than claiming the call failed.

### PR #777 - 2026-08-12
- **Print/PDF export no longer clips the right side or hides tool calls** (closes #774): the print stylesheet only overrode `overflow-y-auto` and `overflow-hidden`, so `<pre>` blocks and other `overflow-x-auto` scrollers kept their horizontal clip in print and a long code line was cut off at the right margin; the print block now also overrides `overflow-x-auto` and forces `<pre>`, its inner `<code>` and the highlight.js token spans to `overflow-wrap: anywhere` + `white-space: pre-wrap` so wide content wraps instead of being clipped (the `<code>` and spans need naming explicitly: the mobile-containment rule from #747 sets `overflow-wrap: normal` on `.chat-messages pre code`, which every token span inherits, so overriding `<pre>` alone left long tokens unbreakable). Lifting the horizontal clip exposed a second cause of the cut-off right side: the chat column is a flex item whose `min-width: auto` was previously held at zero by that clip, so once it was gone a single long line stretched the whole layout to roughly three page-widths and everything past the first page-width was lost — `body *` is now pinned to `min-width: 0` / `max-width: 100%` in print. Tool call rows were absent from the PDF entirely because the compact/classic summary renders inside a `<button>`, and the print hide rule is `button:not(.no-print-hide)`; the summary buttons now carry `no-print-hide` so the tool name and outcome glyph print. The collapsed details (input arguments + output) were also missing because they were rendered only when expanded; a `useIsPrinting()` hook now mounts them for the duration of the print job (`beforeprint`/`afterprint`, committed with `flushSync` so the extra DOM exists before the browser snapshots the page), carrying `hidden print:block` so they appear in the PDF without the user expanding every row first. They stay unmounted on screen: MCP results are not size-bounded, and keeping every collapsed row's serialized arguments and result in hidden `<pre>` elements would cost a `JSON.stringify` per tool call on every render. **Note when sharing an exported PDF:** it now contains every tool call's full input arguments and output — the same text an expanded row shows on screen, with no opt-out — where previously it contained none of them. Display math and markdown tables get their scroller from a stylesheet rule rather than a utility class, so the attribute selectors could not reach them and they are now named explicitly — note that this only removes the clip at the edge of their own box: a display equation wider than the printable page is still truncated at the page edge, because KaTeX lays the formula out at a fixed width and cannot reflow it. Tool output file names print as text instead of leaving a `N file(s) available for download:` label with nothing under it.
### PR #776 - 2026-08-12
- **Stopping a turn no longer discards it** (closes #755): `asyncio.CancelledError` is a `BaseException`, so a stop / disconnect / `reset_session` skipped `ChatService`'s persistence block entirely and the interrupted turn was lost on reload. Both paths now go through one `_commit_turn()`; agent and tools mode flush the in-flight tool calls while unwinding (a call stopped mid-flight persists as `interrupted`, rendered neutrally rather than as an error), plain/RAG keep the text that already streamed, and every stopped turn is closed by an assistant message marked `interrupted`.
- **A follow-up turn can see what the agent already ran**: each agent turn's closing assistant message carries a capped digest of its tool calls in `agent_tool_digest` metadata, folded into that message's content by `get_messages_for_llm()` — no new message, no role-sequence change — so the model stops re-deriving work it did in an earlier turn.

### PR #771 - 2026-08-11
- **WebSocket authentication honours the configured header type**: `AuthMiddleware` branched on `AUTH_USER_HEADER_TYPE` and cryptographically verified `aws-alb-jwt` tokens, but both WebSocket endpoints called `get_user_from_header()` unconditionally — which only strips whitespace. In an ALB-JWT deployment any non-empty `X-User-Email` value therefore authenticated a socket that the same value could not authenticate over HTTP. All three call sites now resolve identity through one `resolve_user_from_auth_header()` helper, so the header type cannot be interpreted differently in different transports.
- **Calculator expression syntax is stricter than `eval` was**: `sum([1, 2, 3])` and `round(x, ndigits=2)` no longer work — use `sum((1, 2, 3))` with parentheses and `round(x, 2)` positionally. String literals, complex numbers such as `2j`, and non-finite results (`inf`, `nan`) are now refused as well, the last two because they cannot be encoded in the tool's JSON response. The tool docstring lists every refused form with its replacement.
- **Calculator MCP tool no longer permits code execution**: `evaluate` ran `eval(expr, {"__builtins__": {}}, allowed_names)` behind only a 200-character cap, but emptying `__builtins__` does not contain Python — a 122-character expression reached `os.system` through attribute access on the class hierarchy, running as the backend's own OS user. The tool ships enabled for the `users` group (which everyone is in) and takes its input from an LLM tool call, so prompt injection reached it too. Evaluation now goes through `atlas/mcp_shared/safe_math_eval.py`, which parses to an AST and walks it against an arithmetic-only allowlist; `Attribute`, `Subscript`, comprehensions, lambdas, and starred/keyword arguments are not representable, a call's callee must be a bare name from the caller's table, and literal exponents are bounded so `9**999999` cannot hang the process.
- **Chat WebSocket rejects cross-origin upgrades**: `/ws` never inspected `Origin`, and a WS upgrade is not preflighted, so any page a logged-in user visited could open a socket that the reverse proxy authenticated from their cookies — cross-site WebSocket hijacking, giving the attacker's page a live session able to read conversations and call tools as that user. `/ws` now validates `Origin` before authentication, allowing loopback, the same host the request was addressed to (per `Host`, ignoring port), and `WEBSOCKET_ALLOWED_ORIGINS`; anything else closes with 1008. A missing `Origin` is still allowed, since browsers always send it on an upgrade and non-browser clients carry no ambient cookies. The same-origin rule means existing deployments need no configuration. The Agent Portal's equivalent check moved to `atlas/core/websocket_origin.py` so both sockets share one implementation, with its stricter behaviour unchanged.

### PR #739 - 2026-08-11
- **Fresh-clone setup no longer boots into a `/api/config` 500** (closes #732): `MCP_TOKEN_ENCRYPTION_KEY` is validated at startup via a shared `resolve_encryption_key()` (missing, repo-public placeholder, or shorter than 32 characters is refused with an actionable message) instead of failing lazily per request; `atlas-init` now generates a unique key on both its minimal and full paths and writes the `.env` mode `0600`, and the Docker/compose/package docs pass or require the variable.
- **Documented dev install includes the demo MCP dependencies**: `.[dev]` omits the `mcp-demos` extra that bundled demo servers import at startup, so `pptx_generator` failed discovery with `Connection closed`; docs now use `.[dev,mcp-demos]`.

### PR #770 - 2026-08-10
- **RAG context no longer breaks a tool-call round**: A RAG + tools turn that took a second tool round was rejected by the provider with "An assistant message with `tool_calls` must be followed by tool messages responding to each `tool_call_id`". The retrieved-context message was injected with `messages.insert(-1, ...)` — "before the last message" — which is the user turn only on the first round; on a continuation round the conversation ends with the assistant `tool_calls` message and its tool replies, so the context landed between them and orphaned a `tool_call_id`. Insertion now goes through `LiteLLMCaller._rag_insert_index()`, which targets the last `user` message (appending when there is none), at all four injection sites: `call_with_rag`, `call_with_rag_and_tools`, `stream_with_rag`, and `stream_with_rag_and_tools`. First-round turns are unaffected, since there the last message *is* the last user message.
- **A rejected continuation round degrades instead of crashing**: The failed round leaves no `LLMResponse`, so the loop substitutes a placeholder whose `tool_calls` is `None`, and the canvas-only shortcut iterated it unguarded — `TypeError: 'NoneType' object is not iterable` — turning every mid-continuation provider error into a hard failure instead of the intended graceful message. Both canvas shortcuts (`modes/tools.py` and `utilities/tool_executor.py`) now treat a response with no tool calls as not canvas-only and fall through to synthesis; an empty list no longer reports the misleading "Content displayed in canvas." either.

### PR #763 - 2026-08-09
- **One line per tool call instead of two**: An auto-approved tool call rendered two transcript rows — `▶ [AUTO-APPROVED] basic_fns_bash · 2 params` followed a second later by `▶ [SUCCESS] basic_fns_bash (basic_fns) · 2 params` — each wrapping at phone widths, so two calls consumed roughly ten lines and pushed the assistant's answer off screen. The approval row is no longer rendered for auto-approved calls (the component still mounts, hidden, because it owns the auto-approval response); the `APPROVAL REQUIRED` review path is unchanged. The surviving `tool_call` row drops the server name (usually a prefix of the tool name) and the param count (the disclosure triangle already signals expandability), and replaces the `SUCCESS`/`FAILED` text pill with a labelled colored glyph, so it fits one line at 390px. The server name moves into the expanded detail. The `Auto-approve ON/OFF` toggle, previously repeated once per call, now lives on the `Active Tools:` strip above the composer where it stays visible instead of scrolling away. Whether a call was auto-approved is also now persisted on the message as `auto_approved` at the moment of the decision and rendering keys off that field, so toggling the setting no longer makes old transcripts re-render as if history had changed (closes #762).
- **Review-bot fixes**: The active tool-call spinner now carries `role="img"` + `aria-label` so screen readers announce "CALLING"/"IN PROGRESS" instead of nothing (Copilot). The auto-approval effect only persists `auto_approved: true` after `sendApprovalResponse` reports a successful send, so a WebSocket drop during the 100ms delay no longer leaves the row stuck hidden with no approval in flight (Codex P2). `buildPersistedMessage` now tucks `auto_approved`, `status`, and `rejection_reason` (plus the renderer's other approval fields) into `metadata` for `tool_approval_request` rows, so reloaded local conversations keep the flag and no longer re-render as if the live setting still applied (Codex P2). Follow-up: the auto-approval effect is gated on a boolean `auto_approved` (and a local sent ref) so an un-memoized `sendApprovalResponse` cannot re-send while status is still `pending`; a failed auto-send persists `auto_approved: false` and toasts so the review row reappears for manual retry; manual approve/reject only patch terminal status after a successful send; the hidden approval wrapper uses the HTML `hidden` attribute so Tailwind `space-y` excludes it.

### PR #758 - 2026-08-08
- **Dev-only flag to skip authorization checks**: New `SKIP_AUTHORIZATION_CHECKS` setting (only usable with `DEBUG_MODE=true`; app refuses to start otherwise) lets local users reach admin-gated routes without configuring `ADMIN_TEST_USER` to match their own email. The validator also refuses to start when `ENVIRONMENT=production` or when `AUTH_GROUP_CHECK_URL` is configured, so the bypass can only ever override the mock group table, never a real authorizer; the `ConfigManager` boot path re-raises the refusal cleanly instead of retrying with a misleading fallback log. Authentication is unaffected (closes #757).

### PR #751 - 2026-08-08
- **Agent mode shows progress between loop steps**: `isThinking` is set once on send and cleared by the first streamed token, so the wait while the agent loop called the LLM for its next turn rendered no spinner and the run looked frozen. A new `AgentBusyIndicator` renders whenever an agent run is in flight and neither the thinking indicator nor a token stream is on screen, reporting the current step number (closes #748).

### PR #750 - 2026-08-08
- **Chat content stays inside the mobile viewport**: Long unbroken strings -- tool names like `basic_memory_discover_topics`, raw URLs, base64 blobs -- set the min-content width of their box, which inside a flex row becomes the item's automatic minimum (`min-width: auto`) and stopped the row from shrinking, scrolling the whole page sideways on a phone. The transcript container now carries a `.chat-messages` scope that clears that minimum and lets long strings wrap (code blocks excluded so snippets keep their own horizontal scroller); the assistant bubble shrinks beside the avatar instead of claiming `w-full` on top of it; tool-call rows, tool logs, file chips and download buttons wrap or truncate; and the header button groups shrink so the model name truncates rather than pushing the header past a 375px screen (closes #747).
- **Controls that cannot wrap keep their size or get a scroller**: Clearing the automatic minimum applies to buttons too, so in the tool-approval row -- a nowrap flex line whose text input holds an intrinsic ~180px minimum -- the fixed-width actions absorbed all the shrinkage and rendered as "Ap" and "Re" at 375px. That row now wraps, its buttons opt out of shrinking, and the rejection-reason input keeps a legible floor so it drops to its own line instead of collapsing to a sliver. Display math and wide markdown tables cannot be broken by `overflow-wrap` either, and the transcript's new `overflow-x-hidden` clipped them silently, so both get their own horizontal scroller -- a six-column table at 375px had collapsed into columns one or two characters wide -- as does the markdown parse-failure fallback `<pre>`. The table scroller is phone-only: `display: block` takes the table out of table layout, so applying it at every width would stop its columns stretching and open a bordered gap beside the last one.
- **Header groups no longer paint over each other**: Letting both header sections shrink meant that in a crowded header (desktop with the sidebar open) the left group -- fixed-size icon buttons with nothing to truncate -- overlapped the model selector by 19px, and the selector in turn overlapped the save-mode button by 23px. Only the right section shrinks now, and the model button's desktop floor drops from 160px to 7rem, which keeps the name legible without spilling; dropping the floor entirely had collapsed the label to a bare chevron. The button also gained a `title` with the full model name.

### PR #741 - 2026-08-08
- **RAG compliance enforced at query time**: Compliance filtering was applied only during RAG *discovery*, so a crafted chat payload could name a configured source outside the active compliance boundary and have it queried anyway. The shared RAG query path (`UnifiedRAGService.query_rag` and `query_rag_batch`, used by both normal UI RAG and the batched multi-source path) now re-checks enabled-source, group, and compliance authorization *before* contacting any HTTP or MCP RAG backend, and raises `DataSourcePermissionError` instead of degrading to a silent non-RAG answer. The compliance level used for the check comes from the selected model's server-side config, never from the client-supplied filter, and is carried on a `ContextVar` (`model_compliance_level` on the session) so it cannot be spoofed per request; enforcement engages only when a trusted level actually resolved, so deployments without per-model compliance levels are unchanged. The RAG picker is aligned with that same boundary — it now filters sources by the selected model's compliance level in addition to the header filter, so it stops offering sources the server would exclude. When only *some* selected sources are out of level, those sources are dropped and the answer is produced from the remainder with the exclusions reported inline; the hard error is reserved for when every selected source is rejected. Denial messages name the offending source and the remedy (`DATA_SOURCE_DISABLED` / `DATA_SOURCE_ACCESS_DENIED` / `DATA_SOURCE_COMPLIANCE_MISMATCH`), and a disabled source raises the same authorization error rather than a bare `ValueError` that the LLM caller's generic handler would swallow into a silent non-RAG answer. Tool authorization is untouched: the user's own compliance level stays in `session.context["compliance_level"]`, which is what scopes MCP discovery, tool execution, and agent context; the model-derived level lives under the separate `model_compliance_level` key. Compliance labels, model identifiers, and raw user identifiers are kept out of the rejection log lines, and a failure to resolve the model's level is logged at WARNING (without naming the model) rather than silently disabling enforcement for the turn. Denial and exclusion messages name the *corpora* the user selected — all of them, in the batch path — rather than the `rag-sources.json` server key, which the UI never displays. The picker mirrors the server's *permissive* treatment of untagged sources and of a missing compliance config, so it never hides something the gate would allow and a failed levels fetch cannot blank the panel; it compares the same per-server label the gate compares, and renders out-of-boundary sources disabled rather than hidden so one that is already selected stays deselectable. **Scope:** the query-time gate covers `http`-origin RAG sources routed through `UnifiedRAGService`. It does *not* cover `mcp`-origin sources, which `mcp_execution` hands directly to `RAGMCPService.synthesize`, where compliance is still filtered only at discovery time; closing that requires lifting the check into a shared policy called by both services and is tracked in #752 (closes #699).

### PR #746 - 2026-08-07
- **Mobile keyboard keeps composer visible**: The chat shell now tracks the browser visual viewport height so the flex layout shrinks when a mobile software keyboard opens, keeping the message composer above the keyboard instead of pinned to the obscured layout viewport (closes #745).

### PR #735 - 2026-07-28
- **Tool-caused provider rejections name the tool**: A malformed tool definition makes the provider reject the whole request, and the resulting `BadRequestError` was flattened into a generic "the LLM service encountered an error" message that pointed users at the model rather than the tool. `_raise_llm_domain_error` now maps it to a new `LLMBadRequestError` carrying the implicated tool names, checked after the context-window branch since `litellm.ContextWindowExceededError` subclasses `BadRequestError`. Concrete litellm exception types are classified before the message-keyword fallbacks, so a rejection quoting a tool named `request_timeout_probe` or `vault_api_key_lookup` is no longer raised as a timeout or auth failure with its attribution stripped, and `_is_retryable_error` treats a `BadRequestError` as permanent rather than retrying a deterministic 400 three times with backoff. Attribution requires positive evidence: the tool the provider named (matched as a whole token), or — when the text points at the tool payload without naming one — every tool in the request so the user can bisect. Rejections unrelated to tools, such as an out-of-range parameter or a malformed message history, report a plain rejection and blame no tool; message-history faults are excluded explicitly, since text like "an assistant message with `tool_calls` must be followed by tool messages" mentions tools but is not fixed by deselecting any. `classify_llm_error` short-circuits on that error instead of re-classifying its own user-facing message, and the RAG fallback paths let it through rather than retrying a request the provider already refused. Error frames carry `error_type: "bad_request"` from both the tools-streaming path (via a new `error_type_for()` mapping) and the WebSocket handler in `main.py` (closes #728, #729).
### PR #740 - 2026-08-03
- **No blocking tokenizer download on the event loop**: Set `litellm.disable_hf_tokenizer_download`. When a streamed response carries no usage block, LiteLLM reconstructs token counts locally at end-of-stream, and for llama-family model names that path made a *blocking* `Tokenizer.from_pretrained()` call to huggingface.co from inside the async event loop. In a network-restricted deployment that stalled the entire single-threaded server -- every user's stream, the WebSocket keepalives, and the `/api/health` probe that a container orchestrator uses to decide whether to restart the pod. LiteLLM already falls back to the bundled tiktoken tokenizer when the download fails, so token counts are unchanged.
- **Graceful mid-turn WebSocket disconnect**: A client that goes away while a turn is running no longer produces error spam or orphaned work. Updates are dropped when the socket is no longer connected (guarded at both send paths: `websocket_update_callback` and `WebSocketConnectionAdapter.send_json`), the in-flight chat task is now cancelled on disconnect instead of continuing to stream tokens and hold MCP sessions against a torn-down session, and a failed error-frame delivery can no longer escape the background task as an unretrieved exception.

### PR #742 - 2026-08-05
- **Per-model group restrictions enforced on the suggestions endpoint**: `POST /api/suggest_followups` takes a model name in its request body and did not check the per-model `groups` access-control list, so a crafted request could reach a model the user cannot see in `/api/config`. The endpoint now rejects restricted models with `404` -- indistinguishable from a nonexistent model, matching the per-user key endpoints -- before any LLM call, and the check is shared with the chat orchestrator via `atlas.core.model_access.check_model_access` so the deny policy cannot drift between entry points (closes #738).

### PR #723 - 2026-07-14
- **Fail-closed MCP tool ACL enforcement at execution time**: Fixed agent mode bypassing the tool authorization filter by enforcing group ACL checks inside `MCPToolManager.execute_tool` at the single execution choke point, keyed on the trusted `context["user_email"]`. Missing user context, disabled servers, group-check exceptions, and unauthorized membership now deny execution rather than fall back to allowing the call. Also made `ToolAuthorizationService.filter_authorized_tools` fail closed: it returns an empty list whenever the ACL check cannot complete instead of returning the unfiltered selection.
- **Runtime-only container compatibility**: Enable the PyO3 stable-ABI compatibility mode while installing LiteLLM so the rolling Chainguard Python 3.14 image does not fail its native extension build.

### PR #717 - 2026-07-08
- **Agent-mode narration persisted**: Intermediate assistant text in agent mode now finalizes its live stream bubble even on tool-call turns, is saved with the conversation before the corresponding tool row, and agent-mode prompts now ask for concise narration before tool calls. The persisted narration is a display-only `agent_intermediate` row (excluded from `get_messages_for_llm()`) so reloaded conversations re-render it without replaying back-to-back assistant turns that strict-alternation providers reject.

### PR #718 - 2026-07-09
- **LiteLLM customer-id header**: Added a per-model `pass_user_as_customer_id` option (default `false`). When enabled, the logged-in user's identifier is sent as the `x-litellm-customer-id` HTTP header on each request, letting a LiteLLM proxy attribute spend/usage to that end user (customer). The header merges with any configured `extra_headers` (an explicit `x-litellm-customer-id` there is authoritative and wins, matched case-insensitively) and is omitted for background/system calls that have no associated user. A companion `customer_id_strip_suffix` option optionally strips a configured email-domain suffix (e.g. `@mydomain.com`) from the reverse-proxy-provided username before sending it, turning `user@mydomain.com` into `user`. Applies to both streaming and non-streaming paths (single `_get_model_kwargs` chokepoint). Added unit tests and documented the fields in `docs/admin/llm-config.md` (closes #718).

### PR #716 - 2026-07-07
- **Configurable file upload size limit**: Added `MAX_FILE_UPLOAD_SIZE_MB` for chat attachments and `/api/files` uploads, with user-facing oversized-file errors before the frontend reads files.

## [0.4.0] - 2026-07-06

### PR #700 - 2026-07-05
- **Agent-mode tool calls persisted in saved conversations**: #685 covered tools mode only; the agentic loop now wraps its tool update callback in the same `ToolCallRecorder` and flushes the captured tool input/output into history after each step (id-reuse safe, always before the final assistant message), so reloaded agent conversations re-render user → tool_call(s) → assistant (also with no websocket connection, e.g. the chat CLI). Added agentic-loop wiring tests and updated `docs/admin/chat-history.md`.

### PR #682 - 2026-06-29
- **Selectable Atlas RAG tools**: Added Atlas RAG pseudo MCP tools (`atlas_rag_discover_data_sources`, `atlas_rag_query`) and surfaced them as an `atlas_rag` pseudo-server in the tools panel (via `/api/config`) so users explicitly choose whether the model can discover/query RAG sources. Selecting RAG sources alone does not enable these tools — the model can only call them when the user selects them in the tools panel (and the pseudo-server stays visible under the compliance filter). They work in both ordinary tools mode and agent mode, honoring the user's selected sources in both (threaded through the tool-execution context) and otherwise querying all sources the user can access. Authorization is enforced server-side: the authenticated user comes only from the execution context (the tools never accept a model-supplied identity); when the compliance-levels feature is enabled the user's active compliance level is validated and carried on the session (never taken from tool arguments), and RAG discovery/query are bounded by it so the model cannot reach or mix in sources outside that level; the query allow-list is the user's group- and compliance-authorized discovered set, so a model- or client-supplied `data_sources` list cannot cross either boundary; HTTP and MCP-backed sources are each routed through the service that can resolve them; and per-source failures are isolated so partial results and any ignored/failed sources are reported instead of discarded.

### PR #685 - 2026-06-28
- **Tool input/output persisted in saved conversations**: Tool calls used to surface only as transient WebSocket events and were never written to `ConversationHistory`, so the tool name, input arguments, and output result vanished when a saved conversation was reloaded or exported (issue #684). The DB schema (`metadata_json`) and the reload path already supported the view; only the save/export sides dropped it. A new `ToolCallRecorder` wraps the turn's update callback in tools mode (non-streaming and streaming), captures the already-UI-sanitized `tool_start`/`tool_complete`/`tool_error` payloads, and flushes them as display-only `tool_call` messages into history just before the final assistant message. Those rows (`role=tool`, `message_type=tool_call`) are excluded from `get_messages_for_llm` so they re-render but are never replayed to the model, and restore now preserves message metadata so they keep their type instead of becoming orphan tool messages; the canvas tool is skipped. The local IndexedDB autosave tucks tool-call fields into message metadata, and the `.txt` export renders an input/output block (large base64 elided; `.json` already carried the fields). Added backend recorder/history-filter/repository round-trip and restore tests plus frontend coverage for `buildPersistedMessage` and `formatToolCallForText`. In-chat agent-loop tool steps remain out of scope. Persisted tool arguments/results are size-capped (large strings, e.g. a base64 upload input or a huge tool output, are truncated with a `…[truncated N chars]` marker before being written to `metadata_json`) so a single tool call can't bloat a saved conversation; the live UI event is unaffected. Restore also folds a top-level `message_type` into message metadata as defense-in-depth, so a display-only row that stored its type as a sibling of `metadata` (e.g. the local autosave shape) still stays out of the LLM context instead of being replayed as an orphan tool message. Added an end-to-end test that drives `ToolsModeRunner.run()` and asserts the recorder is installed and history is flushed as user → tool_call → assistant. Updated `docs/admin/chat-history.md`.

### PR #680 - 2026-06-26
- **MCP file viewer folder display**: Added `display_folder_files`, a sibling file-viewer MCP tool that returns displayable artifacts for files in a local directory up to a requested depth, with skipped-file details for empty, oversized, hidden, or unreadable files. The output is bounded by aggregate file-count and total-byte caps (reporting `truncated`/`omitted_count` when reached), de-duplicates colliding artifact names, and skips hidden files plus common high-noise directories (`.git`, `node_modules`, virtualenvs, build output) by default.

### PR #678 - 2026-06-26
- **Disabled API key inference from model names**: LLM calls now pass the configured per-model key directly to LiteLLM without rewriting provider-specific environment variables, preserving gateway/proxy aliases and admin-selected key sources. Added an end-to-end proof under `mocks/llm-mock` (`e2e_llm_api_key_test.py` + `test/pr-validation/test_pr678_llm_api_key_no_env_coercion.sh`): an OpenAI-looking model (`openai/gpt5.4`) served by the mock gateway makes a real LiteLLM round trip and the mock confirms it received the configured key, not a conflicting `OPENAI_API_KEY`.

### PR #676 - 2026-06-25
- **Opt-in fine-tune capture**: Added a consent-gated feature that records the full LLM input/output of tool-capable turns — prompts, completions, tool calls, tool results, and the available-tool list — for users who voluntarily opt in, so the traffic can be exported as fine-tuning data (SFT examples and DPO preference pairs). Off by default at two independent levels: a system flag (`FEATURE_FINETUNE_CAPTURE_ENABLED`) and a per-user consent record; capture happens only when both are on. The high-value flow is rollback-with-forced-tool: from an assistant turn the user picks the tool the model should have called and re-runs, and the wrong and corrected versions are saved as a `(rejected, chosen)` pair. Because forced `tool_choice="required"` was removed in #664 (providers reject it), "forcing" a tool narrows `selected_tools` to exactly one tool and reuses the existing rewind/edit-resubmit path. Capture is recorded at the LLM-caller streaming chokepoint via a per-turn `ContextVar`, so it is mode-agnostic and a no-op when disabled. Includes consent + admin stats/export routes, a self-delete endpoint, pseudonymized `user_hash` storage, the `atlas-finetune-export` CLI (dpo/sft/raw), Settings opt-in UI, a "Correct this turn" affordance, docs, and tests. The chat CLI (`atlas-chat`) has no per-user consent UI, so CLI turns imply consent and are captured on the system flag alone (records tagged `consent.source="system_flag"` vs the UI's `"user_optin"`); the system flag is still required. No behavior change when the system flag is off.

### PR #675 - 2026-06-25
- **Refactored `mcp_tools/client.py` into focused modules**: The ~3000-line file held a single `MCPToolManager` class that coupled server lifecycle, elicitation/sampling routing, per-user/per-conversation client caching, tool/prompt discovery, execution, and result formatting. Split the class into cohesive mixins across new sibling modules — `mcp_errors`, `mcp_routing`, `mcp_connection`, `mcp_user_clients`, `mcp_user_client_cache`, `mcp_discovery`, `mcp_result_processor`, `mcp_execution` — each well under the 600-line guideline; `client.py` now just assembles the public class and keeps `__init__`/config reload. Pure structural change with no behavior difference: `from atlas.modules.mcp_tools.client import MCPToolManager` and existing `@patch('...client.config_manager'|'.Client'|'.StreamableHttpTransport')` targets all still resolve because the mixins reference those globals through the `client` module. Full backend suite stays green.

### PR #673 - 2026-06-23
- **Compact tool-approval messages**: Tool-approval prompts now render as a compact transcript row matching tool calls (no `System` bubble), with the arguments panel collapsing to a single line and the choice persisted across messages and reloads. Auto-approved calls default collapsed and read/write the persisted preference; approval-required calls use per-message state so they always open expanded for review and never inherit a collapsed global default. Because the backend never echoes an approval status change back (it just unblocks the waiting tool executor), the row now records the user's decision — once Approve/Reject is clicked it swaps to a resolved `[APPROVED]`/`[REJECTED]` badge and guards against a duplicate submit, instead of leaving the buttons live. The decision is written to the message store (keyed by `tool_call_id`) and any non-`pending` status counts as resolved, so the controls stay gone even after the execution lifecycle overwrites the row's status or the list remounts it. Removed the dead `ToolApprovalDialog.jsx` and its test. See `docs/developer/design-notes/compact-tool-system-messages-2026-06-23.md`.
- **Compact-messages toggle**: Added a *Compact Tool Messages* switch under Settings → General (on by default). Turning it off restores the classic per-message layout — avatar, author header, and message bubbles — for tool calls, approval prompts, logs, agent meta, and system notices. The toggle controls chrome only: tool-call details and approval arguments stay collapsible in both modes (defaulting collapsed, matching the pre-#673 layout), and the choice persists in `localStorage` alongside the other user settings. Added frontend coverage for the compact/classic approval paths, review-required default visibility, the persisted-collapse default, `allow_edit=false`, the local decision/duplicate-submit guard, remount and lifecycle-overwrite resolution, and the shared tool-call collapse.

### PR #672 - 2026-06-22
- **Fixed missing Stop button in agent mode**: The agent-mode Stop button was gated on `isThinking`, which the native agentic loop clears the moment the first answer token streams. The button therefore flashed on send and then vanished for the rest of the run — through every tool call and the streamed final answer — leaving no way to interrupt a running agent (regressed in #664). It now hangs off a dedicated `isAgentRunning` flag set on an agent-mode send and cleared only on the terminal agent events (`agent_completion` / `agent_error` / `agent_max_steps`, plus `response_complete` / `chat_response` / `error`, and an explicit stop or the thinking-timeout safety net), so it stays visible for the whole run regardless of streaming sub-state. Normal-mode streaming/Stop behavior is unchanged. Added regression tests that token streaming does not clear the flag mid-run and that each terminal event does.

### PR #671 - 2026-06-22
- **Reorganized `/docs` by audience and lifecycle**: Split dated feature write-ups into `developer/design-notes/`, moved completed/superseded plans into a real `archive/` (already excluded from the doc bundle), promoted end-user feature docs into `user-guide/`, and folded the stray `summaries/`/`superpowers/` dirs in. Rewrote the top-level README and every per-directory index, added the missing ones, and renamed snake_case docs to kebab-case. Added `scripts/check-docs.sh` (fails on orphaned docs or broken relative links) wired into the Build Artifacts workflow, and updated `bundle-docs.sh`, `documentation-bundling.md`, and `AGENTS.md` to match. No application code changed.

### PR #669 - 2026-06-22
- **Fixed stateful MCP session errors in agent mode**: Agent mode didn't thread a `conversation_id` through the agentic loop, so each tool call opened a fresh single-use MCP session and stateful servers lost their session between sequential calls (regular chat was unaffected). `AgentContext` now carries `conversation_id` and the loop forwards it so `MCPSessionManager` reuses one persistent session per conversation. Added a regression test.

### PR #666 - 2026-06-21
- **Fixed transfer MCP `write_file_to_disk` for session files**: Writing a tool-produced session file (e.g. a SolidWorks STEP export) to local disk silently failed — the model would loop on `read_file_from_disk` and give up. The tool only accepted inline `content`, and the backend's session-file injection only rewrites a parameter named exactly `filename`/`file_names` to a tokenized download URL, which `write_file_to_disk` did not declare. So there was no path for the tool to obtain a session artifact's bytes. The tool now accepts a `filename` parameter (the backend rewrites it to a download URL), fetches the bytes over HTTP with the same `MCP_TRANSFER_MAX_BYTES` cap and `Content-Length` fast-fail as the reader, and writes them verbatim. A directory destination is supported — the source name (from the backend-supplied `original_filename`) is appended. Inline `content`/base64 still works; an unresolved bare name now returns a clear error instead of failing silently. Added tests for the backend-fetch path, directory naming, size-cap rejection, missing-source, and unresolved-name cases.
- **Transfer MCP default access guards (home-dir root + no hidden paths)**: The transfer server's `read_file_from_disk` / `write_file_to_disk` tools are auto-approved on developer machines, so they now have sane footgun guards on by default instead of being confined to the server's working directory. The primary root defaults to the **home directory** (`MCP_TRANSFER_BASE_DIR` to override), relative paths anchor there, and access outside it is denied — except directories explicitly whitelisted via `MCP_TRANSFER_ALLOWED_DIRS` (os.pathsep-separated, e.g. `/projects:/mnt` for network mounts). Hidden dotfiles/dot-directories below a root (`~/.ssh`, `~/.aws`, `.env`, `.git`, …) are blocked to protect credentials and configs. Both guards are individually relaxable: `MCP_TRANSFER_ALLOW_HIDDEN=true` permits hidden paths and `MCP_TRANSFER_ALLOW_ANY_PATH=true` disables the guards entirely. Paths are resolved (symlinks and `..`) before the guards run, so neither can escape an allowed root. Documented all transfer env vars in `.env.example`.
- **Removed dead `INCLUDE_FILE_CONTENT_BASE64` setting**: The base64 content-injection fallback was never wired into argument injection (its helper was unreachable), so the setting did nothing. Removed the setting, the unused helper, the `.env.example` entry, and corrected the file-I/O developer guide to note that fetching the rewritten `filename` URL is the only supported mechanism.

### PR #665 - 2026-06-20
- **MCP examples**: Added a `file_viewer` server with a `display_file` tool that reads a file from the local disk and shows it in the canvas, guessing/normalizing the MIME type (extension map, stdlib, magic-number sniffing, text heuristic) to pick the right viewer (image / PDF / HTML / code). Intended for local single-developer use, so it reads any path without sandboxing; size-capped to avoid loading unbounded content into chat.

### PR #664 - 2026-06-20
- **Agent loop simplified to a single native loop**: Agent mode now always uses the `agentic` loop — the model gets the real tools with `tool_choice="auto"` and signals completion by returning text with no tool calls. Removed the `react`, `think-act`, and `act` strategies along with their scaffolding "control tools" (`finished`, `agent_decide_next`, `agent_observe_decide`) and the lossy 400-char observation truncation. Forced `tool_choice="required"` is gone everywhere (it was unsupported by several providers and forbade plain-text answers); the per-request `tool_choice_required` flag and the "Required Tool Usage" UI toggle were removed. `AGENT_LOOP_STRATEGY` / `agent_loop_strategy` are still accepted for backward compatibility but resolve to the agentic loop. The agent step cap default is unified to 10. Aligns with the product direction in `AGENTS.md` (the in-app agent loop is not the focus; deeper agent work belongs in the Agent Portal). See `docs/developer/agentic-loop-2026-02-23.md`.
- **Agent mode keyboard shortcut**: Added `Ctrl+Alt+A` to toggle agent mode on/off (mirrors the existing `Ctrl+Alt+N` new-chat shortcut; `Ctrl+Alt` avoids the `Ctrl+A` "select all" collision). Works while the message input is focused and shows a toast confirming the new state. The shortcut is surfaced in the toggle's tooltip and the menu item.
- **Fixed multi-step tool chains in agent mode**: The agentic loop appended the streaming path's `SimpleNamespace` tool calls directly into the assistant message, so on the *next* turn they serialized to an empty `tool_calls` array and providers like OpenAI rejected the follow-up call (`Invalid 'messages[N].tool_calls': empty array`). Any task needing more than one tool call in a row failed after the first call. The loop now normalizes tool calls to plain dicts before storing them in history (matching the non-agentic tools mode). Added an end-to-end `ChatService` test that chains multiple tools and a regression test asserting the assistant message's `tool_calls` are JSON-serializable dicts. Verified live (3 sequential calculator calls) — see `docs/developer/images/pr664-agent-multi-tool-chain-1.png`.
- **Agent mode requires at least one tool**: Running agent mode with no tools selected gave the loop nothing to call, and tool-seeking prompts could drive the model to emit a tool call the provider then rejected (`tool_choice is none, but model called a tool`). The orchestrator now detects this case, warns the user, and runs the message as a normal chat turn instead. The frontend also blocks the send with a clear toast (choose a tool or turn off agent mode) so the user makes an explicit choice; the backend guard still covers API and older clients.
- **Fixed silent no-response on streaming errors in agent mode**: When the per-step streaming LLM call raised before any text was produced, the agentic loop swallowed the exception and returned empty content, so the UI showed nothing and looked like the model never replied. It now re-raises when nothing was accumulated so the error surfaces as a user-visible message (matching the non-agentic tools/plain/RAG paths); partial text that already streamed is still preserved. Added regression tests for both the error-surfacing and partial-content cases.
- **Fixed agent-mode generated files not appearing in the canvas/session**: In agent mode, tool-generated files (e.g. a `pptx`) were uploaded to storage (and visible in the File library) but never surfaced to the canvas or current session — unlike standard tools mode. The agent runner called the artifact processor with a `None` update callback, and the canvas/files notification helpers (`notify_canvas_files`, `notify_canvas_files_v2`) early-return when the callback is missing, so the `files_update` / `canvas_files` events were silently skipped. Agent mode now passes the real `send_json` callback (mirroring standard tools mode). Added a regression test asserting the artifact processor receives a real callback, not `None`.
- **Bounded multi-round tool calling in standard (non-agent) tools mode**: Standard tools mode did exactly one tool round, then a no-tools synthesis call. If the model tried to call another tool during synthesis (common when a task needs chained calls, e.g. compute a value then build a file), the provider rejected the whole stream (`Tool choice is none, but model called a tool`) and the turn failed with a misleading "LLM service encountered an error." Standard mode now runs a bounded loop: after the first round it may take up to `TOOLS_MODE_MAX_EXTRA_ROUNDS` (default 3) further rounds to chain dependent tool calls, with an anti-loop guard that refuses repeated identical calls so a model can't spin on one tool. Admins can set the value to `0` to restore the classic single-round behavior — no code change. The synthesis call is also hardened: it now explicitly instructs the model not to call further tools, and if a model ignores that and the provider still rejects, the user gets a clear, actionable message (preserving the completed tool results) that recommends Agent Mode **only when it is actually enabled** for the deployment. Added unit tests for chaining, the anti-loop guard, the round budget, single-round mode, and both message variants.

### PR #662 - 2026-06-19
- **MCP examples**: Added a local-development transfer server with tools to read disk files into chat artifacts and write chat content back to disk. Reads are capped at a configurable maximum size to avoid loading unbounded content into chat context.

### PR #660 - 2026-06-19
- **Agent Portal**: Disabled the Agent Portal automatically on Windows hosts so startup no longer imports Unix-only process management code (`fcntl`, `pty`, `termios`). The feature continues to run on Linux/WSL and macOS, where its Linux-only isolation (Landlock, namespaces, cgroups) degrades gracefully when unavailable.

### PR #654 - 2026-06-14
- **UI theme**: Defaulted first-time users to dark mode and improved the auto-approve tools warning contrast in light mode.

### PR #652 - 2026-06-14
- **Rewind / edit a previous prompt**: A pencil affordance on your own chat messages opens an inline editor; resubmitting drops that prompt and everything after it and re-runs from there as a single linear thread (overwrite-in-place). Blocked while streaming; addressed by user-message ordinal so frontend and backend history stay in sync. See `docs/rewind-edit/README.md`.

### PR #650 - 2026-06-13
- **Chat Export**: Exported conversations (both JSON and `.txt`) now show the active custom prompt's name plus the first few lines of its body when available. User-authored prompts (custom prompt library) include a preview of their content; MCP-server prompts continue to show name/description (body lives server-side).

### PR #648 - 2026-06-12
- **PDF uploads**: Models can set `supports_pdf: true` in `llmconfig.yml` to receive uploaded PDFs as inline base64 document content blocks (LiteLLM `file` blocks, mapped to a Bedrock Converse document block for Claude) instead of having their text extracted into the files manifest. Mirrors the existing `supports_vision` plumbing: PDFs are stored on the session file ref and excluded from the text manifest. Their text is still extracted up front as a durable fallback (kept out of the manifest on the turn the PDF is sent natively, so no token duplication) so the content survives follow-up turns and demotion. Guards enforce a 20 MB base64 per-document size cap, a 100-page limit, a 5-document-per-request cap, and an 18 MB aggregate inline-payload budget (to stay under Bedrock's ~20 MB total request limit). PDFs over a limit are sent as their extracted text where extraction is enabled, or as a name-only file reference otherwise, with a user warning. Note: on Bedrock Converse, full visual PDF understanding (charts/scanned pages) requires citations, which the portable LiteLLM `file` block does not currently expose — without it Bedrock performs text extraction only.

### PR #647 - 2026-06-12
- **Docs**: Documented the off-cycle release path and version-reconciliation guardrails in `AGENTS.md` and the release runbook — manual `release-cut` dispatch, deriving the next version from the highest *published* release (not `pyproject.toml` on `main`), closing superseded/no-op release PRs before cutting, and that the cut PR doubles as the back-merge PR.

### PR #641 - 2026-06-09
- **Wormhole MCP auth**: Added opt-in Wormhole support (`FEATURE_WORMHOLE_ENABLED`). Atlas captures the per-session `x-subtoken` request header and forwards it as `X-Token` to MCP servers marked `wormhole: true` in `mcp.json` (header names configurable via `WORMHOLE_SUBTOKEN_HEADER`/`WORMHOLE_FORWARD_HEADER`). The subtoken is held in memory only, logged masked, and cached clients are rebuilt when it rotates or clears. See `docs/admin/mcp-wormhole-authentication.md`.

## [0.3.0] - 2026-06-12

### PR #643 - 2026-06-10
- **Config**: Changed the default application name from "Chat UI" to "ATLAS".

### 2026-06-09
- **RAG**: Removed the `/search` chat quick command, which silently forced RAG on from the message input and was a confusing entry point. RAG is still activated explicitly via the search-button toggle or by selecting one or more data sources; the `/search` autocomplete entry, its green input highlighting, and the `forceRag` send path are gone.

### PR #632 - 2026-06-03
- **Config**: Split the 1300-line `atlas/modules/config/config_manager.py` into focused modules — `models.py` (Pydantic config models + `resolve_env_var`), `settings.py` (`AppSettings` + `build_db_url_from_parts`), and `config_loader.py` (`ConfigManager`). `config_manager.py` is now a thin entry point that re-exports every public symbol plus the singleton and getters, so existing `from atlas.modules.config.config_manager import ...` imports are unchanged. No behavior change; all four modules are now under 500 lines.

### PR #637 - 2026-06-09
- **Vision uploads**: TIFF files (`.tif`/`.tiff`) are accepted as image uploads and converted to PNG before being embedded in LLM vision requests, including high-precision grayscale TIFFs (`I`/`I;16`/`F` modes).
- **Vision uploads**: When a TIFF cannot be converted for vision input, the user is now warned and the file is listed as a reference instead of being silently dropped.
- **Vision uploads**: TIFF attachments show a placeholder thumbnail in the composer instead of a broken `<img>` preview (browsers cannot render TIFF inline).

### PR #635 - 2026-06-09
- **Custom prompts**: Added `FEATURE_CUSTOM_PROMPTS_ENABLED` to hide/disable the per-user prompt library independently, and let Alembic build its database URL from `DB_*` parts when `CHAT_HISTORY_DB_URL` is unset. The flag is now enforced authoritatively on the chat WebSocket path — a custom system prompt sent inline is ignored unless the feature (and its chat-history prerequisite) is enabled — via a single `custom_prompts_effective` derived setting shared by the config payload, the prompt CRUD routes, and the chat handler.

### PR #628 - 2026-06-03
- **MCP tools**: Renamed the Atlas-injected authenticated user argument from `username` to `_atlas_user`; ordinary `username` tool arguments are no longer overwritten. Bundled example tools that use the authenticated user for audit attribution (csv_reporter, code-executor, file_size_test) now declare `_atlas_user` so the value stays backend-injected and cannot be spoofed by the LLM.

### PR #629 - 2026-06-03
- **MCP token storage**: `MCPTokenStorage` now refuses to start when `MCP_TOKEN_ENCRYPTION_KEY` is unset — or still set to the shipped `.env.example` placeholder — instead of silently generating an ephemeral key that made every previously encrypted token unreadable after each restart. The key is validated before any filesystem setup. Operators must configure a stable, unique secret; docs and `.env.example` updated to mark the variable as required.

### PR #624 - 2026-06-01
- **File Library**: Added a bulk delete button to the File Manager's File Library tab that deletes the files currently shown after a confirmation prompt. The button is labeled "Delete All" when no filters are active and "Delete Filtered" when a search/type filter is narrowing the list, so the scope is explicit; it is disabled while a delete batch is in flight.

### PR #621 - 2026-05-29
- **Splash screen**: Message body is now defined in a markdown file (`splash-screen.md`, override via `SPLASH_SCREEN_FILE`) and rendered as markdown. The JSON config keeps presentation settings only; the redundant `enabled` field and `messages` array are dropped — `FEATURE_SPLASH_SCREEN_ENABLED` is the sole switch for showing the splash screen.

### PR #619 - 2026-05-29
- **Chat history**: Default save mode is now Incognito (`none`) instead of Server; the save button cycles Incognito -> Saved Locally -> Saved to Server. Turns taken while incognito are excluded from server persistence even after the user later opts in to saving.

### 2026-05-21
- **CLI**: Added support for specifying a custom `.env` file location. `atlas-init`, `atlas-server`, `atlas-chat`, and `agent_start.sh` now all honor a `--env-file`/`-e` flag and the `ATLAS_ENV_FILE` environment variable, so multiple users can share an Atlas install while keeping their own API keys in a personal file such as `~/.atlasrc`. Resolves the "Specifying location for .env configuration file" issue.

### PR #607 - 2026-05-14
- `MCPToolManager._invalidate_user_client` now calls a new `MCPSessionManager.release_sessions_for_user_server` method after evicting client cache entries, ensuring live sessions that outlived their cache entry (e.g. due to LRU eviction races) are also closed when a token is revoked.

### PR #596 - 2026-05-11
- Added `AGENT_PORTAL_ALLOWED_ORIGINS` to let the Agent Portal WebSocket stream accept Origin headers beyond loopback when the deployment is fronted by an authenticating reverse proxy (e.g. Cloudflare Access). Loopback hosts remain allowed by default; the env var is a comma-separated hostname allowlist and is empty by default, so the gate is unchanged for stock installs.
- Renamed `_origin_is_loopback` to `_origin_is_allowed` in `atlas/routes/agent_portal_routes.py` and updated the rejection log message; updated `docs/agentportal/threat-model.md` to describe the expanded allowlist and its residual risks.
- Added a Remove (stop + drop) affordance for Agent Portal sessions. `ProcessManager.remove()` drops a non-running record (wakes lingering stream subscribers and closes any orphaned PTY master fd); `ProcessManager.stop_and_remove()` cancels, waits a bounded grace window, and removes in one shot.
- New `POST /api/agent-portal/processes/{id}/remove` route — pairs with the existing `DELETE` (which only sends SIGTERM and keeps the record). Writes a `"remove"` audit event with the final status; returns 404 for unknown ids and 409 if the child is still alive after the grace window. Frontend adds a Trash button in the left rail and pane header, with a confirm dialog when the target is still running, and clears the layout slot on success.
- Defense in depth in `ProcessManager.launch`: if a caller sets `namespaces=True` but `probe_isolation_capabilities()` reports the host can't do unprivileged user namespaces (e.g. Ubuntu 24.04 with `apparmor_restrict_unprivileged_userns=1`), the manager strips the flag (and `isolate_network`), logs a warning, and launches with reduced isolation rather than failing with EPERM on `/proc/self/uid_map`. The frontend mirrors this by clearing the flags client-side when the capability probe reports the host unsupported.
- Added per-user ownership graduation TODO for the new `/remove` endpoint and listed it in `docs/agentportal/threat-model.md`'s deferred-items section.

## [0.2.0] - 2026-05-16

### PR #606 - 2026-05-16
- **RAG**: Aligned ATLAS-RAG mock and client with the newest OpenAPI spec (v0.3.0.dev1+). The client now sends a trimmed `{messages, stream, corpora}` payload (no `model`, no `hybrid_search_kwargs`) and parses `metadata.references[].sections[]` (each with `section_ref`, `text`, `relevance`) plus per-reference `citation` and `document_ref`. Legacy `rag_metadata` parsing is retained for rolling migration.
- **Citations UI**: Reference expansion now renders each section snippet as a `.rag-ref-snippet` blockquote under its reference, with `section_ref` and a relevance label. Snippet styling added to `index.css`; `data-section-ref` is allowed through DOMPurify so spans survive sanitization. New vitest cases verify snippet pass-through and that the source-label extractor isn't fooled by them.
- **Mock**: `mocks/atlas-rag-api-mock/main.py` rewritten to the new `RagResponse` shape (bare-list discovery endpoint, top-level `corpora` in the request, per-reference sections built from grep hits). README and screenshots updated.

### PR #605 - 2026-05-13
- **Packaging**: New focused `[pptx]` extras group (`pip install atlas-chat[pptx]`) pulls Pillow + python-pptx for the `pptx_generator` MCP server. Without it the server logged `ModuleNotFoundError: No module named 'PIL'` on a bare wheel install. Pillow stays in the broader `[mcp-demos]` extras as well.
- **CLI**: `atlas-chat --version` now prints `atlas-chat version X.Y.Z` and exits, matching `atlas-init`. The release smoke test references this flag.
- **Docs**: Removed `--tool-choice-required` from the smoke-test recipe in `docs/developer/release-process.md` and the release checklist; it is not (and was not) a real `atlas-chat` flag.

### PR #602 - 2026-05-12
- Docker runtime-only image now installs with `--ignore-requires-python` for LiteLLM compatibility on Chainguard Python 3.14, and CI now validates `Dockerfile.runtimeonly` builds.

### PR #599 - 2026-05-12
- **Dependency**: Bumped minimum LiteLLM version from 1.81.14 to 1.83.10.

### PR #598 - 2026-05-12
- Added an optional `Dockerfile.runtimeonly` that builds a slimmer deployed image using Chainguard bases (Node frontend stage + Python runtime stage), excludes top-level docs/test/scripts trees, and is documented in the README and installation guide.

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
