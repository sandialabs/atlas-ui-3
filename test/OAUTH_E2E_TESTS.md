# OAuth 2.1 / Bearer Token Authentication E2E Tests

This directory contains end-to-end tests for OAuth 2.1 Bearer token authentication between Atlas UI and MCP HTTP servers.

## Overview

The OAuth 2.1 e2e tests verify the complete authentication flow from the frontend through the backend to MCP HTTP servers that require Bearer token authentication.

## Test Files

### Python Tests (`test/oauth_e2e_test.py`)

A comprehensive Python test suite that validates:

- **Backend Configuration**: Verifies the backend config endpoint is accessible and properly configured
- **Environment Variable Resolution**: Tests that `${ENV_VAR}` patterns in `auth_token` config are resolved
- **MCP Server Authentication**: Tests HTTP/SSE MCP servers with Bearer token authentication
- **Token Validation**: Verifies servers reject unauthenticated and invalid token requests
- **Full Flow Simulation**: Simulates the complete OAuth 2.1 flow end-to-end

**Run standalone:**
```bash
cd test
python3 oauth_e2e_test.py
```

### Playwright Tests (`test_e2e/tests/oauth-authentication.spec.js`)

Browser-based tests using Playwright that verify:

- MCP server loading with authentication configured
- Tool execution through WebSocket with authenticated backends
- Frontend display of authenticated tools and servers
- End-to-end OAuth 2.1 flow through the UI

**Run standalone:**
```bash
cd test_e2e
npm test -- oauth-authentication.spec.js
```

## Test Configuration

### Environment Variables

The tests use the following environment variables (with defaults):

- `MCP_MOCK_TOKEN_1`: First test token (default: "test-api-key-123")
- `MCP_MOCK_TOKEN_2`: Second test token (default: "another-test-key-456")

These match the defaults in `mocks/mcp-http-mock/run.sh`.

### MCP HTTP Mock Server

The tests interact with the MCP HTTP mock server at `http://127.0.0.1:8005` which implements:

- Bearer token authentication using FastMCP's `StaticTokenVerifier`
- Multiple test tokens with different permission scopes
- Database simulation tools (select_users, select_orders, select_products)

**Start the mock server:**
```bash
cd mocks/mcp-http-mock
./run.sh
```

Or use agent_start.sh with the `-m` flag:
```bash
./agent_start.sh -m
```

## Running the Tests

### Via Test Runner (Recommended)

The OAuth tests are integrated into the main e2e test suite:

```bash
# Run all e2e tests (includes OAuth tests)
bash test/run_tests.sh e2e

# Or directly
bash test/e2e_tests.sh
```

### Individual Test Suites

```bash
# Python OAuth tests only
python3 test/oauth_e2e_test.py

# Playwright OAuth tests only
cd test_e2e
npm test -- oauth-authentication.spec.js
```

## OAuth 2.1 Flow

The tests verify this authentication flow:

1. **Configuration**: Backend loads MCP server config from `config/overrides/mcp.json`
2. **Token Resolution**: Backend resolves `${ENV_VAR}` patterns in `auth_token` fields
3. **Connection**: Backend connects to MCP HTTP/SSE servers with Bearer tokens
4. **Tool Discovery**: Backend discovers tools from authenticated MCP servers
5. **Tool Execution**: User requests tool execution through WebSocket
6. **Authenticated Request**: Backend makes authenticated request to MCP server with Bearer token
7. **Token Validation**: MCP server validates token using configured auth provider
8. **Response**: Authenticated response flows back through backend to frontend

## MCP Server Configuration

Example MCP server configuration with authentication:

```json
{
  "mcp-http-mock": {
    "url": "http://127.0.0.1:8005/mcp",
    "auth_token": "${MCP_MOCK_TOKEN_1}",
    "groups": ["users"],
    "description": "Authenticated database simulation server"
  }
}
```

The `auth_token` field:
- Can be a literal string: `"auth_token": "my-secret-token"`
- Can reference an environment variable: `"auth_token": "${MY_TOKEN_VAR}"`
- Is automatically included as `Authorization: Bearer <token>` header by FastMCP client

## Test Architecture

```
┌─────────────┐     WebSocket      ┌─────────────┐
│   Frontend  │ ◄─────────────────► │   Backend   │
│  (Browser)  │                     │  (FastAPI)  │
└─────────────┘                     └──────┬──────┘
                                           │
                                           │ HTTP + Bearer Token
                                           │
                                    ┌──────▼──────┐
                                    │ MCP Server  │
                                    │ (FastMCP)   │
                                    │ + StaticTokenVerifier
                                    └─────────────┘
```

## Security Notes

The OAuth 2.1 tests use `StaticTokenVerifier` which is:
- **Only for development and testing**
- **Never for production use**
- Production should use proper JWT/OAuth providers

For production OAuth 2.1 implementations, replace `StaticTokenVerifier` with:
- JWT-based authentication
- OAuth 2.1 compliant token servers
- Proper token validation and refresh mechanisms

## Troubleshooting

### Tests Skip MCP Server Tests

If you see "MCP HTTP server not running - skipping test", ensure:

1. MCP mock server is started: `cd mocks/mcp-http-mock && ./run.sh`
2. Server is listening on port 8005: `curl http://127.0.0.1:8005/mcp`
3. Tokens are configured: check `MCP_MOCK_TOKEN_1` and `MCP_MOCK_TOKEN_2`

### Authentication Failures

If authentication tests fail:

1. Verify environment variables are set correctly
2. Check backend logs for token resolution errors
3. Ensure MCP server is configured with matching tokens
4. Review `backend/modules/mcp_tools/client.py` for auth_token handling

### Backend Connection Issues

If backend isn't accessible:

1. Ensure backend is running on port 8000
2. Check that frontend is built: `cd frontend && npm run build`
3. Verify `X-User-Email` header is being sent in tests
4. Check backend logs: `tail -f logs/app.jsonl`

## Related Documentation

- [Authentication & Authorization](../docs/admin/authentication.md)
- [MCP HTTP Mock Server](../mocks/mcp-http-mock/README.md)
- [E2E Testing](../test/README.md)
- [MCP Configuration](../docs/features/mcp-servers.md)
