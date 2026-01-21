"""MCP Authentication routes for per-user API key and token management.

This module provides user-facing endpoints for managing authentication tokens
with MCP servers. Each user's tokens are stored separately and securely encrypted.

Users can manually upload API keys, JWTs, or bearer tokens for MCP servers that
require authentication. This supports any type of token that can be passed as a
bearer token in the Authorization header.

Updated: 2025-01-21
"""

import logging
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.auth import is_user_in_group
from core.log_sanitizer import get_current_user, sanitize_for_logging
from infrastructure.app_factory import app_factory
from modules.mcp_tools.token_storage import get_token_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp/auth", tags=["mcp-auth"])


class TokenUpload(BaseModel):
    """Request body for uploading an API key or token."""
    token: str
    expires_at: Optional[float] = None  # Unix timestamp, or None for no expiry
    scopes: Optional[str] = None  # Space-separated scopes (optional)


# --- Routes ---


@router.get("/status")
async def get_auth_status(current_user: str = Depends(get_current_user)):
    """Get authentication status for all MCP servers accessible to the user.

    Returns information about which servers require authentication,
    which the user has authenticated with, and token status.
    """
    try:
        mcp_manager = app_factory.get_mcp_manager()
        token_storage = get_token_storage()

        # Get servers the user is authorized to access
        authorized_servers = await mcp_manager.get_authorized_servers(
            current_user, is_user_in_group
        )

        # Get user's current auth status
        user_auth_status = token_storage.get_user_auth_status(current_user)

        # Build response with server auth requirements and user's status
        servers_status = []

        for server_name in authorized_servers:
            server_config = mcp_manager.servers_config.get(server_name, {})
            auth_type = server_config.get("auth_type", "none")

            # Get user's token status for this server
            token_status = user_auth_status.get(server_name)

            server_info = {
                "server_name": server_name,
                "auth_type": auth_type,
                "auth_required": auth_type != "none",
                "authenticated": token_status is not None,
                "description": server_config.get("description", ""),
            }

            # Add token details if authenticated
            if token_status:
                server_info.update({
                    "token_type": token_status["token_type"],
                    "is_expired": token_status["is_expired"],
                    "expires_at": token_status["expires_at"],
                    "time_until_expiry": token_status["time_until_expiry"],
                    "has_refresh_token": token_status["has_refresh_token"],
                    "scopes": token_status["scopes"],
                })

            servers_status.append(server_info)

        return {
            "servers": servers_status,
            "user": current_user,
        }

    except Exception as e:
        logger.error(f"Error getting auth status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{server_name}/token")
async def upload_token(
    server_name: str,
    token_data: TokenUpload,
    current_user: str = Depends(get_current_user),
):
    """Upload an API key or bearer token for an MCP server.

    This allows users to manually provide tokens for servers that require
    authentication. Tokens can be:
    - API keys
    - JWT tokens
    - Bearer tokens
    - Any other string token that can be used in Authorization header

    The token will be securely encrypted and stored per-user.
    """
    try:
        mcp_manager = app_factory.get_mcp_manager()
        token_storage = get_token_storage()

        # Verify server exists and user has access
        authorized_servers = await mcp_manager.get_authorized_servers(
            current_user, is_user_in_group
        )

        if server_name not in authorized_servers:
            raise HTTPException(
                status_code=403,
                detail=f"Not authorized to access server '{server_name}'"
            )

        # Verify server accepts token authentication
        server_config = mcp_manager.servers_config.get(server_name, {})
        auth_type = server_config.get("auth_type", "none")

        if auth_type not in ("jwt", "bearer", "api_key", "oauth"):
            raise HTTPException(
                status_code=400,
                detail=f"Server '{server_name}' does not accept token authentication (auth_type: {auth_type})"
            )

        # Validate token is not empty
        if not token_data.token or not token_data.token.strip():
            raise HTTPException(
                status_code=400,
                detail="Token cannot be empty"
            )

        # Determine token type based on server config
        token_type = "bearer"
        if auth_type == "jwt":
            token_type = "jwt"
        elif auth_type == "api_key":
            token_type = "api_key"

        # Store the token
        stored_token = token_storage.store_token(
            user_email=current_user,
            server_name=server_name,
            token_value=token_data.token.strip(),
            token_type=token_type,
            expires_at=token_data.expires_at,
            scopes=token_data.scopes,
        )

        sanitized_server = sanitize_for_logging(server_name)
        logger.info(f"User uploaded token for MCP server '{sanitized_server}'")

        return {
            "message": f"Token stored for server '{server_name}'",
            "server_name": server_name,
            "token_type": stored_token.token_type,
            "expires_at": stored_token.expires_at,
            "scopes": stored_token.scopes,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading token: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{server_name}/token")
async def remove_token(
    server_name: str,
    current_user: str = Depends(get_current_user),
):
    """Remove stored token for an MCP server (disconnect).

    This removes the user's authentication token for the specified server.
    The user will need to re-authenticate to use the server's tools.
    """
    try:
        token_storage = get_token_storage()

        # Remove the token
        removed = token_storage.remove_token(current_user, server_name)

        if not removed:
            raise HTTPException(
                status_code=404,
                detail=f"No token found for server '{server_name}'"
            )

        sanitized_server = sanitize_for_logging(server_name)
        logger.info(f"User removed token for MCP server '{sanitized_server}'")

        return {
            "message": f"Token removed for server '{server_name}'",
            "server_name": server_name,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing token: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
