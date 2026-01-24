# JWT Demo MCP Server

Last updated: 2025-01-23

Demonstrates MCP server authentication with JWT/bearer tokens.

## Quick Start

**Terminal 1 - Start this server:**
```bash
./run.sh
```

**Terminal 2 - Start the main app:**
```bash
bash agent_start.sh
```

## Testing Protocol

1. **Add to mcp.json** - Copy the config shown when run.sh starts into `config/overrides/mcp.json`

2. **Open browser** - Go to http://localhost:8000

3. **Open Tools panel** - Click the tools icon in the sidebar

4. **Find jwt_demo** - Should show a yellow key icon (unauthenticated)

5. **Click yellow key** - Token input modal appears

6. **Enter any token** - Use `test-token-123` or any string

7. **Verify green key** - Icon turns green after saving

8. **Test a tool** - Ask the LLM to use `whoami` from jwt_demo

9. **Disconnect** - Click green key, confirm disconnect, key turns yellow

## API Testing

```bash
# Check auth status
curl -H "X-User-Email: test@test.com" http://localhost:8000/api/mcp/auth/status | jq

# Upload token
curl -X POST -H "X-User-Email: test@test.com" \
  -H "Content-Type: application/json" \
  -d '{"token": "my-test-jwt"}' \
  http://localhost:8000/api/mcp/auth/jwt_demo/token | jq

# Remove token
curl -X DELETE -H "X-User-Email: test@test.com" \
  http://localhost:8000/api/mcp/auth/jwt_demo/token | jq
```
