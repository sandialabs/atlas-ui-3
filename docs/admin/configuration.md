# Configuration Architecture

Last updated: 2026-01-19

The application uses a layered configuration system that loads settings from three primary sources in the following order of precedence:

1.  **Environment Variables (`.env`)**: Highest priority. These override any settings from files.
2.  **Override Files (`config/overrides/`)**: For custom, instance-specific configurations. These files are not checked into version control.
3.  **Default Files (`config/defaults/`)**: The base configuration that is part of the repository.

**Note**: The definitive source for all possible configuration options and their default values is the `AppSettings` class within `backend/modules/config/config_manager.py`. This class dictates how the application reads and interprets all its settings.

## Key Override Files

To customize your instance, you will place your own versions of the configuration files in the `config/overrides/` directory. The most common files to override are:

*   **`mcp.json`**: Registers and configures the MCP (tool) servers that provide capabilities to the LLM.
*   **`llmconfig.yml`**: Defines the list of available Large Language Models and their connection details.
*   **`compliance-levels.json`**: Defines the security compliance levels (e.g., Public, Internal, HIPAA) and the rules for how they can interact.
*   **`help-config.json`**: Populates the content of the "Help" modal in the user interface.
*   **`splash-config.json`**: Configures the startup splash screen for displaying policies and information to users.
*   **`messages.txt`**: Defines the text for system-wide banner messages that can be displayed to all users.

## The `.env` File

This file is crucial for setting up your instance. Start by copying the example file:

```bash
cp .env.example .env
```

Key settings in the `.env` file include:

*   **API Keys**: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.
*   **Authentication Header**: `AUTH_USER_HEADER` configures the HTTP header name used to extract the authenticated username from your reverse proxy (default: `X-User-Email`).
*   **Feature Flags**: Enable or disable major features like `FEATURE_AGENT_MODE_AVAILABLE`.
*   **Branding Flags**: Control frontend branding such as `VITE_APP_NAME` and the optional `VITE_FEATURE_POWERED_BY_ATLAS` badge on the welcome screen.
*   **S3 Connection**: Configure the connection to your S3-compatible storage. For local testing, you can set `USE_MOCK_S3=true` to use an in-memory mock instead of a real S3 bucket. **This mock must never be used in production.**
*   **Log Level**: The `LOG_LEVEL` variable controls logging verbosity and whether sensitive data (user input/output) is logged. Set to `INFO` for production to avoid logging sensitive content, or `DEBUG` for development/testing. See [Logging and Monitoring](logging-monitoring.md) for details.
*   **Log Directory**: The `APP_LOG_DIR` variable points to the folder where the application log file (`app.jsonl`) will be stored. This path must be updated to a valid directory in your deployment environment.
*   **Security Headers**: Configure Content Security Policy (CSP) and other security headers. See the Security Configuration section below for details.

### MCP Auto-Reconnect Settings

Atlas UI can automatically retry failed MCP server connections using exponential backoff. This is controlled by environment variables in `.env`.

```bash
# Enable automatic reconnection for failed MCP servers (default: false)
FEATURE_MCP_AUTO_RECONNECT_ENABLED=false

# Base interval in seconds between reconnect attempts (default: 60)
MCP_RECONNECT_INTERVAL=60

# Maximum interval in seconds between reconnect attempts (caps exponential backoff, default: 300)
MCP_RECONNECT_MAX_INTERVAL=300

# Multiplier for exponential backoff (default: 2.0)
MCP_RECONNECT_BACKOFF_MULTIPLIER=2.0
```

When `FEATURE_MCP_AUTO_RECONNECT_ENABLED=true`, the backend starts a background task that periodically retries connections for servers that previously failed to initialize.

- The effective delay after the *n*-th failure is:

	$$\text{delay} = \min(\text{MCP\_RECONNECT\_INTERVAL} \times \text{MCP\_RECONNECT\_BACKOFF\_MULTIPLIER}^{(n-1)},\ \text{MCP\_RECONNECT\_MAX\_INTERVAL})$$

- This avoids hammering flaky or down MCP servers while still ensuring they are retried over time.
- You can monitor this behavior via `GET /admin/mcp/status`, which reports per-server backoff details and whether the auto-reconnect loop is currently running.

## Security Configuration (CSP and Headers)

The application includes security headers middleware that sets browser security policies. These are configured via environment variables in `.env`.

### Content Security Policy (CSP)

The `SECURITY_CSP_VALUE` environment variable controls the Content Security Policy header, which restricts what resources the browser can load. This is critical for preventing XSS attacks.

**Default Configuration:**
```bash
SECURITY_CSP_VALUE="default-src 'self'; img-src 'self' data: blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-src 'self' blob: data:; frame-ancestors 'self'"
```

**Key Directives:**
- `default-src 'self'` - Only allow resources from the same origin by default
- `img-src 'self' data: blob:` - Allow images from same origin, data URIs, and blob URLs
- `script-src 'self'` - Only allow JavaScript from same origin
- `style-src 'self' 'unsafe-inline'` - Allow CSS from same origin and inline styles
- `frame-src 'self' blob: data:` - Allow iframes from same origin, blob, and data URIs
- `frame-ancestors 'self'` - Prevent the app from being embedded in external iframes

### Allowing External Iframes

**IMPORTANT:** If your MCP tools need to display external content using iframes (dashboards, visualizations, web applications), you MUST add those domains to the `frame-src` directive.

**Example - Allow specific external domains:**
```bash
SECURITY_CSP_VALUE="default-src 'self'; img-src 'self' data: blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-src 'self' blob: data: https://dashboard.example.com https://analytics.corp.com https://www.sandia.gov/; frame-ancestors 'self'"
```

**Security Considerations:**
- Only add domains you trust and control
- Be specific with full URLs (include `https://` and trailing path if needed)
- Wildcard subdomains (`https://*.example.com`) are supported but less secure
- Document which MCP servers require which domains in your `mcp.json` descriptions

**Troubleshooting:** If iframes appear blank or don't load, check your browser's console for CSP violation errors. The error message will tell you which domain needs to be added to `frame-src`.

### Other Security Headers

Additional security headers can be configured in `.env`:

```bash
# Enable/disable specific headers (default: true)
SECURITY_CSP_ENABLED=true
SECURITY_XFO_ENABLED=true
SECURITY_NOSNIFF_ENABLED=true
SECURITY_REFERRER_POLICY_ENABLED=true

# Header values
SECURITY_XFO_VALUE=SAMEORIGIN
SECURITY_REFERRER_POLICY_VALUE=no-referrer
```

For more details on security headers implementation, see `backend/core/security_headers_middleware.py`.
