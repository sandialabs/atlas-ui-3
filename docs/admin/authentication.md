# Authentication & Authorization

Last updated: 2026-04-12

The application is designed with the expectation that it operates behind a reverse proxy in a production environment. It does **not** handle user authentication (i.e., logging users in) by itself. Instead, it trusts a header that is injected by an upstream authentication service.

## Production Authentication Flow

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

If using AWS Application Load Balancer (ALB) as the Auth Service, the following authentication configuration should be used:

```
    AUTH_USER_HEADER=x-amzn-oidc-data
    AUTH_USER_HEADER_TYPE=aws-alb-jwt
    AUTH_AWS_EXPECTED_ALB_ARN=arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/your-alb-name/...
    AUTH_AWS_REGION=us-east-1
```

This configuration will decode the base64-encoded JWT passed in the x-amzn-oidc-data header, validate it, and extract the user's email address from the validated JWT.

## Development Behavior

In a local development environment (when `DEBUG_MODE=true` in the `.env` file), the system falls back to using a default `test@test.com` user if the configured authentication header is not present.

**Production Mode (`DEBUG_MODE=false`):** HTTP routes raise HTTP 401 ("Not authenticated: missing user identity") if `user_email` is not set on the request state. There is no fallback to a default user — requests that bypass auth middleware are rejected.

## WebSocket Authentication

WebSocket connections follow the same authentication model as HTTP requests:

**Production Mode (`DEBUG_MODE=false`):**
- WebSocket connections **require** the configured auth header (e.g., `X-User-Email`)
- Connections without a valid auth header are **rejected before accepting** with a 1008 (Policy Violation) close code
- Query parameter authentication (`/ws?user=...`) is **disabled** in production
- Test user fallback is **disabled** in production

**Debug Mode (`DEBUG_MODE=true`):**
- Primary: Uses configured auth header if present
- Fallback 1: Uses `?user=` query parameter if no header
- Fallback 2: Uses `TEST_USER` from config (default: `test@test.com`)
- Mock admin authorization uses `ADMIN_TEST_USER` (default: `admin@example.com`)

**Security Note:** The WebSocket endpoint validates authentication **before** accepting the connection. This prevents unauthenticated users from establishing a connection that could receive error messages or timing information.

**Frontend Behavior on Authentication Failure:**
- If the `/api/config` endpoint returns an error (e.g., 401), the UI displays "Chat UI (Unauthenticated)" with user shown as "Unauthenticated"
- If the WebSocket connection is rejected with code 1008, the connection status displays the authentication error reason

### WebSocket Origin Validation

A WebSocket upgrade is not covered by a CORS preflight, so the same-origin
policy does not protect `/ws` the way it protects `fetch`. Without an explicit
check, any page a logged-in user visits could open a socket to Atlas; the
browser would attach their session cookies, the reverse proxy would
authenticate the upgrade on their behalf, and the attacker's page would hold a
live session able to read conversations and call tools as that user. This is
called cross-site WebSocket hijacking.

Atlas therefore validates the `Origin` header before accepting a chat
WebSocket. An upgrade is allowed when the origin is:

- **loopback** (`localhost`, `127.0.0.1`, `::1`), or
- **the same host the request was addressed to**, compared against the `Host`
  header and ignoring port, or
- **listed in `WEBSOCKET_ALLOWED_ORIGINS`** (comma-separated hostnames).

Anything else is rejected with close code 1008 before authentication runs.

A request with **no** `Origin` header is allowed. Browsers always send the
header on an upgrade, so its absence means a non-browser client — a CLI, a
test harness, a service integration — and those carry no ambient cookies for
an attacker page to borrow.

| Variable | Default | Purpose |
|----------|---------|---------|
| `FEATURE_WEBSOCKET_ORIGIN_CHECK_ENABLED` | `true` | Master switch for the check |
| `WEBSOCKET_ALLOWED_ORIGINS` | *(empty)* | Extra hostnames to accept |

**A normal deployment needs no configuration here.** The same-origin rule
covers the case where the browser and the backend agree on the hostname. Set
`WEBSOCKET_ALLOWED_ORIGINS` only if your proxy rewrites `Host` to an internal
name (for example `proxy_set_header Host backend.internal`), so that the
browser's origin no longer matches what the backend sees. Disabling the check
entirely re-opens the hijacking path and should be a last resort.

The Agent Portal stream socket has its own, stricter allowlist
(`AGENT_PORTAL_ALLOWED_ORIGINS`) — it does not consult `Host` and does not
admit a missing `Origin`. See [the Agent Portal threat model](../agentportal/threat-model.md).

## Configuring the Authentication Header

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

## Proxy Secret Authentication (Enabled by Default)

The application requires a secret value in a specific header to validate that requests are coming from your trusted reverse proxy. This prevents direct access to the backend application from spoofing the authentication header (e.g., `X-User-Email`).

**Proxy secret validation is enabled by default.** In production mode (`DEBUG_MODE=false`), if `PROXY_SECRET` is not configured, all requests are rejected with HTTP 503 (fail-closed). This ensures the backend cannot accidentally be exposed without proxy secret protection.

**When to Disable Proxy Secret Authentication:**
- When the backend is guaranteed to be network-isolated (not reachable from untrusted networks)
- Set `FEATURE_PROXY_SECRET_ENABLED=false` explicitly in `.env`

**Configuration:**

Add the following to your `.env` file:

