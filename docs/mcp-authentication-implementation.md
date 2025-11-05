# MCP Authentication Implementation Summary

## Issue
Allow setting secret keys in the MCP config file to authenticate the Atlas UI 3 instance to MCP servers.

## Solution Overview
Implemented API key and custom header authentication for HTTP/SSE MCP servers with environment variable expansion support.

## Changes Made

### 1. Configuration Model Updates (`backend/modules/config/config_manager.py`)

#### Added Fields to MCPServerConfig
- `api_key: Optional[str]` - API key for Bearer token authentication
- `extra_headers: Optional[Dict[str, str]]` - Custom HTTP headers for authentication

#### Environment Variable Expansion
- Added `_expand_mcp_env_vars()` method to expand `${ENV_VAR}` syntax
- Expands variables in both `api_key` and `extra_headers` values
- Logs warnings for undefined environment variables
- Applied to both main MCP config and RAG MCP config

### 2. Client Implementation Updates (`backend/modules/mcp_tools/client.py`)

#### Authentication Header Building
- Constructs headers dictionary from `api_key` and `extra_headers` config
- Converts `api_key` to `Authorization: Bearer <token>` header format
- Merges `extra_headers` into the headers dictionary

#### Transport Initialization
- Passes headers to `SSETransport(url, headers=headers)` for SSE servers
- Passes headers to `Client(url, headers=headers)` for HTTP servers
- Includes defensive try-except blocks for API compatibility
- Logs warnings if FastMCP version doesn't support headers parameter

### 3. Documentation

#### docs/mcp-authentication.md (New)
Comprehensive guide covering:
- API key authentication
- Custom header authentication  
- Environment variable expansion
- Security best practices
- Example configurations
- Troubleshooting guide
- Server-side implementation notes

#### config/mcp-authentication-examples.json (New)
Example configurations demonstrating:
- HTTP server with API key
- SSE server with custom headers
- Combined authentication methods
- Public server (no auth)

#### README.md
- Added feature bullet for MCP authentication
- Links to detailed documentation

### 4. Tests

#### backend/tests/test_mcp_authentication_config.py (New)
Created 11 comprehensive tests:

**TestMCPAuthenticationConfig** (4 tests):
- Verify auth fields exist on MCPServerConfig
- Test api_key configuration
- Test extra_headers configuration
- Test combined authentication

**TestMCPEnvVarExpansion** (7 tests):
- Test api_key environment variable expansion
- Test extra_headers environment variable expansion
- Test undefined environment variables (warnings logged)
- Test mixed defined/undefined variables
- Test multiple servers
- Test servers without auth fields

All tests passing (11/11) ✅

### 5. Configuration Examples

Added commented examples in config file showing:
- Basic API key authentication
- Custom header authentication
- Multi-factor authentication (API key + headers)
- Environment variable usage

## Usage Example

```json
{
  "my-secure-server": {
    "url": "https://mcp.example.com",
    "transport": "http",
    "groups": ["users"],
    "api_key": "${MCP_API_KEY}",
    "extra_headers": {
      "X-Client-ID": "atlas-ui-3",
      "X-Tenant-ID": "${TENANT_ID}"
    }
  }
}
```

```bash
# .env file
MCP_API_KEY=secret-key-123
TENANT_ID=tenant-456
```

Resulting HTTP headers:
```
Authorization: Bearer secret-key-123
X-Client-ID: atlas-ui-3
X-Tenant-ID: tenant-456
```

## Security Considerations

1. **Environment Variables**: Secrets stored in environment variables, not committed to git
2. **HTTPS Required**: API keys should only be used with HTTPS endpoints
3. **Bearer Token Format**: Standard OAuth 2.0 Bearer token format for API keys
4. **Warnings**: Undefined environment variables trigger warnings in logs
5. **Backward Compatible**: Existing configs without auth continue to work

## Testing

- ✅ Unit tests: 11 new tests, all passing
- ✅ Existing tests: 15 config manager tests still passing
- ✅ Python syntax: All files compile without errors
- ✅ Import consistency: Matches project conventions
- ⏳ Integration test: Requires actual HTTP/SSE MCP server (pending deployment)

## Files Modified

1. `backend/modules/config/config_manager.py` - Added auth fields and env var expansion
2. `backend/modules/mcp_tools/client.py` - Added header passing to FastMCP clients
3. `backend/tests/test_mcp_authentication_config.py` - New comprehensive tests
4. `docs/mcp-authentication.md` - New detailed documentation
5. `config/mcp-authentication-examples.json` - New example configurations
6. `config/defaults/mcp.json` - Minor formatting cleanup
7. `README.md` - Added feature description with link to docs

## Backward Compatibility

- ✅ Existing MCP server configs continue to work without changes
- ✅ New fields are optional (default to None)
- ✅ STDIO servers unaffected (headers only for HTTP/SSE)
- ✅ Defensive error handling if FastMCP version lacks headers support

## Future Enhancements

Potential improvements for future iterations:
1. Support for other authentication schemes (API key in query params, etc.)
2. Mutual TLS authentication
3. OAuth 2.0 token refresh flows
4. Per-request authentication token rotation
5. Authentication middleware hooks
