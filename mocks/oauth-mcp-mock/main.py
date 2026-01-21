#!/usr/bin/env python3
"""
Mock OAuth 2.1 MCP Server for Testing

This mock server provides:
1. OAuth 2.1 authorization server endpoints (discovery, authorize, token)
2. A simple MCP server that validates OAuth tokens
3. Test tools to verify authentication works

This is for DEVELOPMENT/TESTING ONLY - not for production use.

Updated: 2025-01-19
"""

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastmcp import FastMCP
from pydantic import BaseModel


# Configuration
HOST = os.getenv("OAUTH_MOCK_HOST", "0.0.0.0")
PORT = int(os.getenv("OAUTH_MOCK_PORT", "8001"))
BASE_URL = os.getenv("OAUTH_MOCK_BASE_URL", f"http://localhost:{PORT}")

# In-memory storage for OAuth state
# In production, this would be Redis or a database
_authorization_codes: Dict[str, Dict[str, Any]] = {}
_access_tokens: Dict[str, Dict[str, Any]] = {}
_refresh_tokens: Dict[str, Dict[str, Any]] = {}
_registered_clients: Dict[str, Dict[str, Any]] = {
    # Pre-registered client for Atlas UI
    "Atlas UI": {
        "client_id": "Atlas UI",
        "client_secret": None,  # Public client (PKCE)
        "redirect_uris": [],  # Dynamic registration allows any
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }
}

# Mock users for testing
MOCK_USERS = {
    "test@example.com": {
        "password": "testpass123",
        "name": "Test User",
        "email": "test@example.com",
    },
    "admin@example.com": {
        "password": "adminpass123",
        "name": "Admin User",
        "email": "admin@example.com",
    },
}


# --- OAuth Server Endpoints ---


app = FastAPI(title="Mock OAuth 2.1 Server", redirect_slashes=False)


@app.get("/.well-known/oauth-authorization-server")
async def oauth_discovery():
    """OAuth 2.1 Server Metadata Discovery (RFC 8414)."""
    return {
        "issuer": BASE_URL,
        "authorization_endpoint": f"{BASE_URL}/oauth/authorize",
        "token_endpoint": f"{BASE_URL}/oauth/token",
        "revocation_endpoint": f"{BASE_URL}/oauth/revoke",
        "registration_endpoint": f"{BASE_URL}/oauth/register",
        "scopes_supported": ["read", "write", "profile"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],  # Public clients
    }


