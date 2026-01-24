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
    Show the current authentication status and token info.

    This reads the actual token sent by the client via get_access_token().
    Use this to verify that Atlas UI is correctly sending your token.
    """
    start = time.perf_counter()
    token = get_access_token()

    if token is None:
        return {
            "authenticated": False,
            "error": "No token received. Make sure you've uploaded a token in the Tools panel.",
            "meta_data": {
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
            }
        }

    # Try to decode if it's a JWT
    jwt_claims = None
    token_preview = str(token.token)[:50] + "..." if len(str(token.token)) > 50 else str(token.token)

    if token.token and '.' in str(token.token):
        jwt_claims = decode_jwt_payload(str(token.token))

    return {
        "authenticated": True,
        "token_preview": token_preview,
        "token_length": len(str(token.token)) if token.token else 0,
        "client_id": token.client_id,
        "scopes": token.scopes,
        "expires_at": str(token.expires_at) if token.expires_at else None,
        "jwt_claims": jwt_claims,
        "meta_data": {
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
        }
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
            "meta_data": {
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
            }
        }

    return {
        "success": True,
        "message": message,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "processed_by": f"token:{str(token.token)[:20]}...",
        "meta_data": {
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
        }
    }


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8001)
