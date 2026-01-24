# WebSocket Authentication Testing

Last updated: 2026-01-23

This guide documents how to test WebSocket authentication behavior, particularly for verifying security in reverse proxy deployments.

## Prerequisites

Install wscat for WebSocket testing:

```bash
npm install -g wscat
```

## Testing Sequence

### 1. Run Automated Tests

First, run the full test suite to verify the authentication logic:

```bash
./test/run_tests.sh all
```

Or run only the WebSocket authentication tests:

```bash
cd backend && python -m pytest tests/test_websocket_auth_header.py -v
```

Expected test cases:
- `test_websocket_uses_x_user_email_header` - Header auth works
- `test_websocket_rejects_unauthenticated_in_production` - No auth rejected in production
- `test_websocket_rejects_query_param_in_production` - Query param rejected in production
- `test_websocket_fallback_to_query_param_debug_mode` - Query param works in debug
- `test_websocket_fallback_to_test_user_debug_mode` - Test user fallback works in debug

### 2. Manual Testing with wscat

#### Test with Auth Header (Simulating Reverse Proxy)

This simulates how a properly configured reverse proxy would inject the user header:

```bash
wscat -c ws://localhost:8000/ws -H "X-User-Email: test@example.com"
```

**Expected result:** Connection succeeds. You should see the WebSocket prompt (`>`).

Type a test message to verify:
```json
{"type": "attach_file", "s3_key": "test.txt"}
```

Press `Ctrl+C` to disconnect.

#### Test Query Parameter (Should Fail in Production)

```bash
wscat -c "ws://localhost:8000/ws?user=test@example.com"
```

**Expected result in production mode (`DEBUG_MODE=false`):**
- HTTP 403 Forbidden (from AuthMiddleware), or
- WebSocket close code 1008 with "Authentication required" message

**Expected result in debug mode (`DEBUG_MODE=true`):**
- Connection succeeds (query param fallback allowed)

#### Test Without Any Authentication

```bash
wscat -c ws://localhost:8000/ws
```

**Expected result in production mode:** Connection rejected (403 or 1008)

**Expected result in debug mode:** Connection succeeds (test user fallback)

### 3. Verify Frontend Behavior

1. Set `DEBUG_MODE=false` in `.env`
2. Start the application: `bash agent_start.sh`
3. Open browser to http://localhost:8000
4. Verify:
   - User shown as "Unauthenticated"
   - App name shows "Chat UI (Unauthenticated)"
   - No LLM models available in dropdown
   - Connection status shows authentication error

### 4. Check Startup Warnings

In production mode, check the backend logs for security warnings:

```bash
# Look for warnings about proxy secret configuration
grep -i "SECURITY WARNING" logs/app.log
```

Expected warnings if proxy secret not configured:
- "Proxy secret validation is DISABLED in production"
- "Proxy secret is ENABLED but PROXY_SECRET is not set"

## Quick Reference

| Test Case | Command | Production Result | Debug Result |
|-----------|---------|-------------------|--------------|
| With header | `wscat -c ws://localhost:8000/ws -H "X-User-Email: user@example.com"` | Connects | Connects |
| Query param only | `wscat -c "ws://localhost:8000/ws?user=user@example.com"` | Rejected | Connects |
| No auth | `wscat -c ws://localhost:8000/ws` | Rejected | Connects (test user) |

## Troubleshooting

**Getting 403 instead of WebSocket 1008:**
The HTTP AuthMiddleware rejects the request before it reaches the WebSocket endpoint. This is correct behavior - the request never reaches the WebSocket layer.

**wscat not found:**
Install globally with `npm install -g wscat` or use `npx wscat`.

**Connection hangs:**
Ensure the backend is running (`bash agent_start.sh`) and check that port 8000 is accessible.

## Related Documentation

- [Authentication & Authorization](authentication.md) - Full authentication configuration guide
- [Troubleshooting](../troubleshooting.md) - General troubleshooting guide
