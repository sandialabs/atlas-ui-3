# Configuration Architecture

Last updated: 2026-09-04

The application uses a layered configuration system that loads settings from two primary sources in the following order of precedence:

1.  **Environment Variables (`.env`)**: Highest priority. These override any settings from files.
2.  **User Config (`config/`)**: For custom, instance-specific configurations. Created by `atlas-init` and not checked into version control. Overrides package defaults.
3.  **Package Defaults (`atlas/config/`)**: The base configuration shipped with the package.

**Note**: The definitive source for all possible configuration options and their default values is the `AppSettings` class within `atlas/modules/config/config_manager.py`. This class dictates how the application reads and interprets all its settings.

## Key Config Files

To customize your instance, place your own versions of the configuration files in the `config/` directory (created by `atlas-init`). User config in `config/` overrides package defaults in `atlas/config/`. The most common files to customize are:

*   **`mcp.json`**: Registers and configures the MCP (tool) servers that provide capabilities to the LLM.
*   **`llmconfig.yml`**: Defines the list of available Large Language Models and their connection details.
*   **`compliance-levels.json`**: Defines the security compliance levels (e.g., Public, Internal, HIPAA) and the rules for how they can interact.
*   **`help.md`**: Populates the content of the "Help" page in the user interface (Markdown).
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
*   **Tool Approval Audit**: `TOOL_CALL_AUDIT_PATH` sets the append-only JSONL file for tool-approval decisions (default `data/tool_call_audit.jsonl`). See [Tool Approval System](tool-approval.md#decision-audit-trail).
*   **Security Headers**: Configure Content Security Policy (CSP) and other security headers. See the Security Configuration section below for details.
*   **RAG**: Enable RAG using `FEATURE_RAG_ENABLED=true` and configure sources in `rag-sources.json`. When disabled, the backend does not initialize RAG services and does not load `rag-sources.json`. RAG discovery is best-effort: a single failing source will not block others. See [RAG Configuration](external-rag-api.md) for details.

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

# Timeout in seconds for MCP discovery calls - list_tools, list_prompts (default: 30)
MCP_DISCOVERY_TIMEOUT=30

# Timeout in seconds for MCP tool calls (default: 120)
MCP_CALL_TIMEOUT=120
```

When `FEATURE_MCP_AUTO_RECONNECT_ENABLED=true`, the backend starts a background task that periodically retries connections for servers that previously failed to initialize.

- The effective delay after the *n*-th failure is:

	$$\text{delay} = \min(\text{MCP\_RECONNECT\_INTERVAL} \times \text{MCP\_RECONNECT\_BACKOFF\_MULTIPLIER}^{(n-1)},\ \text{MCP\_RECONNECT\_MAX\_INTERVAL})$$

- This avoids hammering flaky or down MCP servers while still ensuring they are retried over time.
- You can monitor this behavior via `GET /admin/mcp/status`, which reports per-server backoff details and whether the auto-reconnect loop is currently running.

### Agent Sleep Tool

Agent mode has a built-in `atlas_sleep` tool (part of the built-in `atlas` server, alongside
`atlas_canvas`, `atlas_search` and `atlas_discover_sources`) that pauses a turn so the model can wait for
long-running external work (a simulation, a submitted job) before checking on it again. It runs
in process rather than through an MCP server, so `MCP_CALL_TIMEOUT` does not apply to it.
The pre-consolidation name `atlas_agent_sleep` is still accepted (see issue #855).

```bash
# Maximum seconds a single atlas_sleep call may wait (default: 7200 = 2 hours).
# Requests above this are shortened to it; 0 disables the tool entirely.
AGENT_SLEEP_MAX_SECONDS=7200

# Maximum seconds one turn may spend sleeping across all calls (default: 7200).
# The per-call cap alone bounds nothing, because the model may call the tool again
# on every step; this is the limit that decides how long a turn can hold resources.
AGENT_SLEEP_MAX_TURN_SECONDS=7200
```

- **Kill switch**: `AGENT_SLEEP_MAX_SECONDS=0` removes the tool from the tools panel, from tool
  authorization, and from the schema sent to the model, and refuses it at execution.
- **Step budget**: a sleep consumes one agent step, so `AGENT_MAX_STEPS` also bounds how many
  times a turn can wait. Clients cannot request more steps than `AGENT_MAX_STEPS`.
- **Stopping a run** cancels an in-flight sleep immediately.
- **Reverse proxies**: a turn holds its WebSocket open for the whole wait and sends nothing
  during it. If your proxy has an idle timeout shorter than the waits you allow, it will drop
  the connection before the sleep returns - either raise the proxy timeout or lower these caps
  to fit it.
- **Deploys**: a turn parked in a long sleep delays graceful shutdown; expect such turns to be
  killed by a rolling restart.

## System Prompt Time Injection (issue #823)

The current date/time is appended to the rendered system prompt on every turn so the model
knows what "now" is. When a meaningful gap has opened between turns, an explicit
"approximately N minutes have elapsed since your previous prompt" note is appended too, so
the model can reason about long pauses (a status may have resolved, a deadline may have
passed). The gap is measured from the previous *user* message, so the figure reflects the
time since the user last wrote, not the wall-clock between two assistant turns. This
enrichment is applied to both the default system prompt and a user-supplied custom system
prompt; it is runtime addition, not part of the prompt template, so existing templates are
unchanged.

```bash
# IANA timezone name for the displayed time (default UTC). Unknown names fall
# back to UTC. No per-user timezone plumbing is required.
SYSTEM_PROMPT_TIMEZONE=UTC
# Append the elapsed-time note when the gap between this turn and the previous
# user message meets/exceeds this many minutes (default 5; 0 disables the note
# but still injects the current date/time).
SYSTEM_PROMPT_TIME_REFRESH_MINUTES=5
```

- **Always on**: the current date/time is injected on every turn regardless of the refresh
  setting; the refresh only gates the *elapsed-time note*.
- **No session state**: the gap is derived from the conversation history's existing message
  timestamps (which the conversation loader preserves across save/reload), so no extra
  per-session tracking is required.
- **Custom prompts**: a custom system prompt still fully replaces the default *template*
  content (issue #153); the time/elapsed lines are appended after the custom text.

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

For more details on security headers implementation, see `atlas/core/security_headers_middleware.py`.
