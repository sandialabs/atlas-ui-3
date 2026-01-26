# Username Override Demo MCP Server

This MCP server demonstrates the username override security feature in Atlas UI 3. This feature ensures that tools accepting a `username` parameter always run with the authenticated user's identity, preventing LLMs from impersonating other users.

## Purpose

This server provides tools to demonstrate and explain the username override security mechanism, which is critical for preventing unauthorized actions in multi-user environments.

## Security Feature Explained

### The Problem

Without username override, an LLM could potentially call a tool with a different username:
```
Tool call: create_user_record(username="admin@company.com", record_type="note", data="...")
```

This would allow the LLM to impersonate other users and perform unauthorized actions.

### The Solution

Atlas UI 3 automatically **overrides** the `username` parameter with the authenticated user's email from the `X-User-Email` header (or the dev fallback user). The LLM cannot control this value.

**How it works:**

1. The tool's schema declares a `username` parameter
2. The LLM generates a tool call (with or without a username argument)
3. The Atlas UI backend detects that the tool accepts `username`
4. The backend **injects or overrides** the `username` argument with the authenticated user's email
5. The tool receives the correct, authenticated username

**Code Location:**

The username injection happens in `backend/application/chat/utilities/tool_utils.py`:
- `tool_accepts_username()` checks if a tool's schema includes a `username` parameter
- `inject_context_into_args()` injects the authenticated user's email into the arguments

## Configuration

Add this server to your `config/overrides/mcp.json` or use the example config:

```json
{
  "username-override-demo": {
    "command": ["python", "mcp/username-override-demo/main.py"],
    "cwd": "backend",
    "groups": ["users"],
    "description": "Demonstrates the username override security feature that prevents LLM user impersonation",
    "author": "Atlas UI Team",
    "short_description": "Username override security demo",
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
cat config/mcp-example-configs/mcp-username-override-demo.json

# Manually add the "username-override-demo" entry to your config/overrides/mcp.json
```

## Available Tools

### 1. `get_user_info`

Retrieves information about the authenticated user.

**Parameters:**
- `username` (string): Automatically overridden with authenticated user's email

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
    "security_note": "This username was injected by Atlas UI backend and cannot be spoofed by the LLM"
  },
  "meta_data": {
    "elapsed_ms": 0.123
  }
}
```

### 2. `create_user_record`

Creates a record associated with the authenticated user.

**Parameters:**
- `username` (string): Automatically overridden with authenticated user's email
- `record_type` (string): Type of record (e.g., "note", "task", "document")
- `data` (string): Content for the record

**Example Usage:**
```
Create a note for me with the text "Meeting at 3pm"
```

**Security Guarantee:**
The record will ALWAYS be created for the authenticated user, regardless of what the LLM tries to specify.

**Approval Flow Security:**
When tool approval with argument editing is enabled, users cannot bypass the username override by editing the username field in the approval dialog. The system re-applies security injections after user edits to ensure username and other security-critical parameters cannot be tampered with.

### 3. `check_user_permissions`

Checks if the authenticated user has permission for a specific action.

**Parameters:**
- `username` (string): Automatically overridden with authenticated user's email
- `resource` (string): Resource to check (e.g., "document", "database", "api")
- `action` (string): Action to check (e.g., "read", "write", "delete")

**Example Usage:**
```
Check if I have write permission for the database
```

**Security Guarantee:**
Permission checks are ALWAYS performed for the authenticated user. The LLM cannot check another user's permissions.

### 4. `demonstrate_override_attempt`

Explicitly demonstrates the username override feature in action.

**Parameters:**
- `username` (string): Automatically injected by Atlas UI backend with authenticated user's email
- `attempted_username` (string, optional): A username the LLM might try to use (for demonstration)

**Example Usage:**
```
Try to demonstrate the username override by attempting to use admin@company.com
```

**Example Response:**
```json
{
  "results": {
    "actual_username": "alice@example.com",
    "attempted_username": "admin@company.com",
    "override_occurred": true,
    "impersonation_attempted": true,
    "explanation": "The authenticated user is: alice@example.com. The LLM attempted to use: admin@company.com. Atlas UI backend detected and blocked this impersonation attempt by overriding the username parameter with the real authenticated user's email."
  }
}
```

## Testing the Username Override

To verify the username override feature works:

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
Use demonstrate_override_attempt and try to use the username "admin@company.com"
```
Expected: Shows that the actual username is your authenticated email, not admin@company.com

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

