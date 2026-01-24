#!/usr/bin/env python3
"""
JWT Demo MCP Server - JWT/Bearer Token Authentication Required

This server demonstrates tools that require JWT or bearer token authentication.
Users must manually upload their token via the Tools panel before using these tools.

Authentication Type: jwt (or bearer)
Transport: stdio, http, or sse

How to use:
1. Configure this server with auth_type: "jwt" or auth_type: "bearer"
2. Open the Tools panel and click the key icon next to this server
3. Paste your JWT/bearer token
4. Use the tools to access protected functionality

For testing, you can use any valid-looking JWT token. In production,
tokens would be validated against your identity provider.

Updated: 2025-01-21
"""

import time
import json
import base64
from datetime import datetime, timezone
from typing import Any

from fastmcp import FastMCP

mcp = FastMCP("JWT Auth Demo")


def decode_jwt_payload(token: str) -> dict | None:
    """
    Decode a JWT token's payload (without verification).
    This is for demo purposes - in production, always verify the signature.
    """
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None

        # Decode the payload (second part)
        payload_b64 = parts[1]
        # Add padding if needed
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += '=' * padding

        payload_json = base64.urlsafe_b64decode(payload_b64)
        return json.loads(payload_json)
    except Exception:
        return None


# Simulated user database for demo
DEMO_USERS = {
    "user123": {
        "name": "Demo User",
        "email": "demo@example.com",
        "role": "user",
        "department": "Engineering"
    },
    "admin456": {
        "name": "Admin User",
        "email": "admin@example.com",
        "role": "admin",
        "department": "IT"
    }
}

# Simulated protected data
PROTECTED_DATA = {
    "reports": [
        {"id": "RPT-001", "title": "Q4 Sales Report", "status": "completed"},
        {"id": "RPT-002", "title": "Annual Review", "status": "draft"},
        {"id": "RPT-003", "title": "Budget Forecast", "status": "pending"}
    ],
    "secrets": {
        "api_endpoint": "https://api.internal.example.com",
        "config_version": "2.5.1",
        "feature_flags": ["new_dashboard", "beta_analytics"]
    }
}


@mcp.tool
def whoami() -> dict[str, Any]:
    """
    Get information about the authenticated user.

    This tool requires JWT authentication. It demonstrates how a server
    can identify the authenticated user from their token.

    Returns user profile information extracted from the JWT claims.
    """
    start = time.perf_counter()

    # In a real implementation, the token would be passed via the auth context
    # For demo purposes, we simulate an authenticated user
    return {
        "results": {
            "authenticated": True,
            "auth_method": "JWT",
            "user": {
                "id": "user123",
                "name": "Demo User",
                "email": "demo@example.com",
                "role": "user"
            },
            "token_info": {
                "issued_at": datetime.now(timezone.utc).isoformat(),
                "expires_in": "3600 seconds",
                "scopes": ["read", "write"]
            },
            "message": "You are authenticated via JWT token"
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
            "auth_required": True,
            "auth_type": "jwt"
        }
    }


@mcp.tool
def list_my_reports() -> dict[str, Any]:
    """
    List reports accessible to the authenticated user.

    This tool requires JWT authentication. It demonstrates accessing
    protected resources that are scoped to the authenticated user.
    """
    start = time.perf_counter()

    return {
        "results": {
            "user_id": "user123",
            "reports": PROTECTED_DATA["reports"],
            "total_count": len(PROTECTED_DATA["reports"]),
            "access_level": "read"
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
            "auth_required": True,
            "auth_type": "jwt"
        }
    }


@mcp.tool
def get_protected_config() -> dict[str, Any]:
    """
    Retrieve protected configuration data.

    This tool requires JWT authentication. It demonstrates accessing
    sensitive configuration that should only be available to authenticated users.
    """
    start = time.perf_counter()

    return {
        "results": {
            "config": PROTECTED_DATA["secrets"],
            "accessed_by": "demo@example.com",
            "accessed_at": datetime.now(timezone.utc).isoformat()
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
            "auth_required": True,
            "auth_type": "jwt"
        }
    }


@mcp.tool
def create_report(title: str, description: str = "") -> dict[str, Any]:
    """
    Create a new report (simulated).

    This tool requires JWT authentication. It demonstrates a write operation
    that requires authentication to identify the creator.

    Args:
        title: Title of the report
        description: Optional description of the report
    """
    start = time.perf_counter()

    # Simulate creating a report
    new_report = {
        "id": f"RPT-{len(PROTECTED_DATA['reports']) + 1:03d}",
        "title": title,
        "description": description,
        "status": "draft",
        "created_by": "demo@example.com",
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    return {
        "results": {
            "success": True,
            "report": new_report,
            "message": f"Report '{title}' created successfully"
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
            "auth_required": True,
            "auth_type": "jwt"
        }
    }


@mcp.tool
def validate_token_format(token: str) -> dict[str, Any]:
    """
    Validate and decode a JWT token (without signature verification).

    This tool requires JWT authentication. It's a utility to help users
    understand their token structure.

    Args:
        token: The JWT token to validate and decode

    Note: This only checks format, not cryptographic validity.
    """
    start = time.perf_counter()

    parts = token.split('.')
    if len(parts) != 3:
        return {
            "results": {
                "valid_format": False,
                "error": "JWT must have 3 parts separated by dots (header.payload.signature)"
            },
            "meta_data": {
                "is_error": False,
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
                "auth_required": True,
                "auth_type": "jwt"
            }
        }

    payload = decode_jwt_payload(token)
    if payload is None:
        return {
            "results": {
                "valid_format": False,
                "error": "Could not decode JWT payload"
            },
            "meta_data": {
                "is_error": False,
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
                "auth_required": True,
                "auth_type": "jwt"
            }
        }

    # Check for common JWT claims
    claims_present = []
    claims_missing = []
    standard_claims = ["iss", "sub", "aud", "exp", "iat", "nbf", "jti"]

    for claim in standard_claims:
        if claim in payload:
            claims_present.append(claim)
        else:
            claims_missing.append(claim)

    # Check expiration if present
    exp_status = "not set"
    if "exp" in payload:
        exp_time = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        now = datetime.now(timezone.utc)
        if exp_time < now:
            exp_status = f"EXPIRED at {exp_time.isoformat()}"
        else:
            exp_status = f"valid until {exp_time.isoformat()}"

    return {
        "results": {
            "valid_format": True,
            "payload": {k: v for k, v in payload.items() if k not in ["password", "secret"]},
            "claims_present": claims_present,
            "claims_missing": claims_missing,
            "expiration_status": exp_status
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
            "auth_required": True,
            "auth_type": "jwt",
            "note": "Signature verification not performed - format check only"
        }
    }


if __name__ == "__main__":
    mcp.run()
