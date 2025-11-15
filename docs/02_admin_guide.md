# Administrator's Guide

This guide is for administrators responsible for deploying, configuring, and managing the Atlas UI 3 application. It covers the key configuration files, security settings, and operational details.

## Configuration Architecture

The application uses a layered configuration system that loads settings from three primary sources in the following order of precedence:

1.  **Environment Variables (`.env`)**: Highest priority. These override any settings from files.
2.  **Override Files (`config/overrides/`)**: For custom, instance-specific configurations. These files are not checked into version control.
3.  **Default Files (`config/defaults/`)**: The base configuration that is part of the repository.

**Note**: The definitive source for all possible configuration options and their default values is the `AppSettings` class within `backend/modules/config/config_manager.py`. This class dictates how the application reads and interprets all its settings.

### Key Override Files

To customize your instance, you will place your own versions of the configuration files in the `config/overrides/` directory. The most common files to override are:

*   **`mcp.json`**: Registers and configures the MCP (tool) servers that provide capabilities to the LLM.
*   **`llmconfig.yml`**: Defines the list of available Large Language Models and their connection details.
*   **`compliance-levels.json`**: Defines the security compliance levels (e.g., Public, Internal, HIPAA) and the rules for how they can interact.
*   **`help-config.json`**: Populates the content of the "Help" modal in the user interface.
*   **`messages.txt`**: Defines the text for system-wide banner messages that can be displayed to all users.

### Customizing the Help Modal (`help-config.json`)

You can customize the content that appears in the "Help" or "About" modal in the UI by creating a `help-config.json` file.

*   **Location**: Place your custom file at `config/overrides/help-config.json`.

The file consists of a title and a list of sections, each with a title and content that can include markdown for formatting.

**Example `help-config.json`:**
```json
{
  "title": "About Our Chat Application",
  "sections": [
    {
      "title": "Welcome",
      "content": "This is a custom chat application for our organization. It provides access to internal tools and data sources."
    },
    {
      "title": "Available Tools",
      "content": "You can use tools for:\n\n*   Querying databases\n*   Analyzing documents\n*   Searching our internal knowledge base"
    },
    {
      "title": "Support",
      "content": "For questions or issues, please contact the support team at [support@example.com](mailto:support@example.com)."
    }
  ]
}
```

### The `.env` File

This file is crucial for setting up your instance. Start by copying the example file:

```bash
cp .env.example .env
```

Key settings in the `.env` file include:

*   **API Keys**: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.
*   **Authentication Header**: `AUTH_USER_HEADER` configures the HTTP header name used to extract the authenticated username from your reverse proxy (default: `X-User-Email`).
*   **Feature Flags**: Enable or disable major features like `FEATURE_AGENT_MODE_AVAILABLE`.
*   **S3 Connection**: Configure the connection to your S3-compatible storage. For local testing, you can set `USE_MOCK_S3=true` to use an in-memory mock instead of a real S3 bucket. **This mock must never be used in production.**
*   **Log Directory**: The `APP_LOG_DIR` variable points to the folder where the application log file (`app.jsonl`) will be stored. This path must be updated to a valid directory in your deployment environment.

## File Storage and Tool Integration

The application uses S3-compatible object storage for handling all user-uploaded files. This system is designed to be secure and flexible, allowing tools to access files without ever needing direct S3 credentials.

### Configuration Modes

You can configure the file storage in one of two modes using the `.env` file.

#### 1. Development Mode (Mock S3)
For local development and testing, you can use a built-in mock S3 service.

*   **Setting**: `USE_MOCK_S3=true`
*   **Behavior**: Files are stored on the local filesystem in the `minio-data/` directory. This mode is convenient as it requires no external services or credentials.
*   **Use Case**: Ideal for local development. **This must not be used in production.**

#### 2. Production Mode (Real S3)
For production, you must connect to a real S3-compatible object store like AWS S3, MinIO, or another provider.

