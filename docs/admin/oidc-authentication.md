# OIDC Login, Confidential-Client Authentication, and Delegated Credentials

Last updated: 2026-09-04

Atlas can authenticate users itself as an OpenID Connect relying party, instead
of trusting an identity header set by a reverse proxy. This is an **opt-in
alternative** to the existing trusted-header mode described in
[Authentication & Authorization](authentication.md) -- that mode is unchanged
and remains the default.

The feature is three related capabilities, each independently switchable:

1. **OIDC login** -- Authorization Code flow with PKCE.
2. **Confidential-client authentication** -- Atlas authenticates *itself* to the
   IdP with a client secret or, preferably, a private key assertion.
3. **Delegated downstream authorization** -- RFC 8693 token exchange or
   Microsoft Entra ID On-Behalf-Of, so downstream services (including MCP
   servers) receive a short-lived, audience-specific token rather than the
   user's own.

## Deployment shapes

Atlas supports two parallel deployment models, and this feature adds the second:

| | Trusted-header mode (existing) | OIDC mode (new) |
| --- | --- | --- |
| Who terminates login | Reverse proxy (nginx, ALB) | Atlas |
| How Atlas learns the user | `X-User-Email` header or ALB JWT | Atlas's own session cookie |
| Reverse proxy required | Yes | No (works with or without nginx) |
| Selected by | Default | `FEATURE_OIDC_AUTH_ENABLED=true` |

The two coexist. With OIDC enabled, a request carrying a live Atlas session
authenticates on that basis; anything without one still falls through to the
header path, so a mixed rollout does not break existing traffic.

## Login flow

```
Browser -> Atlas /auth/oidc/login -> IdP (Authorization Code + PKCE)
        -> Atlas /auth/oidc/callback -> Atlas session
```

PKCE (S256) is always used, even though Atlas is a confidential client: it
protects the authorization code independently of client authentication, and
OAuth 2.1 requires it for the code grant. The `state` parameter carries CSRF
protection and the `nonce` binds the ID token to this browser's request; both
are single-use and cleared before the code is redeemed.

**Token material never reaches the browser.** The cookie holds only an opaque
session id; access tokens, refresh tokens, and validated ID token claims live in
a server-side store inside the Atlas credential boundary. Agents and MCP servers
never see them.

### Routes

| Route | Purpose |
| --- | --- |
| `GET /auth/oidc/login` | Start the flow. Optional `?next=/path` returns the user to a same-site path afterwards. |
| `GET /auth/oidc/callback` | Complete the flow and establish the session. |
| `GET /auth/oidc/logout` | Drop the Atlas session, and the IdP session where the provider advertises `end_session_endpoint`. |
| `GET /api/auth/oidc/status` | Whether OIDC is enabled and whether this browser is logged in. Never returns token material. |
| `DELETE /api/auth/oidc/delegated-tokens` | Discard the current user's cached delegated credentials. |

### Configuration

```bash
FEATURE_OIDC_AUTH_ENABLED=true
OIDC_ISSUER=https://idp.example.gov/realms/atlas
OIDC_CLIENT_ID=atlas
OIDC_REDIRECT_URI=https://atlas.example.gov/auth/oidc/callback
OIDC_SESSION_SECRET=<python -c "import secrets; print(secrets.token_urlsafe(32))">
OIDC_SCOPES="openid profile email"
OIDC_USERNAME_CLAIM=email          # falls back to preferred_username, then sub
OIDC_SESSION_MAX_AGE_SECONDS=28800
OIDC_COOKIE_SECURE=                # auto: on when OIDC_REDIRECT_URI is https
```

Endpoints are discovered from `<OIDC_ISSUER>/.well-known/openid-configuration`
and cached for an hour. The document's own `issuer` claim must match the
configured issuer, so a redirect on the discovery URL cannot substitute another
provider's endpoints.

Atlas refuses to enable OIDC login without `OIDC_SESSION_SECRET`, `OIDC_ISSUER`,
and `OIDC_CLIENT_ID`; it logs the reason at startup and falls back to
header-based auth rather than starting a half-configured login flow.

## Confidential-client authentication

Atlas is a server-side application and never behaves as a public client. Pick
one method with `OIDC_CLIENT_AUTH_METHOD`:

| Method | Configuration | Notes |
| --- | --- | --- |
| `client_secret_basic` (default) | `OIDC_CLIENT_SECRET` | Credentials in the HTTP Basic header. |
| `client_secret_post` | `OIDC_CLIENT_SECRET` | Credentials in the form body, for IdPs that require it. |
| `private_key_jwt` | `OIDC_PRIVATE_KEY_PATH`, `OIDC_PRIVATE_KEY_ID`, `OIDC_PRIVATE_KEY_ALGORITHM` | **Preferred.** RFC 7523 signed assertion; no shared secret is transmitted or stored at the IdP. |

```bash
OIDC_CLIENT_AUTH_METHOD=private_key_jwt
OIDC_PRIVATE_KEY_PATH=/etc/atlas/oidc-client-key.pem
OIDC_PRIVATE_KEY_ID=key-1
OIDC_PRIVATE_KEY_ALGORITHM=RS256
```

The assertion's audience is the token endpoint, its lifetime is five minutes,
and each carries a fresh `jti`, so one captured in transit cannot be replayed
against a different endpoint or reused. The private key is read once, cached in
memory, and only ever used to sign; rotate by replacing the file and restarting.

## Delegated downstream authorization