```bash
# Enable proxy secret validation
FEATURE_PROXY_SECRET_ENABLED=true

# Header name for the proxy secret (default: X-Proxy-Secret)
PROXY_SECRET_HEADER=X-Proxy-Secret

# The actual secret value - use a strong, randomly generated value
PROXY_SECRET=your-secure-random-secret-here

# Optional: Customize the redirect URL for failed authentication (default: /auth)
AUTH_REDIRECT_URL=/auth
```

**Reverse Proxy Configuration:**

Configure your reverse proxy to inject the secret header with every request. Examples:

**NGINX:**
```nginx
location / {
    proxy_pass http://backend:8000;
    proxy_set_header X-Proxy-Secret "your-secure-random-secret-here";
    proxy_set_header X-User-Email $remote_user;
    # ... other headers
}
```

**Apache:**
```apache
<Location />
    RequestHeader set X-Proxy-Secret "your-secure-random-secret-here"
    RequestHeader set X-User-Email %{REMOTE_USER}e
    ProxyPass http://backend:8000/
    ProxyPassReverse http://backend:8000/
</Location>
```

**Behavior:**
- When enabled, the middleware validates the proxy secret on every request (except static files and the auth endpoint)
- If the secret is missing or incorrect:
  - **API endpoints** (`/api/*`): Return 401 Unauthorized
  - **Browser endpoints**: Redirect to the configured auth URL
- **Debug mode** (`DEBUG_MODE=true`): Proxy secret validation is automatically disabled for local development

**Security Best Practices:**
- Generate a strong, random secret (e.g., 32+ characters)
- Store the secret securely in environment variables, not in configuration files
- Use different secrets for different environments (dev, staging, production)
- Rotate the secret periodically as part of your security policy
- Never commit the secret to version control

**Startup Warnings:**
In production mode (`DEBUG_MODE=false`), the application logs security messages at startup if:
- `FEATURE_PROXY_SECRET_ENABLED=false` - warns that proxy secret validation is disabled and the auth header can be spoofed via direct access
- `FEATURE_PROXY_SECRET_ENABLED=true` but `PROXY_SECRET` is empty - logs an error that all requests will be rejected (fail-closed)

## Customizing Authorization

**IMPORTANT: For production deployments, configuring authorization is essential.** The default implementation is a mock and **must be replaced** with your organization's actual authorization system. You have two primary methods to achieve this:

### Recommended Method: HTTP Endpoint

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

If `AUTH_GROUP_CHECK_URL` is not set, the application will fall back to the mock implementation in `atlas/core/auth.py`.

When using the mock implementation (no external endpoint configured), **all users are treated as part of the `users` group by default**. This ensures that basic, non-privileged features remain available even without an authorization service. Higher-privilege groups such as `admin` require explicit membership via your real authorization system. The mock group table (which grants admin access to the configured test user) is **only active when `DEBUG_MODE=true`**. In production mode, no admin privileges are granted via the mock — only the default `users` group is available.

### Local Setup Shortcut: `SKIP_AUTHORIZATION_CHECKS`

By default, even in debug mode, the mock authorization table only grants admin access to two hardcoded identities (`ADMIN_TEST_USER`, default `admin@example.com`, and `test@test.com`). A new contributor running locally with their real email would normally need to set `ADMIN_TEST_USER` to match it before reaching admin-gated routes.

Setting `SKIP_AUTHORIZATION_CHECKS=true` skips that step: every authorized-group check (`is_user_in_group`) returns `True` for every user, so any locally authenticated user has full access, including admin. It does **not** affect authentication — you still need a valid identity (real header, or the `DEBUG_MODE` test-user fallback described above). Note that in debug mode a request with no auth header is assigned the configured `test_user` identity, so with this flag on a headerless request is effectively an administrator.

**Blast radius.** The bypass is not limited to admin pages — `is_user_in_group` is the single authorization gate for every group-restricted surface in the app, so enabling it unlocks:

- **Admin routes** (the `/admin/*` config and log endpoints).
- **Group-restricted models** — any model in `llmconfig.yml` whose `required_groups` lists a non-`users` group becomes available to every caller (`atlas/core/model_access.py`).
- **Restricted MCP servers** — any MCP server gated by `required_groups` becomes reachable (`mcp_execution.py`), including advanced tool servers that would otherwise require an elevated group.
- **Feedback/capture routes** and any other endpoint that gates on group membership.

Each request that is granted by the bypass also emits a `logger.warning` at the point of the check (`atlas/core/auth.py`), so the audit trail can distinguish a bypass-granted admin action from one that passed a real group check.

Guardrails:
- Only takes effect when `DEBUG_MODE=true`. The application **refuses to start** if `SKIP_AUTHORIZATION_CHECKS=true` and `DEBUG_MODE=false`.
- Refuses to start if `ENVIRONMENT=production`, even when `DEBUG_MODE=true` -- the bypass is a development-environment convenience only.
- Mutually exclusive with `AUTH_GROUP_CHECK_URL`: the app refuses to start if both are set, so the bypass can only ever override the mock group table, never a configured external authorization service.
- Defaults to `false` — strictly opt-in.
- Logs a startup warning whenever it's active, plus a per-request warning at the bypass point.
- **Never enable this in production.** It grants every group-restricted surface — admin routes, restricted models, restricted MCP servers, and feedback routes — to every request.

### Legacy Method: Modifying the Code

For advanced use cases, you can still directly modify the `is_user_in_group` function located in `atlas/core/auth.py`. The default implementation is a mock and **must be replaced** if you are not using the HTTP endpoint method.
