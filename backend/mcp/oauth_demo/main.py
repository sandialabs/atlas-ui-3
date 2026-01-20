#!/usr/bin/env python3
"""
OAuth 2.1 Demo MCP Server using FastMCP

This server demonstrates OAuth 2.1 authentication for MCP servers.
It requires users to authenticate via OAuth before accessing tools.

To test this server:
1. Start the mock OAuth provider: cd mocks/oauth-mcp-mock && ./run.sh
2. Add the config from mcp-example-configs/mcp-oauth_demo.json to your mcp.json
3. In Atlas UI, click the Key icon and authenticate with the OAuth server
4. Use the tools - they will show your authenticated identity

Test users (for mock OAuth provider):
- test@example.com / testpass123
- admin@example.com / adminpass123

Updated: 2025-01-19
"""

import time
from datetime import datetime
from typing import Any, Dict

from fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("OAuth Demo")


@mcp.tool
def whoami() -> Dict[str, Any]:
    """Get information about the authenticated user.

    This tool demonstrates that OAuth authentication is working by returning
    information about the current session. In a real implementation, this
    would extract user info from the OAuth token.

    Returns:
        User information including authentication status
    """
    start = time.perf_counter()

    # In a real OAuth-protected server, you would extract user info from
    # the authorization header or token. For this demo, we show that
    # the request made it through (meaning auth succeeded).
    result = {
        "results": {
            "authenticated": True,
            "message": "You are authenticated via OAuth 2.1",
            "timestamp": datetime.utcnow().isoformat(),
            "note": "In production, user identity would be extracted from the OAuth token"
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 3)
        }
    }
    return result


@mcp.tool
def get_protected_data(resource_id: str) -> Dict[str, Any]:
    """Access a protected resource that requires authentication.

    This demonstrates accessing data that would normally require
    user-specific authorization. The OAuth token would typically
    be used to verify the user has permission to access this resource.

    Args:
        resource_id: The ID of the resource to access

    Returns:
        The protected resource data
    """
    start = time.perf_counter()

    # Simulated protected data
    protected_resources = {
        "doc-001": {
            "title": "Confidential Report",
            "content": "This is sensitive data only visible to authenticated users.",
            "classification": "internal"
        },
        "doc-002": {
            "title": "Project Roadmap",
            "content": "Q1 goals: Implement OAuth 2.1 support for MCP servers.",
            "classification": "internal"
        },
        "doc-003": {
            "title": "API Keys Reference",
            "content": "See your dashboard for API key management.",
            "classification": "confidential"
        }
    }

    if resource_id in protected_resources:
        result = {
            "results": {
                "resource_id": resource_id,
                "data": protected_resources[resource_id],
                "access_granted": True
            },
            "meta_data": {
                "is_error": False,
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 3)
            }
        }
    else:
        result = {
            "results": {
                "resource_id": resource_id,
                "error": f"Resource '{resource_id}' not found",
                "available_resources": list(protected_resources.keys())
            },
            "meta_data": {
                "is_error": True,
                "reason": "not_found",
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 3)
            }
        }

    return result


@mcp.tool
def list_user_permissions() -> Dict[str, Any]:
    """List the permissions available to the authenticated user.

    In a real OAuth implementation, this would parse the scopes from
    the OAuth token to determine what the user is allowed to do.

    Returns:
        List of permissions/scopes for the current user
    """
    start = time.perf_counter()

    # In production, these would come from the OAuth token scopes
    permissions = {
        "results": {
            "permissions": [
                {"scope": "read", "description": "Read access to resources"},
                {"scope": "write", "description": "Write access to resources"},
            ],
            "note": "Permissions are derived from OAuth token scopes"
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 3)
        }
    }

    return permissions


if __name__ == "__main__":
    mcp.run()
