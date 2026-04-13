# Tool Approval System

Last updated: 2026-01-19

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
