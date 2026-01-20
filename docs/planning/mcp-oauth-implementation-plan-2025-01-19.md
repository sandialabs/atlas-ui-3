# MCP OAuth 2.1 Authentication Implementation Plan

**Created:** 2025-01-19
**Issue:** #96
**Branch:** `feature/mcp-oauth-authentication`

## Overview

Enable Atlas UI to support OAuth 2.1 authentication for MCP servers, allowing users to authenticate with MCP servers that require OAuth. This is a **per-user** authentication mechanism - each user authenticates themselves with MCP servers, and their tokens are stored separately.

## Key Concepts

### Authentication Flow (Per-User)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         User Authentication Flow                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. User sees MCP server requires authentication                         │
│                        │                                                 │
│                        ▼                                                 │
│  2. User clicks "Connect" / "Authenticate"                               │
│                        │                                                 │
│                        ▼                                                 │
│  3. Browser opens OAuth authorization URL                                │
│     (with PKCE code challenge)                                           │
│                        │                                                 │
│                        ▼                                                 │
│  4. User logs in to OAuth provider (e.g., Keycloak)                      │
│     and grants consent                                                   │
│                        │                                                 │
│                        ▼                                                 │
│  5. OAuth provider redirects to Atlas callback                           │
│     with authorization code                                              │
│                        │                                                 │
│                        ▼                                                 │
│  6. Atlas exchanges code for tokens (access + refresh)                   │
│                        │                                                 │
│                        ▼                                                 │
│  7. Tokens stored encrypted, keyed by (user_email, server_name)          │
│                        │                                                 │
│                        ▼                                                 │
│  8. Future MCP calls for this user include the token                     │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Authentication Types

| Type | Description | Use Case |
|------|-------------|----------|
| `none` | No authentication required | Public MCP servers |
| `bearer` | Static bearer token from config | Server-to-server auth with shared token |
| `oauth` | OAuth 2.1 per-user flow | User authenticates with their identity |
| `jwt` | User uploads their own JWT | Manual token provision |

### Token Storage Architecture

```
Token Key: (user_email, server_name)

Storage: Encrypted on disk using Fernet (AES-128-CBC)
- Encryption key from MCP_TOKEN_ENCRYPTION_KEY env var
- Tokens persist across restarts
- Each user's tokens isolated

Example storage structure:
{
  "user@example.com:github-mcp": {
    "token_type": "oauth_access",
    "token_value": "encrypted...",
    "expires_at": 1705678900,
    "refresh_token": "encrypted...",
    "scopes": "read write"
  }
}
```

## Implementation Components

### 1. Backend: Token Storage (Per-User)

**File:** `backend/modules/mcp_tools/token_storage.py`

- Store tokens keyed by `(user_email, server_name)`
- Encrypted at rest using Fernet
- Support token refresh
- Track expiration

### 2. Backend: User Token Routes

**File:** `backend/routes/mcp_auth_routes.py` (new)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/mcp/auth/status` | GET | List servers and user's auth status |
| `/api/mcp/auth/{server}/token` | POST | Upload JWT for a server |
| `/api/mcp/auth/{server}/token` | DELETE | Remove token for a server |
| `/api/mcp/auth/{server}/oauth/start` | GET | Start OAuth flow (returns auth URL) |
| `/api/mcp/auth/{server}/oauth/callback` | GET | OAuth callback handler |

### 3. Backend: MCP Client Enhancement

**File:** `backend/modules/mcp_tools/client.py`

- Check user's token storage before MCP calls
- Inject user's token into `auth` parameter
- Handle token refresh automatically
- Support OAuth helper from FastMCP

### 4. Frontend: Token Management UI

**Location:** Settings or MCP Server panel

- Show authentication status per server
- "Connect" button for OAuth servers
- JWT upload interface
- "Disconnect" to remove tokens

### 5. Configuration Schema

**File:** `backend/modules/config/config_manager.py`

```python
class MCPServerConfig:
    auth_type: str = "none"  # "none", "bearer", "oauth", "jwt"
    auth_token: Optional[str] = None  # For bearer type (admin-configured)
    oauth_config: Optional[OAuthConfig] = None  # OAuth settings
