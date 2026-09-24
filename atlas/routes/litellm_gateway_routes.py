"""Team and model discovery for enterprise LiteLLM gateways.

The model picker uses these to show a gateway's teams for the current user,
then the models the chosen team may call. The chosen pair becomes an ordinary
model key (``<gateway>::<team_id>::<model_id>``) sent with each chat turn; see
``atlas/modules/config/litellm_gateway_models.py``.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from atlas.core.log_sanitizer import get_current_user, sanitize_for_logging
from atlas.core.model_access import is_model_allowed
from atlas.domain.errors import AuthorizationError, LLMAuthenticationError, LLMServiceError
from atlas.infrastructure.app_factory import app_factory
from atlas.modules.config.litellm_gateway_models import GATEWAY_KEY_SEPARATOR, build_gateway_model_key
from atlas.modules.config.models import LiteLLMGatewayConfig
from atlas.modules.llm.litellm_gateway_client import get_gateway_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/llm/gateways", tags=["llm-gateways"])

# ModelConfig capability flags the model picker needs for gateway models.
_CAPABILITY_FIELDS = ("supports_vision", "supports_pdf", "supports_tools")


async def build_gateway_summaries(llm_config: Any, current_user: str, app_settings: Any) -> List[Dict[str, Any]]:
    """Gateways the user may use, in the shape ``/api/config`` returns them."""
    summaries: List[Dict[str, Any]] = []
    for name, gateway in (getattr(llm_config, "litellm_gateways", None) or {}).items():
        if not await is_model_allowed(gateway, current_user):
            continue
        defaults = gateway.model_defaults
        summary: Dict[str, Any] = {
            "name": name,
            "display_name": gateway.display_name or name,
            "description": gateway.description,
            "supports_vision": bool(defaults.get("supports_vision", False)),
            "supports_pdf": bool(defaults.get("supports_pdf", False)),
            "supports_tools": bool(defaults.get("supports_tools", True)),
        }
        if getattr(app_settings, "feature_compliance_levels_enabled", False) and gateway.compliance_level:
            summary["compliance_level"] = gateway.compliance_level
        summaries.append(summary)
    return summaries


async def _authorized_gateway(gateway_name: str, current_user: str) -> LiteLLMGatewayConfig:
    """The gateway's config, or a 404 when it is unknown or not for this user.

    Unknown and group-restricted gateways answer the same way so the endpoint
    does not reveal gateways a user cannot see.
    """
    llm_config = app_factory.get_config_manager().llm_config
    gateway = (getattr(llm_config, "litellm_gateways", None) or {}).get(gateway_name)
    if gateway is None or not await is_model_allowed(gateway, current_user):
        raise HTTPException(status_code=404, detail=f"LLM gateway '{gateway_name}' not found")
    return gateway


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, AuthorizationError):
        raise HTTPException(status_code=403, detail=exc.message) from None
    if isinstance(exc, LLMAuthenticationError):
        raise HTTPException(status_code=401, detail=exc.message) from None
    if isinstance(exc, LLMServiceError):
        raise HTTPException(status_code=502, detail=exc.message) from None
    raise exc


@router.get("/{gateway_name}/teams")
async def list_gateway_teams(
    gateway_name: str,
    refresh: bool = Query(False, description="Bypass the discovery cache"),
    current_user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """The LiteLLM teams the current user belongs to on this gateway."""
    gateway = await _authorized_gateway(gateway_name, current_user)
    client = get_gateway_client(gateway_name, gateway)
    try:
        teams = await client.list_teams(current_user, refresh=refresh)
    except (AuthorizationError, LLMAuthenticationError, LLMServiceError) as exc:
        _raise_http(exc)
    # A team id containing the key separator cannot be carried in a model key.
    usable = [team for team in teams if GATEWAY_KEY_SEPARATOR not in team.team_id]
    logger.info(
        "Listed %d LiteLLM team(s) on gateway %s for %s",
        len(usable), sanitize_for_logging(gateway_name), sanitize_for_logging(current_user),
    )
    return {
        "gateway": gateway_name,
        "teams": [{"team_id": team.team_id, "label": team.label} for team in usable],
    }


@router.get("/{gateway_name}/models")
async def list_gateway_models(
    gateway_name: str,
    team_id: str = Query(..., min_length=1),
    refresh: bool = Query(False, description="Bypass the discovery cache"),
    current_user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """The models one of the user's teams may call, as selectable model keys."""
    gateway = await _authorized_gateway(gateway_name, current_user)
    client = get_gateway_client(gateway_name, gateway)
    try:
        team = await client.require_team(current_user, team_id)
        model_ids = await client.list_models(current_user, team_id, refresh=refresh)
    except (AuthorizationError, LLMAuthenticationError, LLMServiceError) as exc:
        _raise_http(exc)
    models: List[Dict[str, Optional[str]]] = []
    for model_id in model_ids:
        models.append({
            "name": build_gateway_model_key(gateway_name, team.team_id, model_id),
            "model_id": model_id,
            "label": model_id,
        })
    return {
        "gateway": gateway_name,
        "team_id": team.team_id,
        "team_label": team.label,
        "models": models,
    }
