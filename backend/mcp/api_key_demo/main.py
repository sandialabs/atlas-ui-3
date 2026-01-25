"""
MCP Server with API Key Authentication Demo

This server validates X-API-Key header on all tool calls using middleware.
Demonstrates per-user API key authentication for Atlas UI.

Run with: python main.py
Or configure in mcp.json with auth_type: "api_key"
"""

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.dependencies import get_http_headers
from fastmcp.exceptions import ToolError

# Valid API keys for demo purposes
# In production, validate against a database or external service
VALID_KEYS = {
    "test123",
    "demo-api-key-12345",
    "user-specific-key-abcdef",
}


class ApiKeyAuthMiddleware(Middleware):
    """Middleware that validates API key from X-API-Key header."""

    def __init__(self, valid_keys: set[str], header_name: str = "x-api-key"):
        self.valid_keys = valid_keys
        self.header_name = header_name.lower()

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        """Validate API key before each tool call."""
        headers = get_http_headers() or {}
        api_key = headers.get(self.header_name)

        if not api_key:
            raise ToolError(
                f"Authentication required: Missing {self.header_name} header. "
                "Please provide your API key in Atlas UI settings."
            )

        if api_key not in self.valid_keys:
            raise ToolError(
                f"Authentication failed: Invalid API key. "
                "Please check your API key and try again."
            )

        # Continue to the tool
        return await call_next(context)


mcp = FastMCP("ApiKeyDemoServer")

# Add authentication middleware
mcp.add_middleware(ApiKeyAuthMiddleware(valid_keys=VALID_KEYS))


@mcp.tool
def echo(message: str) -> str:
    """Echo back a message. Requires valid API key."""
    return f"Echo: {message}"


@mcp.tool
def add_numbers(a: int, b: int) -> int:
    """Add two numbers together. Requires valid API key."""
    return a + b


@mcp.tool
def get_user_data() -> dict:
    """Get sample user data. Requires valid API key.

    This demonstrates that the API key was validated successfully
    and the user can access protected resources.
    """
    return {
        "server_name": "ApiKeyDemoServer",
        "authenticated": True,
        "message": "You have successfully authenticated with your API key!",
        "sample_data": {
            "items": ["item1", "item2", "item3"],
            "count": 3,
        },
    }


@mcp.tool
def list_valid_keys() -> dict:
    """List the valid demo API keys (for testing purposes only).

    In a real application, this tool would NOT exist.
    It's here just to help users test the demo.
    """
    return {
        "note": "These are demo keys for testing. In production, keys would be user-specific.",
        "valid_keys": list(VALID_KEYS),
    }


if __name__ == "__main__":
    import sys

    # Default port
    port = 8006

    # Allow port override via command line
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print(f"Invalid port: {sys.argv[1]}, using default {port}")

    print(f"Starting API Key Demo MCP server on http://localhost:{port}/mcp")
    print(f"Valid API keys for testing: {VALID_KEYS}")
    print("\nTo test, set your API key in Atlas UI or use the FastMCP client.")

    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
