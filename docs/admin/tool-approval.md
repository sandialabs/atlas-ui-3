# Tool Approval System

Last updated: 2026-01-19

The tool approval system provides a safety layer by requiring user confirmation before a tool is executed. This gives administrators and users fine-grained control over tool usage.

## Admin-Forced Approvals

As an administrator, you can mandate that certain high-risk functions always require user approval.

*   **Configuration**: In your `config/defaults/mcp.json` file (or an overrides directory set via `APP_CONFIG_OVERRIDES`), you can add a `require_approval` list to a server's definition.
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

## User-Controlled Auto-Approval

For tools that are not mandated to require approval by an admin, users can choose to "auto-approve" them to streamline their workflow. This option is available in the user settings panel.