*   **Setting**: `USE_MOCK_S3=false`
*   **Configuration**: You must provide the connection details in your `.env` file:
    ```
    S3_ENDPOINT_URL=https://your-s3-provider.com
    S3_BUCKET_NAME=your-bucket-name
    S3_ACCESS_KEY=your-access-key
    S3_SECRET_KEY=your-secret-key
    S3_REGION=us-east-1
    ```

### How MCP Tools Access Files

The application uses a secure workflow that prevents MCP tools from needing direct access to S3 credentials. Instead, the backend acts as a proxy.

```
1. User uploads file
   [User] -> [Atlas UI Backend] -> [S3 Bucket]
                 |
                 | 2. LLM calls tool with filename
                 v
4. Tool downloads file from Atlas UI API
   [MCP Tool] <- [Atlas UI Backend] <- [S3 Bucket]
                  ^
                  | 3. Backend creates temporary, secure URL
```

1.  **File Upload**: A user uploads a file, which is stored in the configured S3 bucket.
2.  **Tool Call**: The LLM decides to use a tool that needs the file and passes the `filename` as an argument.
3.  **Secure URL Generation**: The Atlas UI backend intercepts the tool call. It generates a temporary, secure URL that points back to its own API (e.g., `/api/files/download/...`). This URL contains a short-lived capability token that grants access only to that specific file.
4.  **Tool Execution**: The backend replaces the original `filename` argument with this new secure URL and sends it to the MCP tool. The tool can then make a simple `GET` request to the URL to download the file content.

This process ensures that MCP tools can access the files they need without ever handling sensitive S3 credentials, enhancing the overall security of the system.

## MCP Server Configuration (`mcp.json`)

The `mcp.json` file defines the MCP (Model Context Protocol) servers that the application can connect to. These servers provide the tools and capabilities available to the LLM.

*   **Location**: The default configuration is at `config/defaults/mcp.json`. You should place your instance-specific configuration in `config/overrides/mcp.json`.

