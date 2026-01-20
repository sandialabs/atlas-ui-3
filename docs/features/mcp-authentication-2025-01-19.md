# MCP Server Authentication

**Updated: 2025-01-19**

This document describes how Atlas UI supports authentication with MCP (Model Context Protocol) servers that require OAuth 2.1 or JWT authentication.

## Overview

Atlas UI supports multiple authentication methods for MCP servers:

| Auth Type | Description | Use Case |
|-----------|-------------|----------|
| `none` | No authentication required | Public MCP servers |
| `bearer` | Static bearer token (admin-configured) | Server-to-server auth with shared token |
| `oauth` | OAuth 2.1 per-user flow | User authenticates with their identity |
| `jwt` | User uploads their own JWT | Manual token provision |

## Per-User Authentication

For servers with `auth_type: "oauth"` or `auth_type: "jwt"`, each user must authenticate individually. Tokens are stored securely:

- **Encrypted at rest** using Fernet (AES-128-CBC)
- **Per-user isolation** - users cannot access each other's tokens
- **Persistent** across application restarts (when `MCP_TOKEN_ENCRYPTION_KEY` is set)

## Configuration

### Server Configuration

In your MCP server configuration (`config/overrides/mcp.json`):

```json
{
  "my-oauth-server": {
    "description": "OAuth-protected MCP server",
    "url": "https://example.com/mcp",
    "transport": "http",
    "auth_type": "oauth",
    "oauth_config": {
      "scopes": ["read", "write"],
      "client_name": "Atlas UI"
    },
    "enabled": true
  },
  "my-jwt-server": {
    "description": "JWT-protected MCP server",
    "url": "https://api.example.com/mcp",
    "transport": "http",
    "auth_type": "jwt",
    "enabled": true
  }
}
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MCP_TOKEN_ENCRYPTION_KEY` | Key for encrypting stored tokens | Random (ephemeral) |

**Important**: Set `MCP_TOKEN_ENCRYPTION_KEY` to persist tokens across restarts. Can be:
- A Fernet key (base64-encoded 32 bytes)
- A passphrase (will be derived using PBKDF2)

Example:
```bash
# Generate a Fernet key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Or use a passphrase
export MCP_TOKEN_ENCRYPTION_KEY="my-secure-passphrase-here"
```

## User Flow

### OAuth 2.1 Flow

1. User clicks the **Key** icon in the header to open MCP Authentication panel
2. Servers requiring authentication are listed with their status
3. User clicks **Connect** for an OAuth server
4. A popup window opens with the OAuth provider's login page
5. User authenticates and grants consent
6. Popup closes automatically, tokens are stored
7. User can now use the server's tools

### JWT Upload Flow

1. User opens MCP Authentication panel
2. Clicks **Add Token** for a JWT server
3. Pastes their JWT token and optionally sets expiration
4. Token is stored and the server is now accessible

### Disconnecting

1. Open MCP Authentication panel
2. Click **Disconnect** for any authenticated server
3. Tokens are removed

## API Endpoints

### User Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/mcp/auth/status` | GET | Get auth status for all servers |
| `/api/mcp/auth/{server}/token` | POST | Upload JWT for a server |
| `/api/mcp/auth/{server}/token` | DELETE | Remove token for a server |
| `/api/mcp/auth/{server}/oauth/start` | GET | Start OAuth flow |
| `/api/mcp/auth/{server}/oauth/callback` | GET | OAuth callback handler |

### Request/Response Examples

**Get Auth Status:**
```bash
curl -H "X-User-Email: user@example.com" \
  http://localhost:8000/api/mcp/auth/status
```

Response:
```json
{
  "servers": [
    {
      "server_name": "github-mcp",
      "auth_type": "oauth",
      "auth_required": true,
      "authenticated": true,
      "is_expired": false,
      "expires_at": 1705678900,
      "time_until_expiry": 3500,
      "scopes": "repo user"
    }
  ],
  "user": "user@example.com"
}
```

**Upload JWT:**
```bash
curl -X POST \
  -H "X-User-Email: user@example.com" \
  -H "Content-Type: application/json" \
  -d '{"token": "eyJ...", "expires_at": 1705678900}' \
  http://localhost:8000/api/mcp/auth/my-jwt-server/token
```

## OAuth 2.1 Requirements

For OAuth to work, the MCP server must implement:

1. **Discovery endpoint**: `/.well-known/oauth-authorization-server`
   - Returns `authorization_endpoint` and `token_endpoint`

2. **Authorization endpoint**: Accepts OAuth 2.1 authorization requests with PKCE

3. **Token endpoint**: Exchanges authorization codes for tokens

See [FastMCP OAuth Documentation](https://gofastmcp.com/clients/auth/oauth) for server implementation details.

## Security Considerations

1. **Token Encryption**: All tokens are encrypted at rest
2. **PKCE Required**: OAuth flow uses PKCE (S256) to prevent code interception
3. **Per-User Isolation**: Users can only access their own tokens
4. **No Token Logging**: Token values are never written to logs
5. **Secure Storage Location**: Tokens stored in `config/secure/` with restricted permissions

## Troubleshooting

### OAuth popup doesn't open
- Check that popups are allowed for the site
- Verify the server URL is accessible

### OAuth callback fails
- Ensure the callback URL matches the registered redirect URI
- Check server logs for detailed error messages

### Token expired
- Click **Connect** to re-authenticate (OAuth)
- Upload a new token (JWT)

### Tokens not persisting
- Set `MCP_TOKEN_ENCRYPTION_KEY` environment variable
- Check write permissions to `config/secure/` directory

## Testing

### Mock OAuth Server

A mock OAuth/MCP server is available for testing:

```bash
cd mocks/oauth-mcp-mock
pip install -r requirements.txt
./run.sh
```

Test users:
- `test@example.com` / `testpass123`
- `admin@example.com` / `adminpass123`

Add to your MCP config:
```json
{
  "test-oauth-server": {
    "url": "http://localhost:8001/mcp",
    "transport": "http",
    "auth_type": "oauth",
    "oauth_config": {
      "scopes": ["read", "write"],
      "client_name": "Atlas UI"
    }
  }
}
```
