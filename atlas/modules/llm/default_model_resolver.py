"""Pick the model a user starts on when they have not chosen one.

``llmconfig.yml`` may name a starting model in two ways:

- a top-level ``default_model``: a static model name or a gateway model key
  (``<gateway>::<team_id>::<model_id>``), and
- per LiteLLM gateway, ``default_team`` plus ``default_model``.

They are tried in that order (gateways in file order). A static model must be
one the user's ``groups`` allow; a gateway model must be on a gateway the user
may use, in a team the gateway lists for the user, and among the models that
team may call -- the same checks the model picker and every chat turn apply.
When none passes, the user starts on the first static model they may use, and
a gateway-only deployment with no usable default leaves the choice to them.

The model picker reads the result from ``GET /api/llm/default-model``;
``AtlasClient.chat()`` uses it when the caller names no model.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.model_access import AuthCheckFunc, is_model_allowed
from atlas.domain.errors import AuthorizationError, LLMAuthenticationError, LLMServiceError
from atlas.modules.config.litellm_gateway_models import resolve_gateway_ref

logger = logging.getLogger(__name__)

# Failures the gateway client raises for one user's discovery. A default is a
# convenience, so any of these just moves on to the next candidate.
_GATEWAY_ERRORS = (AuthorizationError, LLMAuthenticationError, LLMServiceError)

SOURCE_CONFIGURED = "configured"
SOURCE_FIRST_MODEL = "first_model"


@dataclass(frozen=True)
class DefaultModel:
    """The resolved starting model and why it was chosen."""

    model: Optional[str]
    # "configured" (a default_model / gateway default passed), "first_model"
    # (fallback to the first static model the user may use), or None.
    source: Optional[str]
    # The LiteLLM team's display label, for a gateway model.
    team_label: Optional[str] = None


async def _gateway_default_team_label(
    llm_config: Any, model_key: str, user_email: Optional[str], auth_check: Optional[AuthCheckFunc]
) -> Optional[str]:
    """The team's label when the gateway default is usable by the user, else None."""
    from atlas.modules.llm.litellm_gateway_client import get_gateway_client

    ref = resolve_gateway_ref(llm_config, model_key)
    if ref is None:
        return None
    gateway = llm_config.litellm_gateways[ref.gateway]
    if not await is_model_allowed(gateway, user_email, auth_check):
        return None
    client = get_gateway_client(ref.gateway, gateway)
    try:
        # A user outside the team is refused here, before any model listing.
        team = await client.require_team(user_email, ref.team_id)
        model_ids = await client.list_models(user_email, ref.team_id)
    except _GATEWAY_ERRORS as exc:
        # The gateway client already logs the refusal with its details; say
        # only which configured default was skipped (config data, not user
        # input).
        logger.info(
            "Configured default model %s skipped: %s",
            sanitize_for_logging(model_key),
            type(exc).__name__,
        )
        return None
    if ref.model_id not in model_ids:
        # The team exists for the user but does not offer the model: a typo
        # or a stale default, worth a visible signal for the operator.
        logger.warning(
            "Configured default model %s skipped: team %s does not offer model %s",
            sanitize_for_logging(model_key),
            sanitize_for_logging(ref.team_id),
            sanitize_for_logging(ref.model_id),
        )
        return None
    return team.label


async def resolve_default_model(
    llm_config: Any,
    user_email: Optional[str],
    auth_check: Optional[AuthCheckFunc] = None,
) -> DefaultModel:
    """The model ``user_email`` starts on (see the module docstring)."""
    static_models = getattr(llm_config, "models", None) or {}
    configured = getattr(llm_config, "configured_default_models", None)
    candidates = configured() if callable(configured) else []
    # A static top-level default_model the user may use wins outright; no
    # gateway needs to be asked.
    if candidates and candidates[0] in static_models:
        if await is_model_allowed(static_models[candidates[0]], user_email, auth_check):
            return DefaultModel(candidates[0], SOURCE_CONFIGURED)
    # Probe every gateway candidate at once, so per-gateway discovery
    # timeouts do not stack; the first usable one in preference order wins.
    gateway_candidates = [name for name in candidates if name not in static_models]
    labels = dict(zip(
        gateway_candidates,
        await asyncio.gather(*(
            _gateway_default_team_label(llm_config, name, user_email, auth_check)
            for name in gateway_candidates
        )),
    ))
    for name in candidates:
        static = static_models.get(name)
        if static is not None:
            if await is_model_allowed(static, user_email, auth_check):
                return DefaultModel(name, SOURCE_CONFIGURED)
            continue
        if labels.get(name) is not None:
            return DefaultModel(name, SOURCE_CONFIGURED, labels[name])
    for name, model_config in static_models.items():
        if await is_model_allowed(model_config, user_email, auth_check):
            return DefaultModel(name, SOURCE_FIRST_MODEL)
    if candidates or static_models:
        logger.info("No configured or static default model is usable for this user")
    return DefaultModel(None, None)
