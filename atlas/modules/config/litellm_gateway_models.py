"""Model keys for team-scoped models reached through a LiteLLM gateway.

A gateway's models are not listed in ``llmconfig.yml``: the user picks a
LiteLLM team and then one of that team's models. The pick is carried through
Atlas as an ordinary model name of the form::

    <gateway>::<team_id>::<model_id>

so everything that already keys on the model name (the chat request, saved
conversations, access checks, the LLM caller) carries the team along with it
without a parallel field. The model id comes last because LiteLLM model ids
routinely contain ``/`` and ``:``; gateway names and team ids may not contain
the separator.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from atlas.modules.config.models import LLMConfig, ModelConfig

GATEWAY_KEY_SEPARATOR = "::"


@dataclass(frozen=True)
class GatewayModelRef:
    """The three parts of a gateway model key."""

    gateway: str
    team_id: str
    model_id: str

    @property
    def key(self) -> str:
        return build_gateway_model_key(self.gateway, self.team_id, self.model_id)


def build_gateway_model_key(gateway: str, team_id: str, model_id: str) -> str:
    """Compose a gateway model key, rejecting parts that would not round-trip."""
    for label, part in (("gateway", gateway), ("team_id", team_id)):
        if not part or GATEWAY_KEY_SEPARATOR in part:
            raise ValueError(f"Invalid {label} for a gateway model key: {part!r}")
    if not model_id:
        raise ValueError("Invalid model_id for a gateway model key: empty")
    return GATEWAY_KEY_SEPARATOR.join((gateway, team_id, model_id))


def parse_gateway_model_key(model_name: str) -> Optional[GatewayModelRef]:
    """Split a gateway model key, or return None for any other model name."""
    if not isinstance(model_name, str):
        return None
    parts = model_name.split(GATEWAY_KEY_SEPARATOR, 2)
    if len(parts) != 3 or not all(part.strip() for part in parts):
        return None
    gateway, team_id, model_id = parts
    return GatewayModelRef(gateway=gateway, team_id=team_id, model_id=model_id)


def resolve_gateway_ref(llm_config: "LLMConfig", model_name: str) -> Optional[GatewayModelRef]:
    """Parse ``model_name`` and confirm it names a configured gateway."""
    ref = parse_gateway_model_key(model_name)
    if ref is None:
        return None
    gateways = getattr(llm_config, "litellm_gateways", None) or {}
    if ref.gateway not in gateways:
        return None
    return ref


def build_gateway_model_config(llm_config: "LLMConfig", model_name: str) -> Optional["ModelConfig"]:
    """Synthesize the ModelConfig for a gateway model key.

    The result is derived from the gateway configuration alone -- no network
    call -- so a saved conversation or a restarted server resolves the same
    model without rediscovery. Whether the user may actually use the team is
    checked separately, per call, by the gateway client.
    """
    from atlas.modules.config.models import ModelConfig

    ref = resolve_gateway_ref(llm_config, model_name)
    if ref is None:
        return None
    gateway = llm_config.litellm_gateways[ref.gateway]
    gateway_label = gateway.display_name or ref.gateway
    fields = {"description": f"{ref.model_id} via {gateway_label}", **gateway.model_defaults}
    fields.update(
        model_name=ref.model_id,
        model_url=gateway.resolved_base_url(),
        # Delegated gateways get their bearer token per call; an empty key
        # here means "no static key".
        api_key=gateway.api_key if gateway.auth_type == "system" else "",
        groups=list(gateway.groups),
        compliance_level=gateway.compliance_level,
        extra_headers=dict(gateway.extra_headers) if gateway.extra_headers else None,
    )
    return ModelConfig(**fields)
