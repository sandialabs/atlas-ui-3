"""Mock Globus Auth server for local testing of the ALCF token integration.

Simulates the Globus OAuth 2.0 flow locally so the Atlas Globus auth
feature can be tested end-to-end without real Globus credentials.

Endpoints:
  GET  /v2/oauth2/authorize  -- Simulates the Globus authorize page
  POST /v2/oauth2/token       -- Simulates the Globus token exchange
  GET  /v2/oauth2/userinfo    -- Returns mock user info

Usage:
  python mock_globus_auth.py [--port 9999]

Configure Atlas .env:
  FEATURE_GLOBUS_AUTH_ENABLED=true
  GLOBUS_CLIENT_ID=mock-client-id
  GLOBUS_CLIENT_SECRET=mock-client-secret
  GLOBUS_REDIRECT_URI=http://localhost:8000/auth/globus/callback
  GLOBUS_SCOPES=https://auth.globus.org/scopes/681c10cc-f684-4540-bcd7-0b4df3bc26ef/action_all

Then override the Globus Auth base URL in globus_auth.py (or use the test below).

Updated: 2026-02-24
"""

import argparse
import json
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI(title="Mock Globus Auth Server")

# Simulated authorization codes
PENDING_CODES: dict[str, dict] = {}


@app.get("/v2/oauth2/authorize")
async def authorize(
    client_id: str = "",
    redirect_uri: str = "",
    response_type: str = "code",
    scope: str = "",
    state: str = "",
):
    """Simulate the Globus authorization page.

    In a real flow, the user would log in and consent.
    Here we auto-approve and redirect back immediately.
    """
    code = secrets.token_urlsafe(32)
    PENDING_CODES[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "created_at": time.time(),
    }

    # Auto-redirect back to Atlas with the auth code
    params = urlencode({"code": code, "state": state})
    return RedirectResponse(f"{redirect_uri}?{params}", status_code=302)


@app.post("/v2/oauth2/token")
async def token_exchange(request: Request):
    """Simulate the Globus token exchange.

    Returns a token response with main token and other_tokens
    containing the ALCF inference scope token.
    """
    form = await request.form()
    code = form.get("code", "")
    redirect_uri = form.get("redirect_uri", "")

    # Validate the auth code
    code_info = PENDING_CODES.pop(code, None)
    if not code_info:
        return JSONResponse(
            {"error": "invalid_grant", "error_description": "Invalid or expired code"},
            status_code=400,
        )

    now = time.time()
    scopes = code_info.get("scope", "")

    # Build the main identity token
    response = {
        "access_token": f"mock-globus-access-token-{secrets.token_hex(8)}",
        "token_type": "Bearer",
        "expires_in": 172800,
        "resource_server": "auth.globus.org",
        "scope": "openid profile email",
        "refresh_token": f"mock-refresh-{secrets.token_hex(8)}",
        "other_tokens": [],
    }

    # Generate tokens for each requested scope beyond base scopes
    base_scopes = {"openid", "profile", "email"}
    extra_scopes = [s for s in scopes.split() if s not in base_scopes]

    for scope_url in extra_scopes:
        # Extract resource_server UUID from scope URL
        # e.g. https://auth.globus.org/scopes/681c10cc-.../action_all -> 681c10cc-...
        parts = scope_url.split("/scopes/")
        if len(parts) == 2:
            resource_server = parts[1].split("/")[0]
        else:
            resource_server = f"mock-rs-{secrets.token_hex(4)}"

        response["other_tokens"].append({
            "access_token": f"mock-{resource_server}-token-{secrets.token_hex(8)}",
            "token_type": "Bearer",
            "expires_in": 172800,
            "resource_server": resource_server,
            "scope": scope_url,
        })

    return JSONResponse(response)


@app.get("/v2/oauth2/userinfo")
async def userinfo(request: Request):
    """Return mock user info."""
    return JSONResponse({
        "sub": "mock-user-uuid-12345",
        "email": "testuser@alcf.anl.gov",
        "name": "Test ALCF User",
        "preferred_username": "testuser@alcf.anl.gov",
    })


@app.get("/")
async def index():
    """Health check for the mock server."""
    return JSONResponse({
        "status": "ok",
        "service": "Mock Globus Auth Server",
        "endpoints": [
            "GET /v2/oauth2/authorize",
            "POST /v2/oauth2/token",
            "GET /v2/oauth2/userinfo",
        ],
    })


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="Mock Globus Auth Server")
    parser.add_argument("--port", type=int, default=9999, help="Port to run on")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind to")
    args = parser.parse_args()

    print(f"Starting Mock Globus Auth Server on http://{args.host}:{args.port}")
    print("Configure Atlas with:")
    print(f"  GLOBUS_CLIENT_ID=mock-client-id")
    print(f"  GLOBUS_CLIENT_SECRET=mock-client-secret")
    print(f"  GLOBUS_REDIRECT_URI=http://localhost:8000/auth/globus/callback")
    uvicorn.run(app, host=args.host, port=args.port)
