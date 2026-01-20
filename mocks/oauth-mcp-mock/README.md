# Mock OAuth 2.1 MCP Server

A mock OAuth 2.1 authorization server and MCP server for testing Atlas UI's OAuth authentication flow.

**Updated: 2025-01-19**

## Overview

This mock provides:

1. **OAuth 2.1 Authorization Server**
   - Discovery endpoint (`/.well-known/oauth-authorization-server`)
   - Authorization endpoint (`/oauth/authorize`)
   - Token endpoint (`/oauth/token`)
   - Token revocation (`/oauth/revoke`)
   - PKCE support (S256)

2. **OAuth-Protected MCP Server**
   - Mounted at `/mcp`
   - Validates Bearer tokens from the mock OAuth server
   - Test tools for verifying authentication

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server
./run.sh

# Or manually:
python main.py
```

The server runs on `http://localhost:8001` by default.

## Test Users

| Email | Password |
|-------|----------|
| test@example.com | testpass123 |
| admin@example.com | adminpass123 |

## OAuth Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/.well-known/oauth-authorization-server` | GET | OAuth server metadata discovery |
| `/oauth/authorize` | GET | Start authorization flow (shows login form) |
| `/oauth/token` | POST | Exchange code for tokens |
| `/oauth/revoke` | POST | Revoke a token |

## MCP Tools

| Tool | Description |
|------|-------------|
| `get_user_profile` | Returns the authenticated user's profile |
| `echo_message` | Echoes a message (for testing) |
| `get_secret_data` | Returns protected data (requires auth) |

## Testing the OAuth Flow

### 1. Manual Browser Test

1. Start the server: `./run.sh`
2. Open: `http://localhost:8001/oauth/authorize?response_type=code&client_id=Atlas%20UI&redirect_uri=http://localhost:8000/api/mcp/auth/test-oauth-server/oauth/callback&state=test123&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM&code_challenge_method=S256`
3. Log in with test credentials
4. Observe redirect with authorization code

### 2. Integration with Atlas UI

Add this server to your MCP config (`config/overrides/mcp.json`):

```json
{
  "test-oauth-server": {
    "description": "Test OAuth MCP Server",
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

Then:
1. Start the mock server: `cd mocks/oauth-mcp-mock && ./run.sh`
2. Start Atlas UI: `bash agent_start.sh`
3. Go to Settings > MCP Authentication
4. Click "Connect" for the test-oauth-server
5. Log in with test credentials
6. Use the server's tools

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OAUTH_MOCK_HOST` | `0.0.0.0` | Host to bind to |
| `OAUTH_MOCK_PORT` | `8001` | Port to listen on |
| `OAUTH_MOCK_BASE_URL` | `http://localhost:8001` | Base URL for OAuth endpoints |

## Security Notes

This is a **DEVELOPMENT/TESTING ONLY** mock. Do NOT use in production because:

- Credentials are hardcoded
- Tokens are stored in memory (not persistent)
- No rate limiting or security hardening
- No HTTPS enforcement

For production, use a real OAuth provider like:
- Keycloak
- Auth0
- Okta
- Azure AD
