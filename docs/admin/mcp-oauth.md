# OAuth 2.1 Authentication for MCP Servers

Atlas UI 3 supports OAuth 2.1 authentication for MCP servers using the Authorization Code Flow with PKCE (Proof Key for Code Exchange). This allows users to securely authenticate with MCP servers that require user consent and OAuth credentials.

## Table of Contents

- [Overview](#overview)
- [Configuration](#configuration)
- [OAuth Flow](#oauth-flow)
- [Token Storage](#token-storage)
- [Manual JWT Upload](#manual-jwt-upload)
- [Examples](#examples)
- [Security Considerations](#security-considerations)
- [Troubleshooting](#troubleshooting)

## Overview

OAuth 2.1 authentication is suitable for:
- MCP servers that require user-specific authentication
- Services that need user consent before granting access
- Applications where the MCP server acts on behalf of the user

Atlas UI 3 uses the FastMCP OAuth helper to handle the entire OAuth flow, including:
- OAuth server discovery
- Dynamic client registration
- Local callback server for authorization code
- Token exchange with PKCE
- Automatic token refresh
- Encrypted token storage

## Configuration

To enable OAuth 2.1 authentication for an MCP server, add an `oauth_config` section to the server configuration in `config/overrides/mcp.json`:

```json
{
  "my-oauth-server": {
    "url": "https://fastmcp.cloud/mcp",
    "transport": "http",
    "groups": ["users"],
    "description": "Example OAuth-protected MCP server",
    "oauth_config": {
      "enabled": true,
      "scopes": "read write",
      "client_name": "Atlas UI 3",
      "callback_port": null,
      "token_storage_path": "~/.atlas-ui-3/oauth-tokens/my-oauth-server",
      "additional_metadata": null
    }
  }
}
```

### OAuth Configuration Fields

- **`enabled`** (boolean, required): Set to `true` to use OAuth authentication
- **`scopes`** (string, optional): OAuth scopes to request, space-separated (e.g., `"read write"`)
- **`client_name`** (string, optional): Client name for dynamic registration. Defaults to `"Atlas UI 3"`
- **`callback_port`** (integer, optional): Fixed port for OAuth callback server. If not specified, uses a random available port
- **`token_storage_path`** (string, optional): Path to encrypted token storage directory. Defaults to in-memory storage (tokens lost on restart)
- **`additional_metadata`** (object, optional): Extra metadata for client registration

## OAuth Flow

When Atlas UI 3 connects to an OAuth-protected MCP server, the following flow occurs:

1. **Token Check**: The client first checks for existing valid tokens in the configured storage
2. **OAuth Server Discovery**: If no valid tokens exist, discovers OAuth endpoints using well-known URIs
3. **Dynamic Client Registration**: Registers the client with the OAuth server (if not already registered)
4. **Local Callback Server**: Starts a temporary local HTTP server to receive the authorization code
5. **Browser Interaction**: Opens the user's browser to the OAuth server's authorization endpoint
6. **User Authentication**: User logs in and grants the requested scopes
7. **Authorization Code Exchange**: Captures the authorization code and exchanges it for tokens using PKCE
8. **Token Caching**: Saves tokens to encrypted storage for future use
9. **Authenticated Requests**: Access token is automatically included in requests to the MCP server
10. **Token Refresh**: When access token expires, automatically uses refresh token to get a new one

## Token Storage

### Default Storage (In-Memory)

By default, OAuth tokens are stored in memory and lost when the application restarts. This is suitable for testing but not recommended for production.

### Persistent Encrypted Storage

For production use, configure a persistent storage path:

```json
{
  "oauth_config": {
    "enabled": true,
    "token_storage_path": "~/.atlas-ui-3/oauth-tokens/my-server"
  }
}
```

**Security Features:**
- Tokens are encrypted using Fernet (symmetric encryption)
- Encryption key is auto-generated and stored securely with `0600` permissions
- For production, set the `OAUTH_STORAGE_ENCRYPTION_KEY` environment variable:

```bash
export OAUTH_STORAGE_ENCRYPTION_KEY="your-base64-encoded-fernet-key"
```

To generate a new encryption key:

```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

**Important**: Keep your encryption key secure. If the key is lost, stored tokens cannot be decrypted.

## Manual JWT Upload

For scenarios where OAuth flow is not suitable (e.g., service accounts, pre-issued JWTs), administrators can manually upload JWT tokens via the admin API.

### Upload JWT via API

```bash
curl -X POST "http://localhost:8000/admin/mcp/my-server/jwt" \
  -H "Content-Type: application/json" \
  -H "X-User-Email: admin@example.com" \
  -d '{"jwt_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}'
```

### Check JWT Status

```bash
curl "http://localhost:8000/admin/mcp/my-server/jwt" \
  -H "X-User-Email: admin@example.com"
```

### Delete JWT

```bash
curl -X DELETE "http://localhost:8000/admin/mcp/my-server/jwt" \
  -H "X-User-Email: admin@example.com"
```

### JWT Storage Security

- JWTs are encrypted using Fernet encryption
- Stored in `~/.atlas-ui-3/jwt-storage/` by default
- Encryption key auto-generated or set via `JWT_STORAGE_ENCRYPTION_KEY` environment variable
- File permissions set to `0600` (owner read/write only)

## Examples

### Example 1: Basic OAuth Configuration

Minimal OAuth setup with default settings:

```json
{
  "simple-oauth": {
    "url": "https://api.example.com/mcp",
    "transport": "http",
    "groups": ["users"],
    "oauth_config": {
      "enabled": true
    }
  }
}
```

### Example 2: OAuth with Custom Scopes

Request specific OAuth scopes:

```json
{
  "scoped-oauth": {
    "url": "https://api.example.com/mcp",
    "transport": "http",
    "groups": ["users"],
    "oauth_config": {
      "enabled": true,
      "scopes": "mcp:read mcp:write mcp:admin",
      "client_name": "Atlas UI Production"
    }
  }
}
```

### Example 3: OAuth with Fixed Callback Port

Useful for firewall rules or development:

```json
{
  "fixed-port-oauth": {
    "url": "https://api.example.com/mcp",
    "transport": "http",
    "groups": ["users"],
    "oauth_config": {
      "enabled": true,
      "callback_port": 8080,
      "token_storage_path": "~/.atlas-ui-3/oauth-tokens/fixed-port"
    }
  }
}
```

### Example 4: Manual JWT Upload

Configure server to accept manually uploaded JWT:

```json
{
  "jwt-server": {
    "url": "https://api.example.com/mcp",
    "transport": "http",
    "groups": ["admin"],
    "description": "Server using manually uploaded JWT"
  }
}
```

Then upload JWT via admin panel or API (see Manual JWT Upload section).

## Security Considerations

### Production Recommendations

1. **Always use encrypted token storage** with `token_storage_path` configured
2. **Set encryption keys via environment variables**:
   ```bash
   export OAUTH_STORAGE_ENCRYPTION_KEY="your-key-here"
   export JWT_STORAGE_ENCRYPTION_KEY="your-key-here"
   ```
3. **Restrict file permissions** on storage directories (done automatically)
4. **Use HTTPS URLs** for MCP servers in production
5. **Limit OAuth scopes** to minimum required permissions
6. **Rotate encryption keys periodically**

### Key Management

- Store encryption keys in secure key management systems (e.g., AWS Secrets Manager, Azure Key Vault)
- Never commit encryption keys to version control
- Use different keys for development, staging, and production
- Back up encryption keys securely

### Access Control

- OAuth and JWT endpoints require admin group membership
- Configure `admin_group` in `AppSettings` (defaults to `"admin"`)
- In production, use reverse proxy authentication (X-User-Email header)

## Troubleshooting

### OAuth Flow Fails to Start

**Problem**: Browser doesn't open or OAuth flow doesn't initiate

**Solutions**:
- Check that the MCP server URL is correct and accessible
- Verify OAuth server supports dynamic client registration
- Check logs for OAuth discovery errors
- Ensure firewall allows local callback server (random port or `callback_port`)

### Token Storage Errors

**Problem**: "Failed to decrypt tokens" or encryption errors

**Solutions**:
- Verify `OAUTH_STORAGE_ENCRYPTION_KEY` is set correctly
- Check file permissions on token storage directory
- Clear token storage and re-authenticate: `rm -rf ~/.atlas-ui-3/oauth-tokens/`
- Regenerate encryption key if lost (requires re-authentication)

### JWT Upload Fails

**Problem**: Cannot upload JWT via admin API

**Solutions**:
- Verify user is in admin group
- Check JWT format is valid (should start with `eyJ`)
- Verify server exists in MCP configuration
- Check `JWT_STORAGE_ENCRYPTION_KEY` is accessible

### Authentication Still Fails After OAuth

**Problem**: OAuth flow succeeds but MCP server rejects requests

**Solutions**:
- Check requested scopes match server requirements
- Verify access token hasn't expired (should auto-refresh)
- Check MCP server logs for authentication errors
- Try manual re-authentication: delete tokens and reconnect

### Missing Dependencies

**Problem**: `ImportError: No module named 'fastmcp.client.auth'`

**Solutions**:
- Upgrade FastMCP: `pip install fastmcp>=2.6.0`
- Check requirements.txt has `fastmcp>=2.6.0`
- Reinstall dependencies: `pip install -r requirements.txt`

**Problem**: `ImportError: No module named 'key_value'`

**Solutions**:
- Install key-value library: `pip install key-value[aio]>=0.4.0`
- Or disable persistent token storage (use in-memory, not recommended)

## Reference

- [FastMCP OAuth Documentation](https://gofastmcp.com/clients/auth/oauth)
- [OAuth 2.1 Specification](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1)
- [PKCE RFC 7636](https://datatracker.ietf.org/doc/html/rfc7636)
- [MCP Specification](https://modelcontextprotocol.io/)
