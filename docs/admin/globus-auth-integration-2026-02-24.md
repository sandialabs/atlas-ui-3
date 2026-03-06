# Globus OAuth Integration for ALCF Endpoints

Last updated: 2026-02-24

## Overview

Atlas supports Globus OAuth authentication to automatically obtain access tokens
for ALCF inference endpoints and other Globus-scoped services. When enabled, users
log in via Globus Auth and their service-specific tokens are stored server-side
so they never need to manually copy-paste tokens.

## How It Works

1. **User clicks "Log in with Globus"** in the Settings panel
2. Atlas redirects to Globus Auth with configured scopes (including ALCF)
3. User authenticates with their Globus identity
4. Globus returns an authorization code to Atlas's callback URL
5. Atlas exchanges the code for tokens, including `other_tokens` for extra scopes
6. Service-specific tokens (e.g., ALCF inference) are extracted and stored
   encrypted in MCPTokenStorage per-user
7. When the user selects an ALCF model, Atlas automatically uses the stored token

## Configuration

### Environment Variables

Add these to your `.env` file:

```bash
# Enable Globus auth feature
FEATURE_GLOBUS_AUTH_ENABLED=true

# Register at https://app.globus.org/settings/developers
GLOBUS_CLIENT_ID=your-globus-client-id
GLOBUS_CLIENT_SECRET=your-globus-client-secret

# Must match the redirect URI registered with Globus
GLOBUS_REDIRECT_URI=http://localhost:8000/auth/globus/callback

# Scopes to request (base "openid profile email" is always included)
# ALCF Inference Service scope:
GLOBUS_SCOPES=https://auth.globus.org/scopes/681c10cc-f684-4540-bcd7-0b4df3bc26ef/action_all

# Secret for session cookie signing
GLOBUS_SESSION_SECRET=your-random-secret-for-session-signing
```

### Registering a Globus Portal Client

1. Visit [https://app.globus.org/settings/developers](https://app.globus.org/settings/developers)
2. Click "Register a portal..."
3. Select "none of the above - create a new project" and click "Continue"
4. Fill the App Registration form:
   - App Name: Your Atlas Instance Name
   - Redirects: `http://localhost:8000/auth/globus/callback` (or your production URL)
   - Click "Register App"
   - Click "Add Secret Client", enter a name, and click "Generate secret"
5. Copy the Client UUID and secret into `GLOBUS_CLIENT_ID` and `GLOBUS_CLIENT_SECRET`

### LLM Model Configuration

Configure ALCF models in `llmconfig.yml` with `api_key_source: "globus"`:

```yaml
alcf-llama-3:
  model_name: "openai/meta-llama/Meta-Llama-3-70B-Instruct"
  model_url: "https://data-portal-dev.cels.anl.gov/rpc/v1"
  api_key_source: "globus"
  globus_scope: "681c10cc-f684-4540-bcd7-0b4df3bc26ef"
  description: "Llama 3 70B on ALCF (requires Globus login)"
  max_tokens: 4096
  temperature: 0.7
```

The `globus_scope` field should be the resource server UUID from the Globus scope URL.
For the ALCF Inference Service scope
`https://auth.globus.org/scopes/681c10cc-f684-4540-bcd7-0b4df3bc26ef/action_all`,
the resource server UUID is `681c10cc-f684-4540-bcd7-0b4df3bc26ef`.

## API Endpoints

### Browser Routes (no auth required)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/auth/globus/login` | Initiate Globus OAuth login |
| GET | `/auth/globus/callback` | Handle OAuth callback |
| GET | `/auth/globus/logout` | Clear Globus tokens |

### JSON API Routes (auth required)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/globus/status` | Get Globus auth status |
| DELETE | `/api/globus/tokens` | Remove all Globus tokens |

### Status Response Example

```json
{
  "enabled": true,
  "authenticated": true,
  "resource_servers": [
    {
      "resource_server": "681c10cc-f684-4540-bcd7-0b4df3bc26ef",
      "is_expired": false,
      "expires_at": 1740500000.0,
      "scopes": "https://auth.globus.org/scopes/681c10cc/action_all"
    }
  ],
  "user_email": "user@example.com"
}
```

## Token Storage

Globus tokens are stored in the existing MCPTokenStorage system:

- Key format: `globus:{resource_server_uuid}`
- Encrypted at rest with Fernet (AES-128-CBC)
- Per-user isolation
- Automatic expiration checking

## Frontend Integration

The Globus auth section appears in the Settings panel when `FEATURE_GLOBUS_AUTH_ENABLED=true`.
Users see:

- **Not connected**: "Log in with Globus" button
- **Connected**: List of resource servers with token validity, Disconnect and Refresh buttons

Models with `api_key_source: "globus"` show their auth status in the `/api/config` response.

## Multiple Globus Scopes

You can request tokens for multiple services by space-separating the scopes:

```bash
# ALCF Inference + Globus Compute
GLOBUS_SCOPES=https://auth.globus.org/scopes/681c10cc-f684-4540-bcd7-0b4df3bc26ef/action_all https://auth.globus.org/scopes/facd7ccc-c5f4-42aa-916b-a0e270e2c2a9/all
```

Each scope generates a separate token in `other_tokens`, and each is stored with its
own `globus:{resource_server}` key.

## Troubleshooting

1. **"Globus auth is not enabled"**: Set `FEATURE_GLOBUS_AUTH_ENABLED=true` in `.env`
2. **"Globus OAuth not configured"**: Set `GLOBUS_CLIENT_ID` and `GLOBUS_CLIENT_SECRET`
3. **Token exchange failed**: Check that `GLOBUS_REDIRECT_URI` matches the one registered with Globus
4. **"Please log in via Globus"**: User needs to authenticate; the model's `globus_scope` token is missing
5. **Tokens expired**: User needs to re-authenticate via Globus login
