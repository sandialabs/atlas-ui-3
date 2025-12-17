# OAuth 2.1 and JWT Authentication for MCP Servers

This document provides a quick reference for the OAuth 2.1 and JWT authentication features added to Atlas UI 3.

## Quick Start

### Using OAuth 2.1

1. **Configure MCP server with OAuth** in `config/overrides/mcp.json`:

```json
{
  "my-oauth-server": {
    "url": "https://fastmcp.cloud/mcp",
    "transport": "http",
    "groups": ["users"],
    "oauth_config": {
      "enabled": true,
      "scopes": "read write",
      "token_storage_path": "~/.atlas-ui-3/oauth-tokens/my-oauth-server"
    }
  }
}
```

2. **Set encryption key** (optional, auto-generated if not provided):

```bash
export OAUTH_STORAGE_ENCRYPTION_KEY="your-base64-fernet-key"
```

3. **Start Atlas UI** - OAuth flow will initiate when connecting to the server

### Using Manual JWT Upload

1. **Configure MCP server** (standard HTTP config):

```json
{
  "my-server": {
    "url": "https://api.example.com/mcp",
    "transport": "http",
    "groups": ["users"]
  }
}
```

2. **Upload JWT via API**:

```bash
curl -X POST "http://localhost:8000/admin/mcp/my-server/jwt" \
  -H "Content-Type: application/json" \
  -H "X-User-Email: admin@example.com" \
  -d '{"jwt_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}'
```

3. **Set encryption key** (optional):

```bash
export JWT_STORAGE_ENCRYPTION_KEY="your-base64-fernet-key"
```

## Authentication Priority

When multiple authentication methods are configured, Atlas UI uses this priority:

1. **OAuth** (if `oauth_config.enabled` is `true`)
2. **Manual JWT** (if uploaded via admin API)
3. **Bearer Token** (if `auth_token` field is set)
4. **None** (no authentication)

## Files Modified/Created

### Backend
- `backend/modules/config/config_manager.py` - Added OAuth configuration model
- `backend/modules/mcp_tools/jwt_storage.py` - **NEW** - Encrypted JWT storage
- `backend/modules/mcp_tools/client.py` - OAuth integration
- `backend/routes/admin_routes.py` - JWT management endpoints

### Frontend
- `frontend/src/components/admin/MCPConfigurationCard.jsx` - JWT management UI

### Documentation
- `docs/admin/mcp-oauth.md` - **NEW** - Complete OAuth guide
- `docs/admin/mcp-servers.md` - Updated with auth methods
- `config/mcp-example-configs/mcp-oauth-examples.json` - **NEW** - Examples

### Tests
- `backend/tests/modules/mcp_tools/test_jwt_storage.py` - **NEW**
- `backend/tests/modules/mcp_tools/test_oauth.py` - **NEW**
- `backend/tests/test_jwt_routes.py` - **NEW**

### Configuration
- `requirements.txt` - Updated FastMCP to 2.6.0+, added key-value library
- `CHANGELOG.md` - Feature summary

## API Endpoints

### JWT Management

- `POST /admin/mcp/{server_name}/jwt` - Upload JWT
- `GET /admin/mcp/{server_name}/jwt` - Check JWT status
- `DELETE /admin/mcp/{server_name}/jwt` - Delete JWT
- `GET /admin/mcp/jwt/list` - List all servers with JWTs

All endpoints require admin authentication.

## Security Features

1. **Encryption**: All tokens (OAuth and JWT) are encrypted with Fernet
2. **File Permissions**: Storage files have `0600` permissions (owner read/write only)
3. **Encryption Keys**: Auto-generated with secure storage or via environment variables
4. **No Secrets in Config**: Use environment variables for sensitive data
5. **Separate Storage**: OAuth and JWT tokens stored in different directories

## Storage Locations

- OAuth tokens: `~/.atlas-ui-3/oauth-tokens/{server_name}/` (configurable)
- JWT tokens: `~/.atlas-ui-3/jwt-storage/` (fixed location)
- Encryption keys: `.encryption_key` file in each storage directory (if auto-generated)

## Environment Variables

- `OAUTH_STORAGE_ENCRYPTION_KEY` - Fernet key for OAuth token storage
- `JWT_STORAGE_ENCRYPTION_KEY` - Fernet key for JWT storage

Generate keys with:
```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

## Troubleshooting

### OAuth flow doesn't start
- Check MCP server URL is correct and accessible
- Verify OAuth server supports dynamic client registration
- Check logs for OAuth discovery errors

### JWT upload fails
- Verify user is in admin group
- Check JWT format (should start with `eyJ`)
- Verify server exists in MCP configuration

### Authentication still fails after setup
- Check requested scopes match server requirements
- Verify tokens haven't expired
- Check MCP server logs for authentication errors
- Try manual re-authentication (delete tokens and reconnect)

## Documentation

Full documentation: [docs/admin/mcp-oauth.md](docs/admin/mcp-oauth.md)

## Testing

Run tests:
```bash
# JWT storage tests
pytest backend/tests/modules/mcp_tools/test_jwt_storage.py -v

# OAuth configuration tests
pytest backend/tests/modules/mcp_tools/test_oauth.py -v

# JWT API tests
pytest backend/tests/test_jwt_routes.py -v
```

## References

- [FastMCP OAuth Documentation](https://gofastmcp.com/clients/auth/oauth)
- [OAuth 2.1 Specification](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1)
- [MCP Specification](https://modelcontextprotocol.io/)
