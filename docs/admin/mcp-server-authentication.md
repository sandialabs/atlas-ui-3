# MCP API Key Authentication

**Created:** 2025-01-21
**Updated:** 2026-01-25
**PR:** #253

## Overview

This feature enables Atlas UI users to manually provide API keys, JWTs, or bearer tokens for MCP servers that require authentication. This is a **per-user** authentication mechanism - each user provides their own credentials, which are securely stored and used for their MCP tool calls.

## Supported Token Types

| Type | Description | Use Case |
|------|-------------|----------|
| `api_key` | User-provided API key | Services like OpenAI, Anthropic, external APIs |
| `jwt` | JSON Web Token | Identity-aware services, internal APIs |
| `bearer` | Bearer token | Generic OAuth-style tokens, session tokens |
| `none` | No authentication required | Public MCP servers |

## How It Works

### User Flow

1. User opens the Tools panel and sees servers that require authentication
2. User clicks the key icon next to a server requiring auth
3. User pastes their API key or token in the modal
4. Token is securely encrypted and stored per-user
5. Future MCP tool calls automatically include the token

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    API Key Authentication Flow                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. User sees key icon next to server in Tools panel                     │
│                        │                                                 │
│                        ▼                                                 │
│  2. User clicks key icon, TokenInputModal opens                          │
│                        │                                                 │
│                        ▼                                                 │
│  3. User pastes API key/token and optionally sets expiration             │
│                        │                                                 │
│                        ▼                                                 │
│  4. Frontend calls POST /api/mcp/auth/{server}/token                     │
│                        │                                                 │
│                        ▼                                                 │
│  5. Backend encrypts token and stores in token_storage.py                │
│     Key: (user_email, server_name)                                       │
│                        │                                                 │
│                        ▼                                                 │
│  6. Future MCP calls include token in Authorization header               │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Token Storage

Tokens are stored encrypted on disk using Fernet (AES-128-CBC):

- **Location:** Set via `MCP_TOKEN_STORAGE_DIR` env var, or defaults to `config/secure/mcp_tokens.enc`
- **Encryption key:** From `MCP_TOKEN_ENCRYPTION_KEY` environment variable
- **Key format:** `{user_email}:{server_name}`

Each user's tokens are isolated - users cannot access each other's tokens.

## Configuration

### Server Configuration

Configure MCP servers in `config/overrides/mcp.json`:

```json
{
  "my-api-server": {
    "description": "My API Server",
    "url": "https://api.example.com/mcp",
    "transport": "http",
    "auth_type": "api_key",
    "auth_header": "X-API-Key"
  },
  "jwt-protected-server": {
    "description": "JWT Protected Server",
    "url": "https://jwt.example.com/mcp",
    "transport": "http",
    "auth_type": "jwt"
  }
}
```

**Configuration fields:**
- `auth_type`: Type of authentication required (`api_key`, `jwt`, `bearer`, or `none`)
- `auth_header`: (Optional) Custom header name for API key auth. Defaults to `X-API-Key`. Only used when `auth_type` is `api_key`.

**Note:** Per-user authentication (`auth_type: jwt`, `bearer`, `api_key`) is only supported for HTTP/SSE transport servers. Stdio-based servers cannot use per-user authentication because tokens are injected via HTTP headers.

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `MCP_TOKEN_ENCRYPTION_KEY` | Key for encrypting stored tokens | Recommended |
| `MCP_TOKEN_STORAGE_DIR` | Directory path for token storage file | Optional |

**Encryption Key:** If `MCP_TOKEN_ENCRYPTION_KEY` is not set, an ephemeral key is generated. Tokens will not persist across application restarts in this case.

**Storage Location:** If `MCP_TOKEN_STORAGE_DIR` is not set, tokens are stored in the first writable location from:
1. `{project_root}/config/secure/`
2. `{project_root}/runtime/tokens/`
3. `~/.atlas-ui/tokens/`
4. System temp directory (fallback)

## API Endpoints

### GET /api/mcp/auth/status

Get authentication status for all MCP servers the user can access.

**Response:**
```json
{
  "servers": [
    {
      "server_name": "my-api-server",
      "auth_type": "api_key",
      "auth_required": true,
      "authenticated": true,
      "token_type": "api_key",
      "is_expired": false,
      "expires_at": null,
      "description": "My API Server"
    }
  ],
  "user": "user@example.com"
}
```

### POST /api/mcp/auth/{server_name}/token

Upload an API key or token for a server.

**Request:**
```json
{
  "token": "sk-abc123...",
  "expires_at": 1705678900,
  "scopes": "read write"
}
```

**Response:**
```json
{
  "message": "Token stored for server 'my-api-server'",
  "server_name": "my-api-server",
  "token_type": "api_key",
  "expires_at": 1705678900,
  "scopes": "read write"
}
```

### DELETE /api/mcp/auth/{server_name}/token

Remove a stored token (disconnect from server).

**Response:**
```json
{
  "message": "Token removed for server 'my-api-server'",
  "server_name": "my-api-server"
}
```

## UI Components

### TokenInputModal

A reusable modal component for entering API keys or tokens.

**Props:**
- `isOpen`: boolean - Whether the modal is visible
- `serverName`: string - Name of the server to authenticate
- `onClose`: function - Called when modal should close
- `onUpload`: function(tokenData) - Called with `{ token, expires_at }` when user submits
- `isLoading`: boolean - Whether upload is in progress

### ToolsPanel Integration

The Tools panel shows authentication status for servers with `auth_type` of `api_key`, `jwt`, or `bearer`:

- **Green shield icon:** Authenticated successfully
- **Yellow key icon:** Authentication required (click to add token)

## Security Considerations

1. **Encryption at Rest:** All tokens encrypted using Fernet (AES-128-CBC)
2. **Per-User Isolation:** Users cannot access each other's tokens
3. **No Token Logging:** Token values are never logged (sanitized)
4. **Expiration Tracking:** Optional expiration date tracked and validated
5. **Secure Storage:** Tokens stored in dedicated secure directory

## Demo Server

### api_key_demo

A demo MCP server requiring API key authentication. Demonstrates the full per-user API key flow.

**Location:** `backend/mcp/api_key_demo/`

**Config:** `config/mcp-example-configs/mcp-api_key_demo.json`

**Valid test keys:**
- `test123` - Test user (developer role)
- `admin123` - Admin user (admin role)

**Running the demo:**
```bash
cd backend/mcp/api_key_demo
bash run.sh
```

The server runs on port 8006 by default and validates API keys via the `X-API-Key` header.

## Files

### Backend

- `backend/modules/mcp_tools/token_storage.py` - Encrypted token storage
- `backend/routes/mcp_auth_routes.py` - API endpoints for token management
- `backend/modules/mcp_tools/client.py` - MCP client with token injection
- `backend/modules/config/config_manager.py` - auth_type configuration

### Frontend

- `frontend/src/components/TokenInputModal.jsx` - Token input modal
- `frontend/src/hooks/useServerAuthStatus.js` - Auth status hook
- `frontend/src/components/ToolsPanel.jsx` - Auth indicators in Tools panel

### Demo Server

- `backend/mcp/api_key_demo/main.py` - API key auth demo server
- `backend/mcp/api_key_demo/run.sh` - Startup script (prints config snippet)

## Related

- [MCP Server Configuration](./mcp-servers.md) - General MCP server configuration
