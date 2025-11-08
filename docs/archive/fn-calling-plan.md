# Tool Approval: Option B Implementation Plan

This plan implements Option B (cleaner) and addresses all failing requirements:

- Only add ToolApprovalConfig entries when a tool is explicitly listed under `require_approval` in `mcp.json`.
- Treat `allow_edit` as moot for approval requirement. UI will always allow editing when approval is requested.
- Add FORCE flag with correct semantics: `FORCE_TOOL_APPROVAL_GLOBALLY=true` forces approval for all tools (admin-required) and overrides everything else.
- Ensure the UI does not display “Thinking…” while waiting on user approval.

## Backend changes

1) AppSettings: FORCE flag
- Add `force_tool_approval_globally: bool = Field(default=False, validation_alias="FORCE_TOOL_APPROVAL_GLOBALLY")` to `AppSettings`.

2) Approval config build (Option B)
- In `ConfigManager.tool_approvals_config`:
  - Build `tools` map ONLY for tools that are explicitly in a server’s `require_approval` list (fully-qualified `server_tool`).
  - Do NOT add entries for tools that appear only in `allow_edit` lists; `allow_edit` does not affect approval requirement.

3) requires_approval(tool_name, config_manager)
- Short-circuit: if `config_manager.app_settings.force_tool_approval_globally` is true, return `(True, True, True)` — approval required, admin-enforced, always editable.
- Per-tool: if tool present in `tool_approvals_config.tools` (i.e., explicitly require_approval), return `(True, True, True)`.
- Default: fall back to `tool_approvals_config.require_approval_by_default`:
  - If true: `(True, True, True)` (admin-required by default).
  - If false: `(True, True, False)` (user-level approval; UI can auto-approve if user enables it).

Notes:
- “Always editable” is enforced at the UI level; backend can keep sending `allow_edit` for compatibility but it no longer gates editing.

## Frontend changes

4) Always-allow editing in approval UI
- In `Message.jsx` ToolApprovalMessage, remove the `message.allow_edit` condition and always render the Edit controls.

5) Hide Thinking during approval wait
- In `websocketHandlers.js`, when receiving `tool_approval_request`, call `setIsThinking(false)` before adding the approval message.

## Docs and env

6) .env example
- Add `FORCE_TOOL_APPROVAL_GLOBALLY=false` with comment: “true = force approval for all tools (admin-required).”

## Tests (follow-up)

- Backend unit tests: FORCE short-circuit, per-tool precedence, allow_edit-only doesn’t disable defaults, default true vs false behavior.
- Frontend tests: thinking hidden on approval request; edit controls visible regardless of `allow_edit`.

## Acceptance criteria

- With `REQUIRE_TOOL_APPROVAL_BY_DEFAULT=true` (and FORCE=false), any tool call triggers an approval request (admin-required). UI shows approval with editable args; no “Thinking…” while waiting.
- With `FORCE_TOOL_APPROVAL_GLOBALLY=true`, same approval behavior regardless of per-tool/default settings.
- With `REQUIRE_TOOL_APPROVAL_BY_DEFAULT=false` and user auto-approve ON, non-admin-required approvals auto-approve; admin-required approvals still prompt; inline toggle visible.
