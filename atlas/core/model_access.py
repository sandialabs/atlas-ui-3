"""Per-model group-based access control.

A model in ``llmconfig.yml`` may declare an optional ``groups`` list. When the
list is empty (the default) the model is available to everyone, preserving the
historical behavior. When it is non-empty, only users who belong to at least one
of the listed groups may see or use that model.

This mirrors the ``groups`` access-control convention already used for MCP
servers (``MCPServerConfig``) and RAG sources (``RAGSourceConfig``), and funnels
every membership decision through :func:`atlas.core.auth.is_user_in_group`.
"""

import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from atlas.core.auth import is_user_in_group

logger = logging.getLogger(__name__)

# An async ``(user_email, group_id) -> bool`` membership check. Injectable so the
# helpers stay unit-testable without a live authorization backend.
AuthCheckFunc = Callable[[str, str], Awaitable[bool]]


def _model_groups(model_config: Any) -> list:
    """Return the configured access groups for a model, tolerating dict or model."""
    if model_config is None:
        return []
    if isinstance(model_config, dict):
        groups = model_config.get("groups")
    else:
        groups = getattr(model_config, "groups", None)
    return list(groups) if groups else []


async def is_model_allowed(
    model_config: Any,
    user_email: Optional[str],
    auth_check_func: Optional[AuthCheckFunc] = None,
) -> bool:
    """Return True if ``user_email`` may access the model described by ``model_config``.

    Access rules (identical to the MCP/RAG ``groups`` convention):
    - No ``groups`` configured (empty/absent) -> allowed for everyone.
    - ``groups`` configured -> allowed only if the user is a member of at least
      one listed group. A missing user is denied when a restriction exists.

    ``auth_check_func`` defaults to :func:`atlas.core.auth.is_user_in_group`;
    it is resolved at call time so it stays injectable/patchable in tests.
    """
    required_groups = _model_groups(model_config)
    if not required_groups:
        return True
    if not user_email:
        return False
    check = auth_check_func or is_user_in_group
    for group in required_groups:
        try:
            if await check(user_email, group):
                return True
        except Exception:
            # Fail closed on this group; a backend hiccup must not grant access.
            logger.debug(
                "Group membership check failed for group %s; treating as not-a-member",
                group,
                exc_info=True,
            )
    return False


async def filter_authorized_models(
    models: Dict[str, Any],
    user_email: Optional[str],
    auth_check_func: Optional[AuthCheckFunc] = None,
) -> Dict[str, Any]:
    """Return only the ``{name: model_config}`` entries the user may access."""
    authorized: Dict[str, Any] = {}
    for name, model_config in (models or {}).items():
        if await is_model_allowed(model_config, user_email, auth_check_func):
            authorized[name] = model_config
    return authorized
