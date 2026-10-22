"""Startup validation of the configured authentication modes.

Atlas authenticates users through trusted-header auth (an identity header set
by a reverse proxy), OIDC login, or both. ``FEATURE_HEADER_AUTH_ENABLED=false``
turns header auth off completely, which only makes sense when OIDC login is
there to take its place. Without either mode no request could ever
authenticate, and a deployment that asked for OIDC-only but lost OIDC at
startup (missing secret, issuer or client id) must not quietly serve with no
working login, so both cases stop startup instead.
"""

import logging

logger = logging.getLogger(__name__)


class AuthConfigurationError(RuntimeError):
    """The configured authentication modes leave no way to sign in."""


def validate_auth_modes(app_settings, *, oidc_requested: bool) -> None:
    """Fail startup when no authentication mode is available.

    ``oidc_requested`` is the operator's original ``FEATURE_OIDC_AUTH_ENABLED``
    value, before startup disabled OIDC for missing configuration; it only
    shapes the error message.
    """
    if app_settings.feature_header_auth_enabled:
        return
    if app_settings.feature_oidc_auth_enabled:
        logger.info(
            "Header auth is disabled (FEATURE_HEADER_AUTH_ENABLED=false): "
            "OIDC login is the only way to authenticate."
        )
        return
    if oidc_requested:
        raise AuthConfigurationError(
            "FEATURE_HEADER_AUTH_ENABLED=false requires OIDC login, but OIDC auth "
            "was disabled at startup because its configuration is incomplete "
            "(see the preceding log line). Fix the OIDC settings or re-enable "
            "header auth."
        )
    raise AuthConfigurationError(
        "No authentication mode is enabled: FEATURE_HEADER_AUTH_ENABLED and "
        "FEATURE_OIDC_AUTH_ENABLED are both false. Enable at least one."
    )
