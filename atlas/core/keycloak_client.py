"""Keycloak OIDC client for ATLAS.

Handles token validation, user info extraction, role mapping to Cerbos,
and service-account token exchange for agent credentials.
"""

import logging
import os
import time
from typing import Any, Dict, Optional

import httpx
import jwt
from jwt import PyJWKClient

logger = logging.getLogger(__name__)

# Configuration from environment
KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://keycloak:8080/auth")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "atlas")
KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "atlas-ui")
KEYCLOAK_AGENT_CLIENT_ID = os.getenv("KEYCLOAK_AGENT_CLIENT_ID", "atlas-agent-service")
KEYCLOAK_AGENT_CLIENT_SECRET = os.getenv("KEYCLOAK_AGENT_CLIENT_SECRET", "atlas-agent-service-secret")
FEATURE_KEYCLOAK_ENABLED = os.getenv("FEATURE_KEYCLOAK_ENABLED", "false").lower() in ("true", "1")

# OIDC endpoints (derived from well-known)
_REALM_URL = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
_TOKEN_URL = f"{_REALM_URL}/protocol/openid-connect/token"
_USERINFO_URL = f"{_REALM_URL}/protocol/openid-connect/userinfo"
_CERTS_URL = f"{_REALM_URL}/protocol/openid-connect/certs"

# Role mapping: Keycloak realm roles -> Cerbos roles
ROLE_MAPPING = {
    "atlas-admin": "admin",
    "atlas-operator": "operator",
    "atlas-user": "user",
    "atlas-viewer": "viewer",
    "hpc-user": "hpc_user",
    "classified-access": "classified",
}


class KeycloakClient:
    """Handles Keycloak OIDC operations for ATLAS."""

    def __init__(self):
        self._jwks_client: Optional[PyJWKClient] = None
        self._service_token: Optional[str] = None
        self._service_token_expiry: float = 0
        self._available: Optional[bool] = None

    @property
    def enabled(self) -> bool:
        return FEATURE_KEYCLOAK_ENABLED

    def _get_jwks_client(self) -> PyJWKClient:
        """Lazy-init JWK client for token verification."""
        if self._jwks_client is None:
            self._jwks_client = PyJWKClient(_CERTS_URL)
        return self._jwks_client

    async def is_healthy(self) -> bool:
        """Check if Keycloak is reachable."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{_REALM_URL}/.well-known/openid-configuration")
                healthy = resp.status_code == 200
                if healthy and not self._available:
                    logger.info("Keycloak is available at %s", _REALM_URL)
                self._available = healthy
                return healthy
        except (httpx.RequestError, httpx.HTTPStatusError):
            if self._available is not False:
                logger.warning("Keycloak unreachable at %s", _REALM_URL)
            self._available = False
            return False

    def validate_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Validate a JWT access token from Keycloak.

        Returns the decoded claims if valid, None otherwise.
        """
        try:
            jwks = self._get_jwks_client()
            signing_key = jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=KEYCLOAK_CLIENT_ID,
                options={"verify_exp": True},
            )
            return claims
        except jwt.ExpiredSignatureError:
            logger.debug("Keycloak token expired")
            return None
        except jwt.InvalidTokenError as exc:
            logger.warning("Invalid Keycloak token: %s", exc)
            return None
        except Exception as exc:
            logger.error("Keycloak token validation error: %s", exc)
            return None

    def extract_user_info(self, claims: Dict[str, Any]) -> Dict[str, Any]:
        """Extract user identity and attributes from Keycloak JWT claims.

        Returns a dict with:
        - email: user email
        - username: preferred_username
        - roles: Cerbos-mapped roles
        - groups: Keycloak group memberships
        - keycloak_roles: raw Keycloak realm roles
        """
        email = claims.get("email", claims.get("preferred_username", "unknown"))
        username = claims.get("preferred_username", email)

        # Extract realm roles from token
        keycloak_roles = claims.get("realm_roles", [])
        if not keycloak_roles:
            # Fallback: some Keycloak configs put roles under realm_access
            realm_access = claims.get("realm_access", {})
            keycloak_roles = realm_access.get("roles", [])

        # Map to Cerbos roles
        cerbos_roles = []
        for kc_role in keycloak_roles:
            mapped = ROLE_MAPPING.get(kc_role)
            if mapped:
                cerbos_roles.append(mapped)

        if not cerbos_roles:
            cerbos_roles = ["viewer"]

        # Extract groups
        groups = claims.get("groups", [])

        return {
            "email": email,
            "username": username,
            "roles": cerbos_roles,
            "groups": groups,
            "keycloak_roles": keycloak_roles,
            "sub": claims.get("sub"),
        }

    async def get_service_token(self) -> Optional[str]:
        """Get a service-account token for backend-to-backend calls.

        Uses client_credentials grant with the atlas-agent-service client.
        Caches the token until near expiry.
        """
        now = time.time()
        if self._service_token and now < self._service_token_expiry - 30:
            return self._service_token

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    _TOKEN_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": KEYCLOAK_AGENT_CLIENT_ID,
                        "client_secret": KEYCLOAK_AGENT_CLIENT_SECRET,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            self._service_token = data["access_token"]
            self._service_token_expiry = now + data.get("expires_in", 300)
            return self._service_token

        except Exception as exc:
            logger.error("Failed to get Keycloak service token: %s", exc)
            return None

    async def exchange_token_for_agent(
        self,
        user_token: str,
        agent_id: str,
    ) -> Optional[Dict[str, str]]:
        """Exchange a user's token for a scoped agent token.

        Uses Keycloak's token exchange (requires token-exchange feature enabled).
        The agent token inherits the user's roles but has a shorter lifespan
        and can be further constrained by audience.

        Falls back to service account token if token exchange is not available.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    _TOKEN_URL,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                        "client_id": KEYCLOAK_AGENT_CLIENT_ID,
                        "client_secret": KEYCLOAK_AGENT_CLIENT_SECRET,
                        "subject_token": user_token,
                        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                        "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
                        "audience": KEYCLOAK_AGENT_CLIENT_ID,
                    },
                )

                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "access_token": data["access_token"],
                        "expires_in": data.get("expires_in", 300),
                        "token_type": data.get("token_type", "Bearer"),
                        "agent_id": agent_id,
                    }

                # Token exchange not supported; fall back to service account
                logger.info(
                    "Token exchange not available (HTTP %d); using service account for agent %s",
                    resp.status_code,
                    agent_id,
                )

        except Exception as exc:
            logger.warning("Token exchange failed: %s; falling back to service account", exc)

        # Fallback: issue a service account token
        svc_token = await self.get_service_token()
        if svc_token:
            return {
                "access_token": svc_token,
                "expires_in": 300,
                "token_type": "Bearer",
                "agent_id": agent_id,
                "note": "service-account-fallback",
            }
        return None

    async def get_user_info(self, token: str) -> Optional[Dict[str, Any]]:
        """Fetch user info from Keycloak's userinfo endpoint."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    _USERINFO_URL,
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code == 200:
                    return resp.json()
                return None
        except Exception as exc:
            logger.error("Failed to get user info from Keycloak: %s", exc)
            return None


# Module-level singleton
_keycloak_client: Optional[KeycloakClient] = None


def get_keycloak_client() -> KeycloakClient:
    """Get or create the module-level Keycloak client singleton."""
    global _keycloak_client
    if _keycloak_client is None:
        _keycloak_client = KeycloakClient()
    return _keycloak_client
