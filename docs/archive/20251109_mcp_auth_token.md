# Add Bearer Token Authentication for MCP Servers

**Date:** November 9, 2025
**Author:** Gemini
**Status:** Proposed

## Objective

This document outlines the plan to add support for bearer token authentication when connecting to MCP (Model-as-a-Component-of-a-Process) servers. This will allow Atlas UI to securely connect to MCP servers that require an `Authorization: Bearer <token>` header.

## Background

Some MCP servers are protected and require authentication. The `fastmcp` library, which is used to connect to these servers, supports passing an authentication token. We need to update our application to allow specifying this token in the `mcp.json` configuration file and use it when establishing a connection.

This change will involve:
1. Adding environment variable substitution for secure token management
2. Modifying the configuration model to support `auth_token` field
3. Updating the client initialization logic to pass tokens to FastMCP
4. Providing examples in the default configuration

## Key Design Decision: Environment Variable Substitution

**Security-first approach**: Instead of storing tokens directly in config files, this implementation supports environment variable substitution using the pattern `"auth_token": "${ENV_VAR_NAME}"`.

**Benefits:**
- ✅ No secrets in config files (even in git-ignored overrides)
- ✅ Standard practice for production deployments
- ✅ Works seamlessly with Docker, Kubernetes, CI/CD
- ✅ Easy to rotate tokens without changing configs
- ✅ Still supports direct strings for development/testing

**Example Usage:**
```bash
# Set environment variable
export MCP_SERVER_TOKEN="secret-api-key-123"

# In config/defaults/mcp.json or config/overrides/mcp.json
{
  "my-server": {
    "url": "https://api.example.com/mcp",
    "auth_token": "${MCP_SERVER_TOKEN}"
  }
}
```

## Implementation Steps

### 1. Add Environment Variable Substitution Utility

Before modifying the configuration model, we need a utility function to resolve environment variables from config values.

*   **File to create/modify:** `backend/modules/config/config_manager.py`
*   **Function to add:** `resolve_env_var`

Add this helper function to support environment variable substitution:

```python
import os
import re

def resolve_env_var(value: Optional[str]) -> Optional[str]:
    """
    Resolve environment variables in config values.

    Supports patterns like:
    - "${ENV_VAR_NAME}" -> replaced with os.environ.get("ENV_VAR_NAME")
    - "literal-string" -> returned as-is
    - None -> returned as-is

    Args:
        value: Config value that may contain env var pattern

    Returns:
        Resolved value with env vars substituted, or None if value is None

    Raises:
        ValueError: If env var pattern is found but variable is not set
    """
    if value is None:
        return None

    # Pattern: ${VAR_NAME}
    pattern = r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}'
    match = re.match(pattern, value)

    if match:
        env_var_name = match.group(1)
        env_value = os.environ.get(env_var_name)

        if env_value is None:
            raise ValueError(
                f"Environment variable '{env_var_name}' is not set but required in config"
            )

        return env_value

    # Return literal string if no pattern found
    return value
```

**Design Notes:**
- Only supports exact pattern `${VAR_NAME}` (not partial substitution like `"prefix-${VAR}-suffix"`)
- Raises clear error if env var is referenced but not set (fail-fast)
- Returns literal string if no pattern detected (backward compatible)

### 2. Modify the MCP Configuration Model

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

### 3. Update the MCP Client Initialization

Now we need to use the `auth_token` when creating the `fastmcp.Client`, with environment variable resolution.

*   **File to modify:** `backend/modules/mcp_tools/client.py`
*   **Function to modify:** `_initialize_single_client`

In this function, when preparing to create a `Client` for an `http` or `sse` transport, we will:
1. Get the `auth_token` from the server's configuration
2. Resolve any environment variable pattern using `resolve_env_var()`
3. Pass the resolved token to the `auth` parameter

**For HTTP transport:**

**Before:**
```python
# Use HTTP transport (StreamableHttp)
logger.debug(f"Creating HTTP client for {server_name} at {url}")
client = Client(url)
```

