# Security Architecture

## Overview

This application is designed to operate as part of a multi-service architecture with defense-in-depth security. Authentication and authorization are handled by external components, not within this application itself.

## Production Architecture

### Component Separation

This application consists of multiple isolated services:

- **Authentication Service**: Handles user authentication, session management, and authorization
- **Main Application**: This codebase (chat UI backend and API)
- **Reverse Proxy**: Edge layer handling TLS termination, routing, and authentication delegation

### Network Topology

```
Internet → Reverse Proxy → Authentication Service
                         → Main Application (this repo)
```

**Critical Security Requirement:**
- Main application MUST NOT be directly accessible from the internet
- All traffic MUST flow through the reverse proxy
- Use network isolation (Docker networks, VPCs, firewalls) to enforce this

## Authentication Flow

### HTTP API Requests

```
1. Client → Reverse Proxy (with credentials)
2. Reverse Proxy → Auth Service (validates credentials)
3. If invalid → Redirect to /login
4. If valid → Auth Service returns user identity
5. Reverse Proxy → Main App (with authenticated user header)
6. Main App processes request for authenticated user
```

### WebSocket Connections

```
1. Client → Reverse Proxy (WebSocket handshake with credentials)
2. Reverse Proxy → Auth Service (validates during handshake)
3. If invalid → Connection rejected (HTTP 401)
4. If valid → Auth Service returns user identity header
5. Reverse Proxy → Main App (with X-Authenticated-User header)
6. Main App accepts WebSocket connection
7. All subsequent messages occur over established connection
```

**Important Differences from HTTP:**
- Authentication occurs ONCE during initial handshake
- WebSocket cannot redirect to /login (not HTTP)
- Client must handle rejection and redirect to login page
- Token expiration requires WebSocket reconnection

## Trust Model

### Header-Based Trust

The main application trusts the `X-Authenticated-User` header because:

1. **Network Isolation**: Main app is not publicly accessible
2. **Single Entry Point**: Only reverse proxy can reach main app
3. **Upstream Validation**: Auth service validates before header is set
4. **No Client Control**: Clients cannot set headers directly on main app

### Why This Looks Insecure

When examining this codebase in isolation, the WebSocket endpoint appears to lack authentication:

```python
user_email = websocket.headers.get('X-Authenticated-User')
```

This is **intentional by design**. The security controls exist in the infrastructure layer, not the application layer.

**This design is secure IF AND ONLY IF:**
- Main app has no direct public access
- Reverse proxy is properly configured
- Network isolation is enforced
- Auth service validates correctly

## Development vs Production

### Development Environment

For local development without the full infrastructure:

```python
# Falls back to query parameter
user_email = websocket.query_params.get('user')
```

**This is INSECURE** and only suitable for local development.

### Production Environment

Production deployments MUST:

1. Deploy reverse proxy with auth delegation
2. Deploy separate authentication service
3. Isolate main app from public access
4. Configure reverse proxy to set X-Authenticated-User header
5. Never expose main app ports publicly

### Example Network Configuration

```yaml
services:
  reverse-proxy:
    ports:
      - "443:443"        # Only component with public port
    networks:
      - frontend

  auth-service:
    expose:
      - "8001"           # Exposed to internal network only
    networks:
      - frontend

  main-app:
    expose:
      - "8000"           # Exposed to internal network only
    networks:
      - frontend
```

## Authentication Service Requirements

The external authentication service must:

1. **Validate credentials** (JWT, session cookies, API keys, etc.)
2. **Extract user identity** from valid credentials
3. **Return user information** in response header
4. **Reject invalid requests** with appropriate HTTP status

### Expected Interface

**Request from Reverse Proxy:**
```http
GET /auth/validate HTTP/1.1
Cookie: session_token=xyz
Authorization: Bearer jwt_token_here
```

**Response if Valid:**
```http
HTTP/1.1 200 OK
X-User-Email: user@example.com
```

**Response if Invalid:**
```http
HTTP/1.1 401 Unauthorized
```

## Custom Authorization Logic

### backend/core/auth.py

This file contains **mock authorization logic** that must be replaced with your organization's custom business logic before production deployment.