### Comprehensive Example

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
    "url": null,
    "transport": "stdio",
    "compliance_level": "Internal",
    "require_approval": ["dangerous_tool", "another_risky_tool"],
    "allow_edit": ["dangerous_tool"]
  }
}
```

### Configuration Fields Explained

*   **`enabled`**: (boolean) If `false`, the server is completely disabled and will not be loaded.
*   **`description`**: (string) A detailed description of the server's purpose and capabilities. This is shown to users in the MCP Marketplace.
*   **`short_description`**: (string) A brief, one-line description used for tooltips or other compact UI elements.
*   **`author`**: (string) The name of the team or individual who created the server.
*   **`help_email`**: (string) A contact email for users who need help with the server's tools.
*   **`groups`**: (list of strings) A list of user groups that are allowed to access this server. If a user is not in any of these groups, the server will be hidden from them.
*   **`command`**: (list of strings) For servers using `stdio` transport, this is the command and its arguments used to start the server process.
*   **`cwd`**: (string) The working directory from which to run the `command`.
*   **`url`**: (string) For servers using `http` or `sse` transport, this is the URL of the server's endpoint.
*   **`transport`**: (string) The communication protocol to use. Can be `stdio`, `http`, or `sse`. This takes priority over auto-detection.
*   **`auth_token`**: (string) For HTTP/SSE servers, the bearer token used for authentication. Use environment variable substitution (e.g., `"${MCP_SERVER_TOKEN}"`) to avoid storing secrets in config files. Stdio servers ignore this field.
*   **`compliance_level`**: (string) The security compliance level of this server (e.g., "Public", "Internal", "SOC2"). This is used for data segregation and access control.
*   **`require_approval`**: (list of strings) A list of tool names (without the server prefix) that will always require user approval before execution.
*   **`allow_edit`**: (list of strings) A list of tool names for which the user is allowed to edit the arguments before approving. (Note: This is a legacy field and may be deprecated; the UI may allow editing for all approval requests).

### Server Types

The system can connect to different types of MCP servers:
*   **Standard I/O (`stdio`)**: Servers that are started as a subprocess and communicate over `stdin` and `stdout`.
*   **HTTP (`http`)**: Servers that expose a standard HTTP endpoint.
*   **Server-Sent Events (`sse`)**: Servers that stream responses over an HTTP connection.

### MCP Server Authentication

For MCP servers that require authentication, you can configure bearer token authentication using the `auth_token` field.

#### Environment Variable Substitution

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

#### How It Works

1. **HTTP/SSE Servers**: The `auth_token` value is passed as a Bearer token in the `Authorization` header when connecting to the MCP server.
2. **Stdio Servers**: The `auth_token` field is ignored since stdio servers don't use HTTP authentication.
3. **Environment Variables**: If the token contains `${VAR_NAME}` pattern, it's replaced with the value of the environment variable `VAR_NAME`.
4. **Error Handling**: If a required environment variable is missing, the server initialization will fail gracefully with a clear error message.

#### Security Considerations

- **Recommended**: Use environment variables for all production tokens
- **Alternative**: For development/testing, you can use direct string values (not recommended for production)
- **Never**: Commit tokens to `config/defaults/mcp.json` or any version-controlled files

### Access Control with Groups

You can restrict access to MCP servers based on user groups. This is a critical feature for controlling which users can access powerful or sensitive tools. If a user is not in the required group, the server will be completely invisible to them in the UI, and any attempt to call its functions will be blocked.

### A Note on the `username` Argument

As a security measure, if a tool is designed to accept a `username` argument, the Atlas UI backend will **always** overwrite this argument with the authenticated user's identity before calling the tool. This ensures that a tool always runs with the correct user context and prevents the LLM from impersonating another user.

## User Authentication

The application is designed with the expectation that it operates behind a reverse proxy in a production environment. It does **not** handle user authentication (i.e., logging users in) by itself. Instead, it trusts a header that is injected by an upstream authentication service.

### Production Authentication Flow

The intended flow for user authentication in a production environment is as follows:

```
   +-----------+      +-----------------+      +----------------+      +--------------------+
   |           |      |                 |      |                |      |                    |
   |   User    |----->|  Reverse Proxy  |----->|  Auth Service  |----->|  Atlas UI Backend  |
   |           |  1.  |                 |  2.  |                |  3.  |                    |
   +-----------+      +-----------------+      +----------------+      +--------------------+
```

1.  The user makes a request to the application's public URL, which is handled by the **Reverse Proxy**.
2.  The Reverse Proxy communicates with an **Authentication Service** (e.g., an SSO provider, an OAuth server) to validate the user's credentials (like cookies or tokens).
3.  Once the user is authenticated, the Reverse Proxy **injects the user's identity** (e.g., their email address) into an HTTP header and forwards the request to the **Atlas UI Backend**.

The backend application reads this header to identify the user. The header name is configurable via the `AUTH_USER_HEADER` environment variable (default: `X-User-Email`). This allows flexibility for different reverse proxy setups that may use different header names (e.g., `X-Authenticated-User`, `X-Remote-User`). This model is secure only if the backend is not directly exposed to the internet, ensuring that all requests are processed by the proxy first.

### Development Behavior

In a local development environment (when `DEBUG_MODE=true` in the `.env` file), the system falls back to using a default `test@test.com` user if the configured authentication header is not present.

### Configuring the Authentication Header

Different reverse proxy setups use different header names to pass authenticated user information. The application supports configuring the header name via the `AUTH_USER_HEADER` environment variable.

**Default Configuration:**
```
AUTH_USER_HEADER=X-User-Email
```

**Common Alternative Headers:**
```
# For Apache mod_auth setups
AUTH_USER_HEADER=X-Remote-User

# For some SSO providers
AUTH_USER_HEADER=X-Authenticated-User

