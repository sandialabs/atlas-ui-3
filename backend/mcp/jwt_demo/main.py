#!/usr/bin/env python3
"""
JWT Demo MCP Server - Demonstrates per-user token authentication.

This server uses FastMCP's get_access_token() to read the bearer token
sent by the client. Use this to verify that Atlas UI is correctly
sending user tokens to MCP servers.

Updated: 2025-01-23
"""

import time
import json
import base64
from datetime import datetime, timezone
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token

mcp = FastMCP("JWT Auth Demo")

# Test tokens for demo - in production, validate against your identity provider
TEST_TOKENS = {
    "test123": {"email": "test@test.com", "name": "Test User", "role": "user"},
    "admin456": {"email": "admin@example.com", "name": "Admin User", "role": "admin"},
    "demo": {"email": "demo@example.com", "name": "Demo User", "role": "viewer"},
}


def lookup_user(token_value: str) -> dict | None:
    """Look up user by token. Returns user info or None if not found."""
    return TEST_TOKENS.get(token_value)


def decode_jwt_payload(token: str) -> dict | None:
    """Decode a JWT token's payload (without verification)."""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += '=' * padding
        payload_json = base64.urlsafe_b64decode(payload_b64)
        return json.loads(payload_json)
    except Exception:
        return None


@mcp.tool
def whoami() -> dict[str, Any]:
    """
    Show the current authentication status and user info.

    Test tokens: test123, admin456, demo
    """
    start = time.perf_counter()
    token = get_access_token()

    if token is None:
        return {
            "authenticated": False,
            "error": "No token received. Upload a token in the Tools panel.",
            "hint": "Try one of: test123, admin456, demo",
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
        }

    token_value = str(token.token) if token.token else ""
    user = lookup_user(token_value)

    if user:
        return {
            "authenticated": True,
            "user": user,
            "token": token_value,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
        }

    # Token received but not recognized
    jwt_claims = decode_jwt_payload(token_value) if '.' in token_value else None

    return {
        "authenticated": True,
        "user": None,
        "token": token_value[:50] + "..." if len(token_value) > 50 else token_value,
        "message": "Token received but not in test database",
        "jwt_claims": jwt_claims,
        "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
    }


@mcp.tool
def protected_action(message: str) -> dict[str, Any]:
    """
    A protected action that requires authentication.

    Args:
        message: A message to echo back
    """
    start = time.perf_counter()
    token = get_access_token()

    if token is None:
        return {
            "success": False,
            "error": "Authentication required",
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
        }

    token_value = str(token.token) if token.token else ""
    user = lookup_user(token_value)

    return {
        "success": True,
        "message": message,
        "processed_by": user["email"] if user else f"unknown ({token_value[:10]}...)",
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
    }


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8001)
