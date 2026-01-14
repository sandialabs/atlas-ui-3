# MCP Server Configuration

The `mcp.json` file defines the MCP (Model Context Protocol) servers that the application can connect to. These servers provide the tools and capabilities available to the LLM.

*   **Location**: The default configuration is at `config/defaults/mcp.json`. You should place your instance-specific configuration in `config/overrides/mcp.json`.

## Comprehensive Example

Here is an example of a server configuration that uses all available options.

```json
{
  "MyExampleServer": {
    "enabled": true,
    "description": "A full description of what this server does, which appears in the marketplace.",
    "short_description": "A short description for tooltips.",
    "author": "Your Team Name",
    "help_email": "support@example.com",
    "groups": ["admin", "engineering"],
    "command": ["python", "mcp/MyExampleServer/main.py"],
    "cwd": "backend",
    "env": {
      "API_KEY": "${MY_API_KEY}",
      "DEBUG_MODE": "false",
      "MAX_RETRIES": "3"
    },
    "url": null,
    "transport": "stdio",
    "compliance_level": "Internal",
    "require_approval": ["dangerous_tool", "another_risky_tool"],
    "allow_edit": ["dangerous_tool"]
  }
}
```

## Configuration Fields Explained

*   **`enabled`**: (boolean) If `false`, the server is completely disabled and will not be loaded.
*   **`description`**: (string) A detailed description of the server's purpose and capabilities. This is shown to users in the MCP Marketplace.
*   **`short_description`**: (string) A brief, one-line description used for tooltips or other compact UI elements.
*   **`author`**: (string) The name of the team or individual who created the server.
*   **`help_email`**: (string) A contact email for users who need help with the server's tools.
*   **`groups`**: (list of strings) A list of user groups that are allowed to access this server. If a user is not in any of these groups, the server will be hidden from them.
*   **`command`**: (list of strings) For servers using `stdio` transport, this is the command and its arguments used to start the server process.
*   **`cwd`**: (string) The working directory from which to run the `command`.
*   **`env`**: (object) Environment variables to set for `stdio` servers. Keys are variable names, values can be literal strings or use environment variable substitution (e.g., `"${ENV_VAR}"`). This is only applicable to stdio servers and will be ignored for HTTP/SSE servers.
*   **`url`**: (string) For servers using `http` or `sse` transport, this is the URL of the server's endpoint.
*   **`transport`**: (string) The communication protocol to use. Can be `stdio`, `http`, or `sse`. This takes priority over auto-detection.
*   **`auth_token`**: (string) For HTTP/SSE servers, the bearer token used for authentication. Use environment variable substitution (e.g., `"${MCP_SERVER_TOKEN}"`) to avoid storing secrets in config files. Stdio servers ignore this field.
*   **`compliance_level`**: (string) The security compliance level of this server (e.g., "Public", "Internal", "SOC2"). This is used for data segregation and access control.
*   **`require_approval`**: (list of strings) A list of tool names (without the server prefix) that will always require user approval before execution.
*   **`allow_edit`**: (list of strings) A list of tool names for which the user is allowed to edit the arguments before approving. (Note: This is a legacy field and may be deprecated; the UI may allow editing for all approval requests).

## Server Types

The system can connect to different types of MCP servers:
*   **Standard I/O (`stdio`)**: Servers that are started as a subprocess and communicate over `stdin` and `stdout`.
*   **HTTP (`http`)**: Servers that expose a standard HTTP endpoint.
*   **Server-Sent Events (`sse`)**: Servers that stream responses over an HTTP connection.

## Hot Reload Note

After editing `config/overrides/mcp.json`, you do **not** need to restart the backend. Admins can:

- Call `POST /admin/mcp/reload` to reload `mcp.json`, reinitialize MCP clients, and rediscover tools/prompts.
- Use `GET /admin/mcp/status` to see which servers are connected or failing.
- Use `POST /admin/mcp/reconnect` (plus the auto-reconnect feature flag) to retry failed servers with exponential backoff.

## MCP Server Authentication

