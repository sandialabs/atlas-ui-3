# Environment Variable Demo MCP Server

This MCP server demonstrates the environment variable passing capability added to Atlas UI 3. It shows how to configure and use environment variables for MCP servers through the `mcp.json` configuration file.

## Purpose

This server provides tools to:
- Retrieve specific environment variables
- List all configured environment variables
- Demonstrate practical usage patterns for environment variables

## Configuration

The server is configured in `config/mcp.json` (or `atlas/config/mcp.json` for package defaults) with the `env` field:

```json
{
  "env-demo": {
    "command": ["python", "mcp/env-demo/main.py"],
    "cwd": "atlas",
    "env": {
      "CLOUD_PROFILE": "demo-profile",
      "CLOUD_REGION": "us-west-2",
      "DEBUG_MODE": "true",
      "ENVIRONMENT": "development",
      "API_KEY": "${DEMO_API_KEY}"
    },
    "groups": ["users"],
    "description": "Demonstrates environment variable passing to MCP servers",
    "compliance_level": "Public"
  }
}
```

## Environment Variable Features

### Literal Values
Set environment variables with literal string values:
```json
"env": {
  "CLOUD_REGION": "us-west-2",
  "DEBUG_MODE": "true"
}
```

### Variable Substitution
Reference system environment variables using `${VAR_NAME}` syntax:
```json
"env": {
  "API_KEY": "${DEMO_API_KEY}"
}
```

Before starting Atlas UI, set the system environment variable:
```bash
export DEMO_API_KEY="your-secret-key"
```

## Available Tools

### 1. `get_env_var`
Retrieves the value of a specific environment variable.

**Input:**
- `var_name` (string): Name of the environment variable

**Example Usage:**
```
Get the value of CLOUD_REGION
```

### 2. `list_configured_env_vars`
Lists all configured environment variables that are commonly expected.

**Example Usage:**
```
Show me the configured environment variables
```

### 3. `demonstrate_env_usage`
Shows practical examples of using environment variables.

**Input:**
- `operation` (string): Type of demonstration
  - `"info"`: General information about env var configuration
  - `"config"`: Demonstrates configuration usage (profile, region)
  - `"credentials"`: Demonstrates secure credential handling

**Example Usage:**
```
Demonstrate how to use environment variables for configuration
```

## Use Cases

### Cloud Configuration
```json
"env": {
  "CLOUD_PROFILE": "production-profile",
  "CLOUD_REGION": "us-east-1",
  "AVAILABILITY_ZONE": "us-east-1a"
}
```

### API Credentials
```json
"env": {
  "API_KEY": "${MY_SERVICE_API_KEY}",
  "API_ENDPOINT": "https://api.example.com"
}
```

### Feature Flags
```json
"env": {
  "DEBUG_MODE": "false",
  "ENABLE_CACHING": "true",
  "MAX_RETRIES": "3"
}
```

## Security Best Practices

1. **Never commit secrets**: Use `${VAR_NAME}` substitution for sensitive values
2. **Set system env vars**: Configure sensitive values at the system level
3. **Use appropriate compliance levels**: Mark servers with sensitive access appropriately
4. **Document required variables**: Clearly document which env vars are needed

## Testing

To test this server:

1. Set any optional environment variables:
```bash
export DEMO_API_KEY="test-key-123"
```

2. Start Atlas UI (the server will automatically load with the configured env vars)

3. In the chat interface, try:
```
List all configured environment variables for the env-demo server
```

```
Get the value of CLOUD_REGION
```

```
Demonstrate how environment variables are used for credentials
```

## Notes

- Environment variables are only passed to stdio servers (not HTTP/SSE servers)
- If a `${VAR_NAME}` reference cannot be resolved, server initialization will fail with a clear error message
- Empty `env: {}` is valid and will set no environment variables
- The `env` field is optional; servers work without it as before
