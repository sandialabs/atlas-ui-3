# Changelog

All notable changes to Atlas UI 3 will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### PR #951 - 2026-09-16
- Files produced by an MCP tool can now be downloaded from chat, not only from the File Manager library: storage sanitizes a filename on the way in (`Q3 Sales Report (final).csv` is stored as `Q3_Sales_Report_final_.csv`) while chat download controls carry the name the tool advertised, so the by-name lookup missed and the click was silently dropped in the browser. Ingestion now records the advertised name alongside the stored one and the download resolves against it, names that sanitize alike no longer displace each other in the session -- each keeps its own entry and downloads its own bytes whichever order they arrive in, including when one tool result carries both, a re-emitted artifact refreshes its own entry, a user's attachment is never shadowed by a tool advertising the same name, and the canvas resolves display names through the same lookup so it cannot preview one artifact's bytes under another's name -- a download request names its conversation so a parallel conversation's same-named file cannot answer it, and a failed download says so in the transcript instead of only in the console.

### PR #947 - 2026-09-15
- Admin MCP status now distinguishes unauthenticated OAuth servers from genuinely unreachable servers, labeling the former as requiring OAuth authentication while preserving connection failures as failed.

### PR #946 - 2026-09-15
- **Admin access is now granted by `ADMIN_USERS` *or* dynamic membership in `ADMIN_GROUP`** (closes #945). A deployment that already resolves group membership outside this repository can point Atlas at a group name (`ADMIN_GROUP=my-org-atlas-admins`) and stop there -- previously the only supported paths both required restating individual users in public-facing config, and a bare group name in `AUTH_STATIC_GROUPS` was skipped as malformed. `ADMIN_USERS` changes from a fallback to an override: it is consulted **before** `AUTH_GROUP_CHECK_URL` and grants the configured admin group even when the authorizer denies, errors, or is unreachable, which is what makes it usable as the emergency allowlist it was always described as. It only ever grants, only for `ADMIN_GROUP`, and only for identities typed out by hand. `AUTH_STATIC_GROUPS` is unchanged -- still `group:user1,user2` only, still yields to a configured authorizer, still fails closed when a URL is set without a usable API key -- and its malformed-entry warning now points operators at `ADMIN_GROUP`. `docs/admin/authentication.md` gains a table distinguishing the three mechanisms and which of them survive a configured external authorizer.
- **Upgrade action for deployments running both `ADMIN_USERS` and `AUTH_GROUP_CHECK_URL`**: those `ADMIN_USERS` entries were previously inert and become live admin grants on the restart that picks up this version. Review the list and remove anyone -- de-provisioned staff especially -- who should no longer hold break-glass admin. Atlas logs a startup warning naming the count and the admin group whenever both are set, and logs a warning on each grant that actually overrides the authorization service.

### PR #948 - 2026-09-15
- Remove obsolete `.gemini` settings, `.env.otel.example`, and archived PR 921 screenshots.


### PR #939 - 2026-09-13
- **`atlas_launch`: a conversation can launch sub-conversations** (closes #925). A new built-in `atlas` tool takes a `workspace`, a `model` and a `prompt`, starts a real run for the child, and returns its `run_id`/`conversation_id` immediately instead of waiting for an answer; the child's transcript is its own conversation in history. The workspace carries the child's tools *and* data sources: the tool list is re-filtered through the caller's ACLs at launch time and the sources are authorized per source when queried, so a sub-conversation can never reach a tool, model, data source or workspace its caller could not. A launch from an incognito or local-save turn is refused rather than silently persisting a transcript, and approving the `atlas_launch` call is what approves the sub-conversation's own tool calls (admin-mandated approvals still prompt inside it). `ATLAS_LAUNCH_MAX_DEPTH` and `ATLAS_LAUNCH_MAX_CHILDREN_PER_RUN` bound recursion and fan-out (per-user run concurrency still applies), stopping a conversation now stops the ones it launched, and the tool is gated by `FEATURE_ATLAS_LAUNCH_ENABLED` (off by default). Design record: `docs/developer/design-notes/atlas-launch-sub-conversations-2026-09-13.md`.

### PR #936 - 2026-09-13
- Cached per-user MCP clients no longer keep presenting an OAuth token the provider has already rotated away (closes #935): each cache entry records a fingerprint of the token it was built with, `refresh_stored_token` now evicts cached clients for the (user, server) pair -- idle entries immediately, in-use entries via the fingerprint check on their next use, so streaming calls are never torn down -- and a 401 from a per-user-auth tool call triggers one evict-refresh-retry cycle (concurrent 401s share a single rotation) before surfacing a friendly "re-authorize the server" error instead of the raw upstream transport error.

### PR #932 - 2026-09-13
- **Agent mode is on by default and its start banner is gone** (closes #849): the "Agent Mode Started (strategy: agentic, max steps: N)" transcript row is replaced by the small purple "Agent" box alone, and the Agent toggle now defaults to on everywhere for browsers with no stored choice -- an existing explicit off preference is still honored, and the toggle still works. The Max Agent Iterations slider in Tools and Settings is now bounded by the admin-configured `agent_max_steps` (exposed via `/api/config` and `/api/config/shell`) instead of a hardcoded 50, so the slider's upper end matches what the backend actually honors.

### PR #933 - 2026-09-13
- Chat transcript exports (.json and .txt) now open in a new browser tab by default instead of forcing a file download (closes #908); the old download remains as the fallback when the browser blocks the popup tab, and saving is still available from the opened tab (Ctrl+S) or Print / Save as PDF.

### PR #930 - 2026-09-13