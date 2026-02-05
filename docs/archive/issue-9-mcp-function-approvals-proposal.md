# Proposal: User Approval Workflow for MCP Function Calls (Issue #9)

## Overview

This proposal introduces an approval workflow that lets users require review before any MCP function executes. When a tool call is about to run, the UI shows a modal with the function name and arguments; the user can edit arguments, approve, or deny (optionally with a reason). Users can also toggle auto-approval behavior at three scopes:

- Per function
- Per MCP server (group-level)
- Global (all functions)

The feature is guarded by a new flag and integrates with existing Chat UI events, agent modes, and the compliance-level filtering already present in the repository.


## Scope and success criteria

Success criteria:
- A pending MCP function call triggers a UI approval modal with schema-aware argument editing.
- Approve with edited, valid args executes the function using those args; deny prevents execution and surfaces a denial message.
- Auto-approval toggles are available per function, per server, and globally, with precedence Function > Server > Global.
- Decisions and policy changes are auditable and sanitized.
- Feature can be enabled/disabled via config without impacting current behavior when disabled.

Out of scope (initially):
- Time-limited approvals (e.g., “approve for 15 minutes”)
- Complex role-based policies beyond the three scopes above


## Architecture fit (current repo)

Relevant existing code and patterns:
- Tool execution flow: `atlas/application/chat/utilities/tool_utils.py` (especially `execute_single_tool`, argument injection/sanitization) and `notification_utils.py` (tool_* events).
- WebSocket publisher: `atlas/infrastructure/events/websocket_publisher.py`.
- Config and feature flags: `atlas/modules/config/manager.py` (`AppSettings`, config file resolution).
- Message types doc: `docs/messages_types_to_ui.md`.
- Compliance levels feature already implemented (flag and API surfacing).

We introduce an approval gate at the point just before tool execution in `execute_single_tool`, using the existing update callback/event stream to request a client decision and apply it.


## UX design

### Approval modal

- Trigger: receipt of `tool_approval_required` WebSocket event.
- Content:
  - Header: server name, function name; show compliance badge if available.
  - Arguments editor:
    - Prefer schema-driven inputs (from JSON Schema if provided by backend).
    - Fallback: JSON editor with validation.
  - Actions:
    - Approve: submit decision with edited args (if changed).
    - Deny: optional textual reason; submit decision.
  - Quick toggles (persisted and enforced server-side):
    - Auto-approve this function
    - Auto-approve all functions from this server
    - Auto-approve all functions
  - Hints: show precedence note “Function > Server > Global.”
  - Optional: countdown indicator if backend enforces a timeout.

### Settings surface

- Add a “Tool approvals” section in settings or tools panel:
  - Global toggle
  - Per-server toggles (with compliance badges)
  - Expand server to per-function toggles
  - Search/filter and “reset to defaults”

### Frontend plumbing

- Handle new WebSocket events (see Events section below).
- Expose approve/deny actions in `ChatContext`.
- Persist user preferences locally for UX; synchronize to backend policy so enforcement is server-side.


## Backend design

### Feature flag

- Add `FEATURE_TOOL_APPROVALS_ENABLED` to `AppSettings` (default `false`).
- When disabled, the system behaves exactly as it does today (no approval requests or gating).

### Policy model and precedence

- Effective policy is determined by highest-precision match:
  1) Function (e.g., `calculator_add`)
  2) Server (derived from name prefix or explicit server key)
  3) Global
- Data structure (conceptual):
  {
    "global": { "auto_approve": false },
    "servers": { "calculator": { "auto_approve": true } },
    "functions": { "calculator_add": { "auto_approve": false } }
  }
- Optional compliance-aware defaults (see below) can prime the effective policy but are always overrideable by explicit scope settings.

### Storage and persistence

- Bootstrapped from config files:
  - `config/defaults/mcp-approvals.json`
  - `config/overrides/mcp-approvals.json`
- Runtime policy changes:
  - Keep in-memory effective policy.
  - Persist updates to `data/` (e.g., `data/mcp-approvals.json`) or per-user files (e.g., `data/user-policies/{user_id}.json`) if multi-user persistence is desired now.
- Configuration surface in `/api/config` may include a summary of current policy for initial UI render.

### Enforcement point

- Intercept in `execute_single_tool` after argument preparation and before `notify_tool_start` + execution:
  1) Derive `server_name` (current logic uses function name prefix before last underscore) and `tool_name`.
  2) Evaluate `is_auto_approved(tool_name, server_name, user)`.
  3) If auto-approved → proceed as-is.
  4) If approval is required:
     - Create a pending-approval entry keyed by `tool_call.id`.
     - Emit `tool_approval_required` with sanitized display arguments and (optionally) JSON Schema.
     - Await a decision with a configurable timeout.
     - On approve: validate edited args, re-run injections, then proceed to `notify_tool_start`, execute, `notify_tool_complete`.
     - On deny or timeout: emit `tool_denied` and return a `ToolResult(success=false, error=…)` to continue the chat flow gracefully.
- Concurrency: multiple approvals in-flight, mapped by `tool_call_id`.

### Security and audit

- Sanitization: Use existing `_sanitize_args_for_ui` for outbound event arguments; do not leak capability URLs/tokens.
- Validation: Server validates edited args against declared schema; rebuild/augment args from canonical injections post-approval.
- Authorization: Only the session owner can approve their own pending calls; verify `session_id`/`user_email` on inbound decisions.
- Audit logging: Write to `logs/security_high_risk.jsonl` (or similar) with fields: timestamp, user, session_id, tool_name, server_name, decision, reason (if any), compliance_level (if any), policy_scope_used, and args hash (never raw full args).
- Timeouts: Configurable; denial reason “timeout.”


