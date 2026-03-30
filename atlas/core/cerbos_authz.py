"""Cerbos-enhanced authorization layer for MCP tools.

Wraps the existing group-based RBAC with fine-grained Cerbos policy checks.
When Cerbos is available, it provides attribute-based authorization that
considers compliance levels, sandbox constraints, and resource ownership.
When Cerbos is unavailable, the existing group-based check is used as fallback.
"""

import logging
import os
from typing import Any, Callable, Coroutine, Dict, List

from atlas.core.cerbos_client import get_cerbos_client

logger = logging.getLogger(__name__)

# Feature flag to enable/disable Cerbos integration
CERBOS_ENABLED = os.getenv("FEATURE_CERBOS_ENABLED", "true").lower() in ("true", "1")


async def check_tool_access(
    user_email: str,
    server_name: str,
    tool_name: str,
    server_config: Dict[str, Any],
    user_roles: List[str],
    user_attrs: Dict[str, Any],
) -> bool:
    """Check if a user can invoke a specific MCP tool via Cerbos.

    This is the fine-grained check. It runs *after* the coarse group-based
    server authorization (get_authorized_servers), adding tool-level control.

    Args:
        user_email: The authenticated user's email.
        server_name: The MCP server name.
        tool_name: The specific tool being invoked.
        server_config: The server's config dict from mcp.json.
        user_roles: Cerbos roles for this user (e.g. ["admin", "user"]).
        user_attrs: Cerbos principal attributes (authorized_servers, compliance levels, etc.).

    Returns:
        True if the tool invocation is allowed.
    """
    if not CERBOS_ENABLED:
        return True

    cerbos = get_cerbos_client()
    result = await cerbos.check_action(
        principal_id=user_email,
        principal_roles=user_roles,
        principal_attrs=user_attrs,
        resource_kind="mcp_tool",
        resource_id=f"{server_name}:{tool_name}",
        resource_attrs={
            "server_name": server_name,
            "tool_name": tool_name,
            "compliance_level": server_config.get("compliance_level", "Public"),
            "sandbox_allowed": server_config.get("sandbox_allowed", True),
        },
        action="invoke",
    )
    return result


async def check_data_source_access(
    user_email: str,
    source_id: str,
    classification_level: str,
    user_roles: List[str],
    user_attrs: Dict[str, Any],
) -> bool:
    """Check if a user/agent can access a RAG data source."""
    if not CERBOS_ENABLED:
        return True

    cerbos = get_cerbos_client()
    return await cerbos.check_action(
        principal_id=user_email,
        principal_roles=user_roles,
        principal_attrs=user_attrs,
        resource_kind="data_source",
        resource_id=source_id,
        resource_attrs={
            "id": source_id,
            "classification_level": classification_level,
        },
        action="read",
    )


async def get_authorized_servers_with_cerbos(
    user_email: str,
    servers_config: Dict[str, Any],
    group_check_func: Callable[..., Coroutine],
    user_roles: List[str],
    user_attrs: Dict[str, Any],
) -> List[str]:
    """Enhanced server authorization using both group RBAC and Cerbos policies.

    First applies the existing group-based filter, then additionally checks
    Cerbos policies for each server. This is additive security: a server must
    pass BOTH checks to be authorized.

    Args:
        user_email: The authenticated user.
        servers_config: Full MCP servers config dict.
        group_check_func: The existing is_user_in_group function.
        user_roles: Cerbos roles for this user.
        user_attrs: Cerbos principal attributes.

    Returns:
        List of authorized server names.
    """
    authorized = []

    for server_name, server_config in servers_config.items():
        if not server_config.get("enabled", True):
            continue

        # Step 1: Existing group-based RBAC
        required_groups = server_config.get("groups", [])
        if required_groups:
            group_checks = [
                await group_check_func(user_email, group)
                for group in required_groups
            ]
            if not any(group_checks):
                continue

        # Step 2: Cerbos policy check (if enabled)
        if CERBOS_ENABLED:
            cerbos = get_cerbos_client()
            result = await cerbos.check_action(
                principal_id=user_email,
                principal_roles=user_roles,
                principal_attrs=user_attrs,
                resource_kind="mcp_tool",
                resource_id=f"{server_name}:*",
                resource_attrs={
                    "server_name": server_name,
                    "compliance_level": server_config.get("compliance_level", "Public"),
                    "sandbox_allowed": server_config.get("sandbox_allowed", True),
                },
                action="invoke",
            )
            if not result:
                logger.debug(
                    "Cerbos denied server '%s' for user '%s'",
                    server_name,
                    user_email,
                )
                continue

        authorized.append(server_name)

    return authorized
