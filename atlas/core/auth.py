"""Authentication and authorization module."""

import hmac
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

import httpx
import jwt

from atlas.modules.config.config_manager import config_manager

logger = logging.getLogger(__name__)

# Cache with TTL for ALB public keys: {(kid, region): (key, expiry_time)}
_alb_key_cache: Dict[Tuple[str, str], Tuple[str, datetime]] = {}


async def is_user_in_group(user_id: str, group_id: str) -> bool:
    """
    Check if a user is in a specified group.

    Resolution order:
    1. Dev-only bypass: when ``DEBUG_MODE=true`` and ``SKIP_AUTHORIZATION_CHECKS``
       is set, return ``True`` for any user/group. This is mutually exclusive with
       a configured external authorizer -- ``AppSettings.validate_skip_authorization_checks_dev_only``
       refuses to start if ``AUTH_GROUP_CHECK_URL`` is also set, or if the flag
       is on outside debug mode / a development environment -- so the bypass can
       only ever override the mock table below, never a real authorization service.
    2. External endpoint: when ``AUTH_GROUP_CHECK_URL`` and ``AUTH_GROUP_CHECK_API_KEY``
       are configured, query the HTTP authorization service for membership.
    3. Mock table (fallback for local development): everyone is in the ``users``
       group; the debug-only mock table grants admin to the configured test users.

    Args:
        user_id: User email/identifier.
        group_id: Group identifier.

    Returns:
        True if the user is in the group, False otherwise.
    """
    app_settings = config_manager.app_settings

    # Dev-only convenience: bypass authorization entirely so a new local user
    # does not have to configure ADMIN_TEST_USER to reach admin-gated routes.
    # This never affects authentication (identity resolution is unchanged) and
    # is only reachable when DEBUG_MODE=true, ENVIRONMENT is not "production",
    # and no AUTH_GROUP_CHECK_URL is configured -- enforced at startup by
    # AppSettings.validate_skip_authorization_checks_dev_only, which refuses to
    # boot otherwise. The mutual-exclusivity with an external authorizer means
    # this branch can only ever override the mock table below, never a real
    # authorization service.
    if app_settings.debug_mode and app_settings.skip_authorization_checks:
        # Request-time audit signal: the startup warning is the only other
        # indicator, so without this an admin action granted by the bypass is
        # indistinguishable from one that passed a real group check.
        logger.warning(
            "Authorization bypass active: granting group '%s' to user '%s' "
            "via SKIP_AUTHORIZATION_CHECKS (DEBUG_MODE=true, dev-only).",
            group_id,
            user_id,
        )
        return True

    auth_url = app_settings.auth_group_check_url
    api_key = app_settings.auth_group_check_api_key

    if auth_url and api_key:
        # Use the external HTTP endpoint for authorization
        try:
            async with httpx.AsyncClient() as client:
                headers = {"Authorization": f"Bearer {api_key}"}
                payload = {"user_id": user_id, "group_id": group_id}
                response = await client.post(auth_url, json=payload, headers=headers, timeout=5.0)
                response.raise_for_status()
                # Assuming the endpoint returns a simple JSON like {"is_member": true}
                return response.json().get("is_member", False)
        except httpx.RequestError as e:
            logger.error(f"HTTP request to auth endpoint failed: {e}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"Error during external auth check: {e}", exc_info=True)
            return False
    else:
        # Everybody is in the users group by default
        if (group_id == "users"):
            return True
        # Mock group membership is only available in debug mode
        if not app_settings.debug_mode:
            return False
        # Allow configured test user to access admin group in debug mode
        if (user_id == app_settings.test_user and
                group_id == app_settings.admin_group):
            return True

        mock_groups = {
            "test@test.com": ["users", "mcp_basic", "admin"],
            "user@example.com": ["users", "mcp_basic"],
            app_settings.admin_test_user: ["admin", "users", "mcp_basic", "mcp_advanced"]
        }
        user_groups = mock_groups.get(user_id, [])
        return group_id in user_groups


def _get_alb_public_key(kid: str, aws_region: str) -> Optional[str]:
    """
    Fetch and cache AWS ALB public key by key ID.

    Caching reduces latency and API calls since AWS ALB rotates keys infrequently.
    Cache has a 1-hour TTL to handle key rotation.

    Args:
        kid: Key ID from JWT header
        aws_region: AWS region (e.g., 'us-east-1')

    Returns:
        Public key string, or None if fetch fails
    """
    # Security: Validate inputs to prevent URL injection and cache poisoning attacks
    # kid and region are used in URL construction, so strict validation is critical
    if not re.match(r'^[a-zA-Z0-9\-]+$', kid):
        logger.error(f"Invalid kid format: {kid}")
        return None
    if not re.match(r'^[a-z]{2}-[a-z]+-\d+$', aws_region):
        logger.error(f"Invalid AWS region format: {aws_region}")
        return None

    # Security: TTL-based cache (1 hour) allows key rotation and prevents stale keys
    # if AWS rotates keys or a key is compromised
    cache_key = (kid, aws_region)
    now = datetime.utcnow()
    if cache_key in _alb_key_cache:
        cached_key, expiry = _alb_key_cache[cache_key]
        if now < expiry:
            return cached_key
        else:
            # Expired, remove from cache
            del _alb_key_cache[cache_key]

    url = f'https://public-keys.auth.elb.{aws_region}.amazonaws.com/{kid}'
    try:
        response = httpx.get(url, timeout=5.0)
        response.raise_for_status()
        pub_key = response.text

        # Cache with 1-hour TTL
        expiry = now + timedelta(hours=1)
        _alb_key_cache[cache_key] = (pub_key, expiry)

        return pub_key
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching ALB public key from {url}: {e.response.status_code}")
        return None
    except httpx.RequestError as e:
        logger.error(f"Error fetching ALB public key from {url}: {e}")
        return None


def get_user_from_aws_alb_jwt(encoded_jwt, expected_alb_arn, aws_region):
    """
    Validates the AWS ALB JWT and parses the email address from the payload.

    Args:
        encoded_jwt (str): The JWT from the x-amzn-oidc-data header.
        expected_alb_arn (str): The ARN of your Application Load Balancer.
        aws_region (str): The AWS region where your ALB is located (e.g., 'us-east-1').

    Returns:
        str: The user's email address, or None if validation fails.
    """
    if not encoded_jwt:
        return None
    try:
        # Step 1: Decode the JWT header to get the key ID (kid) and signer using PyJWT
        header = jwt.get_unverified_header(encoded_jwt)
        kid = header.get('kid')
        received_alb_arn = header.get('signer')

        if not kid:
            logger.error("Error: 'kid' not found in JWT header")
            return None

        # Step 2: Validate the signer matches the expected ALB ARN
        # Security: hmac.compare_digest prevents timing attacks that could reveal the ARN
        if not received_alb_arn or not hmac.compare_digest(received_alb_arn, expected_alb_arn):
            logger.error(f"Error: Invalid signer ARN. Expected {expected_alb_arn}, got {received_alb_arn}")
            return None

        # Step 3: Get the public key from the regional endpoint (with caching)
        pub_key = _get_alb_public_key(kid, aws_region)
        if not pub_key:
            logger.error("Error: Failed to fetch ALB public key")
            return None

        # Step 4: Validate the signature and claims using PyJWT
        # The decode method handles signature verification and standard claims (like expiration)
        # The ALB uses ES256 algorithm
        payload = jwt.decode(
            encoded_jwt,
            pub_key,
            algorithms=['ES256'],
            # Optional: Add audience or issuer validation if needed, though ALB handles most standard claims validation
            options={"verify_aud": False, "verify_iss": False}
        )

        # Step 5: Extract the email address from the payload
        email_address = payload.get('email')
        if email_address:
            # Security: Validate email format to prevent injection attacks and ensure
            # the email claim contains a properly formatted email address
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not isinstance(email_address, str) or not re.match(email_pattern, email_address):
                logger.error(f"Error: Invalid email format in JWT payload: {email_address}")
                return None
            logger.debug("Successfully authenticated user via AWS ALB JWT")
            return email_address
        else:
            logger.error("Error: 'email' claim not found in JWT payload")
            return None

    except jwt.ExpiredSignatureError:
        logger.error("Error: Token has expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.error(f"Error: Invalid token - {e}")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        return None


def get_user_from_header(x_email_header: Optional[str]) -> Optional[str]:
    """Extract user email from authentication header value."""
    if not x_email_header:
        return None
    return x_email_header.strip()
