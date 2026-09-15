"""Authentication and authorization module."""

import asyncio
import hmac
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

import httpx
import jwt

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.modules.config.config_manager import config_manager

logger = logging.getLogger(__name__)

# Cache with TTL for ALB public keys: {(kid, region): (key_or_None, expiry)}.
# A None value is a negatively cached failure -- see _get_alb_public_key.
_alb_key_cache: Dict[Tuple[str, str], Tuple[Optional[str], datetime]] = {}

# Short TTL for failed fetches: long enough to absorb a burst of bogus `kid`
# values, short enough that a transient network failure recovers on its own.
_ALB_NEGATIVE_TTL = timedelta(minutes=5)

# Ceiling on cache entries. `kid` is attacker-influenced (it comes from an
# unverified JWT header), so without a bound the negative cache is itself a
# memory-growth lever.
_ALB_CACHE_MAX_ENTRIES = 256

# The ADMIN_USERS audit line fires on an authorization check, and admin-gated
# routes are polled by the UI -- an unthrottled line per request buries the
# signal it exists to provide. Log once per identity/group per TTL instead.
# Keys are identities that already matched the configured allowlist, so the
# table is bounded by that list; the ceiling is belt-and-braces.
_ADMIN_OVERRIDE_LOG_TTL = timedelta(hours=1)
_ADMIN_OVERRIDE_LOG_MAX_ENTRIES = 256
_admin_override_logged: Dict[Tuple[str, str, bool], datetime] = {}


def _should_log_admin_override(user: str, group: str, overrides_authorizer: bool) -> bool:
    """Whether to emit the ADMIN_USERS audit line for this grant now.

    ``overrides_authorizer`` is part of the key, not just the message: the two
    cases log at different severities, and an authorizer configured partway
    through a TTL would otherwise have its first warnings swallowed by an
    info-level entry cached before it existed.
    """
    now = datetime.utcnow()
    for key in [k for k, exp in _admin_override_logged.items() if exp <= now]:
        _admin_override_logged.pop(key, None)

    cache_key = (user, group, overrides_authorizer)
    if cache_key in _admin_override_logged:
        return False

    while len(_admin_override_logged) >= _ADMIN_OVERRIDE_LOG_MAX_ENTRIES:
        oldest = min(_admin_override_logged, key=lambda k: _admin_override_logged[k])
        _admin_override_logged.pop(oldest, None)
    _admin_override_logged[cache_key] = now + _ADMIN_OVERRIDE_LOG_TTL
    return True


def _reset_admin_override_log_for_tests() -> None:
    """Clear the audit-line throttle so tests do not depend on each other."""
    _admin_override_logged.clear()


