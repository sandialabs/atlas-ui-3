"""
MCP Server with API Key Authentication Demo

This server validates X-API-Key header on all tool calls using middleware.
Demonstrates per-user API key authentication for Atlas UI.

Run with: python main.py
Or configure in mcp.json with auth_type: "api_key"
"""

from contextvars import ContextVar

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.dependencies import get_http_headers
from fastmcp.exceptions import ToolError

# Context variable to store current user during request
current_user_var: ContextVar[dict | None] = ContextVar("current_user", default=None)

# API keys mapped to user info
# In production, this would be a database lookup
API_KEY_USERS = {
    "test123": {
        "email": "test@example.com",
        "name": "Test User",
        "role": "developer",
    },
    "admin123": {
        "email": "admin@example.com",
        "name": "Admin User",
        "role": "admin",
    },
    "demo-api-key-12345": {
        "email": "demo@example.com",
        "name": "Demo User",
        "role": "viewer",
    },
}


def get_current_user() -> dict | None:
    """Get the current authenticated user from context."""
    return current_user_var.get()


class ApiKeyAuthMiddleware(Middleware):
    """Middleware that validates API key and sets current user context."""

    def __init__(self, api_key_users: dict[str, dict], header_name: str = "x-api-key"):
        self.api_key_users = api_key_users
        self.header_name = header_name.lower()

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        """Validate API key and set user context before each tool call."""
        # Get tool name from context.message
        tool_name = context.message.name
        print(f"[AUTH] Checking API key for tool '{tool_name}'...", flush=True)
        headers = get_http_headers() or {}
        api_key = headers.get(self.header_name)

        if not api_key:
            print(f"[AUTH FAILED] Tool '{tool_name}': Missing {self.header_name} header", flush=True)
            raise ToolError(
                f"Authentication required: Missing {self.header_name} header. "
                "Please provide your API key in Atlas UI settings."
            )

        user = self.api_key_users.get(api_key)
        if not user:
            # Don't log the API key value, even partially
            print(f"[AUTH FAILED] Tool '{tool_name}': Invalid API key", flush=True)
            raise ToolError(
                "Authentication failed: Invalid API key. "
                "Please check your API key and try again."
            )

        # Log successful authentication - only role, not sensitive user details
        print(f"[AUTH OK] Tool '{tool_name}': Authenticated user with role: {user['role']}", flush=True)

        # Set current user in context for tools to access
        token = current_user_var.set(user)
        try:
            return await call_next(context)
        finally:
            current_user_var.reset(token)


mcp = FastMCP("ApiKeyDemoServer")

# Add authentication middleware
mcp.add_middleware(ApiKeyAuthMiddleware(api_key_users=API_KEY_USERS))


@mcp.tool
def echo(message: str) -> str:
    """Echo back a message. Requires valid API key."""
    user = get_current_user()
    return f"Echo from {user['name']}: {message}"


@mcp.tool
def add_numbers(a: int, b: int) -> int:
    """Add two numbers together. Requires valid API key."""
    return a + b


@mcp.tool
def whoami() -> dict:
    """Show who you are based on your API key.

    Returns the user information associated with your API key.
    """
    user = get_current_user()
    return {
        "authenticated": True,
        "user": user,
        "message": f"Hello, {user['name']}! You are authenticated as {user['role']}.",
    }


@mcp.tool
def get_user_data() -> dict:
    """Get sample user data. Requires valid API key.

    This demonstrates that the API key was validated successfully
    and shows personalized data for the authenticated user.
    """
    user = get_current_user()
    return {
        "server_name": "ApiKeyDemoServer",
        "authenticated": True,
        "user": user,
        "message": f"Welcome back, {user['name']}!",
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
        "valid_keys": {key: user["email"] for key, user in API_KEY_USERS.items()},
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
    print(f"Valid API key count: {len(API_KEY_USERS)} (use list_valid_keys tool to see them)")
    print("\nTo test, set your API key in Atlas UI or use the FastMCP client.")

    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
