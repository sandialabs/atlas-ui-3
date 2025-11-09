# Add Bearer Token Authentication for MCP Servers

**Date:** November 9, 2025
**Author:** Gemini
**Status:** Proposed

## Objective

This document outlines the plan to add support for bearer token authentication when connecting to MCP (Model-as-a-Component-of-a-Process) servers. This will allow Atlas UI to securely connect to MCP servers that require an `Authorization: Bearer <token>` header.

## Background

Some MCP servers are protected and require authentication. The `fastmcp` library, which is used to connect to these servers, supports passing an authentication token. We need to update our application to allow specifying this token in the `mcp.json` configuration file and use it when establishing a connection.

This change will involve modifying the configuration model, updating the client initialization logic, and providing an example in the default configuration.

## Implementation Steps

### 1. Modify the MCP Configuration Model

We need to add a field to our MCP server configuration model to hold the authentication token.

*   **File to modify:** `backend/modules/config/config_manager.py`
*   **Model to modify:** `MCPServerConfig`

Add a new optional field `auth_token` of type `str` to the `MCPServerConfig` Pydantic model.

**Before:**
```python
class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server."""
    description: Optional[str] = None
    author: Optional[str] = None
    # ... (other fields)
    transport: Optional[str] = None      # Explicit transport: "stdio", "http", "sse" - takes priority over auto-detection
    compliance_level: Optional[str] = None  # Compliance/security level (e.g., "SOC2", "HIPAA", "Public")
    require_approval: List[str] = Field(default_factory=list)
    allow_edit: List[str] = Field(default_factory=list)
```

**After:**
```python
class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server."""
    description: Optional[str] = None
    author: Optional[str] = None
    # ... (other fields)
    transport: Optional[str] = None      # Explicit transport: "stdio", "http", "sse" - takes priority over auto-detection
    auth_token: Optional[str] = None     # Bearer token for MCP server authentication
    compliance_level: Optional[str] = None  # Compliance/security level (e.g., "SOC2", "HIPAA", "Public")
    require_approval: List[str] = Field(default_factory=list)
    allow_edit: List[str] = Field(default_factory=list)
```

### 2. Update the MCP Client Initialization

Now we need to use the `auth_token` when creating the `fastmcp.Client`.

*   **File to modify:** `backend/modules/mcp_tools/client.py`
*   **Function to modify:** `_initialize_single_client`

In this function, when preparing to create a `Client` for an `http` or `sse` transport, we will check for the `auth_token` in the server's configuration. If it exists, we will pass it to the `auth` parameter of the `Client` or `Transport` constructor.

**For HTTP transport:**

**Before:**
```python
# Use HTTP transport (StreamableHttp)
logger.debug(f"Creating HTTP client for {server_name} at {url}")
client = Client(url)
```

**After:**
```python
# Use HTTP transport (StreamableHttp)
logger.debug(f"Creating HTTP client for {server_name} at {url}")
token = config.get("auth_token")
client = Client(url, auth=token)
```

**For SSE transport:**

**Before:**
```python
# Use explicit SSE transport
logger.debug(f"Creating SSE client for {server_name} at {url}")
from fastmcp.client.transports import SSETransport
transport = SSETransport(url)
client = Client(transport)
```

**After:**
```python
# Use explicit SSE transport
logger.debug(f"Creating SSE client for {server_name} at {url}")
from fastmcp.client.transports import SSETransport
token = config.get("auth_token")
# The `auth` parameter might need to be passed to the transport directly
# depending on the `fastmcp` version. Assuming it's supported on the transport.
transport = SSETransport(url, auth=token)
client = Client(transport)
```
*Note: A quick look at the `fastmcp` documentation or source would be needed to confirm if `SSETransport` accepts the `auth` parameter. If not, the `auth` parameter should be passed to the `Client` constructor instead: `client = Client(transport, auth=token)`.*

### 3. Update the Default Configuration

To make it easy for other developers to use this feature, we will add an example to the default `mcp.json` configuration file.

*   **File to modify:** `config/defaults/mcp.json`

Add the `auth_token` field to one of the existing server configurations. We'll use the `ui-demo` server as an example.

**Before:**
```json
  "ui-demo": {
    "command": ["python", "mcp/ui-demo/main.py"],
    "cwd": "backend",
    "groups": ["users"],
    "is_exclusive": false,
    "description": "Demo server showcasing custom UI modification capabilities",
    "author": "Chat UI Team",
    "short_description": "UI customization demo",
    "help_email": "support@chatui.example.com",
    "compliance_level": "Public"
  }, 
```

**After:**
```json
  "ui-demo": {
    "command": ["python", "mcp/ui-demo/main.py"],
    "cwd": "backend",
    "groups": ["users"],
    "is_exclusive": false,
    "description": "Demo server showcasing custom UI modification capabilities",
    "author": "Chat UI Team",
    "short_description": "UI customization demo",
    "help_email": "support@chatui.example.com",
    "compliance_level": "Public",
    "auth_token": null
  }, 
```
*Note: We are setting it to `null` to show the field is available. A developer can replace `null` with an actual token string in `config/overrides/mcp.json`.*