def _prune_alb_cache() -> None:
    """Drop expired entries, then oldest-first if still over the ceiling."""
    now = datetime.utcnow()
    for key in [k for k, (_, exp) in _alb_key_cache.items() if exp <= now]:
        _alb_key_cache.pop(key, None)
    while len(_alb_key_cache) >= _ALB_CACHE_MAX_ENTRIES:
        oldest = min(_alb_key_cache, key=lambda k: _alb_key_cache[k][1])
        _alb_key_cache.pop(oldest, None)


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
    2. Explicit admin override: an identity listed in ``ADMIN_USERS`` satisfies a
       check against ``ADMIN_GROUP``, whether or not an external authorizer is
       configured. Admin access is therefore granted when *either* the static
       override lists the user *or* the normal membership check below says the
       user is in ``ADMIN_GROUP`` (issue #945). It only ever grants, and only
       for the configured admin group.
    3. External endpoint: when ``AUTH_GROUP_CHECK_URL`` and ``AUTH_GROUP_CHECK_API_KEY``
       are configured, query the HTTP authorization service for membership. A real
       authorization service remains authoritative for every group: the static
       table below is not consulted as an additional grant when an endpoint is
       configured. The identity and group are forwarded **as received**, not
       normalized -- the service owns its own matching rules, and rewriting the
       values Atlas asks about could change its answer.
    4. Static config: ``AUTH_STATIC_GROUPS`` grants explicit static membership
       without an external service, in production mode as well as debug.

    Case-insensitive, whitespace-tolerant matching applies to every source Atlas
    evaluates itself (the admin override, the static table, the ``users``
    short-circuit and the debug mock table) -- not to the external endpoint,
    which receives the raw values per (3).
    5. Mock table (fallback for local development): everyone is in the ``users``
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
            sanitize_for_logging(group_id),
            sanitize_for_logging(user_id),
        )
        return True

    # Group names arrive from hand-edited config (the `groups` lists on MCP
    # servers and models) and identities from IdP claims, so casing and stray
    # whitespace are noise on both.
    normalized_group = (group_id or "").strip().lower()
    normalized_user = (user_id or "").strip().lower()

    # Explicit admin override (ADMIN_USERS), checked before the external
    # authorizer rather than after it. Operators need two independent ways to
    # reach admin: a small static allowlist they control in their own config,
    # and dynamic membership in ADMIN_GROUP resolved by whatever group logic
    # the deployment already runs. Either one grants admin (issue #945).
    #
    # This deliberately differs from the AUTH_STATIC_GROUPS table below, which
    # still yields to a configured authorizer: ADMIN_USERS is the emergency
    # path that must keep working when the authorization service is the thing
    # that broke, and it is scoped to the admin group and to identities the
    # operator typed out by hand.
    auth_url = app_settings.auth_group_check_url
    api_key = app_settings.auth_group_check_api_key

    if (
        normalized_group
        and normalized_group == (app_settings.admin_group or "").strip().lower()
        and normalized_user in app_settings.admin_user_set
    ):
        # Audit signal, for the same reason the SKIP_AUTHORIZATION_CHECKS
        # bypass above logs one: after an incident, an admin action taken via
        # the break-glass list has to be distinguishable from one backed by
        # real group membership. It is a warning only when it actually
        # overrides an authorizer -- on a deployment with no authorizer this
        # is the ordinary way admins are configured, and warning on every
        # admin request would train operators to ignore the line.
        if not _should_log_admin_override(
            normalized_user, normalized_group, bool(auth_url)
        ):
            return True
        if auth_url:
            logger.warning(
                "Admin override: granting admin group '%s' to user '%s' via "
                "ADMIN_USERS, bypassing the configured authorization service.",
                sanitize_for_logging(group_id),
                sanitize_for_logging(user_id),
            )
        else:
            logger.info(
                "Granting admin group '%s' to user '%s' via ADMIN_USERS.",
                sanitize_for_logging(group_id),
                sanitize_for_logging(user_id),
            )
        return True

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
        # Statically configured membership (AUTH_STATIC_GROUPS, plus the
        # ADMIN_USERS entry it is unioned with). Checked before the ``users``
        # short-circuit and the mock table, and -- unlike the mock table --
        # available outside debug mode. Without it, a deployment with no
        # external authorizer has no way to make anyone an admin or to scope an
        # MCP server to a subset of users (issue #910).
        #
        # Gated on ``auth_url`` alone, not on the ``auth_url and api_key`` pair
        # that selects the branch above: a deployment that configures an
        # endpoint but whose API key is missing (a failed secret injection, a
        # misspelled variable) lands here, and granting static admin in that
        # window would preserve access precisely when the authoritative service
        # has dropped out. Failing closed there is the whole point of calling
        # the endpoint authoritative.
        if not auth_url:
            static_members = app_settings.static_group_members.get(normalized_group)
            if static_members and normalized_user in static_members:
                return True

        # A blank identity or group is never a grant, and this sits above the
        # ``users`` short-circuit deliberately: without it an identity-less
        # request is "in users", which is the group most features check. Routes
        # authenticate before asking, so this is defence in depth rather than
        # the control -- but "nobody" should not be a member of anything.
        #
        # Both blanks are also reachable from config: an empty
        # ADMIN_TEST_USER/TEST_USER would key the debug mock table on "", and
        # an empty ADMIN_GROUP would put "" in every mock user's group list,
        # making a blank group name (an MCP server declaring `groups: [""]`)
        # one that everybody is in.
        if not normalized_user or not normalized_group:
            return False

        # Everybody is in the users group by default
        if normalized_group == "users":
            return True
        # Mock group membership is only available in debug mode
        if not app_settings.debug_mode:
            return False
        # Allow configured test user to access admin group in debug mode.
        # Compared normalized, like every other branch: the documented
        # case/whitespace tolerance should not stop at the static table.
        normalized_admin_group = (app_settings.admin_group or "").strip().lower()
        normalized_test_user = (app_settings.test_user or "").strip().lower()
        if (normalized_user
                and normalized_user == normalized_test_user
                and normalized_group == normalized_admin_group):
            return True

        # The admin entries use the *configured* admin group rather than a
        # literal "admin": ADMIN_GROUP is deployment-specific, and the
        # test_user branch above already honours it. Hardcoding "admin" here
        # meant that on any deployment with a renamed admin group the
        # configured ADMIN_TEST_USER was granted a group nothing checks, so
        # debug-mode admin routes were unreachable for that identity.
        mock_groups = {
            "test@test.com": ["users", "mcp_basic", normalized_admin_group],
            "user@example.com": ["users", "mcp_basic"],
            (app_settings.admin_test_user or "").strip().lower(): [
                normalized_admin_group, "users", "mcp_basic", "mcp_advanced"
            ],
        }
        # An empty ADMIN_TEST_USER/TEST_USER would otherwise key this table on
        # "", handing admin to a blank or missing identity -- the one input an
        # unauthenticated request is most likely to arrive with.
        mock_groups.pop("", None)
        user_groups = mock_groups.get(normalized_user, [])
        return normalized_group in user_groups


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

    def _remember_failure() -> None:
        """Negatively cache a failed fetch.

        Without this, a caller presenting a fresh ``kid`` on every request
        forces one outbound HTTPS call per upgrade -- an amplification lever
        against both this process and the ALB key endpoint. The TTL is short
        so a genuine transient failure recovers quickly.
        """
        _prune_alb_cache()
        _alb_key_cache[cache_key] = (None, now + _ALB_NEGATIVE_TTL)

    try:
        response = httpx.get(url, timeout=5.0)
        response.raise_for_status()
        pub_key = response.text

        # Cache with 1-hour TTL
        _prune_alb_cache()
        expiry = now + timedelta(hours=1)
        _alb_key_cache[cache_key] = (pub_key, expiry)

        return pub_key
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching ALB public key from {url}: {e.response.status_code}")
        _remember_failure()
        return None
    except httpx.RequestError as e:
        logger.error(f"Error fetching ALB public key from {url}: {e}")
        _remember_failure()
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
    """Extract user email from a plain ``email-string`` authentication header.

    This performs NO verification -- it trusts the value entirely, which is
    only sound when a reverse proxy has authenticated the request and strips
    client-supplied copies of the header. Call
    :func:`resolve_user_from_auth_header` instead of calling this directly, so
    the configured header type is always honoured.
    """
    if not x_email_header:
        return None
    return x_email_header.strip()


