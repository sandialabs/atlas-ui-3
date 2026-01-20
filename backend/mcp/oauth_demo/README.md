# OAuth Demo MCP Server

**Updated: 2025-01-19**

This MCP server demonstrates OAuth 2.1 and JWT authentication for MCP servers.

## Overview

This server provides simple tools that require authentication to access. It demonstrates how Atlas UI handles per-user authentication for MCP servers.

## Tools

| Tool | Description |
|------|-------------|
| `whoami` | Get information about the authenticated user |
| `get_protected_data` | Access protected resources (doc-001, doc-002, doc-003) |
| `list_user_permissions` | List permissions derived from OAuth token scopes |

## Configuration

Two example configurations are provided:

### OAuth 2.1 Configuration

Use `config/mcp-example-configs/mcp-oauth_demo.json`:

```json
{
  "oauth_demo": {
    "command": ["python", "mcp/oauth_demo/main.py"],
    "cwd": "backend",
    "auth_type": "oauth",
    "oauth_config": {
      "scopes": ["read", "write"],
      "client_name": "Atlas UI"
    }
  }
}
```

### JWT Configuration

Use `config/mcp-example-configs/mcp-jwt_demo.json`:

```json
{
  "jwt_demo": {
    "command": ["python", "mcp/oauth_demo/main.py"],
    "cwd": "backend",
    "auth_type": "jwt"
  }
}
```

## Testing with Mock OAuth Provider

1. Start the mock OAuth provider:
   ```bash
   cd mocks/oauth-mcp-mock
   pip install -r requirements.txt
   ./run.sh
   ```

2. Copy the OAuth demo config to your overrides:
   ```bash
   # Add contents of mcp-oauth_demo.json to config/overrides/mcp.json
   ```

3. Start Atlas UI:
   ```bash
   bash agent_start.sh
   ```

4. In the browser:
   - Click the Key icon in the header
   - Find "oauth_demo" server
   - Click "Connect" to start OAuth flow
   - Log in with test credentials:
     - `test@example.com` / `testpass123`
     - `admin@example.com` / `adminpass123`

5. Use the tools:
   - Ask: "Who am I?" (uses `whoami` tool)
   - Ask: "Get protected document doc-001" (uses `get_protected_data` tool)

## Authentication Types

| Type | Description | User Action |
|------|-------------|-------------|
| `none` | No authentication required | N/A |
| `bearer` | Admin-configured static token | N/A (configured by admin) |
| `oauth` | OAuth 2.1 per-user flow | Click "Connect", authenticate with provider |
| `jwt` | User uploads JWT manually | Click "Add Token", paste JWT |

## Security Notes

- Tokens are encrypted at rest using Fernet (AES-128-CBC)
- Each user's tokens are stored separately
- OAuth uses PKCE (S256) to prevent code interception
- Token values are never logged
