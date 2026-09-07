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

Configure MCP servers in `config/mcp.json`:

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
- `auth_type`: Type of authentication required (`api_key`, `jwt`, `bearer`, `oauth`, `delegated`, or `none`)
- `auth_header`: (Optional) Custom header name for API key auth. Defaults to `X-API-Key`. Only used when `auth_type` is `api_key`.
- `oauth_config`: (Optional) Overrides for `auth_type: oauth`. See [OAuth 2.1 servers](#oauth-21-servers).

**Note:** Per-user authentication (`auth_type: jwt`, `bearer`, `api_key`, `oauth`) is only supported for HTTP/SSE transport servers. Stdio-based servers cannot use per-user authentication because tokens are injected via HTTP headers.

## OAuth 2.1 servers

`auth_type: "oauth"` is for remote MCP servers that implement the MCP
authorization spec: they answer an unauthenticated request with `401` and a
`WWW-Authenticate: Bearer resource_metadata="..."` challenge. Instead of asking
the user to obtain a token by hand, Atlas runs the Authorization Code flow with
PKCE for them, in the browser, and stores the result per user.

### Configuration

Most servers need nothing beyond the auth type, because everything is
discovered at runtime:

```json
{
  "remote-oauth-server": {
    "description": "A remote MCP server that requires OAuth",
    "url": "https://mcp.example.com/mcp",
    "transport": "http",
    "auth_type": "oauth",
    "groups": ["users"]
  }
}
```

`oauth_config` exists for the cases discovery cannot cover:

| Field | Purpose |
|-------|---------|
| `scopes` | Scopes to request. Defaults to what the resource or authorization server advertises. |
| `client_name` | Name presented at dynamic client registration. Defaults to `Atlas UI`. |
| `client_id` | A pre-registered client id, for providers that do not offer dynamic registration. Skips registration entirely. |
| `client_secret` | Only when a provider insists on a confidential client. Prefer leaving this unset; PKCE is what secures the flow. |
| `callback_port` | Deprecated and ignored. The callback is an Atlas route, not a local listener. |

### Required setting

Atlas must know the URL browsers use to reach it, so it can build the
`redirect_uri` it registers with the provider. This is **never** derived from
the inbound `Host` header, which an attacker controls:

```bash
MCP_OAUTH_REDIRECT_BASE_URL=https://atlas.example.gov   # falls back to BACKEND_PUBLIC_URL
```

The resulting callback is `<base>/api/mcp/auth/<server>/oauth/callback`. It
must be reachable by the user's browser and must be `https://` (an `http://`
loopback address is accepted for local development).

### What happens

1. The user clicks the key icon next to the server in the Tools panel, which
   navigates to `GET /api/mcp/auth/<server>/oauth/start`.
2. Atlas discovers the authorization server: the `401` challenge names an
   RFC 9728 protected-resource document, which names the authorization
   server, whose RFC 8414 metadata supplies the endpoints. A server that
   publishes no challenge is still resolved from the well-known paths.
3. If Atlas holds no client credentials for that authorization server, it
   registers itself via RFC 7591 Dynamic Client Registration, as a public
   client using PKCE. The registration is per MCP server (not per user) and is
   persisted encrypted, so it is reused across restarts.
4. The browser is redirected to the provider with `code_challenge_method=S256`
   and a single-use `state` bound to the user's Atlas session. When the
   protected-resource document names a `resource`, it is sent as an RFC 8707
   resource indicator so the issued token is audience-bound.
5. The provider returns the user to the Atlas callback, which validates the
   state, redeems the code, and stores the access and refresh tokens
   encrypted per user.
6. Later tool calls use the access token. When it expires, Atlas refreshes it
   silently from the refresh token; only if that fails is the user asked to
   authorize again.
7. Disconnecting removes the stored tokens, invalidates cached clients, and
   revokes at the provider's `revocation_endpoint` when one is advertised.

### How this differs from Globus and OIDC login

These are three separate things and are configured independently:

- **[OIDC login](./oidc-authentication.md)** makes Atlas itself an OAuth
  relying party so users can log in to Atlas. One statically configured
  provider.
- **Globus auth** is a single, pre-registered provider used for Globus
  transfer and identity, with its own fixed callback.
- **MCP OAuth** (this page) is per MCP server: an arbitrary number of
  third-party resource servers, discovered at runtime, each with its own
  authorization server, its own dynamically registered client credentials, and
  its own per-user tokens.

`auth_type: "delegated"` is a fourth, different option: Atlas exchanges the
user's existing OIDC token for a downstream credential without any browser
interaction. Use `oauth` when the MCP server has its own identity provider the
user must consent to; use `delegated` when it trusts your OIDC issuer.

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `MCP_TOKEN_ENCRYPTION_KEY` | Key for encrypting stored tokens | **Required** |
| `MCP_TOKEN_STORAGE_DIR` | Directory path for token storage file | Optional |