## API and WebSocket contracts

### WebSocket events (server → client)

- `tool_approval_required`
  - `tool_call_id`: string
  - `tool_name`: string
  - `server_name`: string
  - `arguments`: object (sanitized for display)
  - `schema`?: JSON Schema object for parameters
  - `compliance_level`?: string
  - `timeout_seconds`?: number

- `tool_approval_granted`
  - `tool_call_id`: string
  - `tool_name`: string
  - `server_name`: string
  - `approved_args`?: object (sanitized/shape-only)

- `tool_denied`
  - `tool_call_id`: string
  - `tool_name`: string
  - `server_name`: string
  - `reason`?: string

- `tool_approval_policy_update` (optional, for live-sync of settings panel)
  - `scope`: "global" | "server" | "function"
  - `id`?: server_name or tool_name
  - `auto_approve`: boolean

These complement existing tool events (`tool_start`, `tool_complete`, `tool_error`).

### WebSocket inbound command (client → server)

- `tool_approval_decision`
  - `tool_call_id`: string
  - `decision`: "approve" | "deny"
  - `edited_args`?: object
  - `reason`?: string

### REST endpoints (optional but recommended)

- `GET /api/tools/approval/policy` → returns effective policy snapshot
- `PUT /api/tools/approval/policy` → update policy at one of the scopes
  - Body: `{ scope, id?, auto_approve }`
- `POST /api/tools/approval/decision` → REST fallback to submit an approval decision if socket unavailable


## Compliance-level integration (optional)

- Default policy presets by compliance level (leveraging `FEATURE_COMPLIANCE_LEVELS_ENABLED`):
  - Public: auto-approve by default
  - External/Internal: configurable
  - SOC2/HIPAA/FedRAMP: require approval by default
- UI: show compliance badge in modal and a note when the compliance level influences default behavior.
- Policy always respects explicit overrides at function/server/global scopes.


## Rollout plan

1) Add `FEATURE_TOOL_APPROVALS_ENABLED=false` default and `.env.example` documentation.
2) Implement approval gating behind the flag; when off, skip all new paths.
3) Provide default config `config/defaults/mcp-approvals.json` with safe defaults (recommend `global.auto_approve=false`).
4) Update `/api/config` to surface `features.approvals` and optional policy summary for front-end bootstrap.
5) Documentation updates:
   - `docs/messages_types_to_ui.md`: add new event types and payloads
   - New user guide section under `docs/advanced-features.md` or a dedicated page


## Testing plan

- Backend unit tests
  - Policy precedence resolution (Function > Server > Global)
  - Approval required → approve path → tool executes with edited args
  - Approval required → deny path → no execution, denial event emitted
  - Timeout → denial with reason
  - Schema validation failures on edited args return meaningful errors and keep modal open (when using REST decision path)

- Frontend unit tests
  - Modal renders with function name and pre-filled args; validates edits
  - Approve/deny actions dispatch correct decisions
  - Toggles update effective policy and persist
  - Socket path and REST fallback path

- Integration/E2E tests (Playwright)
  - Simulate LLM producing a tool call that requires approval
  - Approve with edited args; verify subsequent `tool_start` and `tool_complete`
  - Deny; verify `tool_denied` and no `tool_start`

- Manual validation (aligns with existing `docs/advanced-features.md` validation approach)
  - Trigger modal; verify behavior under different compliance filters
  - Confirm that auto-approval toggles suppress future prompts per scope


## Open questions / decisions

- Persistence granularity: shared/global vs per-user policy files; initial proposal favors global with option to add per-user later.
- Session-scoped approvals: do we add a “for this session only” memory in v1?
- Multiple concurrent modals: queue vs multiple visible dialogs; initial approach supports parallel modals mapped by `tool_call_id`.
- Timeout duration default and UI countdown.
- Where to surface the settings UI: header menu vs dedicated settings page vs tools panel.


## Implementation checklist (file-level)

- Backend
  - `atlas/modules/config/manager.py`: add flag, load approvals config
  - `atlas/application/chat/utilities/tool_utils.py`: intercept and gate in `execute_single_tool`
  - `atlas/application/chat/utilities/notification_utils.py`: add `notify_tool_approval_required`, `notify_tool_denied`, `notify_tool_approval_granted`
  - `atlas/infrastructure/events/websocket_publisher.py`: optional thin wrappers for the above
  - `atlas/routes/tools_approval_routes.py`: policy GET/PUT and decision POST
  - WebSocket handler (where inbound messages are parsed): consume `tool_approval_decision`
  - Audit: write structured JSON lines to security log on decisions/timeouts

- Frontend
  - `src/contexts/ChatContext.jsx`: handle new events; expose decision dispatchers
  - `src/components/modals/ToolApprovalModal.jsx`: modal UI
  - `src/components/Settings/ToolApprovals.jsx`: toggles UI
  - `src/api/client.ts`: policy GET/PUT, decision POST fallback
  - Schema-driven form helper (with JSON editor fallback)

- Docs
  - This proposal
  - Update `docs/messages_types_to_ui.md` with new events


## Prior art and rationale

- Aligns with existing event-driven updates and the `update_callback` pattern.
- Reuses established arg-sanitization to prevent leaking secrets.
- Uses simple precedence rules that are intuitive and easily explainable.
- Optional tie-in with compliance levels supports stronger governance without complexity.


## Acceptance criteria (for Issue #9)

- With approvals enabled and auto-approve disabled at the effective scope, a tool call presents an approval modal.
- Approve with edited args runs the function with those args; deny prevents execution.
- Auto-approval toggles at function, server, and global levels work with documented precedence and persist across sessions.
- Decisions and policy changes are logged and auditable.
- When the feature flag is disabled, behavior is unchanged from current mainline.