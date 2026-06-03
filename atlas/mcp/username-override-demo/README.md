# Atlas User Override Demo MCP Server

This MCP server demonstrates the _atlas_user override security feature in Atlas UI 3. This feature ensures that tools accepting an `_atlas_user` parameter always run with the authenticated user's identity, preventing LLMs from impersonating other users.

## Purpose

This server provides tools to demonstrate and explain the _atlas_user override security mechanism, which is critical for preventing unauthorized actions in multi-user environments.

## Security Feature Explained

### The Problem

Without _atlas_user override, an LLM could potentially call a tool with a different injected user:
```
Tool call: create_user_record(_atlas_user="admin@company.com", record_type="note", data="...")
```

This would allow the LLM to impersonate other users and perform unauthorized actions.

### The Solution

Atlas UI 3 automatically **overrides** the `_atlas_user` parameter with the authenticated user's email from the `X-User-Email` header (or the dev fallback user). The LLM cannot control this value.

**How it works:**

1. The tool's schema declares an `_atlas_user` parameter
2. The LLM generates a tool call (with or without an _atlas_user argument)
3. The Atlas UI backend detects that the tool accepts `_atlas_user`
4. The backend **injects or overrides** the `_atlas_user` argument with the authenticated user's email
5. The tool receives the correct, authenticated user in `_atlas_user`

**Code Location:**

The _atlas_user injection happens in `atlas/application/chat/utilities/tool_executor.py`:
- `tool_accepts_atlas_user()` checks if a tool's schema includes an `_atlas_user` parameter
- `inject_context_into_args()` injects the authenticated user's email into the arguments

## Configuration

Add this server to your `config/mcp.json` or use the example config:

```json
{
  "username-override-demo": {
    "command": ["python", "mcp/username-override-demo/main.py"],
    "cwd": "atlas",
    "groups": ["users"],
    "description": "Demonstrates the _atlas_user override security feature that prevents LLM user impersonation",
    "author": "Atlas UI Team",
    "short_description": "_atlas_user override security demo",
    "help_email": "support@example.com",
    "compliance_level": "Public"
  }
}
```

Or reference the pre-configured example:
```bash
# WARNING: Do NOT simply copy the example file as it will overwrite your existing config!
# Instead, manually merge the server configuration into your existing mcp.json file,
# or use a JSON merge tool to combine the configurations.

# View the example configuration:
cat atlas/config/mcp-example-configs/mcp-username-override-demo.json

# Manually add the "username-override-demo" entry to your config/mcp.json
```

## Available Tools

### 1. `get_user_info`

Retrieves information about the authenticated user.

**Parameters:**
- `_atlas_user` (string): Automatically overridden with authenticated user's email

**Example Usage:**
```
Get my user information
```

**Example Response:**
```json
{
  "results": {
    "username": "alice@example.com",
    "message": "Current authenticated user: alice@example.com",
    "security_note": "This _atlas_user was injected by Atlas UI backend and cannot be spoofed by the LLM"
  },
  "meta_data": {
    "elapsed_ms": 0.123
  }
}
```

### 2. `create_user_record`

Creates a record associated with the authenticated user.

**Parameters:**
- `_atlas_user` (string): Automatically overridden with authenticated user's email
- `record_type` (string): Type of record (e.g., "note", "task", "document")
- `data` (string): Content for the record

**Example Usage:**
```
Create a note for me with the text "Meeting at 3pm"
```

**Security Guarantee:**
The record will ALWAYS be created for the authenticated user, regardless of what the LLM tries to specify.

**Approval Flow Security:**
When tool approval with argument editing is enabled, users cannot bypass the _atlas_user override by editing the _atlas_user field in the approval dialog. The system re-applies security injections after user edits to ensure _atlas_user and other security-critical parameters cannot be tampered with.

### 3. `check_user_permissions`

Checks if the authenticated user has permission for a specific action.

**Parameters:**
- `_atlas_user` (string): Automatically overridden with authenticated user's email
- `resource` (string): Resource to check (e.g., "document", "database", "api")
- `action` (string): Action to check (e.g., "read", "write", "delete")

**Example Usage:**
```
Check if I have write permission for the database
```

**Security Guarantee:**
Permission checks are ALWAYS performed for the authenticated user. The LLM cannot check another user's permissions.

### 4. `demonstrate_override_attempt`

Explicitly demonstrates the _atlas_user override feature in action.

**Parameters:**
- `_atlas_user` (string): Automatically injected by Atlas UI backend with authenticated user's email
- `attempted_username` (string, optional): A username the LLM might try to use (for demonstration)

**Example Usage:**
```
Try to demonstrate the _atlas_user override by attempting to use admin@company.com
```

**Example Response:**
```json
{
  "results": {
    "actual_username": "alice@example.com",
    "attempted_username": "admin@company.com",
    "override_occurred": true,
    "impersonation_attempted": true,
    "explanation": "The authenticated user is: alice@example.com. The LLM attempted to use: admin@company.com. Atlas UI backend detected and blocked this impersonation attempt by overriding the _atlas_user parameter with the real authenticated user's email."
  }
}
```

## Testing the _atlas_user Override

To verify the _atlas_user override feature works:

