# Tool Approval System

Last updated: 2026-09-04

The tool approval system provides a safety layer by requiring user confirmation before a tool is executed. This gives administrators and users fine-grained control over tool usage.

## Admin-Forced Approvals

As an administrator, you can mandate that certain high-risk functions always require user approval.

*   **Configuration**: In your `config/mcp.json` file, you can add a `require_approval` list to a server's definition.
*   **Behavior**: Any function listed here will always prompt the user for approval, and the user cannot disable this check.

**Example:**
```json
{
  "filesystem_tool": {
    "groups": ["admin"],
    "require_approval": ["delete_file", "overwrite_file"]
  }
}
```

## Global Approval Requirement

You can enforce that **all** tools require user approval by setting the following in your `.env` file:

```
FORCE_TOOL_APPROVAL_GLOBALLY=true
```

This setting overrides all other user preferences and is a simple way to enforce maximum safety.

## User-Binding and Cross-User Protection

In multi-user deployments, each approval request is bound to the authenticated user who triggered it. When a user responds to an approval prompt, the system verifies that the responding user matches the request owner. If a different user attempts to approve, reject, or edit arguments for another user's pending tool call, the response is rejected and a security warning is logged.

This prevents cross-user approval bypass where one user who learned another user's pending `tool_call_id` could hijack their tool execution.

**Backward compatibility**: In single-user or legacy deployments where `user_email` is not set, the ownership check is skipped and approvals work as before.

## User-Controlled Auto-Approval

For tools that are not mandated to require approval by an admin, users can choose to "auto-approve" them to streamline their workflow. This option is available in the user settings panel.

## Decision Audit Trail

Every human approval path appends one JSONL row to the tool-call decision audit file. This is Phase 0 evidence for correlating what was presented, what was authorized, and the later execution telemetry for the same `tool_call_id`. It does not change approval behavior.

### Path and permissions

*   **Default**: `data/tool_call_audit.jsonl`
*   **Override**: `TOOL_CALL_AUDIT_PATH` (absolute, or relative to the project root)
*   **Permissions**: newly created parent directories are `0o700`; the audit file is `0o600`
*   **Retention**: the file is append-only and unbounded. Operators must rotate or archive it under local retention policy. Atlas does not prune it.

Writes are best-effort. A malformed payload or unwritable path is logged and skipped; it never blocks approve, reject, timeout, or ownership-check behavior.

### Record format

```json
{
  "ts": "2026-09-04T16:00:00+00:00",
  "event": "tool_approval_decision",
  "user": "responder@example.com",
  "request_owner": "owner@example.com",
  "tool_call_id": "call-123",
  "tool_name": "shell_bash",
  "decision_args_sha256": "…64 hex chars…",
  "decision": "approved",
  "decision_origin": "approval_response",
  "arguments_edited": false,
  "reason_present": false
}
```

`decision` is one of `approved`, `rejected`, `timeout`, or `invalid_responder`. `user` is the actor who produced the row; `request_owner` is the user bound to the pending call. They differ only on cross-user ownership failures. Raw tool arguments and rejection reasons are never written.

### Hash threat model

`decision_args_sha256` is SHA-256 over the canonical JSON of the arguments at the **decision** boundary: the client-visible PresentedCall, or the approved edited form (including an explicit `{}`). It is a correlation/integrity fingerprint, not a confidentiality control. Low-entropy values such as filenames, flags, and IDs can be confirmed by enumerating likely inputs. Do not treat the hash as a substitute for file permissions or access control.

The hash is intentionally **not** an execution hash. After an edited approval, Atlas may re-inject trusted fields such as `_atlas_user` before the tool runs. Correlate later execution evidence by `tool_call_id` rather than assuming the hashes are equal.