```

## Testing Strategy

### Unit Tests

- Token storage encryption/decryption
- Token expiration logic
- Per-user token isolation

### Integration Tests

**Mock MCP Server:** `mocks/oauth-mcp-server-mock/`

- FastMCP server with OAuth protection
- Validates tokens against mock provider
- Returns tools only when authenticated

### Mock OAuth Server (Development)

A simple mock OAuth server is provided for development testing:

**Location:** `mocks/oauth-mcp-mock/`

**Setup:**
```bash
cd mocks/oauth-mcp-mock
pip install -r requirements.txt
./run.sh
```

**Test Users:**
- `test@example.com` / `testpass123`
- `admin@example.com` / `adminpass123`

### End-to-End Tests with Keycloak (Production-like)

For more realistic testing, Keycloak can be used:

**Setup:** `docker-compose.oauth-test.yml`

```yaml
services:
  keycloak:
    image: quay.io/keycloak/keycloak:latest
    environment:
      KEYCLOAK_ADMIN: admin
      KEYCLOAK_ADMIN_PASSWORD: admin
    ports:
      - "8080:8080"
    command: start-dev
```

**Test Scenarios:**

1. User initiates OAuth flow
2. User authenticates with OAuth provider
3. Callback exchanges code for tokens
4. Tokens stored and used for MCP calls
5. Token refresh when expired
6. Token revocation

## Implementation Order

1. **Phase 1: Token Storage** (current)
   - [x] Create encrypted token storage module
   - [x] Extend MCPServerConfig with auth_type
   - [ ] Update storage to be per-user keyed

2. **Phase 2: User Routes**
   - [ ] Create mcp_auth_routes.py
   - [ ] JWT upload endpoint
   - [ ] OAuth start/callback endpoints
   - [ ] Token status endpoint

3. **Phase 3: MCP Client Integration**
   - [ ] Modify client to check user token storage
   - [ ] Inject tokens into MCP calls
   - [ ] Handle token refresh

4. **Phase 4: Frontend UI**
   - [ ] Auth status display component
   - [ ] OAuth connect flow
   - [ ] JWT upload interface

5. **Phase 5: Testing**
   - [ ] Create mock OAuth MCP server
   - [ ] Set up Keycloak test container
   - [ ] Write E2E tests

## Security Considerations

1. **Token Encryption:** All tokens encrypted at rest with Fernet
2. **Per-User Isolation:** Users cannot access each other's tokens
3. **PKCE Required:** OAuth flow uses PKCE to prevent interception
4. **Refresh Token Rotation:** Support rotating refresh tokens
5. **Token Scope Validation:** Validate scopes match server requirements
6. **No Logging Tokens:** Token values never logged (sanitized)

## Configuration Example

```json
{
  "github-mcp": {
    "description": "GitHub MCP Server",
    "url": "https://mcp.github.com/api",
    "transport": "http",
    "auth_type": "oauth",
    "oauth_config": {
      "scopes": ["repo", "user"],
      "client_name": "Atlas UI"
    }
  },
  "internal-api": {
    "description": "Internal API Server",
    "url": "https://internal.example.com/mcp",
    "transport": "sse",
    "auth_type": "jwt"
  }
}
```

## Open Questions

1. Should we support multiple OAuth providers per server?
2. How to handle OAuth for stdio servers? (N/A - OAuth is HTTP-only)
3. Token migration if encryption key changes?

## References

- [FastMCP OAuth Documentation](https://gofastmcp.com/clients/auth/oauth)
- [OAuth 2.1 Specification](https://oauth.net/2.1/)
- [MCP Authentication Spec](https://spec.modelcontextprotocol.io/specification/security/)
- [Keycloak Documentation](https://www.keycloak.org/documentation)