**After:**
```python
from backend.modules.config.config_manager import resolve_env_var

# Use HTTP transport (StreamableHttp)
logger.debug(f"Creating HTTP client for {server_name} at {url}")
raw_token = config.get("auth_token")
token = resolve_env_var(raw_token)  # Resolve ${ENV_VAR} if present
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
from backend.modules.config.config_manager import resolve_env_var

# Use explicit SSE transport
logger.debug(f"Creating SSE client for {server_name} at {url}")
from fastmcp.client.transports import SSETransport
raw_token = config.get("auth_token")
token = resolve_env_var(raw_token)  # Resolve ${ENV_VAR} if present
client = Client(url, auth=token)  # Pass auth to Client constructor
```

**Note:** Per FastMCP documentation, the `auth` parameter is passed to the `Client` constructor, not the transport.

### 4. Update the Default Configuration

To make it easy for other developers to use this feature, we will add examples to the default `mcp.json` configuration file.

*   **File to modify:** `config/defaults/mcp.json`

Add the `auth_token` field to server configurations with examples showing both environment variable and null patterns.

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
  "external-api-example": {
    "url": "https://api.example.com/mcp",
    "transport": "http",
    "groups": ["users"],
    "description": "Example external MCP server requiring authentication",
    "auth_token": "${MCP_EXTERNAL_API_TOKEN}"
  }
```

**Note:**
- Stdio servers (like `ui-demo`) can use `null` since they don't need authentication
- HTTP/SSE servers show the `${ENV_VAR_NAME}` pattern for environment variable substitution
- In production, set the environment variable: `export MCP_EXTERNAL_API_TOKEN="your-secret-token"`
- Alternatively, use `config/overrides/mcp.json` with direct string values (not recommended for secrets)

**Security Note:** The recommended approach is to use environment variables with the `"auth_token": "${VAR_NAME}"` pattern. This ensures tokens are never stored in config files or committed to version control.

## Testing

To test this feature, you will need an MCP server that is configured to require a bearer token.

### Manual Testing

1.  **Set up authenticated MCP server**: Use the updated `mocks/mcp-http-mock` (see "Update Mock Server" section)
2.  **Test environment variable substitution**:
    ```bash
    export MCP_TEST_TOKEN="test-api-key-123"
    ```
3.  **Configure in `config/overrides/mcp.json`**:
    ```json
    {
      "mcp-http-mock": {
        "url": "http://localhost:8001/mcp",
        "transport": "http",
        "auth_token": "${MCP_TEST_TOKEN}"
      }
    }
    ```
4.  **Run backend** and verify successful connection:
    ```bash
    cd backend
    python main.py
    # Check logs for "Successfully connected to mcp-http-mock"
    ```
5.  **Test failure cases**:
    - Unset env var: `unset MCP_TEST_TOKEN` - should see clear error about missing env var
    - Invalid token: `export MCP_TEST_TOKEN="wrong-token"` - should fail authentication
    - Direct string: `"auth_token": "test-api-key-123"` - should work (for comparison)

### Unit Testing

#### 1. Test Environment Variable Resolution

**File:** `backend/tests/modules/config/test_config_manager.py`

Create comprehensive tests for the `resolve_env_var()` function:

```python
import os
import pytest
from backend.modules.config.config_manager import resolve_env_var


