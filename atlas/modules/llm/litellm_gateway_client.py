"""Client for enterprise LiteLLM proxies with team-scoped models.

Three LiteLLM proxy calls back the team-then-model selection:

- ``GET /team/list?user_id=<id>``   -- the teams the user belongs to
- ``GET /models?team_id=<team_id>`` -- the models that team may call
- ``POST /chat/completions``        -- made by LiteLLM's SDK in the caller,
  with the team in the ``x-litellm-team-id`` header (configurable)

The credential for all three is either a static service key
(``auth_type: "system"``) or a short-lived token minted from the user's OIDC
login by the existing delegation layer (``auth_type: "delegated"``, which
covers Microsoft Entra On-Behalf-Of).

Atlas only sends a team header for a team the gateway lists for the user.
With a delegated token LiteLLM enforces that itself; with a shared service
key it cannot tell users apart, so the check here is what stops a crafted
model key from charging someone else's team.
"""

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import httpx

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.domain.errors import AuthorizationError, LLMAuthenticationError, LLMServiceError
from atlas.modules.config.litellm_gateway_models import GatewayModelRef
from atlas.modules.config.models import LiteLLMGatewayConfig, resolve_env_var

logger = logging.getLogger(__name__)

# Minimum age of a cached team or model list before a user-triggered refresh
# goes back to LiteLLM.
FORCED_REFRESH_MIN_INTERVAL_SECONDS = 10.0


@dataclass(frozen=True)
class GatewayTeam:
    team_id: str
    label: str


@dataclass(frozen=True)
class GatewayCredential:
    """The bearer token for LiteLLM and the user id LiteLLM knows the user by."""

    bearer_token: str
    litellm_user_id: str


