# Reverse Proxy Configuration Examples

Last updated: 2026-01-19

This document provides secure configuration examples for deploying Atlas UI 3 behind a reverse proxy with proper authentication header handling.

## Critical Security Requirement

**The reverse proxy MUST strip client-provided authentication headers before adding its own.**

Without this, attackers can inject headers like `X-User-Email: admin@company.com` and bypass authentication even when the proxy is properly configured.

## Nginx Configuration

### Secure Example (RECOMMENDED ✅)

```nginx
# Atlas UI 3 - Secure Configuration
upstream atlas_backend {
    server main-app:8000;
}

upstream auth_service {
    server auth-service:8001;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /etc/ssl/certs/your-domain.crt;
    ssl_certificate_key /etc/ssl/private/your-domain.key;

    # WebSocket endpoint with authentication
    location /ws {
        # STEP 1: Authenticate via auth service
        auth_request /auth/validate;
        auth_request_set $authenticated_user $upstream_http_x_user_email;

        # STEP 2: CRITICAL - Strip any X-User-Email headers from client
        # This prevents header injection attacks
        proxy_set_header X-User-Email "";

        # STEP 3: Set X-User-Email from authenticated user only
        proxy_set_header X-User-Email $authenticated_user;

        # Standard WebSocket proxy settings
        proxy_pass http://atlas_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeouts for long-lived connections
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    # HTTP API endpoints with authentication
    location /api/ {
        auth_request /auth/validate;
        auth_request_set $authenticated_user $upstream_http_x_user_email;

        # CRITICAL: Strip client headers before adding authenticated header
        proxy_set_header X-User-Email "";
        proxy_set_header X-User-Email $authenticated_user;

        proxy_pass http://atlas_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Admin endpoints with authentication
    location /admin/ {
        auth_request /auth/validate;
        auth_request_set $authenticated_user $upstream_http_x_user_email;

        proxy_set_header X-User-Email "";
        proxy_set_header X-User-Email $authenticated_user;

        proxy_pass http://atlas_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Static files (no auth required)
    location / {
        proxy_pass http://atlas_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Internal auth validation endpoint (not exposed to clients)
    location = /auth/validate {
        internal;
        proxy_pass http://auth_service/validate;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header X-Original-URI $request_uri;
        proxy_set_header Cookie $http_cookie;
        proxy_set_header Authorization $http_authorization;
    }
}
```

### Vulnerable Example (DO NOT USE ❌)

```nginx
# VULNERABLE: Missing header stripping!
location /ws {
    auth_request /auth/validate;
    auth_request_set $authenticated_user $upstream_http_x_user_email;

    # DANGER: Only adds header without clearing client's version
    # If client sends X-User-Email: attacker@evil.com, BOTH headers arrive!
    proxy_set_header X-User-Email $authenticated_user;  # ❌ INSECURE

    proxy_pass http://atlas_backend;
    # ... rest of config
}
```

