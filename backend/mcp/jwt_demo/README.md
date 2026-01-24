# JWT Demo MCP Server

Last updated: 2025-01-23

Demonstrates per-user token authentication using FastMCP's `get_access_token()`.

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

2. **Restart the main app** or refresh browser

3. **Open Tools panel** - Find jwt_demo with yellow key icon

4. **Click yellow key** - Enter any token (e.g., `my-secret-token`)

5. **Ask the LLM**: "Use the whoami tool from jwt_demo"

6. **Verify response** - Should show your token was received:
   ```json
   {
     "authenticated": true,
     "token_preview": "my-secret-token",
     "token_length": 15
   }
   ```

If you see `"authenticated": false`, the token isn't being sent correctly.

## API Testing

```bash
# Check auth status
curl -H "X-User-Email: test@test.com" http://localhost:8000/api/mcp/auth/status | jq

# Upload token
curl -X POST -H "X-User-Email: test@test.com" \
  -H "Content-Type: application/json" \
  -d '{"token": "my-test-token"}' \
  http://localhost:8000/api/mcp/auth/jwt_demo/token | jq
```