class TestResolveEnvVar:
    """Test environment variable substitution in config values."""

    def test_resolve_env_var_with_none(self):
        """Should return None when input is None."""
        assert resolve_env_var(None) is None

    def test_resolve_env_var_with_literal_string(self):
        """Should return literal string unchanged."""
        assert resolve_env_var("my-literal-token") == "my-literal-token"

    def test_resolve_env_var_with_existing_env_var(self, monkeypatch):
        """Should resolve ${VAR_NAME} when env var exists."""
        monkeypatch.setenv("TEST_TOKEN", "secret-123")
        assert resolve_env_var("${TEST_TOKEN}") == "secret-123"

    def test_resolve_env_var_with_missing_env_var(self):
        """Should raise ValueError when env var doesn't exist."""
        with pytest.raises(ValueError, match="Environment variable 'MISSING_VAR' is not set"):
            resolve_env_var("${MISSING_VAR}")

    def test_resolve_env_var_with_empty_string(self):
        """Should return empty string unchanged."""
        assert resolve_env_var("") == ""

    def test_resolve_env_var_with_empty_env_var(self, monkeypatch):
        """Should allow empty env var values."""
        monkeypatch.setenv("EMPTY_VAR", "")
        assert resolve_env_var("${EMPTY_VAR}") == ""

    def test_resolve_env_var_with_partial_pattern(self):
        """Should not match partial patterns like 'prefix-${VAR}'."""
        # Only exact ${VAR} pattern is supported, not embedded in strings
        assert resolve_env_var("prefix-${VAR}") == "prefix-${VAR}"

    def test_resolve_env_var_with_invalid_pattern(self):
        """Should return strings with invalid patterns unchanged."""
        assert resolve_env_var("${123_INVALID}") == "${123_INVALID}"
        assert resolve_env_var("${INVALID-NAME}") == "${INVALID-NAME}"
        assert resolve_env_var("$VAR") == "$VAR"
        assert resolve_env_var("{VAR}") == "{VAR}"

    def test_resolve_env_var_case_sensitive(self, monkeypatch):
        """Environment variable names should be case-sensitive."""
        monkeypatch.setenv("MY_VAR", "value1")
        monkeypatch.setenv("my_var", "value2")
        assert resolve_env_var("${MY_VAR}") == "value1"
        assert resolve_env_var("${my_var}") == "value2"

    def test_resolve_env_var_with_special_chars(self, monkeypatch):
        """Should handle env vars with special characters in values."""
        monkeypatch.setenv("SPECIAL_TOKEN", "abc!@#$%^&*()_+-=[]{}|;:,.<>?")
        assert resolve_env_var("${SPECIAL_TOKEN}") == "abc!@#$%^&*()_+-=[]{}|;:,.<>?"
```

#### 2. Test MCP Configuration Model

**File:** `backend/tests/modules/config/test_config_manager.py`

Add tests to ensure `MCPServerConfig` correctly handles the `auth_token` field:

```python
from backend.modules.config.config_manager import MCPServerConfig


class TestMCPServerConfig:
    """Test MCPServerConfig with auth_token field."""

    def test_auth_token_is_optional(self):
        """auth_token field should be optional."""
        config = MCPServerConfig(
            description="Test server",
            command=["python", "server.py"]
        )
        assert config.auth_token is None

    def test_auth_token_accepts_string(self):
        """auth_token should accept string values."""
        config = MCPServerConfig(
            description="Test server",
            command=["python", "server.py"],
            auth_token="my-token-123"
        )
        assert config.auth_token == "my-token-123"

    def test_auth_token_accepts_env_var_pattern(self):
        """auth_token should accept environment variable patterns."""
        config = MCPServerConfig(
            description="Test server",
            url="http://localhost:8000",
            auth_token="${MY_TOKEN}"
        )
        assert config.auth_token == "${MY_TOKEN}"

    def test_auth_token_accepts_none(self):
        """auth_token should explicitly accept None."""
        config = MCPServerConfig(
            description="Test server",
            command=["python", "server.py"],
            auth_token=None
        )
        assert config.auth_token is None
```

#### 3. Test MCP Client Initialization

**File:** `backend/tests/modules/mcp_tools/test_client.py`

Add tests to verify auth token is passed correctly to FastMCP client:

```python
import os
import pytest
from unittest.mock import Mock, patch, AsyncMock
from backend.modules.mcp_tools.client import MCPToolManager