**Why this is vulnerable:**
1. Client sends: `X-User-Email: admin@evil.com`
2. Proxy adds: `X-User-Email: realuser@example.com`
3. Backend receives BOTH headers
4. `request.headers.get('X-User-Email')` returns the FIRST one (attacker's!)

## Apache Configuration

### Secure Example (RECOMMENDED ✅)

```apache
<VirtualHost *:443>
    ServerName your-domain.com

    SSLEngine on
    SSLCertificateFile /etc/ssl/certs/your-domain.crt
    SSLCertificateKeyFile /etc/ssl/private/your-domain.key

    # Enable WebSocket proxying
    RewriteEngine On
    RewriteCond %{HTTP:Upgrade} =websocket [NC]
    RewriteRule ^/ws(.*)$ ws://main-app:8000/ws$1 [P,L]

    # WebSocket location
    <Location /ws>
        # Authenticate via external service
        AuthType Bearer
        AuthName "Atlas UI Authentication"
        AuthBasicProvider external-auth-provider
        Require valid-user

        # CRITICAL: Remove client-provided header
        RequestHeader unset X-User-Email

        # Set header from authenticated user
        RequestHeader set X-User-Email %{REMOTE_USER}e

        ProxyPass http://main-app:8000/ws
        ProxyPassReverse http://main-app:8000/ws

        # WebSocket settings
        ProxyPreserveHost On
        ProxyTimeout 3600
    </Location>

    # API endpoints
    <Location /api/>
        AuthType Bearer
        AuthName "Atlas UI Authentication"
        AuthBasicProvider external-auth-provider
        Require valid-user

        RequestHeader unset X-User-Email
        RequestHeader set X-User-Email %{REMOTE_USER}e

        ProxyPass http://main-app:8000/api/
        ProxyPassReverse http://main-app:8000/api/
    </Location>

    # Static files (no auth)
    <Location />
        ProxyPass http://main-app:8000/
        ProxyPassReverse http://main-app:8000/
    </Location>
</VirtualHost>
```

## Testing Header Injection Prevention

### Test 1: Verify Header Stripping

This test verifies that client-provided headers are stripped:

```bash
# Try to inject a malicious header through the proxy
curl -i \
  -H "X-User-Email: attacker@evil.com" \
  -H "Cookie: valid_session_token_here" \
  https://your-domain.com/api/config

# Expected: Should work, but backend receives legitimate user from auth
# Check logs to confirm backend saw the REAL user, not "attacker@evil.com"
```

### Test 2: Verify Direct Access is Blocked

```bash
# Try to connect directly to the backend (bypassing proxy)
curl -i \
  -H "X-User-Email: admin@company.com" \
  http://main-app:8000/api/config

# Expected: Connection refused or timeout (network isolation)
```

### Test 3: WebSocket Header Injection Test

```python
import websocket
import json

# Try to inject header during WebSocket handshake
ws = websocket.WebSocket()
ws.connect(
    "wss://your-domain.com/ws",
    header=["X-User-Email: attacker@evil.com"],
    cookie="valid_session_token_here"
)

# Send a message that reveals the user
ws.send(json.dumps({"type": "chat", "content": "Who am I?"}))
response = json.loads(ws.recv())

# Check logs: backend should see the REAL user from auth, not the injected one
ws.close()
```

## Deployment Checklist

Before deploying to production, verify:

- [ ] Reverse proxy configuration includes explicit header stripping (`proxy_set_header X-User-Email ""` or `RequestHeader unset X-User-Email`)
- [ ] Auth service properly validates credentials before setting header
- [ ] Direct access to main app is blocked at network level (test with curl)
- [ ] WebSocket connections are properly authenticated during handshake
- [ ] Header injection test confirms client headers are stripped (see Test 1 above)
- [ ] Logs confirm backend receives authenticated user, not client-provided headers
- [ ] SSL/TLS is properly configured with valid certificates
- [ ] WebSocket upgrade is working correctly through the proxy

## Common Mistakes

### 1. Forgetting to Strip Headers
```nginx
# WRONG: Only adding header
proxy_set_header X-User-Email $authenticated_user;

# RIGHT: Strip first, then add
proxy_set_header X-User-Email "";
proxy_set_header X-User-Email $authenticated_user;
```

### 2. Wrong Header Order
```nginx
# WRONG: Set then clear (clears the authenticated header!)
proxy_set_header X-User-Email $authenticated_user;
proxy_set_header X-User-Email "";

# RIGHT: Clear then set
proxy_set_header X-User-Email "";
proxy_set_header X-User-Email $authenticated_user;
```

### 3. Not Testing Header Injection
Always run the header injection tests above to verify your configuration is secure.

### 4. Exposing Backend Ports
```yaml
# WRONG: Exposing backend port publicly
services:
  main-app:
    ports:
      - "8000:8000"  # ❌ Bypasses proxy!

# RIGHT: Only expose internally
services:
  main-app:
    expose:
      - "8000"  # ✅ Internal network only
```

## Additional Resources

- [Nginx ngx_http_auth_request_module](https://nginx.org/en/docs/http/ngx_http_auth_request_module.html)
- [Apache mod_headers](https://httpd.apache.org/docs/current/mod/mod_headers.html)
- [WebSocket RFC 6455](https://tools.ietf.org/html/rfc6455)
- [OWASP Header Injection Prevention](https://cheatsheetseries.owasp.org/cheatsheets/HTTP_Headers_Cheat_Sheet.html)
