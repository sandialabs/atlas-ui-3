# Authentication & Authorization

Last updated: 2026-01-23

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

**Security Note:** The WebSocket endpoint validates authentication **before** accepting the connection. This prevents unauthenticated users from establishing a connection that could receive error messages or timing information.

**Frontend Behavior on Authentication Failure:**
- If the `/api/config` endpoint returns an error (e.g., 401), the UI displays "Chat UI (Unauthenticated)" with user shown as "Unauthenticated"
- If the WebSocket connection is rejected with code 1008, the connection status displays the authentication error reason

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

## Proxy Secret Authentication (Optional Security Layer)

For additional security, you can configure the application to require a secret value in a specific header to validate that requests are coming from your trusted reverse proxy. This prevents direct access to the backend application, even if it's accidentally exposed.

**When to Use Proxy Secret Authentication:**
- When you want an additional layer of security beyond network isolation
- To prevent unauthorized access if the backend accidentally becomes publicly accessible
- To ensure requests only come from your approved reverse proxy

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
In production mode (`DEBUG_MODE=false`), the application logs security warnings at startup if:
- `FEATURE_PROXY_SECRET_ENABLED=false` - warns that proxy secret validation is disabled
- `FEATURE_PROXY_SECRET_ENABLED=true` but `PROXY_SECRET` is empty - warns that authentication will fail

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

When using the mock implementation (no external endpoint configured), **all users are treated as part of the `users` group by default**. This ensures that basic, non-privileged features remain available even without an authorization service. Higher-privilege groups such as `admin` still require explicit membership via the mock group table or your real authorization system.

### Legacy Method: Modifying the Code

For advanced use cases, you can still directly modify the `is_user_in_group` function located in `atlas/core/auth.py`. The default implementation is a mock and **must be replaced** if you are not using the HTTP endpoint method.