This username override feature is critical for:

1. **Preventing User Impersonation**: LLMs cannot impersonate other users
2. **Audit Trail Integrity**: All actions are correctly attributed to the authenticated user
3. **Authorization Enforcement**: Permission checks always use the real user identity
4. **Data Isolation**: Users can only access their own data, even if the LLM tries otherwise

## Implementation Notes

### For Tool Developers

When creating MCP tools that need user context:

1. **Add a `username` parameter to your tool's schema** if the tool needs to know who is calling it
2. **Trust the username value** - it will always be the authenticated user
3. **Never accept username from other parameters** - use the injected `username` parameter only
4. **Document that username is automatically provided** - callers don't need to supply it

**Example Tool Schema:**
```python
@mcp.tool
def my_user_specific_tool(username: str, other_param: str) -> Dict[str, Any]:
    """Do something for the authenticated user.
    
    Args:
        username: The authenticated user (automatically injected by Atlas UI)
        other_param: Some other parameter the LLM provides
    """
    # username is guaranteed to be the authenticated user
    return {
        "results": {
            "username": username,
            "result": f"Action performed for {username}"
        }
    }
```

### Schema Awareness

The username injection is **schema-aware**:
- Only tools that declare a `username` parameter in their schema receive it
- Tools without a `username` parameter are not affected
- This prevents breaking tools that don't expect this parameter

### Default User in Development

When running locally without a reverse proxy:
- Atlas UI falls back to a test user (configured in `APP_DEV_USER_EMAIL`)
- The username override still works, using the dev fallback user
- This allows testing the feature in development environments

## Related Documentation

- **Admin Guide**: `docs/admin/mcp-servers.md` - See "A Note on the `username` Argument" section
- **MCP Tool Outputs**: `docs/developer/mcp-tool-outputs.md` - Best practices for tool development
- **Authentication**: `.github/copilot-instructions.md` - How X-User-Email authentication works

## Troubleshooting

**Q: The LLM says it needs a username parameter, what should I provide?**

A: Tell the LLM that the username is automatically provided by the system and it should not include it in the tool call arguments.

**Q: How can I verify which user is authenticated?**

A: Use the `get_user_info` tool from this server, or check the `/api/config` endpoint which returns the current user.

**Q: Can admin users override the username for testing?**

A: No. The username override is enforced for all users, including admins. This ensures consistent security. For testing, you would need to authenticate as the target user.

**Q: What if I want to build a tool that can act on behalf of other users (e.g., an admin tool)?**

A: The tool should accept a separate parameter (e.g., `target_user`) and then check if the authenticated `username` has admin permissions before acting on behalf of `target_user`.

## Security Best Practices

1. **Always use the `username` parameter** for user-specific operations
2. **Never trust username from other sources** (e.g., tool name, other parameters, environment variables)
3. **Log the username** for audit trails and debugging
4. **Implement additional authorization checks** based on the username (e.g., check if user has permission for the requested action)
5. **Document security assumptions** in your tool descriptions

## Example: Admin Tool Pattern

If you need a tool that can act on behalf of other users (admin use case):

```python
@mcp.tool
def admin_create_record_for_user(
    username: str,  # The authenticated admin (auto-injected)
    target_user: str,  # The user to create record for
    record_type: str,
    data: str
) -> Dict[str, Any]:
    """Create a record for another user (admin only).
    
    Args:
        username: The authenticated admin user (automatically injected)
        target_user: The user to create the record for
        record_type: Type of record
        data: Record content
    """
    # Check if username has admin permissions
    if not is_admin(username):
        return {
            "results": {
                "error": f"User {username} does not have admin permissions"
            }
        }
    
    # Now safe to create record for target_user
    return {
        "results": {
            "success": True,
            "admin_user": username,
            "target_user": target_user,
            "message": f"Admin {username} created {record_type} for {target_user}"
        }
    }
```

This pattern ensures:
- The authenticated user (`username`) is always correct
- Admin authorization is checked
- Audit logs show who performed the action
- The LLM cannot impersonate an admin