**Encryption Key:** `MCP_TOKEN_ENCRYPTION_KEY` must be set to a stable secret (at least 32 characters) before starting Atlas. The application refuses to start without it, because a generated ephemeral key would make every previously encrypted token unreadable after each restart. Rotating the key invalidates all stored tokens, so choose a value you can keep stable across deployments (and back it up alongside other deployment secrets).

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

The Tools panel shows authentication status for servers with `auth_type` of `api_key`, `jwt`, `bearer`, or `oauth`:

- **Green shield icon:** Authenticated successfully. Click to disconnect.
- **Yellow key icon:** Authentication required. For `oauth` servers this starts
  the browser authorization flow; for the others it opens the token modal.

After an OAuth flow the callback returns the browser with
`?mcp_auth_server=<name>` and either `mcp_auth_success=1` or
`mcp_auth_error=<code>`, which the panel surfaces as a banner before stripping
the parameters from the URL.

## Security Considerations

1. **Encryption at Rest:** All tokens encrypted using Fernet (AES-128-CBC)
2. **Per-User Isolation:** Users cannot access each other's tokens
3. **No Token Logging:** Token values are never logged (sanitized)
4. **Expiration Tracking:** Optional expiration date tracked and validated
5. **Secure Storage:** Tokens stored in dedicated secure directory
6. **CSRF Protection (OAuth):** The `state` is single-use, expires after ten
   minutes, and is bound to both the server and the user who started the flow,
   so a callback replayed in another account or aimed at another server is
   rejected
7. **No Reflected Errors (OAuth):** Provider-supplied error strings are mapped
   through an allowlist before being echoed into the redirect
8. **Transport and destination (OAuth):** Every discovered endpoint must be
   `https://`, and the protected-resource metadata URL (plus every redirect
   hop) must share the MCP endpoint's own origin, so a compromised server
   cannot downgrade the flow or aim Atlas's fetches at arbitrary hosts.
   Loopback `http://` is accepted only when the MCP server is itself on
   loopback, so a remote server cannot name `127.0.0.1`.

   **Residual risk worth knowing:** the authorization server legitimately
   lives on a different origin from the resource (that is the normal shape),
   so its URL cannot be origin-pinned. A malicious or compromised MCP server
   can therefore still cause one `GET` to
   `https://<host-it-names>/.well-known/oauth-authorization-server`. That
   request carries no Atlas credentials and its response must parse as valid
   authorization-server metadata to go any further, but it does reach the
   named host from inside your network. Only add MCP servers you trust, and
   put egress controls in front of Atlas if that request matters in your
   environment
9. **Encrypted Client Registrations:** Dynamic client registrations, including
   any issued `client_secret`, are encrypted with the same key as the tokens

## Demo Server

### api_key_demo

A demo MCP server requiring API key authentication. Demonstrates the full per-user API key flow.

**Location:** `atlas/mcp/api_key_demo/`

**Config:** `atlas/config/mcp-example-configs/mcp-api_key_demo.json`

**Valid test keys:**
- `test123` - Test user (developer role)
- `admin123` - Admin user (admin role)

**Running the demo:**
```bash
cd atlas/mcp/api_key_demo
bash run.sh
```

The server runs on port 8006 by default and validates API keys via the `X-API-Key` header.

## Files

### Backend

- `atlas/modules/mcp_tools/token_storage.py` - Encrypted token storage
- `atlas/modules/mcp_tools/mcp_oauth.py` - OAuth 2.1 discovery, DCR, token endpoint
- `atlas/modules/mcp_tools/mcp_oauth_service.py` - Flow orchestration
- `atlas/modules/mcp_tools/oauth_client_store.py` - Encrypted client registrations
- `atlas/routes/mcp_auth_routes.py` - API endpoints for token management and the OAuth routes
- `atlas/modules/mcp_tools/client.py` - MCP client with token injection
- `atlas/modules/config/config_manager.py` - auth_type configuration

### Frontend

- `frontend/src/components/TokenInputModal.jsx` - Token input modal
- `frontend/src/hooks/useServerAuthStatus.js` - Auth status hook
- `frontend/src/components/ToolsPanel.jsx` - Auth indicators in Tools panel

### Demo Server

- `atlas/mcp/api_key_demo/main.py` - API key auth demo server
- `atlas/mcp/api_key_demo/run.sh` - Startup script (prints config snippet)

## Related

- [MCP Server Configuration](./mcp-servers.md) - General MCP server configuration
