"""Delegated tokens for LLM endpoints.

LiteLLM gateways with ``auth_type: "delegated"`` and models with
``api_key_source: "delegated"`` both send a token Atlas obtains for the
logged-in user: their OIDC access token, exchanged through the configured
delegation provider (RFC 8693 token exchange or Entra On-Behalf-Of) for a
short-lived token for the endpoint. Neither falls back to another credential.
"""

import logging
from typing import Optional

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.domain.errors import LLMAuthenticationError

logger = logging.getLogger(__name__)


async def mint_delegated_llm_token(
    user_email: Optional[str], delegation, *, endpoint: str, actor: str
) -> str:
    """Return a delegated token for ``endpoint`` (e.g. "LiteLLM gateway 'teams'").

    ``delegation`` carries the token's audience, resource and scope. Raises
    LLMAuthenticationError when no token can be obtained.
    """
    from atlas.core.oidc import mcp_delegation
    from atlas.core.oidc.delegation import (
        DelegationError,
        DelegationRequest,
        get_delegation_manager_async,
    )

    log_endpoint = sanitize_for_logging(endpoint)
    if not user_email:
        raise LLMAuthenticationError(f"{endpoint} needs a signed-in user.")
    manager = await get_delegation_manager_async()
    if manager is None:
        logger.error("%s uses delegated auth but OIDC delegation is not configured", log_endpoint)
        raise LLMAuthenticationError(f"{endpoint} requires delegated sign-in, which is not configured.")
    subject_token = await mcp_delegation.resolve_subject_token(user_email)
    if not subject_token:
        raise LLMAuthenticationError(
            f"Your sign-in session has no token to present to {endpoint}. Please sign in again."
        )
    request = DelegationRequest(
        user_id=user_email,
        subject_token=subject_token,
        audience=delegation.audience if delegation else None,
        resource=delegation.resource if delegation else None,
        scope=delegation.scope if delegation else None,
        actor=actor,
    )
    try:
        token = await manager.get_token(request)
    except DelegationError as exc:
        logger.error(
            "Delegated token exchange failed for %s: %s", log_endpoint, sanitize_for_logging(str(exc))
        )
        raise LLMAuthenticationError(f"Could not obtain a token for {endpoint}. Please sign in again.") from None
    return token.access_token