## Testing

To test this feature, you will need an MCP server that is configured to require a bearer token.

1.  Set up a local MCP server that inspects the `Authorization` header and rejects requests without a valid token.
2.  In `config/overrides/mcp.json`, configure this server and provide a valid `auth_token`.
3.  Run the Atlas UI backend and verify that it can successfully connect to the MCP server and list its tools.
4.  Remove the `auth_token` or provide an invalid one and verify that the connection fails.
5.  Add unit tests to `backend/tests/` to cover the new logic in `_initialize_single_client`. You can mock the `fastmcp.Client` and assert that it's called with the correct `auth` parameter.

## Update Documentation

To ensure users and developers are aware of this new feature, the following documents must be updated:

1.  **Admin Guide (`docs/02_admin_guide.md`)**:
    *   Add a section explaining how to configure a bearer token for an MCP server.
    *   Provide an example of the `auth_token` field in the `mcp.json` configuration.

2.  **Developer Guide (`docs/03_developer_guide.md`)**:
    *   Update the section on MCP server configuration to include the new `auth_token` field in the `MCPServerConfig` model.
    *   Briefly explain that this token is used for bearer authentication with HTTP/SSE-based MCP servers.

## API Key Authentication Example

Here's a minimal code example for API key authentication between a FastMCP client and server:

### Server Side

```python
# server.py
from fastmcp import FastMCP
from fastmcp.server.auth.providers.bearer import StaticTokenVerifier

# Create a token verifier with your API keys
verifier = StaticTokenVerifier(
    tokens={
        "my-secret-api-key-123": {
            "user_id": "user_1",
            "scopes": ["read", "write"]
        },
        "another-api-key-456": {
            "user_id": "user_2",
            "scopes": ["read"]
        }
    }
)

# Create server with auth
mcp = FastMCP("API Key Server", auth=verifier)

@mcp.tool()
def greet(name: str) -> str:
    """A protected greeting tool"""
    return f"Hello, {name}!"

if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=8000,
        path="/mcp"
    )
```

### Client Side

```python
# client.py
import asyncio
from fastmcp import Client

async def main():
    # Connect with API key in Authorization header
    async with Client(
        "http://127.0.0.1:8000/mcp",
        auth="my-secret-api-key-123"  # Your API key
    ) as client:
        # Call the protected tool
        result = await client.call_tool("greet", {"name": "World"})
        print(result)

if __name__ == "__main__":
    asyncio.run(main())
```

### How It Works

1. **Server**: The `StaticTokenVerifier` validates API keys passed in the `Authorization: Bearer <api-key>` header
2. **Client**: When you pass `auth="my-secret-api-key-123"`, FastMCP automatically adds it as a Bearer token in the Authorization header
3. The server validates the token and grants access if it matches one of the configured keys

### Alternative: Custom Header Name

If you want to use a custom header (like `X-API-Key` instead of `Authorization`):

```python
# Client with custom headers
async with Client(
    "http://127.0.0.1:8000/mcp",
    headers={"X-API-Key": "my-secret-api-key-123"}
) as client:
    result = await client.call_tool("greet", {"name": "World"})
```

For the custom header approach, you'd need to implement a custom token verifier on the server side to extract the key from the `X-API-Key` header instead of the `Authorization` header.

## Update Mock Server

To facilitate testing of the new authentication mechanism, the `mocks/mcp-http-mock` should be updated to require bearer token authentication using the `StaticTokenVerifier` approach shown above.

1.  **Modify `mocks/mcp-http-mock/main.py` (or equivalent file):**
    *   Import `FastMCP` and `StaticTokenVerifier` from `fastmcp.server.auth.providers.bearer`.
    *   Instantiate `StaticTokenVerifier` with a dictionary of valid API keys and their claims.
    *   Pass the verifier to the `FastMCP` constructor using the `auth` parameter.

**Example Implementation:**

```python
from fastmcp import FastMCP
from fastmcp.server.auth.providers.bearer import StaticTokenVerifier

# Define test API keys
verifier = StaticTokenVerifier(
    tokens={
        "test-api-key-123": {
            "user_id": "test_user",
            "scopes": ["read", "write"]
        },
        "another-test-key-456": {
            "user_id": "another_user",
            "scopes": ["read"]
        }
    }
)

# Pass the verifier to the FastMCP server
mcp = FastMCP(name="Authenticated Mock MCP", auth=verifier)

# ... (rest of the mock server implementation)
```

With this change, the mock server will only accept requests with valid API keys in the `Authorization: Bearer <api-key>` header. This will allow developers to test the `auth_token` configuration in `mcp.json` by setting it to one of the test keys like `"test-api-key-123"`.