# For custom reverse proxy configurations
AUTH_USER_HEADER=X-Custom-Auth-Header
```

This setting allows the application to work with various authentication infrastructures without code changes.

### WebSocket Authentication

The application uses WebSocket connections for real-time chat functionality. WebSocket authentication works consistently with HTTP authentication, using the same configured header.

**Authentication Flow for WebSockets:**

1. Client initiates WebSocket connection to `/ws`
2. Reverse proxy intercepts the WebSocket upgrade request (HTTP Upgrade)
3. Reverse proxy validates authentication and adds the configured auth header (e.g., `X-User-Email`)
4. Backend extracts user identity from the header during WebSocket handshake
5. All subsequent WebSocket operations use the authenticated user identity

**Important Security Notes:**

- The reverse proxy **must** strip any client-provided authentication headers before adding its own (otherwise attackers could inject headers like `X-User-Email: admin@company.com`)
- The backend trusts the header value because it assumes the reverse proxy has already validated the user
- Direct access to the backend (bypassing the reverse proxy) would allow header injection attacks

**Development Fallback:**

In development mode (`DEBUG_MODE=true`), if the authentication header is not present, the WebSocket connection falls back to:
1. The `user` query parameter (e.g., `/ws?user=test@test.com`)
2. The configured test user from `TEST_USER` environment variable
3. Default test user: `test@test.com`

This fallback behavior makes local development easier but should never be used in production.

### Customizing Authorization

**IMPORTANT: For production deployments, configuring authorization is essential.** The default implementation is a mock and **must be replaced** with your organization's actual authorization system. You have two primary methods to achieve this:

#### Recommended Method: HTTP Endpoint

You can configure the application to call an external HTTP endpoint to check for group membership. This is the most flexible and maintainable solution, requiring no code changes to the application itself.

1.  **Configure the Endpoint in `.env`**:
    Add the following variables to your `.env` file:
    ```
    # The URL of your authorization service
    AUTH_GROUP_CHECK_URL=https://your-auth-service.example.com/api/check-group

    # The API key for authenticating with your service
    AUTH_GROUP_CHECK_API_KEY=your-secret-api-key
    ```

2.  **Endpoint Requirements**:
    Your authorization endpoint must:
    *   Accept a `POST` request.
    *   Expect a JSON body with `user_id` and `group_id`:
        ```json
        {
          "user_id": "user@example.com",
          "group_id": "admin"
        }
        ```
    *   Authenticate requests using a bearer token in the `Authorization` header.
    *   Return a JSON response with a boolean `is_member` field:
        ```json
        {
          "is_member": true
        }
        ```

If `AUTH_GROUP_CHECK_URL` is not set, the application will fall back to the mock implementation in `backend/core/auth.py`.

#### Legacy Method: Modifying the Code

For advanced use cases, you can still directly modify the `is_user_in_group` function located in `backend/core/auth.py`. The default implementation is a mock and **must be replaced** if you are not using the HTTP endpoint method.

## Compliance and Data Security

The compliance system is designed to prevent the unintentional mixing of data from different security environments. This is essential for organizations that handle sensitive information.

### Compliance Levels

You can assign a `compliance_level` to LLM endpoints, RAG data sources, and MCP servers. These levels are defined in `config/defaults/compliance-levels.json` (which can be overridden).

**Example:** A tool that accesses internal-only data can be marked with `compliance_level: "Internal"`, while a tool that uses a public API can be marked as `compliance_level: "Public"`.

### The Allowlist Model

The compliance system uses an explicit **allowlist**. Each compliance level defines which other levels it is allowed to interact with. This prevents data from a highly secure environment (e.g., "HIPAA") from being accidentally sent to a less secure one (e.g., "Public").

For example, a session running with a "HIPAA" compliance level will not be able to use tools or data sources marked as "Public", preventing sensitive data from being exposed.

## Tool Approval System

The tool approval system provides a safety layer by requiring user confirmation before a tool is executed. This gives administrators and users fine-grained control over tool usage.

### Admin-Forced Approvals

As an administrator, you can mandate that certain high-risk functions always require user approval.

*   **Configuration**: In your `config/overrides/mcp.json` file, you can add a `require_approval` list to a server's definition.
*   **Behavior**: Any function listed here will always prompt the user for approval, and the user cannot disable this check.

**Example:**
```json
{
  "filesystem_tool": {
    "groups": ["admin"],
    "require_approval": ["delete_file", "overwrite_file"]
  }
}
```

### Global Approval Requirement

You can enforce that **all** tools require user approval by setting the following in your `.env` file:

```
FORCE_TOOL_APPROVAL_GLOBALLY=true
```

This setting overrides all other user preferences and is a simple way to enforce maximum safety.

### User-Controlled Auto-Approval

For tools that are not mandated to require approval by an admin, users can choose to "auto-approve" them to streamline their workflow. This option is available in the user settings panel.

## Admin Panel

The application includes an admin panel that provides access to configuration values and application logs.

*   **Access**: To access the admin panel, a user must be in the `admin` group. This requires a correctly configured `is_user_in_group` function.
*   **Icon**: Admin users will see a shield icon on the main page, which leads to the admin panel.
*   **Features**:
    *   View the current application configuration.
    *   View the application logs (`app.jsonl`).

## Logging System

The application produces structured logs in JSON Lines format (`.jsonl`), which makes them easy to parse and analyze.

### The `app.jsonl` File

All application events, errors, and important information are written to a single log file named `app.jsonl`. This file is the primary source for debugging issues and monitoring the application's health. You can view the contents of this file directly from the **Admin Panel**.

### Configuring the Log Directory

It is essential to configure the location where the `app.jsonl` file is stored, especially in a production environment.

*   **Configuration**: Set the `APP_LOG_DIR` variable in your `.env` file.
*   **Example**:
    ```
    APP_LOG_DIR=/var/logs/atlas-ui
    ```
*   **Default**: If this variable is not set, the application will attempt to create a `logs` directory in the project's root, which may not be desirable or possible in a production deployment. Ensure the specified directory exists and the application has the necessary permissions to write to it.

## LLM Configuration (`llmconfig.yml`)

The `llmconfig.yml` file is where you define all the Large Language Models that the application can use. The application uses the `LiteLLM` library, which allows it to connect to a wide variety of LLM providers.

*   **Location**: The default configuration is at `config/defaults/llmconfig.yml`. You should place your instance-specific configuration in `config/overrides/llmconfig.yml`.

### Comprehensive Example

Here is an example of a model configuration that uses all available options.

```yaml
models:
  MyCustomGPT:
    model_name: openai/gpt-4-turbo-preview
    model_url: https://api.openai.com/v1/chat/completions
    api_key: "${OPENAI_API_KEY}"
    description: "The latest and most capable model from OpenAI."
    max_tokens: 8000
    temperature: 0.7
    extra_headers:
      "x-my-custom-header": "value"
    compliance_level: "External"

  OpenRouterLlama:
    model_name: meta-llama/llama-3-70b-instruct
    model_url: https://openrouter.ai/api/v1
    api_key: "${OPENROUTER_API_KEY}"
    description: "Llama 3 70B via OpenRouter"
    max_tokens: 4096
    temperature: 0.7
    extra_headers:
      "HTTP-Referer": "${OPENROUTER_SITE_URL}"
      "X-Title": "${OPENROUTER_SITE_NAME}"
    compliance_level: "External"