class TestMCPClientAuthentication:
    """Test MCP client initialization with authentication."""

    @pytest.mark.asyncio
    @patch('backend.modules.mcp_tools.client.Client')
    async def test_http_client_with_env_var_token(self, mock_client_class, monkeypatch):
        """Should resolve env var and pass token to HTTP client."""
        monkeypatch.setenv("MCP_AUTH_TOKEN", "secret-token-123")

        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        server_config = {
            "url": "http://localhost:8000/mcp",
            "transport": "http",
            "auth_token": "${MCP_AUTH_TOKEN}"
        }

        # Initialize client (implementation details may vary)
        # This test verifies the client is created with auth parameter
        mock_client_class.assert_called_with(
            "http://localhost:8000/mcp",
            auth="secret-token-123"
        )

    @pytest.mark.asyncio
    @patch('backend.modules.mcp_tools.client.Client')
    async def test_http_client_with_literal_token(self, mock_client_class):
        """Should pass literal token string to HTTP client."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        server_config = {
            "url": "http://localhost:8000/mcp",
            "transport": "http",
            "auth_token": "direct-token-456"
        }

        # Verify client is created with direct token
        mock_client_class.assert_called_with(
            "http://localhost:8000/mcp",
            auth="direct-token-456"
        )

    @pytest.mark.asyncio
    @patch('backend.modules.mcp_tools.client.Client')
    async def test_http_client_without_token(self, mock_client_class):
        """Should pass None when no auth_token specified."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        server_config = {
            "url": "http://localhost:8000/mcp",
            "transport": "http"
        }

        # Verify client is created with auth=None
        mock_client_class.assert_called_with(
            "http://localhost:8000/mcp",
            auth=None
        )

    @pytest.mark.asyncio
    @patch('backend.modules.mcp_tools.client.Client')
    async def test_sse_client_with_token(self, mock_client_class):
        """Should pass auth token to SSE client."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        server_config = {
            "url": "http://localhost:8000/sse",
            "transport": "sse",
            "auth_token": "sse-token-789"
        }

        # Verify SSE client is created with auth parameter
        # Note: auth goes to Client constructor, not transport
        mock_client_class.assert_called_with(
            "http://localhost:8000/sse",
            auth="sse-token-789"
        )

    @pytest.mark.asyncio
    async def test_missing_env_var_raises_error(self):
        """Should raise clear error when env var is missing."""
        server_config = {
            "url": "http://localhost:8000/mcp",
            "transport": "http",
            "auth_token": "${MISSING_TOKEN_VAR}"
        }

        with pytest.raises(ValueError, match="Environment variable 'MISSING_TOKEN_VAR' is not set"):
            # Attempt to initialize client with missing env var
            # Implementation will call resolve_env_var() which should raise
            pass

    @pytest.mark.asyncio
    @patch('backend.modules.mcp_tools.client.StdioServerParameters')
    async def test_stdio_client_ignores_token(self, mock_stdio_params):
        """stdio clients should ignore auth_token (no auth mechanism)."""
        # Stdio transport doesn't support HTTP auth headers
        server_config = {
            "command": ["python", "server.py"],
            "auth_token": "ignored-token"
        }

        # Verify that stdio client initialization doesn't try to use auth_token
        # (implementation-specific - may just log a warning or silently ignore)
        pass
```

#### 4. Integration Test

**File:** `backend/tests/integration/test_mcp_auth_integration.py`

Create an end-to-end test with the mock MCP server:

```python
import pytest
import os
from backend.modules.mcp_tools.client import MCPToolManager


@pytest.mark.integration
class TestMCPAuthenticationIntegration:
    """Integration tests for MCP authentication."""

    @pytest.mark.asyncio
    async def test_authenticated_connection_success(self, monkeypatch):
        """Should successfully connect to authenticated MCP server."""
        monkeypatch.setenv("MCP_TEST_TOKEN", "test-api-key-123")

        # Configure test server with auth requirement
        config = {
            "mcp-http-mock": {
                "url": "http://localhost:8001/mcp",
                "transport": "http",
                "auth_token": "${MCP_TEST_TOKEN}"
            }
        }

        # Initialize tool manager and verify connection
        # (implementation details depend on your setup)
        assert True  # Replace with actual connection test

    @pytest.mark.asyncio
    async def test_authenticated_connection_failure_invalid_token(self):
        """Should fail to connect with invalid token."""
        config = {
            "mcp-http-mock": {
                "url": "http://localhost:8001/mcp",
                "transport": "http",
                "auth_token": "invalid-token"
            }
        }

        # Verify connection fails with authentication error
        with pytest.raises(Exception, match="authentication|unauthorized|403|401"):
            # Attempt connection with invalid token
            pass

    @pytest.mark.asyncio
    async def test_authenticated_tool_execution(self, monkeypatch):
        """Should successfully execute tool after authentication."""
        monkeypatch.setenv("MCP_TEST_TOKEN", "test-api-key-123")

        # Connect and execute a tool on authenticated server
        # Verify tool execution succeeds
        assert True  # Replace with actual tool execution test
```

#### Running Tests

**Execute all unit tests:**
```bash
# Run all tests
./test/run_tests.sh all

# Run specific test file
pytest backend/tests/modules/config/test_config_manager.py -v

# Run specific test class
pytest backend/tests/modules/config/test_config_manager.py::TestResolveEnvVar -v

# Run with coverage
pytest backend/tests/ --cov=backend.modules.config --cov-report=html
```

**Test Coverage Requirements:**
- `resolve_env_var()` function: 100% coverage
- `MCPServerConfig` model: 100% coverage for auth_token field
- Client initialization: 90%+ coverage for auth-related code paths
- Integration tests: All success and failure scenarios

## Update Documentation

To ensure users and developers are aware of this new feature, the following documents must be updated:

1.  **Admin Guide (`docs/02_admin_guide.md`)**:
    *   Add a section explaining how to configure bearer token authentication for MCP servers
    *   Show environment variable pattern: `"auth_token": "${MCP_SERVER_TOKEN}"`
    *   Explain how to set environment variables before starting the backend
    *   Include security best practices:
        - **Recommended**: Use environment variables for production (tokens never touch filesystem)
        - **Alternative**: Use `config/overrides/mcp.json` with direct strings (for development only)
        - **Never**: Commit tokens to `config/defaults/mcp.json` or any version-controlled files

2.  **Developer Guide (`docs/03_developer_guide.md`)**:
    *   Update MCP server configuration section to include `auth_token` field in `MCPServerConfig`
    *   Document the `resolve_env_var()` function and its behavior
    *   Explain that tokens are used for bearer authentication with HTTP/SSE-based MCP servers
    *   Note that stdio servers ignore the `auth_token` field

3.  **Environment Variables Reference** (if exists, or add to Admin Guide):
    *   Document MCP-related environment variables that can be used
    *   Example: `MCP_EXTERNAL_API_TOKEN` - Token for external MCP server authentication

## API Key Authentication Example

Here's a minimal code example for API key authentication between a FastMCP client and server.

**Official Documentation:**
- Server-side: https://gofastmcp.com/servers/auth/token-verification (Static Token Verification)
- Client-side: https://gofastmcp.com/clients/auth/bearer.md

**Important:** `StaticTokenVerifier` stores tokens as plain text and is designed exclusively for development and testing. It should **NEVER be used in production environments**.

### Server Side

```python
# server.py
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

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

## Additional Improvements

### Tool Index Optimization

As part of this implementation, the tool lookup mechanism was improved to use an index-based approach instead of string parsing. This fixes issues with tool names containing underscores.

**Problem Fixed:**
- Previous implementation derived server names by splitting on underscores, which failed when tool names contained underscores (e.g., `ui-demo_create_form_demo` would incorrectly parse server as `ui-demo_create_form`)

**Solution:**
- Added `_tool_index` dictionary for O(1) lookups of fully-qualified tool names
- Eliminates fragile string parsing logic
- Improves performance and reliability of tool execution

## Update Mock Server

To facilitate testing of the new authentication mechanism, the `mocks/mcp-http-mock` should be updated to require bearer token authentication using the `StaticTokenVerifier` approach shown above.

1.  **Modify `mocks/mcp-http-mock/main.py` (or equivalent file):**
    *   Import `FastMCP` and `StaticTokenVerifier` from `fastmcp.server.auth.providers.jwt`.
    *   Instantiate `StaticTokenVerifier` with a dictionary of valid API keys and their claims.
    *   Pass the verifier to the `FastMCP` constructor using the `auth` parameter.

**Example Implementation:**

```python
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

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