For MCP servers that require authentication, you can configure bearer token authentication using the `auth_token` field.

### Environment Variable Substitution

**Security Best Practice**: Never store API keys or tokens directly in configuration files. Instead, use environment variable substitution:

```json
{
  "my-external-api": {
    "url": "https://api.example.com/mcp",
    "transport": "http",
    "auth_token": "${MCP_EXTERNAL_API_TOKEN}",
    "groups": ["users"]
  }
}
```

Then set the environment variable:
```bash
export MCP_EXTERNAL_API_TOKEN="your-secret-api-key"
```

### How It Works

1. **HTTP/SSE Servers**: The `auth_token` value is passed as a Bearer token in the `Authorization` header when connecting to the MCP server.
2. **Stdio Servers**: The `auth_token` field is ignored since stdio servers don't use HTTP authentication.
3. **Environment Variables**: If the token contains `${VAR_NAME}` pattern, it's replaced with the value of the environment variable `VAR_NAME`.
4. **Error Handling**: If a required environment variable is missing, the server initialization will fail gracefully with a clear error message.

### Security Considerations

- **Recommended**: Use environment variables for all production tokens
- **Alternative**: For development/testing, you can use direct string values (not recommended for production)
- **Never**: Commit tokens to `config/defaults/mcp.json` or any version-controlled files

## Environment Variables for Stdio Servers

For stdio servers, you can pass custom environment variables to the server process using the `env` field. This is useful for:
- Configuring server behavior without modifying command arguments
- Passing credentials or API keys securely
- Setting runtime configuration options

### Example Configuration

```json
{
  "my-external-tool": {
    "command": ["wrapper-cli", "my.external.tool@latest", "--allow-write"],
    "cwd": "backend",
    "env": {
      "CLOUD_PROFILE": "my-profile-9",
      "CLOUD_REGION": "us-east-7",
      "API_KEY": "${MY_API_KEY}",
      "DEBUG_MODE": "false"
    },
    "groups": ["users"]
  }
}
```

Then set the environment variable before starting Atlas UI:
```bash
export MY_API_KEY="your-secret-api-key"
```

### Environment Variable Features

- **Literal Values**: Environment variables can contain literal string values (e.g., `"CLOUD_REGION": "us-east-7"`)
- **Variable Substitution**: Use `${VAR_NAME}` syntax to reference system environment variables (e.g., `"API_KEY": "${MY_API_KEY}"`)
- **Empty Values**: An empty object `{}` is valid and will set no environment variables
- **Error Handling**: If a referenced environment variable (e.g., `${MY_API_KEY}`) is not set in the system, the server initialization will fail with a clear error message
- **Stdio Only**: The `env` field only applies to stdio servers; it is ignored for HTTP/SSE servers

### Security Best Practices

- Use environment variable substitution for all sensitive values (API keys, passwords, tokens)
- Never store secrets directly in the `env` object values
- Set environment variables via your deployment system (Docker, Kubernetes, systemd, etc.)
- Use different values for development, staging, and production environments

## Access Control with Groups

You can restrict access to MCP servers based on user groups. This is a critical feature for controlling which users can access powerful or sensitive tools. If a user is not in the required group, the server will be completely invisible to them in the UI, and any attempt to call its functions will be blocked.

## A Note on the `username` Argument

As a security measure, if a tool is designed to accept a `username` argument, the Atlas UI backend will **always** overwrite this argument with the authenticated user's identity before calling the tool. This ensures that a tool always runs with the correct user context and prevents the LLM from impersonating another user.


## Advanced MCP Features

### User Elicitation

MCP tools can request structured input from users during tool execution. See the [Elicitation Documentation](../developer/elicitation.md) for details.

### LLM Sampling

MCP tools can request LLM text generation during tool execution, enabling agentic workflows. See the [Sampling Documentation](../developer/sampling.md) for details.

### Progress Updates

MCP tools can send real-time progress updates during long-running operations. See the [Progress Updates Documentation](../developer/progress-updates.md) for details.

