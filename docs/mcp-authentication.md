# MCP Server Authentication

This document describes how to configure authentication for HTTP/SSE MCP servers.

## Overview

Atlas UI 3 supports authenticating to MCP servers using API keys and custom HTTP headers. This allows MCP servers to verify that requests are coming from a trusted Atlas UI instance by checking a shared secret.

## Configuration

Authentication is configured in the MCP configuration file (`config/overrides/mcp.json` or `config/defaults/mcp.json`).

### API Key Authentication

Use the `api_key` field to specify an API key that will be sent as a Bearer token in the Authorization header:

```json
{
  "my-secure-server": {
    "url": "https://mcp.example.com",
    "transport": "http",
    "groups": ["users"],
    "api_key": "${MCP_API_KEY}",
    "description": "Secure MCP server with API key authentication"
  }
}
```

The API key is sent as:
```
Authorization: Bearer ${MCP_API_KEY}
```

### Custom Headers

Use the `extra_headers` field to specify additional HTTP headers for authentication or other purposes:

```json
{
  "my-custom-server": {
    "url": "https://mcp.example.com/sse",
    "transport": "sse",
    "groups": ["users"],
    "extra_headers": {
      "X-API-Key": "${MCP_SECRET_KEY}",
      "X-Client-ID": "atlas-ui-3",
      "X-Custom-Header": "custom-value"
    },
    "description": "MCP server with custom authentication headers"
  }
}
```

### Combining API Key and Extra Headers

You can use both `api_key` and `extra_headers` together:

```json
{
  "my-enterprise-server": {
    "url": "https://enterprise-mcp.example.com",
    "transport": "http",
    "groups": ["admin"],
    "api_key": "${MCP_BEARER_TOKEN}",
    "extra_headers": {
      "X-Tenant-ID": "${TENANT_ID}",
      "X-Environment": "production"
    },
    "description": "Enterprise MCP server with multi-factor authentication"
  }
}
```

## Environment Variable Expansion

Both `api_key` and `extra_headers` values support environment variable expansion using the `${VARIABLE_NAME}` syntax, similar to the LLM configuration.

### Setting Environment Variables

Create a `.env` file in the project root:

```bash
# .env
MCP_API_KEY=your-secret-api-key-here
MCP_SECRET_KEY=another-secret-key
TENANT_ID=tenant-123
```

Or set them in your shell before starting the application:

```bash
export MCP_API_KEY=your-secret-api-key-here
export MCP_SECRET_KEY=another-secret-key
python backend/main.py
```

### Security Best Practices

1. **Never commit secrets to version control** - Always use environment variables for sensitive values
2. **Add `.env` to `.gitignore`** - Prevent accidental commits of secrets
3. **Use different keys for different environments** - Development, staging, and production should use different API keys
4. **Rotate keys regularly** - Change API keys periodically for better security
5. **Use HTTPS** - Always use HTTPS URLs for production MCP servers to encrypt API keys in transit

## Example Configuration

Here's a complete example showing different authentication methods:

```json
{
  "public-calculator": {
    "command": ["python", "mcp/calculator/main.py"],
    "cwd": "backend",
    "groups": ["users"],
    "description": "Public calculator - no authentication needed"
  },
  
  "authenticated-api": {
    "url": "https://api.example.com/mcp",
    "transport": "http",
    "groups": ["users"],
    "api_key": "${EXAMPLE_API_KEY}",
    "description": "External API with Bearer token authentication"
  },
  
  "custom-auth-server": {
    "url": "https://custom.example.com/sse",
    "transport": "sse",
    "groups": ["admin"],
    "extra_headers": {
      "X-API-Key": "${CUSTOM_API_KEY}",
      "X-Workspace-ID": "${WORKSPACE_ID}"
    },
    "description": "Custom server with header-based authentication"
  },
  
  "multi-auth-server": {
    "url": "https://secure.example.com",
    "transport": "http",
    "groups": ["admin"],
    "api_key": "${SECURE_BEARER_TOKEN}",
    "extra_headers": {
      "X-Client-ID": "atlas-ui-3",
      "X-Version": "3.0"
    },
    "description": "Server with both Bearer token and custom headers"
  }
}
```

## Troubleshooting

### Environment variables not expanding

If you see literal `${VARIABLE_NAME}` in logs or errors:

1. Check that the environment variable is set: `echo $VARIABLE_NAME`
2. Ensure the `.env` file is in the project root
3. Restart the application after setting environment variables

### Authentication failures

If the MCP server rejects requests:

1. Check server logs for authentication errors
2. Verify the API key or header values are correct
3. Ensure the header names match what the server expects (case-sensitive)
4. Check that the server supports the authentication method you're using

### Logs showing warnings

The application will log warnings if:
- An environment variable referenced in the config is undefined
- The expansion results in an empty value

Check the logs for messages like:
```
MCP server 'my-server' api_key references undefined environment variable: ${UNDEFINED_VAR}
```

## Server-Side Implementation

MCP servers that want to authenticate Atlas UI clients should:

1. Check for the `Authorization` header (if using `api_key`)
2. Verify the Bearer token matches their expected secret
3. Optionally check additional headers (if using `extra_headers`)
4. Return appropriate HTTP status codes (401 Unauthorized, 403 Forbidden)

Example server-side check (pseudocode):
```python
def verify_request(request):
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return 401  # Unauthorized
    
    token = auth_header[7:]  # Remove "Bearer " prefix
    if token != os.environ['EXPECTED_API_KEY']:
        return 403  # Forbidden
    
    # Additional header checks
    if request.headers.get('X-Client-ID') != 'atlas-ui-3':
        return 403
    
    return None  # Success
```
