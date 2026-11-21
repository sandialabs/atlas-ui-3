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


async def _gateway_default_usable(
    llm_config: Any, model_key: str, user_email: Optional[str], auth_check: Optional[AuthCheckFunc]
) -> bool:
    from atlas.modules.llm.litellm_gateway_client import get_gateway_client

    ref = resolve_gateway_ref(llm_config, model_key)
    if ref is None:
        return False
    gateway = llm_config.litellm_gateways[ref.gateway]
    if not await is_model_allowed(gateway, user_email, auth_check):
        return False
    client = get_gateway_client(ref.gateway, gateway)
    try:
        # list_models() confirms membership first (require_team), so a user
        # outside the team is refused before any model listing.
        model_ids = await client.list_models(user_email, ref.team_id)
    except _GATEWAY_ERRORS as exc:
        logger.info(
            "Default model %s not usable for %s: %s",
            sanitize_for_logging(model_key),
            sanitize_for_logging(user_email or ""),
            sanitize_for_logging(str(exc)),
        )
        return False
    return ref.model_id in model_ids


async def resolve_default_model(
    llm_config: Any,
    user_email: Optional[str],
    auth_check: Optional[AuthCheckFunc] = None,
) -> DefaultModel:
    """The model ``user_email`` starts on (see the module docstring)."""
    static_models = getattr(llm_config, "models", None) or {}
    configured = getattr(llm_config, "configured_default_models", None)
    for name in configured() if callable(configured) else []:
        static = static_models.get(name)
        if static is not None:
            if await is_model_allowed(static, user_email, auth_check):
                return DefaultModel(name, SOURCE_CONFIGURED)
            continue
        if await _gateway_default_usable(llm_config, name, user_email, auth_check):
            return DefaultModel(name, SOURCE_CONFIGURED)
    for name, model_config in static_models.items():
        if await is_model_allowed(model_config, user_email, auth_check):
            return DefaultModel(name, SOURCE_FIRST_MODEL)
    return DefaultModel(None, None)
