"""Central authorization for every MCP capability that can act for a user.

MCP exposes three capabilities that reach user context: tool invocation,
prompt retrieval, and resource reads. They used to enforce different things at
different call sites -- tools had a group and classification choke point,
prompts had only their own per-server auth routing, and resources had no gate
at all -- so an unauthorized server could still inject a prompt into model
context.

This module is the single policy point. Every operation runs the same ordered
checks against the server that owns the capability:

1. the server exists and is enabled;
2. a user identity is present when the operation is attributed to one (prompts
   and resources) or when the server requires group membership;
3. the user matches the server's ``groups`` ACL;
4. the active data classification is one the server declares; and
5. when an operation selects a model, the user may use that model (the
   per-model ``groups`` ACL).

Checks fail closed: a missing server, missing identity, an authorization
backend error, or an unreadable classification declaration all deny.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Optional

from atlas.core.compliance import declared_classifications
from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.model_access import ModelAccessDecision, check_model_access
from atlas.core.user_identity import normalize_user_email

logger = logging.getLogger(__name__)

GroupCheck = Callable[[str, str], Awaitable[bool]]

# Auth types that require a per-user credential. Kept in sync with
# ``UserClientMixin._requires_user_auth``; exposed here so the authorization
# policy can reason about the token boundary without importing the client.
PER_USER_AUTH_TYPES = frozenset({"oauth", "jwt", "bearer", "api_key", "delegated"})


class McpOperation(str, Enum):
    """An MCP capability whose access is being decided."""

    TOOL = "tool"
    PROMPT = "prompt"
    RESOURCE = "resource"


class McpDenialKind(str, Enum):
    """Why an operation was denied, so callers can shape their message."""

    SERVER = "server"
    USER = "user"
    GROUP = "group"
    CLASSIFICATION = "classification"
    MODEL = "model"


@dataclass(frozen=True)
class McpAuthorizationDecision:
    """Result of authorizing one MCP operation.

    ``user_email`` is the normalized identity the decision was made for, so the
    caller can attribute the operation without re-normalizing (or trusting a
    different copy of the value).
    """

    allowed: bool
    operation: McpOperation
    server_name: str
    user_email: str = ""
    denial: Optional[McpDenialKind] = None
    reason: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.allowed


def _server_config(
    server_name: str,
    servers_config: Optional[Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    """Resolve a server's config from the authoritative configuration.

    Discovery entries are deliberately not used as a fallback: during a config
    reload or after a server is disabled/removed, a stale discovery snapshot
    could still carry the old ACL and let authorization pass for a server that
    no longer exists. A missing entry fails closed.
    """
    config = (servers_config or {}).get(server_name)
    return config if isinstance(config, Mapping) else None


def _deny(
    operation: McpOperation,
    server_name: str,
    user_email: str,
    kind: McpDenialKind,
    reason: str,
) -> McpAuthorizationDecision:
    return McpAuthorizationDecision(
        allowed=False,
        operation=operation,
        server_name=server_name,
        user_email=user_email,
        denial=kind,
        reason=reason,
    )


async def authorize_mcp_server(
    server_name: str,
    *,
    operation: McpOperation,
    servers_config: Optional[Mapping[str, Any]],
    user_email: Optional[str],
    compliance_level: Optional[str] = None,
    group_check: Optional[GroupCheck] = None,
    model_name: Optional[str] = None,
    models: Any = None,
) -> McpAuthorizationDecision:
    """Authorize one MCP operation against its owning server.

    ``operation`` decides whether a user identity is mandatory. Prompts and
    resources are always attributed to the requesting user; tools keep the
    historical behaviour where an unauthenticated caller may run a tool on an
    open server (the per-user auth path still refuses a credential-bearing
    server without an identity).

    ``compliance_level`` is the active classification from the trusted request
    context -- never a tool/prompt argument. ``model_name`` is supplied only by
    an operation that chooses a model; when present the user's per-model group
    access is enforced as well.
    """
    normalized_user = normalize_user_email(user_email)
    # `is_user_in_group` forwards the identity to an external authorizer as
    # received; only whitespace is stripped here. Lowercasing is correct for
    # our own comparisons but could change an external service's answer.
    policy_user = user_email.strip() if user_email else ""
    config = _server_config(server_name, servers_config)

    if config is None:
        return _deny(
            operation,
            server_name,
            normalized_user,
            McpDenialKind.SERVER,
            f"MCP server '{server_name}' is not configured",
        )

    if not config.get("enabled", True):
        return _deny(
            operation,
            server_name,
            normalized_user,
            McpDenialKind.SERVER,
            f"MCP server '{server_name}' is disabled",
        )

    # Prompts and resources are injected into model context under a user's
    # authority, so an unattributed request is refused even on an open server.
    attributed = operation in (McpOperation.PROMPT, McpOperation.RESOURCE)
    if attributed and not normalized_user:
        return _deny(
            operation,
            server_name,
            normalized_user,
            McpDenialKind.USER,
            f"MCP {operation.value} '{server_name}' requires an authenticated user",
        )

    required_groups = config.get("groups") or []
    if required_groups:
        if not normalized_user:
            return _deny(
                operation,
                server_name,
                normalized_user,
                McpDenialKind.USER,
                f"MCP {operation.value} '{server_name}' requires an authenticated user",
            )

        if group_check is None:
            # Resolve at call time so tests can patch ``atlas.core.auth`` and a
            # custom authorizer is honoured.
            from atlas.core.auth import is_user_in_group

            group_check = is_user_in_group

        try:
            checks = [
                await group_check(policy_user, group) for group in required_groups
            ]
        except Exception:
            logger.warning(
                "MCP %s authorization check failed for server '%s' user '%s'; "
                "failing closed",
                operation.value,
                sanitize_for_logging(server_name),
                sanitize_for_logging(normalized_user),
                exc_info=True,
            )
            checks = []

        if not any(checks):
            return _deny(
                operation,
                server_name,
                normalized_user,
                McpDenialKind.GROUP,
                f"MCP {operation.value} '{server_name}' is not authorized for this user",
            )

    if compliance_level:
        permitted = False
        compliance_mgr = None
        try:
            from atlas.core.compliance import get_compliance_manager

            compliance_mgr = get_compliance_manager()
            permitted = compliance_mgr.classification_permits(
                compliance_level, declared_classifications(config)
            )
        except Exception:
            logger.warning(
                "Could not check the classification of MCP server '%s'; denying %s",
                sanitize_for_logging(server_name),
                operation.value,
                exc_info=True,
            )
        if not permitted:
            # The user reads this level name, so show its configured friendly
            # label (issue #1117); the check above compared canonical names.
            level_label = (
                compliance_mgr.get_display_name(compliance_level)
                if compliance_mgr is not None
                else None
            ) or compliance_level
            return _deny(
                operation,
                server_name,
                normalized_user,
                McpDenialKind.CLASSIFICATION,
                f"MCP {operation.value} '{server_name}' is not approved for "
                f"{level_label} data",
            )

    if model_name:
        # A server must not be able to pick a model outside the requester's
        # per-model ACL. Without a readable model catalogue the check cannot
        # be made, so it denies rather than failing open. A *configured* model
        # the user may not use is denied immediately; an unknown name in an
        # otherwise readable catalogue is left to the LLM caller to reject,
        # matching chat's own model gate.
        if models is None:
            return _deny(
                operation,
                server_name,
                normalized_user,
                McpDenialKind.MODEL,
                f"MCP {operation.value} '{server_name}' cannot verify the "
                "selected model",
            )
        decision = await check_model_access(
            models, model_name, policy_user, context=f"MCP {operation.value}"
        )
        if decision is ModelAccessDecision.DENIED:
            return _deny(
                operation,
                server_name,
                normalized_user,
                McpDenialKind.MODEL,
                f"MCP {operation.value} '{server_name}' cannot use the selected model",
            )

    return McpAuthorizationDecision(
        allowed=True,
        operation=operation,
        server_name=server_name,
        user_email=normalized_user,
    )


def server_requires_user_auth(config: Optional[Mapping[str, Any]]) -> bool:
    """Whether a server's config needs a per-user credential."""
    if not config:
        return False
    return config.get("auth_type", "none") in PER_USER_AUTH_TYPES