```

**Note**: The second example demonstrates environment variable expansion in `extra_headers`, which is useful for services like OpenRouter that require site identification headers.

### Environment Variable Expansion in LLM Configs

Similar to MCP server authentication, LLM configurations support environment variable expansion for API keys and header values. This feature provides security and flexibility in managing sensitive credentials.

#### Security Best Practice

**Never store API keys directly in configuration files.** Instead, use environment variable substitution:

```yaml
models:
  my-openai-model:
    model_name: openai/gpt-4
    model_url: https://api.openai.com/v1
    api_key: "${OPENAI_API_KEY}"
    extra_headers:
      "X-Custom-Header": "${MY_CUSTOM_HEADER_VALUE}"
```

Then set the environment variables:
```bash
export OPENAI_API_KEY="sk-your-secret-api-key"
export MY_CUSTOM_HEADER_VALUE="your-custom-value"
```

#### How It Works

1. **API Key Expansion**: The `api_key` value is processed at runtime. If it contains the `${VAR_NAME}` pattern, it's replaced with the value of the environment variable `VAR_NAME`.
2. **Extra Headers Expansion**: Each value in the `extra_headers` dictionary is also processed for environment variable expansion, allowing you to use dynamic values for headers like `HTTP-Referer` or `X-Title`.
3. **Error Handling**: If a required environment variable is missing, the application will raise a clear error message indicating which variable needs to be set. This prevents silent failures where unexpanded variables might be sent to the API provider.
4. **Literal Values**: You can still use literal string values without environment variables for development or testing purposes (though not recommended for production).

#### Common Use Cases

**OpenRouter Configuration:**
```yaml
models:
  openrouter-claude:
    model_name: anthropic/claude-3-opus
    model_url: https://openrouter.ai/api/v1
    api_key: "${OPENROUTER_API_KEY}"
    extra_headers:
      "HTTP-Referer": "${OPENROUTER_SITE_URL}"
      "X-Title": "${OPENROUTER_SITE_NAME}"