def _decode_jwt_claims(token: str) -> Dict[str, Any]:
    """Read a JWT's claims without verifying it.

    Only used on a token Atlas just received from its own identity provider,
    to learn the user's object id; never as an authorization decision.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def parse_team_list(payload: Any) -> List[GatewayTeam]:
    """Normalize a ``/team/list`` response into teams.

    LiteLLM returns a bare list of team objects; the ``teams``/``data``
    envelopes are accepted for the paginated ``/v2/team/list`` shape.
    """
    items = payload
    if isinstance(payload, dict):
        items = payload.get("teams") or payload.get("data") or []
    teams: List[GatewayTeam] = []
    seen = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        team_id = item.get("team_id") or item.get("teamId") or item.get("id")
        if not isinstance(team_id, str) or not team_id.strip() or team_id in seen:
            continue
        seen.add(team_id)
        label = item.get("team_alias") or item.get("teamAlias") or item.get("label") or team_id
        teams.append(GatewayTeam(team_id=team_id, label=str(label)))
    return teams


def parse_model_list(payload: Any) -> List[str]:
    """Normalize an OpenAI-style ``/models`` response into model ids."""
    items = payload.get("data", []) if isinstance(payload, dict) else payload
    models: List[str] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("id") or item.get("model_name") or item.get("name")
        else:
            continue
        if isinstance(name, str) and name and name not in models:
            models.append(name)
    return models


class LiteLLMGatewayClient:
    """Team and model discovery plus per-call auth for one gateway."""

    def __init__(
        self,
        name: str,
        config: LiteLLMGatewayConfig,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        # Gateway names come from configuration, but routes pass them through
        # from the request path; log a sanitized copy.
        self._log_name = sanitize_for_logging(name)
        self.config = config
        self._transport = transport
        self._clock = clock
        self._team_cache: Dict[str, Tuple[float, List[GatewayTeam]]] = {}
        self._model_cache: Dict[Tuple[str, str], Tuple[float, List[str]]] = {}

    # -- credentials -----------------------------------------------------

    async def get_credential(self, user_email: Optional[str]) -> GatewayCredential:
        if not user_email:
            raise LLMAuthenticationError(
                f"LiteLLM gateway '{self.name}' needs a signed-in user."
            )
        if self.config.auth_type == "delegated":
            token = await self._mint_delegated_token(user_email)
        else:
            try:
                token = resolve_env_var(self.config.api_key) or ""
            except ValueError as exc:
                logger.error(
                "LiteLLM gateway '%s' API key is not configured: %s",
                self._log_name, sanitize_for_logging(str(exc)),
            )
                raise LLMAuthenticationError(
                    f"LiteLLM gateway '{self.name}' is not configured with an API key."
                ) from None
        if not token:
            raise LLMAuthenticationError(
                f"LiteLLM gateway '{self.name}' has no credential to present."
            )
        return GatewayCredential(bearer_token=token, litellm_user_id=self._litellm_user_id(user_email, token))

    async def _mint_delegated_token(self, user_email: str) -> str:
        from atlas.core.oidc import mcp_delegation
        from atlas.core.oidc.delegation import (
            DelegationError,
            DelegationRequest,
            get_delegation_manager_async,
        )

        manager = await get_delegation_manager_async()
        if manager is None:
            logger.error(
                "LiteLLM gateway '%s' uses delegated auth but OIDC delegation is not configured",
                self._log_name,
            )
            raise LLMAuthenticationError(
                f"LiteLLM gateway '{self.name}' requires delegated sign-in, which is not configured."
            )
        subject_token = await mcp_delegation.resolve_subject_token(user_email)
        if not subject_token:
            raise LLMAuthenticationError(
                "Your sign-in session has no token to present to the LiteLLM gateway. "
                "Please sign in again."
            )
        delegation = self.config.delegation
        request = DelegationRequest(
            user_id=user_email,
            subject_token=subject_token,
            audience=delegation.audience if delegation else None,
            resource=delegation.resource if delegation else None,
            scope=delegation.scope if delegation else None,
            actor=f"litellm-gateway:{self.name}",
        )
        try:
            token = await manager.get_token(request)
        except DelegationError as exc:
            logger.error(
                "Delegated token exchange failed for LiteLLM gateway '%s': %s",
                self._log_name, sanitize_for_logging(str(exc)),
            )
            raise LLMAuthenticationError(
                f"Could not obtain a token for LiteLLM gateway '{self.name}'. Please sign in again."
            ) from None
        return token.access_token

    def _litellm_user_id(self, user_email: str, token: str) -> str:
        if self.config.effective_user_id_source() == "token_claim":
            claim = _decode_jwt_claims(token).get(self.config.user_id_claim)
            if isinstance(claim, str) and claim.strip():
                return claim
            raise LLMAuthenticationError(
                f"The LiteLLM gateway token has no '{self.config.user_id_claim}' claim "
                "to identify the user."
            )
        suffix = self.config.user_id_strip_suffix
        if suffix and user_email.lower().endswith(suffix.lower()) and len(user_email) > len(suffix):
            return user_email[: -len(suffix)]
        return user_email

    # -- HTTP --------------------------------------------------------------

    def _url(self, path: str) -> str:
        try:
            base = self.config.resolved_base_url()
        except ValueError:
            logger.error("LiteLLM gateway '%s' base_url names an unset environment variable", self._log_name)
            raise LLMServiceError(f"LiteLLM gateway '{self.name}' is not configured.") from None
        base = base if base.endswith("/") else base + "/"
        return urljoin(base, path.lstrip("/"))

    async def _get_json(self, path: str, params: Dict[str, str], bearer_token: str) -> Any:
        url = self._url(path)
        headers = {"Accept": "application/json"}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=self.config.discovery_timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            logger.error(
                "LiteLLM gateway '%s' unreachable at %s: %s",
                self._log_name, sanitize_for_logging(path), type(exc).__name__,
            )
            raise LLMServiceError(f"LiteLLM gateway '{self.name}' is unreachable.") from None
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "LiteLLM gateway '%s' GET %s -> %s in %.0f ms",
            self._log_name, sanitize_for_logging(path), response.status_code, elapsed_ms,
        )
        if response.status_code in (401, 403):
            raise LLMAuthenticationError(
                f"LiteLLM gateway '{self.name}' rejected the credentials (HTTP {response.status_code})."
            )
        if response.status_code >= 400:
            raise LLMServiceError(
                f"LiteLLM gateway '{self.name}' returned HTTP {response.status_code} for {path}."
            )
        try:
            return response.json()
        except ValueError:
            raise LLMServiceError(f"LiteLLM gateway '{self.name}' returned a non-JSON response.") from None

    # -- discovery -----------------------------------------------------------

    def _fresh(self, entry: Optional[Tuple[float, Any]], refresh: bool = False) -> bool:
        """Whether a cache entry may be served.

        A forced refresh is honored only once the entry is a few seconds old:
        refreshes are triggered by users (``?refresh=true``, a made-up team id)
        and each one is an uncached call to LiteLLM.
        """
        if entry is None:
            return False
        age = self._clock() - entry[0]
        if refresh:
            return age < FORCED_REFRESH_MIN_INTERVAL_SECONDS
        return age < self.config.discovery_cache_seconds

    async def list_teams(self, user_email: Optional[str], *, refresh: bool = False) -> List[GatewayTeam]:
        cache_key = (user_email or "").lower()
        cached = self._team_cache.get(cache_key)
        if self._fresh(cached, refresh):
            return cached[1]
        credential = await self.get_credential(user_email)
        payload = await self._get_json(
            self.config.team_list_path, {"user_id": credential.litellm_user_id}, credential.bearer_token
        )
        teams = parse_team_list(payload)
        self._team_cache[cache_key] = (self._clock(), teams)
        return teams

    async def list_models(
        self, user_email: Optional[str], team_id: str, *, refresh: bool = False
    ) -> List[str]:
        await self.require_team(user_email, team_id, refresh=refresh)
        cache_key = ((user_email or "").lower(), team_id)
        cached = self._model_cache.get(cache_key)
        if self._fresh(cached, refresh):
            return cached[1]
        credential = await self.get_credential(user_email)
        payload = await self._get_json(
            self.config.models_path, {"team_id": team_id}, credential.bearer_token
        )
        models = parse_model_list(payload)
        self._model_cache[cache_key] = (self._clock(), models)
        return models

    async def require_team(
        self, user_email: Optional[str], team_id: str, *, refresh: bool = False
    ) -> GatewayTeam:
        """Return the team if the gateway lists it for this user, else refuse."""
        teams = await self.list_teams(user_email, refresh=refresh)
        match = next((team for team in teams if team.team_id == team_id), None)
        if match is None and not refresh:
            # The team may have been granted since the cache filled.
            teams = await self.list_teams(user_email, refresh=True)
            match = next((team for team in teams if team.team_id == team_id), None)
        if match is None:
            logger.warning(
                "Refused LiteLLM gateway '%s' team %s for user %s: not a member",
                self._log_name, sanitize_for_logging(team_id), sanitize_for_logging(user_email or ""),
            )
            raise AuthorizationError(
                "You are not a member of the selected LiteLLM team. Choose another team.",
                code="LLM_TEAM_ACCESS_DENIED",
            )
        return match

    # -- chat calls --------------------------------------------------------------

    async def apply_request_auth(
        self, ref: GatewayModelRef, user_email: Optional[str], model_kwargs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Add the bearer token and team header to LiteLLM call kwargs.

        The team and the model are both checked: the model part of the key is
        client-supplied, and with a shared service key LiteLLM may not apply
        the header team's model allowlist itself.
        """
        await self.require_team(user_email, ref.team_id)
        models = await self.list_models(user_email, ref.team_id)
        if ref.model_id not in models:
            models = await self.list_models(user_email, ref.team_id, refresh=True)
        if ref.model_id not in models:
            logger.warning(
                "Refused LiteLLM gateway '%s' model %s for team %s: not in the team's models",
                self._log_name, sanitize_for_logging(ref.model_id), sanitize_for_logging(ref.team_id),
            )
            raise AuthorizationError(
                "The selected model is not available to the selected LiteLLM team.",
                code="LLM_TEAM_MODEL_DENIED",
            )
        credential = await self.get_credential(user_email)
        if credential.bearer_token:
            model_kwargs["api_key"] = credential.bearer_token
        headers = {
            key: value
            for key, value in (model_kwargs.get("extra_headers") or {}).items()
            if key.lower() != self.config.team_header.lower()
        }
        headers[self.config.team_header] = ref.team_id
        model_kwargs["extra_headers"] = headers
        return model_kwargs


_clients: Dict[str, Tuple[LiteLLMGatewayConfig, LiteLLMGatewayClient]] = {}


def get_gateway_client(name: str, config: LiteLLMGatewayConfig) -> LiteLLMGatewayClient:
    """Return the shared client for a gateway, rebuilt when its config changes."""
    cached = _clients.get(name)
    if cached is not None and cached[0] == config:
        return cached[1]
    client = LiteLLMGatewayClient(name, config)
    _clients[name] = (config, client)
    return client


def reset_gateway_clients() -> None:
    """Forget every gateway client and its caches (tests, config reload)."""
    _clients.clear()