**Current Implementation:**

The file provides:
- `is_user_in_group(user_id, group_id)` - Mock group membership checks
- `get_user_from_header(x_email_header)` - Header parsing utility

**Mock Data (Development Only):**

```python
mock_groups = {
    "test@test.com": ["users", "mcp_basic", "admin"],
    "user@example.com": ["users", "mcp_basic"],
    "admin@example.com": ["admin", "users", "mcp_basic", "mcp_advanced"]
}
```

**Production Requirements:**

Replace mock implementation with integration to your authorization system:

- LDAP/Active Directory group lookups
- Database-backed role management
- External authorization service (OAuth scopes, RBAC, ABAC)
- Custom business logic (department-based, hierarchy-based, etc.)

**Example Integration:**

```python
def is_user_in_group(user_id: str, group_id: str) -> bool:
    """Production implementation example."""
    # Option 1: Query your authorization database
    # return db.query_user_groups(user_id).contains(group_id)

    # Option 2: Call external auth service
    # return auth_service.check_permission(user_id, group_id)

    # Option 3: LDAP/AD lookup
    # return ldap_client.is_member(user_id, f"cn={group_id},ou=groups")
```

**Where It's Used:**

This authorization logic controls access to:
- MCP server groups (group-based tool access control)
- Admin endpoints
- Feature flags and capabilities

**Important:** This is **authorization** (what a user can do), separate from **authentication** (who the user is). Authentication is handled by the external auth service, while authorization logic in this file determines permissions for authenticated users.

## Security Considerations

### Token Expiration

Since WebSocket authentication happens only at handshake:

- Long-lived connections won't detect expired tokens
- Implement periodic reconnection or heartbeat
- Client should reconnect before token expiration
- Server can close connections after max lifetime

### Header Injection Prevention

**Risk:** If main app is publicly accessible, attackers can inject headers

**Mitigation:**
- Network isolation (main app not reachable publicly)
- Reverse proxy strips client-provided headers
- Only reverse proxy can set X-Authenticated-User

### Defense in Depth

Additional security layers:

- TLS/SSL for all external connections
- Rate limiting at reverse proxy
- CORS restrictions
- Content Security Policy headers
- Regular security audits
- Monitoring and alerting

## Deployment Checklist

Before deploying to production:

- [ ] Main application is NOT publicly accessible
- [ ] Reverse proxy is configured with auth delegation
- [ ] Authentication service is deployed and tested
- [ ] Network isolation is enforced (firewall rules, VPC, etc.)
- [ ] TLS certificates are valid and renewed
- [ ] WebSocket upgrade is properly proxied
- [ ] X-Authenticated-User header is set by reverse proxy
- [ ] Client-provided headers are stripped
- [ ] Logging and monitoring are configured
- [ ] Token expiration and refresh are tested

## Testing Authentication

### Manual Testing

1. **Test without credentials:**
   ```bash
   curl -i --no-buffer \
     -H "Connection: Upgrade" \
     -H "Upgrade: websocket" \
     http://proxy-url/ws
   # Should return 401
   ```

2. **Test with invalid credentials:**
   ```bash
   curl -i --no-buffer \
     -H "Connection: Upgrade" \
     -H "Upgrade: websocket" \
     -H "Cookie: invalid_token" \
     http://proxy-url/ws
   # Should return 401
   ```

3. **Test direct access (should fail):**
   ```bash
   curl -i --no-buffer \
     -H "Connection: Upgrade" \
     -H "Upgrade: websocket" \
     -H "X-Authenticated-User: attacker@example.com" \
     http://main-app:8000/ws
   # Should NOT be reachable from outside network
   ```

### Automated Testing

Include in CI/CD pipeline:
- Infrastructure validation tests
- Network isolation tests
- Authentication flow tests
- Header injection tests

## References

- OAuth 2.0 and JWT best practices
- WebSocket security considerations
- Reverse proxy security patterns
- Zero-trust architecture principles

## Incident Response

If this application is found to be directly accessible:

1. Immediately block public access via firewall
2. Review access logs for unauthorized access
3. Rotate all tokens and sessions
4. Audit infrastructure configuration
5. Update deployment procedures