def resolve_user_from_auth_header(
    header_value: Optional[str],
    *,
    header_type: str,
    expected_alb_arn: str = "",
    aws_region: str = "us-east-1",
) -> Optional[str]:
    """Resolve the authenticated user from the configured auth header.

    The single place where ``AUTH_USER_HEADER_TYPE`` is interpreted. It exists
    because it previously was not: HTTP middleware branched on the header type
    and cryptographically verified ``aws-alb-jwt``, while both WebSocket
    endpoints called :func:`get_user_from_header` unconditionally. In an
    ALB-JWT deployment that meant HTTP verified an ES256 signature and the
    signer ARN, while a WebSocket upgrade accepted any non-empty header value
    as the user's identity -- a full authentication bypass on the socket for
    anyone able to reach the backend directly or through a proxy that does not
    strip the header.

    Args:
        header_value: Raw value of the configured auth header.
        header_type: ``"aws-alb-jwt"`` for a signed ALB token, anything else
            for a trusted plain email string.
        expected_alb_arn: ARN the JWT's signer must match, for ALB mode.
        aws_region: Region whose public keys verify the JWT, for ALB mode.

    Returns:
        The verified user email, or None if the header is absent or fails
        verification.
    """
    if not header_value:
        return None
    if header_type == "aws-alb-jwt":
        return get_user_from_aws_alb_jwt(header_value, expected_alb_arn, aws_region)
    return get_user_from_header(header_value)


async def resolve_user_from_auth_header_async(
    header_value: Optional[str],
    *,
    header_type: str,
    expected_alb_arn: str = "",
    aws_region: str = "us-east-1",
) -> Optional[str]:
    """Async form of :func:`resolve_user_from_auth_header`.

    JWT verification can fetch the ALB public key over the network, and that
    fetch is a synchronous ``httpx.get`` with a 5-second timeout. Called
    directly from a coroutine it blocks the event loop, so one cache miss
    stalls every other in-flight request and connection for up to 5 seconds.
    Running it in a worker thread keeps the loop free.

    The plain-header path does no I/O, so it stays inline -- pushing every
    request through a thread would cost more than it saves.
    """
    if not header_value:
        return None
    if header_type != "aws-alb-jwt":
        return get_user_from_header(header_value)
    return await asyncio.to_thread(
        get_user_from_aws_alb_jwt, header_value, expected_alb_arn, aws_region
    )