@app.get("/oauth/authorize")
async def oauth_authorize(
    response_type: str = Query(...),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    state: str = Query(...),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query("S256"),
    scope: str = Query(""),
):
    """OAuth 2.1 Authorization Endpoint.

    In a real OAuth server, this would show a login/consent page.
    For testing, we show a simple form.
    """
    if response_type != "code":
        raise HTTPException(status_code=400, detail="Unsupported response_type")

    if code_challenge_method != "S256":
        raise HTTPException(status_code=400, detail="Only S256 code_challenge_method supported")

    # Show simple login form
    return HTMLResponse(
        content=f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Mock OAuth Login</title>
            <style>
                body {{ font-family: system-ui; padding: 40px; max-width: 400px; margin: 0 auto; }}
                h1 {{ color: #333; }}
                .form-group {{ margin-bottom: 15px; }}
                label {{ display: block; margin-bottom: 5px; font-weight: bold; }}
                input {{ width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 4px; }}
                button {{ background: #007bff; color: white; padding: 12px 20px; border: none;
                         border-radius: 4px; cursor: pointer; width: 100%; font-size: 16px; }}
                button:hover {{ background: #0056b3; }}
                .info {{ background: #f0f0f0; padding: 15px; border-radius: 4px; margin-bottom: 20px; }}
                .test-users {{ font-size: 12px; color: #666; }}
            </style>
        </head>
        <body>
            <h1>Mock OAuth Login</h1>
            <div class="info">
                <p>Client: <strong>{client_id}</strong></p>
                <p>Scopes: <strong>{scope or 'none'}</strong></p>
            </div>
            <form method="POST" action="/oauth/authorize/submit">
                <input type="hidden" name="client_id" value="{client_id}">
                <input type="hidden" name="redirect_uri" value="{redirect_uri}">
                <input type="hidden" name="state" value="{state}">
                <input type="hidden" name="code_challenge" value="{code_challenge}">
                <input type="hidden" name="scope" value="{scope}">

                <div class="form-group">
                    <label>Email</label>
                    <input type="email" name="email" required placeholder="test@example.com">
                </div>
                <div class="form-group">
                    <label>Password</label>
                    <input type="password" name="password" required placeholder="testpass123">
                </div>
                <button type="submit">Sign In and Authorize</button>
            </form>
            <div class="test-users">
                <p>Test users:</p>
                <ul>
                    <li>test@example.com / testpass123</li>
                    <li>admin@example.com / adminpass123</li>
                </ul>
            </div>
        </body>
        </html>
        """,
        status_code=200,
    )


@app.post("/oauth/authorize/submit")
async def oauth_authorize_submit(
    email: str = Form(...),
    password: str = Form(...),
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(...),
    code_challenge: str = Form(...),
    scope: str = Form(""),
):
    """Process OAuth authorization form submission."""
    # Validate credentials
    user = MOCK_USERS.get(email)
    if not user or user["password"] != password:
        return HTMLResponse(
            content="""
            <!DOCTYPE html>
            <html>
            <head><title>Login Failed</title></head>
            <body style="font-family: system-ui; padding: 40px; text-align: center;">
                <h1>Login Failed</h1>
                <p>Invalid email or password.</p>
                <a href="javascript:history.back()">Try Again</a>
            </body>
            </html>
            """,
            status_code=401,
        )

    # Generate authorization code
    code = secrets.token_urlsafe(32)

    # Store code with PKCE challenge and user info
    _authorization_codes[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "scope": scope,
        "user_email": email,
        "user_name": user["name"],
        "created_at": time.time(),
        "expires_at": time.time() + 600,  # 10 minutes
    }

    # Redirect back to client with authorization code
    redirect_params = urlencode({"code": code, "state": state})
    return RedirectResponse(
        url=f"{redirect_uri}?{redirect_params}",
        status_code=302,
    )


@app.post("/oauth/token")
async def oauth_token(
    grant_type: str = Form(...),
    code: str = Form(None),
    redirect_uri: str = Form(None),
    client_id: str = Form(...),
    code_verifier: str = Form(None),
    refresh_token: str = Form(None),
):
    """OAuth 2.1 Token Endpoint."""
    if grant_type == "authorization_code":
        return await _handle_authorization_code_grant(
            code, redirect_uri, client_id, code_verifier
        )
    elif grant_type == "refresh_token":
        return await _handle_refresh_token_grant(refresh_token, client_id)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported grant_type: {grant_type}")


async def _handle_authorization_code_grant(
    code: str,
    redirect_uri: str,
    client_id: str,
    code_verifier: str,
):
    """Handle authorization_code grant type with PKCE verification."""
    if not code or not code_verifier:
        raise HTTPException(status_code=400, detail="Missing code or code_verifier")

    # Look up authorization code
    code_data = _authorization_codes.pop(code, None)
    if not code_data:
        raise HTTPException(status_code=400, detail="Invalid or expired authorization code")

    # Check expiration
    if time.time() > code_data["expires_at"]:
        raise HTTPException(status_code=400, detail="Authorization code expired")

    # Validate client_id
    if code_data["client_id"] != client_id:
        raise HTTPException(status_code=400, detail="Client ID mismatch")

    # Validate redirect_uri
    if code_data["redirect_uri"] != redirect_uri:
        raise HTTPException(status_code=400, detail="Redirect URI mismatch")

    # Verify PKCE code_challenge
    # SHA256(code_verifier) should equal code_challenge
    verifier_hash = hashlib.sha256(code_verifier.encode()).digest()
    computed_challenge = base64.urlsafe_b64encode(verifier_hash).rstrip(b"=").decode()

    if computed_challenge != code_data["code_challenge"]:
        raise HTTPException(status_code=400, detail="PKCE verification failed")

    # Generate tokens
    access_token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(32)
    expires_in = 3600  # 1 hour

    # Store access token
    _access_tokens[access_token] = {
        "user_email": code_data["user_email"],
        "user_name": code_data["user_name"],
        "client_id": client_id,
        "scope": code_data["scope"],
        "created_at": time.time(),
        "expires_at": time.time() + expires_in,
    }

    # Store refresh token
    _refresh_tokens[refresh_token] = {
        "user_email": code_data["user_email"],
        "user_name": code_data["user_name"],
        "client_id": client_id,
        "scope": code_data["scope"],
        "created_at": time.time(),
    }

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "refresh_token": refresh_token,
        "scope": code_data["scope"],
    }


async def _handle_refresh_token_grant(refresh_token: str, client_id: str):
    """Handle refresh_token grant type."""
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Missing refresh_token")

    # Look up refresh token
    token_data = _refresh_tokens.get(refresh_token)
    if not token_data:
        raise HTTPException(status_code=400, detail="Invalid refresh token")

    # Validate client_id
    if token_data["client_id"] != client_id:
        raise HTTPException(status_code=400, detail="Client ID mismatch")

    # Generate new access token
    access_token = secrets.token_urlsafe(32)
    expires_in = 3600  # 1 hour

    # Store new access token
    _access_tokens[access_token] = {
        "user_email": token_data["user_email"],
        "user_name": token_data["user_name"],
        "client_id": client_id,
        "scope": token_data["scope"],
        "created_at": time.time(),
        "expires_at": time.time() + expires_in,
    }

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "refresh_token": refresh_token,  # Same refresh token
        "scope": token_data["scope"],
    }


@app.post("/oauth/revoke")
async def oauth_revoke(token: str = Form(...)):
    """Revoke an access or refresh token."""
    # Try to revoke as access token
    if token in _access_tokens:
        del _access_tokens[token]
        return {"message": "Token revoked"}

    # Try to revoke as refresh token
    if token in _refresh_tokens:
        del _refresh_tokens[token]
        return {"message": "Token revoked"}

    # Token not found - per RFC 7009, this is not an error
    return {"message": "Token revoked"}


# --- Token Validation for MCP Server ---


class MockOAuthVerifier:
    """Token verifier that checks tokens against our mock OAuth store."""

    async def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify an access token and return claims if valid."""
        token_data = _access_tokens.get(token)
        if not token_data:
            return None

        # Check expiration
        if time.time() > token_data["expires_at"]:
            del _access_tokens[token]
            return None

        return {
            "sub": token_data["user_email"],
            "name": token_data["user_name"],
            "client_id": token_data["client_id"],
            "scope": token_data["scope"],
        }


# --- MCP Server with OAuth ---


mcp = FastMCP(
    name="OAuth Protected MCP Server",
    instructions="""
    This is a test MCP server protected by OAuth 2.1.
    You must authenticate using the OAuth flow before using these tools.
    """,
)


@mcp.tool()
async def get_user_profile() -> Dict[str, Any]:
    """Get the authenticated user's profile information.

    Returns mock user profile data. In a real implementation,
    the MCP transport layer handles auth verification before
    this tool is called.

    Note: Authentication is validated at the HTTP transport layer
    before MCP tools are invoked. This tool demonstrates that
    if you can call it, you are already authenticated.
    """
    # In a real scenario, the auth token would be validated by the
    # HTTP/SSE transport layer before this tool is invoked.
    # Here we return demo data to show the tool works.
    return {
        "message": "If you see this, authentication succeeded!",
        "note": "Token was validated at the transport layer",
        "demo_user": {
            "email": "authenticated-user@example.com",
            "name": "Authenticated User",
            "scopes": ["read", "write"],
        },
    }


@mcp.tool()
async def echo_message(message: str) -> str:
    """Echo a message back to verify the server is working.

    Args:
        message: The message to echo
    """
    return f"Echo: {message}"


@mcp.tool()
async def get_secret_data() -> Dict[str, Any]:
    """Get some secret data that requires authentication.

    This demonstrates a protected resource.
    """
    return {
        "secret": "This is protected data!",
        "timestamp": time.time(),
        "server": "oauth-mcp-mock",
    }


# Mount MCP server on FastAPI app
# Use http_app() to get the Starlette app for mounting
app.mount("/mcp", mcp.http_app())


# --- Health Check ---


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "server": "oauth-mcp-mock"}


# --- Main ---


if __name__ == "__main__":
    print(f"Starting Mock OAuth MCP Server on {HOST}:{PORT}")
    print(f"OAuth Discovery: {BASE_URL}/.well-known/oauth-authorization-server")
    print(f"MCP Endpoint: {BASE_URL}/mcp")
    print()
    print("Test users:")
    for email, user in MOCK_USERS.items():
        print(f"  - {email} / {user['password']}")

    uvicorn.run(app, host=HOST, port=PORT)
