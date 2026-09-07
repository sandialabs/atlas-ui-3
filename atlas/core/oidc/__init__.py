"""OIDC login, confidential-client authentication, and delegated OAuth credentials.

Three related capabilities live under this package:

1. :mod:`atlas.core.oidc.oidc_client` -- interactive login via the OIDC
   Authorization Code flow with PKCE, with Atlas acting as the relying party.
2. :mod:`atlas.core.oidc.client_authentication` -- Atlas authenticating itself
   to the IdP as a *confidential* client (client secret or, preferably, a
   private key assertion per RFC 7523).
3. :mod:`atlas.core.oidc.delegation` -- pluggable delegated downstream
   authorization: RFC 8693 token exchange and Microsoft Entra ID On-Behalf-Of.

The trusted-header auth mode is unchanged and remains the default; OIDC login
is opt-in via ``FEATURE_OIDC_AUTH_ENABLED``.
"""

from atlas.core.oidc.discovery import ProviderMetadata, get_provider_metadata
from atlas.core.oidc.oidc_client import (
    build_authorize_url,
    exchange_code_for_tokens,
    generate_pkce_pair,
    refresh_access_token,
    validate_id_token,
)
from atlas.core.oidc.session import OIDCSession, get_session_store

__all__ = [
    "ProviderMetadata",
    "get_provider_metadata",
    "build_authorize_url",
    "exchange_code_for_tokens",
    "generate_pkce_pair",
    "refresh_access_token",
    "validate_id_token",
    "OIDCSession",
    "get_session_store",
]