1. Start Atlas UI and enable this server
2. Open the chat interface
3. Try these test scenarios:

**Test 1: Basic Override**
```
Use get_user_info to show me who I am
```
Expected: Returns your authenticated email from the X-User-Email header

**Test 2: Explicit Override Attempt**
```
Use demonstrate_override_attempt and try to use attempted_username "admin@company.com"
```
Expected: Shows that the actual authenticated user is your authenticated email, not admin@company.com

**Test 3: Record Creation**
```
Create a note for user "someoneelse@company.com" with the data "test"
```
Expected: The record is created for YOUR authenticated email, not someoneelse@company.com

**Test 4: Permission Check**
```
Check if user "admin@company.com" has write permission for database
```
Expected: The permission check is performed for YOUR authenticated email, not admin

## Security Implications

This _atlas_user override feature is critical for:

1. **Preventing User Impersonation**: LLMs cannot impersonate other users
2. **Audit Trail Integrity**: All actions are correctly attributed to the authenticated user
3. **Authorization Enforcement**: Permission checks always use the real user identity
4. **Data Isolation**: Users can only access their own data, even if the LLM tries otherwise

## Implementation Notes

### For Tool Developers

When creating MCP tools that need user context:

1. **Add an `_atlas_user` parameter to your tool's schema** if the tool needs to know who is calling it
2. **Trust the _atlas_user value** - it will always be the authenticated user
3. **Never accept authenticated-user identity from other parameters** - use the injected `_atlas_user` parameter only
4. **Document that _atlas_user is automatically provided** - callers don't need to supply it

**Example Tool Schema:**
```python
@mcp.tool
def my_user_specific_tool(_atlas_user: str, other_param: str) -> Dict[str, Any]:
    """Do something for the authenticated user.
    
    Args:
        _atlas_user: The authenticated user (automatically injected by Atlas UI)
        other_param: Some other parameter the LLM provides
    """
    # _atlas_user is guaranteed to be the authenticated user
    return {
        "results": {
            "username": _atlas_user,
            "result": f"Action performed for {_atlas_user}"
        }
    }
```

### Schema Awareness

The _atlas_user injection is **schema-aware**:
- Only tools that declare an `_atlas_user` parameter in their schema receive it
- Tools without an `_atlas_user` parameter are not affected
- This prevents breaking tools that don't expect this parameter

### Default User in Development

When running locally without a reverse proxy:
- Atlas UI falls back to a test user (configured in `APP_DEV_USER_EMAIL`)
- The _atlas_user override still works, using the dev fallback user
- This allows testing the feature in development environments

## Related Documentation

- **Admin Guide**: `docs/admin/mcp-servers.md` - See "A Note on the `_atlas_user` Argument" section
- **MCP Tool Outputs**: `docs/developer/mcp-tool-outputs.md` - Best practices for tool development
- **Authentication**: `.github/copilot-instructions.md` - How X-User-Email authentication works

## Troubleshooting

**Q: The LLM says it needs an _atlas_user parameter, what should I provide?**

A: Tell the LLM that the _atlas_user value is automatically provided by the system and it should not include it in the tool call arguments.

**Q: How can I verify which user is authenticated?**

A: Use the `get_user_info` tool from this server, or check the `/api/config` endpoint which returns the current user.

**Q: Can admin users override _atlas_user for testing?**

A: No. The _atlas_user override is enforced for all users, including admins. This ensures consistent security. For testing, you would need to authenticate as the target user.

**Q: What if I want to build a tool that can act on behalf of other users (e.g., an admin tool)?**

A: The tool should accept a separate parameter (e.g., `target_user`) and then check if the authenticated `_atlas_user` has admin permissions before acting on behalf of `target_user`.

## Security Best Practices

1. **Always use the `_atlas_user` parameter** for user-specific operations
2. **Never trust username from LLM-controlled sources** (e.g., tool name, other parameters, environment variables)
3. **Log the authenticated user** for audit trails and debugging
4. **Implement additional authorization checks** based on _atlas_user (e.g., check if user has permission for the requested action)
5. **Document security assumptions** in your tool descriptions

## Example: Admin Tool Pattern

If you need a tool that can act on behalf of other users (admin use case):

```python
@mcp.tool
def admin_create_record_for_user(
    _atlas_user: str,  # The authenticated admin (auto-injected)
    target_user: str,  # The user to create record for
    record_type: str,
    data: str
) -> Dict[str, Any]:
    """Create a record for another user (admin only).
    
    Args:
        _atlas_user: The authenticated admin user (automatically injected)
        target_user: The user to create the record for
        record_type: Type of record
        data: Record content
    """
    # Check if _atlas_user has admin permissions
    if not is_admin(_atlas_user):
        return {
            "results": {
                "error": f"User {_atlas_user} does not have admin permissions"
            }
        }
    
    # Now safe to create record for target_user
    return {
        "results": {
            "success": True,
            "admin_user": _atlas_user,
            "target_user": target_user,
            "message": f"Admin {_atlas_user} created {record_type} for {target_user}"
        }
    }
```

This pattern ensures:
- The authenticated user (`_atlas_user`) is always correct
- Admin authorization is checked
- Audit logs show who performed the action
- The LLM cannot impersonate an admin
