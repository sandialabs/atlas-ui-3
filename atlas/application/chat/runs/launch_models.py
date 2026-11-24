"""The LLM models a user may launch a sub-conversation on (issue #1227).

Launch discovery offers the same models the main chat model picker does:

- statically configured ``models`` the user's groups allow, and
- every model of every LiteLLM team the user belongs to on each gateway the
  user's groups allow, as ``<gateway>::<team_id>::<model_id>`` keys -- the
  same team and model lists the picker reads from
  ``/api/llm/gateways/{name}/teams`` and ``.../models``.

When the launching turn has an active compliance level, models not approved
for it are dropped, as the picker hides them; launch admission refuses them
at that level anyway, so offering them would only invite a failed launch.

A gateway that cannot be reached, or that refuses this user's credential, is
skipped with a warning: one broken gateway must not hide the models of the
others, and its failure is the same one the picker and chat report.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from atlas.core.compliance import declared_classifications
from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.model_access import filter_authorized_models, is_model_allowed
from atlas.domain.errors import AuthorizationError, LLMAuthenticationError, LLMServiceError
from atlas.modules.config.litellm_gateway_models import build_gateway_model_key, is_key_safe_part
from atlas.modules.config.models import LiteLLMGatewayConfig, lookup_model_config

logger = logging.getLogger(__name__)

AuthCheck = Callable[[str, str], Awaitable[bool]]

# Failures the gateway client raises for one user's discovery; anything else
# is a bug and propagates to the caller's "discovery failed" refusal.
_GATEWAY_DISCOVERY_ERRORS = (AuthorizationError, LLMAuthenticationError, LLMServiceError)


async def _gateway_model_keys(
    name: str, gateway: LiteLLMGatewayConfig, user_email: str
) -> List[str]:
    """Every team model key the user may call on one gateway, or [] on failure."""
    from atlas.modules.llm.litellm_gateway_client import get_gateway_client

    client = get_gateway_client(name, gateway)
    try:
        teams = await client.list_teams(user_email)
    except _GATEWAY_DISCOVERY_ERRORS:
        logger.warning(
            "Launch discovery skipped LiteLLM gateway %s: team listing failed",
            sanitize_for_logging(name),
            exc_info=True,
        )
        return []
    # A team id containing the key separator cannot be carried in a model key
    # (the picker drops it the same way).
    usable = [team for team in teams if is_key_safe_part(team.team_id)]

    async def _team_keys(team_id: str) -> List[str]:
        try:
            model_ids = await client.list_models(user_email, team_id)
        except _GATEWAY_DISCOVERY_ERRORS:
            logger.warning(
                "Launch discovery skipped team %s on LiteLLM gateway %s: model listing failed",
                sanitize_for_logging(team_id),
                sanitize_for_logging(name),
                exc_info=True,
            )
            return []
        return [build_gateway_model_key(name, team_id, model_id) for model_id in model_ids if model_id]

    per_team = await asyncio.gather(*(_team_keys(team.team_id) for team in usable))
    return [key for keys in per_team for key in keys]


async def _authorized_gateway_models(
    llm_config: Any, user_email: str, auth_check: AuthCheck
) -> Dict[str, Any]:
    """``{model_key: ModelConfig}`` for the user's gateway team models."""
    gateways = getattr(llm_config, "litellm_gateways", None) or {}
    keys: List[str] = []
    for name, gateway in gateways.items():
        if not isinstance(gateway, LiteLLMGatewayConfig):
            continue
        if not await is_model_allowed(gateway, user_email, auth_check):
            continue
        keys.extend(await _gateway_model_keys(name, gateway, user_email))
    models: Dict[str, Any] = {}
    for key in keys:
        # Resolved exactly as a chat turn resolves the key, so the model
        # discovery offers is the model launch admission will check.
        model_config = lookup_model_config(llm_config, key)
        if model_config is not None:
            models[key] = model_config
    return models


def _permitted_at_level(models: Dict[str, Any], compliance_level: Optional[str]) -> Dict[str, Any]:
    """Drop models not approved for the active compliance level."""
    if not compliance_level:
        return models
    from atlas.core.compliance import get_compliance_manager

    manager = get_compliance_manager()
    return {
        name: config
        for name, config in models.items()
        if manager.classification_permits(compliance_level, declared_classifications(config))
    }


async def list_launchable_models(
    config_manager: Any,
    user_email: str,
    auth_check: AuthCheck,
    *,
    compliance_level: Optional[str] = None,
) -> Dict[str, Any]:
    """``{model_name: model_config}`` the user may launch a run on.

    ``compliance_level`` is the launching turn's level; it filters only when
    compliance levels are enabled, the same condition chat enforces under.
    """
    llm_config = getattr(config_manager, "llm_config", None)
    models = await filter_authorized_models(
        getattr(llm_config, "models", None) or {}, user_email, auth_check_func=auth_check
    )
    models.update(await _authorized_gateway_models(llm_config, user_email, auth_check))
    app_settings = getattr(config_manager, "app_settings", None)
    if getattr(app_settings, "feature_compliance_levels_enabled", False) is True:
        models = _permitted_at_level(models, compliance_level)
    return models
