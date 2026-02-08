# API Key Demo MCP Server

Last updated: 2026-01-25

This MCP server demonstrates per-user API key authentication. It validates the `X-API-Key` header on all tool calls using FastMCP middleware.

## Running the Server

```bash
# From this directory
./run.sh

# Or with custom port
./run.sh 9000

# Or directly with Python
python main.py
```

## Valid Test Keys

For demo purposes, these API keys are accepted:
- `test123` (developer)
- `admin123` (admin)
- `demo-api-key-12345` (viewer)

## Configuration in Atlas

Add to `config/mcp.json`:

```json
{
  "api_key_demo": {
    "url": "http://127.0.0.1:8006/mcp",
    "transport": "http",
    "groups": ["users"],
    "description": "API key authentication demo",
    "auth_type": "api_key",
    "auth_header": "X-API-Key",
    "auth_prompt": "Enter your API key for the demo server"
  }
}
```

## How It Works

1. **Server Side**: The `ApiKeyAuthMiddleware` class intercepts all tool calls and validates the `X-API-Key` header against a set of valid keys.

2. **Client Side**: Atlas UI detects `auth_type: "api_key"` in the config and prompts users to enter their API key via the TokenInputModal.

3. **Storage**: User API keys are stored encrypted in `config/secure/mcp_tokens.enc` per-user per-server.

4. **Injection**: When calling tools, Atlas creates a per-user MCP client with the API key injected via `StreamableHttpTransport(headers={"X-API-Key": key})`.

## Available Tools

- `echo(message)` - Echo back a message
- `add_numbers(a, b)` - Add two numbers
- `get_user_data()` - Get sample protected data
- `list_valid_keys()` - List valid demo keys (for testing)

## Testing with FastMCP Client

```python
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

transport = StreamableHttpTransport(
    "http://localhost:8006/mcp",
    headers={"X-API-Key": "demo-api-key-12345"}
)

async with Client(transport=transport) as client:
    result = await client.call_tool("echo", {"message": "Hello!"})
    print(result)
```