```

**Custom LLM Provider with Authentication Headers:**
```yaml
models:
  custom-provider:
    model_name: custom/model-name
    model_url: https://custom-llm.example.com/v1
    api_key: "${CUSTOM_PROVIDER_API_KEY}"
    extra_headers:
      "X-Tenant-ID": "${TENANT_IDENTIFIER}"
      "X-Region": "${DEPLOYMENT_REGION}"
```

#### Security Considerations

- **Recommended**: Use environment variables for all production API keys and sensitive header values
- **Alternative**: For development/testing, you can use direct string values (not recommended for production)
- **Never**: Commit API keys to `config/defaults/llmconfig.yml` or any version-controlled files

This environment variable expansion system works identically to the MCP server `auth_token` field, providing consistent behavior across all authentication and configuration mechanisms in the application.

### Configuration Fields Explained

*   **`model_name`**: (string) The identifier for the model that will be sent to the LLM provider. For `LiteLLM`, you often need to prefix this with the provider name (e.g., `openai/`, `anthropic/`).
*   **`model_url`**: (string) The API endpoint for the model.
*   **`api_key`**: (string) The API key for authenticating with the model's provider. **Security Best Practice**: Use environment variable substitution with the `${VAR_NAME}` syntax (e.g., `"${OPENAI_API_KEY}"`). The application will automatically expand these variables at runtime and provide clear error messages if a required variable is not set. This works identically to the `auth_token` field in MCP server configurations. You can also use literal API key values for development/testing (not recommended for production).
*   **`description`**: (string) A short description of the model that will be shown to users in the model selection dropdown.
*   **`max_tokens`**: (integer) The maximum number of tokens to generate in a response.
*   **`temperature`**: (float) A value between 0.0 and 1.0 that controls the creativity of the model's responses. Higher values are more creative.
*   **`extra_headers`**: (dictionary) A set of custom HTTP headers to include in the request, which is useful for some proxy services or custom providers. **Environment Variable Support**: Header values can also use the `${VAR_NAME}` syntax for environment variable expansion. This is particularly useful for services like OpenRouter that require headers like `HTTP-Referer` and `X-Title`. If an environment variable is missing, the application will raise a clear error message.
*   **`compliance_level`**: (string) The security compliance level of this model (e.g., "Public", "Internal"). This is used to filter which models can be used in certain compliance contexts.
