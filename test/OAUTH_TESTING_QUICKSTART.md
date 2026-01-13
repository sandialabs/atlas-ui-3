# Testing OAuth 2.1 Authentication - Quick Start

This guide shows how to quickly test the OAuth 2.1 Bearer token authentication with the MCP HTTP mock server.

## Prerequisites

1. Python 3.8+
2. Node.js 18+
3. Backend dependencies installed
4. Frontend built

## Quick Test (Without Mock Server)

The OAuth e2e tests can run without the MCP HTTP mock server. Tests that require the server will be skipped gracefully.

```bash
# Run OAuth tests only
python3 test/oauth_e2e_test.py

# Run full e2e suite (includes OAuth tests)
bash test/e2e_tests.sh
```

Expected output:
```
OAuth 2.1 / Bearer Token Authentication E2E Test Suite
================================================================================
Waiting for server at http://127.0.0.1:8000...
Server at http://127.0.0.1:8000 is ready

================================================================================
TEST: Backend Configuration Endpoint
================================================================================
User: test@test.com
Models available: 3
Tools configured: 7
Backend config endpoint test passed
...
```

## Full Test (With Mock Server)

To test the complete OAuth 2.1 flow including actual MCP server authentication:

### Step 1: Start Backend

```bash
# Terminal 1: Start backend
cd backend
python main.py
```

Or use the agent_start script:
```bash
./agent_start.sh -b  # backend only
```

### Step 2: Start MCP HTTP Mock Server

```bash
# Terminal 2: Start MCP mock server
cd mocks/mcp-http-mock
./run.sh
```

Or use agent_start with mock flag:
```bash
./agent_start.sh -b -m  # backend + mock server
```

The mock server will:
- Listen on http://127.0.0.1:8005
- Accept Bearer tokens: `test-api-key-123` and `another-test-key-456`
- Provide database simulation tools

### Step 3: Configure Backend to Use Mock Server

Add to `config/overrides/mcp.json`:

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

### Step 4: Set Environment Variables

```bash
export MCP_MOCK_TOKEN_1="test-api-key-123"
export MCP_MOCK_TOKEN_2="another-test-key-456"
```

Or add to `.env`:
```
MCP_MOCK_TOKEN_1=test-api-key-123
MCP_MOCK_TOKEN_2=another-test-key-456
```

### Step 5: Run Tests

```bash
# Terminal 3: Run OAuth tests
python3 test/oauth_e2e_test.py
```

Expected output with mock server:
```
MCP server at http://127.0.0.1:8005 is ready

================================================================================
TEST: MCP HTTP Server Authentication Requirement
================================================================================
MCP server correctly rejects unauthenticated requests (401)

================================================================================
TEST: MCP HTTP Server with Valid Authentication
================================================================================
MCP server accepted valid Bearer token
Response: {"jsonrpc": "2.0", "result": {...}}...

================================================================================
TEST: MCP HTTP Server with Invalid Authentication
================================================================================
MCP server correctly rejects invalid token (status 401)
```

## Playwright Tests

The Playwright tests verify the OAuth flow through the browser UI.

### Run Playwright Tests

```bash
# Ensure backend is running on port 8000
cd test_e2e
npm test -- oauth-authentication.spec.js
```

Or run with UI:
```bash
cd test_e2e
npm run test:ui -- oauth-authentication.spec.js
```

## Test Scenarios

### Scenario 1: Valid Token Authentication

1. Backend loads MCP config with `auth_token: "${MCP_MOCK_TOKEN_1}"`
2. Backend resolves environment variable to actual token
3. Backend connects to MCP server with Bearer token
4. MCP server validates token
5. Tools are discovered and available in UI

**Test:** `test_mcp_http_server_with_valid_auth()`

### Scenario 2: Invalid Token Rejection

1. Backend attempts connection with invalid token
2. MCP server rejects with 401/403
3. Server is marked as failed
4. Tools are not available

**Test:** `test_mcp_http_server_with_invalid_auth()`

### Scenario 3: Environment Variable Resolution

1. Config has `auth_token: "${MY_TOKEN_VAR}"`
2. Backend resolves `${MY_TOKEN_VAR}` to environment value
3. Resolved token is used in requests

**Test:** `test_environment_variable_resolution()`

### Scenario 4: Full Stack Integration

1. User opens chat UI
2. Clicks Toggle Tools
3. Sees tools from authenticated MCP servers
4. Requests tool execution
5. Backend makes authenticated request to MCP server
6. Result streams back to frontend

**Test:** Playwright `test_oauth_token_flow_simulation()`

## Troubleshooting

### "MCP HTTP server not running - skipping test"

The mock server isn't running. This is OK - those tests will be skipped.

To run full tests, start the mock server:
```bash
cd mocks/mcp-http-mock
./run.sh
```

### "Backend server not available - aborting tests"

Backend isn't running on port 8000. Start it:
```bash
cd backend
python main.py
```

### "Authentication failed: 401 Unauthorized"

Token mismatch. Verify:
1. Environment variables are set: `echo $MCP_MOCK_TOKEN_1`
2. MCP server is using same tokens (check `mocks/mcp-http-mock/run.sh`)
3. Config has correct token reference: `"auth_token": "${MCP_MOCK_TOKEN_1}"`

### Backend logs show "Failed to resolve auth_token"

Environment variable not found. Set it:
```bash
export MCP_MOCK_TOKEN_1="test-api-key-123"
```

Or check the variable name in config matches the environment variable.

## Manual Testing

You can also test the OAuth flow manually:

### Test Authentication with curl

```bash
# Without authentication (should fail)
curl -X POST http://127.0.0.1:8005/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{},"id":1}'

# With valid token (should succeed)
curl -X POST http://127.0.0.1:8005/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-api-key-123" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{},"id":1}'

# With invalid token (should fail)
curl -X POST http://127.0.0.1:8005/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer invalid-token" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{},"id":1}'
```

## Next Steps

- Review test implementation: `test/oauth_e2e_test.py`
- See full documentation: `test/OAUTH_E2E_TESTS.md`
- Check MCP mock server: `mocks/mcp-http-mock/README.md`
- Review authentication docs: `docs/admin/authentication.md`