Atlas **never forwards a user's inbound access token to a downstream service.**
When a downstream call needs the user's authority, Atlas exchanges the user's
token for a new one that is short-lived, bound to that specific audience, and
scoped to what the call needs.

```
User -> OIDC login -> Atlas
Atlas -> RFC 8693 Token Exchange -> downstream service
Atlas -> Entra On-Behalf-Of      -> downstream service   (Entra deployments)
```

```bash
FEATURE_OIDC_DELEGATION_ENABLED=true
OIDC_DELEGATION_PROVIDER=token_exchange      # or: entra_obo
# Defaults to the token endpoint discovered from OIDC_ISSUER
OIDC_DELEGATION_TOKEN_ENDPOINT=
OIDC_DELEGATION_MIN_TTL_SECONDS=60
```

The two mechanisms are interchangeable plugins selected by name, so a
deployment picks one by configuration and a third can be added without touching
call sites:

- **`token_exchange`** -- RFC 8693. Sends `grant_type=...token-exchange` with the
  user's token as `subject_token` plus `audience`/`resource` and `scope`.
- **`entra_obo`** -- Microsoft Entra ID. Entra predates RFC 8693 and uses the JWT
  bearer grant with `requested_token_use=on_behalf_of`, expressing the target
  through the scope; a configured audience becomes `<audience>/.default` when no
  explicit scope is given.

Delegated tokens are cached per user, audience, and scope, and re-minted once
fewer than `OIDC_DELEGATION_MIN_TTL_SECONDS` remain. A token whose issuer
advertises no expiry is never cached across calls. Logging out discards the
user's cached delegated tokens along with the session.

### Delegated MCP servers

Give an MCP server `auth_type: "delegated"` in `mcp.json` and Atlas mints its
credential per user rather than asking the user to upload one:

```json
{
  "protected-tools": {
    "url": "https://tools.example.gov/mcp",
    "auth_type": "delegated",
    "delegation": {
      "audience": "api://tools.example.gov",
      "scope": "tools.read tools.invoke"
    }
  }
}
```

`audience` defaults to the server's URL, which is the canonical resource
identifier the MCP authorization specification uses.

**Tool discovery caveat.** Tool discovery runs once at startup with the
process-level client, before any user has logged in, so there is no session to
delegate from. A delegated server that also requires authorization on
`initialize`/`tools/list` will therefore register no tools. Delegated servers
must currently allow unauthenticated discovery and enforce authorization on
tool *invocation*; per-user lazy discovery is the fix and is not in this
change.

If delegation is disabled,
the user has no OIDC session, or the exchange fails, the server simply reports
as unauthenticated -- exactly as an unauthenticated bearer server does today.
A tool call never fails with a delegation stack trace.

## Security notes

- **The proxy secret.** With OIDC enabled, a request backed by a live Atlas
  session skips the `PROXY_SECRET` gate. That gate exists to stop a caller who
  reaches the backend directly from spoofing an identity *header*; an Atlas
  session cannot be spoofed that way, since the cookie is signed and its id only
  resolves against this process's session store. This is what lets OIDC mode run
  with no reverse proxy at all. Requests with no session still face the gate
  unchanged.
- **The session cookie.** It is the login credential, so it carries `Secure`
  whenever `OIDC_REDIRECT_URI` is https (override with `OIDC_COOKIE_SECURE`),
  `HttpOnly`, and `SameSite=Lax`. Without `Secure`, a hostname that also has an
  http listener -- an http-to-https redirect, typically -- would leak the cookie
  in plaintext before the redirect fires.
- **Token lifetime.** The IdP's access token usually expires long before the
  Atlas session does, so it is refreshed on demand, in-process, with one
  in-flight refresh per session. The refresh token never leaves the server.
- **Revoking delegated credentials.** Logout and
  `DELETE /api/auth/oidc/delegated-tokens` clear all three places a delegated
  credential is held: the delegation cache, the encrypted token store, and any
  MCP client already built around it. Tokens the user uploaded themselves are
  left alone.
- **Sessions are per-process.** The session store is in memory, so a restart or
  a second uvicorn worker forces a fresh (silent) IdP round trip rather than
  putting long-lived credentials into shared storage. Run a single worker, or
  use the trusted-header mode behind a proxy that holds the session.
- **WebSockets.** The chat socket honours the same login session, so no separate
  token is needed for the socket.
- **Open redirect.** The `?next=` parameter is constrained to a same-site
  absolute path; anything else returns the user to `/`.
- **Error reporting.** Only an allowlist of OAuth error codes is echoed back to
  the SPA or written to the log, so an IdP-supplied string cannot be reflected
  into a redirect URL.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `/auth/oidc/login` returns 404 | `FEATURE_OIDC_AUTH_ENABLED` is false, or startup disabled it -- check the log for the reason. |
| Redirect to `/?oidc_error=discovery_failed` | The issuer's discovery document is unreachable or its `issuer` claim does not match. |
| Redirect to `/?oidc_error=invalid_state` | The session cookie was lost between login and callback (secret changed, or the process restarted). |
| Redirect to `/?oidc_error=token_exchange_failed` | Client authentication was rejected, or the ID token failed validation. Check the redirect URI is registered exactly. |
| Redirect to `/?oidc_error=misconfigured` | Client credentials could not be built -- e.g. `private_key_jwt` with an unreadable key file. |
| MCP server shows as unauthenticated | Delegation disabled, no OIDC session for that user, or the exchange was refused by the IdP. |
