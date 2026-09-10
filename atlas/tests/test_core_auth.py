
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.modules.config.config_manager import config_manager
from atlas.modules.config.settings import AppSettings


def _disable_external_authorizer(monkeypatch):
    """Force ``core.auth`` down its local (mock-table) branch.

    ``is_user_in_group`` prefers a configured external authorizer over the
    local table, so a developer or CI runner with ``AUTH_GROUP_CHECK_URL`` +
    ``AUTH_GROUP_CHECK_API_KEY`` exported would otherwise send these tests at a
    live authorization service over the network. The external path has its own
    dedicated coverage below.
    """
    monkeypatch.delenv("AUTH_GROUP_CHECK_URL", raising=False)
    monkeypatch.delenv("AUTH_GROUP_CHECK_API_KEY", raising=False)


@pytest.mark.asyncio
async def test_is_user_in_group_debug_admin(monkeypatch):
    """The documented debug-only bypass: the configured ``test_user`` is granted
    the configured admin group when DEBUG_MODE=true and no external authorizer
    is configured."""
    monkeypatch.setenv("DEBUG_MODE", "true")
    _disable_external_authorizer(monkeypatch)
    config_manager.reload_configs()

    from atlas.core.auth import is_user_in_group  # import after reload to use env

    test_user = config_manager.app_settings.test_user
    admin_group = config_manager.app_settings.admin_group

    assert await is_user_in_group(test_user, admin_group) is True


@pytest.mark.asyncio
async def test_is_user_in_group_users_group_allowed_without_authorizer(monkeypatch):
    """The ``users`` group short-circuits to True for anyone, in debug mode or
    not, whenever no external authorizer is configured. This is what keeps a
    default deployment usable, so it must survive changes to the mock table."""
    monkeypatch.setenv("DEBUG_MODE", "false")
    # Dev-only preview flag; AppSettings refuses to build with it on outside
    # debug mode.
    monkeypatch.setenv("FEATURE_AGENT_PORTAL_ENABLED", "false")
    _disable_external_authorizer(monkeypatch)
    config_manager.reload_configs()

    from atlas.core.auth import is_user_in_group

    assert await is_user_in_group("nobody-in-any-table@example.com", "users") is True


@pytest.mark.asyncio
async def test_is_user_in_group_skip_authorization_checks_grants_any_group(skip_auth_checks_env):
    """SKIP_AUTHORIZATION_CHECKS (with DEBUG_MODE=true) should authorize any
    user for any group, without needing ADMIN_TEST_USER configured.

    Uses the ``skip_auth_checks_env`` fixture so both ``DEBUG_MODE`` and
    ``SKIP_AUTHORIZATION_CHECKS`` are saved and restored and the ConfigManager
    cache is reset on exit (Copilot review on PR #758).
    """
    from atlas.core.auth import is_user_in_group

    assert await is_user_in_group("someone-not-in-any-mock-table@example.com", "admin") is True
    assert await is_user_in_group("someone-not-in-any-mock-table@example.com", "mcp_advanced") is True


def test_skip_authorization_checks_requires_debug_mode(monkeypatch):
    """Startup must refuse SKIP_AUTHORIZATION_CHECKS=true without DEBUG_MODE=true.

    Pins the debug-mode guardrail by name: the other two refusal messages
    (production environment, external auth endpoint) also contain the
    substring ``SKIP_AUTHORIZATION_CHECKS``, so matching on ``DEBUG_MODE`` with
    the other two guardrails satisfied makes this test fail if the debug-mode
    branch of the validator is deleted (AGENT-REVIEW-BOT-3 review on PR #758).
    """
    monkeypatch.setenv("DEBUG_MODE", "false")
    monkeypatch.setenv("SKIP_AUTHORIZATION_CHECKS", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("AUTH_GROUP_CHECK_URL", raising=False)
    monkeypatch.delenv("AUTH_GROUP_CHECK_API_KEY", raising=False)
    monkeypatch.setenv("FEATURE_AGENT_PORTAL_ENABLED", "false")
    with pytest.raises(ValueError, match="DEBUG_MODE"):
        AppSettings()


def test_skip_authorization_checks_refused_in_production_environment(monkeypatch):
    """Startup must refuse SKIP_AUTHORIZATION_CHECKS=true when ENVIRONMENT=production,
    even with DEBUG_MODE=true -- a prod deployment that accidentally has debug mode
    on must still be denied the bypass (AGENT-REVIEW-BOT-3 review on PR #758)."""
    monkeypatch.setenv("DEBUG_MODE", "true")
    monkeypatch.setenv("SKIP_AUTHORIZATION_CHECKS", "true")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("AUTH_GROUP_CHECK_URL", raising=False)
    monkeypatch.setenv("FEATURE_AGENT_PORTAL_ENABLED", "false")
    with pytest.raises(ValueError, match="ENVIRONMENT"):
        AppSettings()


def test_skip_authorization_checks_refused_with_external_auth_endpoint(monkeypatch):
    """Startup must refuse SKIP_AUTHORIZATION_CHECKS=true when an external
    AUTH_GROUP_CHECK_URL is configured -- the bypass must never silently override
    a real authorizer (AGENT-REVIEW-BOT-3 review on PR #758)."""
    monkeypatch.setenv("DEBUG_MODE", "true")
    monkeypatch.setenv("SKIP_AUTHORIZATION_CHECKS", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_GROUP_CHECK_URL", "https://auth.example.com/check")
    monkeypatch.setenv("AUTH_GROUP_CHECK_API_KEY", "secret")
    monkeypatch.setenv("FEATURE_AGENT_PORTAL_ENABLED", "false")
    with pytest.raises(ValueError, match="AUTH_GROUP_CHECK_URL"):
        AppSettings()


@pytest.mark.asyncio
async def test_is_user_in_group_denied_in_production_mode(monkeypatch):
    """With DEBUG_MODE=false and no external authorizer, the mock admin table is
    disabled, so an otherwise-admin identity is NOT an admin. This is the invariant
    the ``mock_admin_authorization`` / ``skip_auth_checks_env`` fixtures opt out of;
    deleting the ``if not app_settings.debug_mode: return False`` guard in
    ``core.auth`` must turn this test red (AGENT-REVIEW-BOT-3 review on PR #758).
    """
    monkeypatch.setenv("DEBUG_MODE", "false")
    monkeypatch.setenv("FEATURE_AGENT_PORTAL_ENABLED", "false")
    _disable_external_authorizer(monkeypatch)
    config_manager.reload_configs()

    from atlas.core.auth import is_user_in_group

    admin_test_user = config_manager.app_settings.admin_test_user
    admin_group = config_manager.app_settings.admin_group
    assert await is_user_in_group(admin_test_user, admin_group) is False


# ---------------------------------------------------------------------------
# External authorizer delegation (the production path)
# ---------------------------------------------------------------------------
#
# A real deployment configures AUTH_GROUP_CHECK_URL + AUTH_GROUP_CHECK_API_KEY,
# and every membership decision -- admin included -- is the external service's
# to make. The mock-table tests above deliberately switch that endpoint off, so
# without these tests nothing pins the branch that production actually runs.


_AUTHORIZER_URL = "https://auth.example.com/check"
# Not a credential -- an arbitrary token this test asserts is forwarded verbatim.
_AUTHORIZER_API_KEY = "test-authorizer-key-not-a-credential"


def _external_authorizer_env(monkeypatch, *, debug_mode: str = "false"):
    monkeypatch.setenv("DEBUG_MODE", debug_mode)
    monkeypatch.setenv("FEATURE_AGENT_PORTAL_ENABLED", "false")
    monkeypatch.setenv("AUTH_GROUP_CHECK_URL", _AUTHORIZER_URL)
    monkeypatch.setenv("AUTH_GROUP_CHECK_API_KEY", _AUTHORIZER_API_KEY)
    monkeypatch.delenv("SKIP_AUTHORIZATION_CHECKS", raising=False)
    config_manager.reload_configs()


def _patched_authorizer(is_member):
    """Patch httpx so ``is_user_in_group`` sees an authorizer replying ``is_member``."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"is_member": is_member})

    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    return patch("atlas.core.auth.httpx.AsyncClient", return_value=client), client


@pytest.mark.asyncio
@pytest.mark.parametrize("is_member", [True, False])
async def test_is_user_in_group_delegates_to_external_authorizer(monkeypatch, is_member):
    """With an authorizer configured, the verdict is whatever it returns -- for the
    configured admin group and the configured admin identity alike. The local
    mock table must not be consulted at all."""
    _external_authorizer_env(monkeypatch)

    from atlas.core.auth import is_user_in_group

    admin_test_user = config_manager.app_settings.admin_test_user
    admin_group = config_manager.app_settings.admin_group

    patcher, client = _patched_authorizer(is_member)
    with patcher:
        assert await is_user_in_group(admin_test_user, admin_group) is is_member

    # Assert the request against the *configured* endpoint and key rather than
    # literals repeated from the fixture, so a regression that drops the
    # credential (or posts somewhere other than AUTH_GROUP_CHECK_URL) cannot be
    # masked by the test and the fixture drifting together.
    app_settings = config_manager.app_settings
    client.post.assert_awaited_once()
    args, kwargs = client.post.call_args
    assert args[0] == app_settings.auth_group_check_url
    assert kwargs["json"] == {"user_id": admin_test_user, "group_id": admin_group}
    assert (
        kwargs["headers"]["Authorization"]
        == f"Bearer {app_settings.auth_group_check_api_key}"
    )


@pytest.mark.asyncio
async def test_external_authorizer_wins_over_debug_mock_table(monkeypatch):
    """DEBUG_MODE must not resurrect the mock table once a real authorizer is
    configured: the configured ``test_user`` is an admin locally, but a denying
    authorizer still denies."""
    _external_authorizer_env(monkeypatch, debug_mode="true")

    from atlas.core.auth import is_user_in_group

    test_user = config_manager.app_settings.test_user
    admin_group = config_manager.app_settings.admin_group

    patcher, _client = _patched_authorizer(False)
    with patcher:
        assert await is_user_in_group(test_user, admin_group) is False


@pytest.mark.asyncio
async def test_external_authorizer_failure_denies(monkeypatch):
    """A transport failure talking to the authorizer must fail closed."""
    import httpx

    _external_authorizer_env(monkeypatch)

    from atlas.core.auth import is_user_in_group

    client = MagicMock()
    client.post = AsyncMock(side_effect=httpx.RequestError("boom"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("atlas.core.auth.httpx.AsyncClient", return_value=client):
        assert await is_user_in_group("anyone@example.com", "users") is False


def test_skip_authorization_checks_boot_path_refuses_to_start(monkeypatch):
    """The boot path (``ConfigManager.app_settings``) must re-raise the
    ``ValueError`` from ``AppSettings.validate_skip_authorization_checks_dev_only``,
    not swallow it and retry with default settings. The three refusal tests above
    construct ``AppSettings()`` directly, one layer below this property, so
    without this test a regression in ``config_loader.app_settings`` (e.g. catching
    ``ValueError`` and falling back) leaves the suite green while the documented
    "refuses to start" guarantee degrades (AGENT-REVIEW-BOT-3 review on PR #758).
    """
    monkeypatch.setenv("DEBUG_MODE", "false")
    monkeypatch.setenv("SKIP_AUTHORIZATION_CHECKS", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("AUTH_GROUP_CHECK_URL", raising=False)
    monkeypatch.delenv("AUTH_GROUP_CHECK_API_KEY", raising=False)
    monkeypatch.setenv("FEATURE_AGENT_PORTAL_ENABLED", "false")
    config_manager.reload_configs()
    try:
        with pytest.raises(ValueError, match="DEBUG_MODE"):
            config_manager.app_settings
    finally:
        # Leave the cache clean so the next test reconstructs with restored env.
        config_manager.reload_configs()


# ---------------------------------------------------------------------------
# Statically configured groups (issue #910)
# ---------------------------------------------------------------------------


def _production_static_env(monkeypatch):
    """Production-shaped env with no external authorizer and no mock table."""
    monkeypatch.setenv("DEBUG_MODE", "false")
    monkeypatch.setenv("FEATURE_AGENT_PORTAL_ENABLED", "false")
    monkeypatch.delenv("SKIP_AUTHORIZATION_CHECKS", raising=False)
    _disable_external_authorizer(monkeypatch)


def test_parse_static_groups_normalizes_and_merges_admin_users():
    from atlas.modules.config.settings import parse_static_groups

    table = parse_static_groups(
        " Admin : Alice@Example.ORG , bob@example.org ; mcp_advanced:alice@example.org ",
        admin_users="Carol@Example.org, ,",
        admin_group="admin",
    )

    assert table == {
        "admin": frozenset({"alice@example.org", "bob@example.org", "carol@example.org"}),
        "mcp_advanced": frozenset({"alice@example.org"}),
    }


def test_parse_static_groups_skips_malformed_entries_without_raising():
    """A typo in one entry must not take the deployment down, and must not
    silently become a group named after the whole entry."""
    from atlas.modules.config.settings import parse_static_groups

    table = parse_static_groups("no-colon-here;;good:a@b.com;empty_group:  ,")

    assert table == {"good": frozenset({"a@b.com"})}


def test_admin_users_sugar_targets_the_configured_admin_group(monkeypatch):
    """ADMIN_USERS is sugar for a single ``<ADMIN_GROUP>:`` entry, so a renamed
    admin group must be honoured rather than a literal "admin"."""
    # Dev-only preview flag; AppSettings refuses to build with it on outside
    # debug mode.
    monkeypatch.setenv("FEATURE_AGENT_PORTAL_ENABLED", "false")

    # ``admin_group``/``debug_mode`` have no validation alias, so they must be
    # passed by field name; ``ADMIN_USERS`` does, so it is passed by alias.
    settings = AppSettings(
        debug_mode=False,
        admin_group="atlas_admins",
        ADMIN_USERS="alice@example.org",
    )

    assert settings.static_group_members == {"atlas_admins": frozenset({"alice@example.org"})}


@pytest.mark.asyncio
async def test_static_admin_users_grants_admin_in_production_mode(monkeypatch):
    """The core of issue #910: with DEBUG_MODE=false and no authorizer, a listed
    admin is an admin -- previously nobody could be."""
    _production_static_env(monkeypatch)
    monkeypatch.setenv("ADMIN_USERS", "alice@example.org,bob@example.org")
    config_manager.reload_configs()

    from atlas.core.auth import is_user_in_group

    admin_group = config_manager.app_settings.admin_group
    assert await is_user_in_group("alice@example.org", admin_group) is True
    assert await is_user_in_group("bob@example.org", admin_group) is True
    assert await is_user_in_group("mallory@example.org", admin_group) is False


@pytest.mark.asyncio
async def test_static_matching_is_case_insensitive_and_whitespace_tolerant(monkeypatch):
    """Identities arrive from IdP claims, where casing and padding are noise."""
    _production_static_env(monkeypatch)
    monkeypatch.setenv("ADMIN_USERS", "  Alice@Example.ORG ")
    config_manager.reload_configs()

    from atlas.core.auth import is_user_in_group

    admin_group = config_manager.app_settings.admin_group
    assert await is_user_in_group("ALICE@example.org", admin_group) is True
    assert await is_user_in_group(" alice@example.org ", admin_group) is True
    assert await is_user_in_group("alice@example.org", admin_group.upper()) is True


@pytest.mark.asyncio
async def test_static_groups_scope_arbitrary_groups_in_production_mode(monkeypatch):
    """``groups:`` on an MCP server is meaningless outside debug mode unless
    arbitrary groups can be configured statically."""
    _production_static_env(monkeypatch)
    monkeypatch.setenv(
        "AUTH_STATIC_GROUPS", "admin:alice@example.org;mcp_advanced:alice@example.org,dev@example.org"
    )
    config_manager.reload_configs()

    from atlas.core.auth import is_user_in_group

    assert await is_user_in_group("dev@example.org", "mcp_advanced") is True
    assert await is_user_in_group("dev@example.org", "admin") is False
    assert await is_user_in_group("alice@example.org", "admin") is True
    # Unlisted users keep the pre-existing behaviour: users-only.
    assert await is_user_in_group("nobody@example.org", "mcp_advanced") is False
    assert await is_user_in_group("nobody@example.org", "users") is True


@pytest.mark.asyncio
async def test_external_authorizer_wins_over_static_config(monkeypatch):
    """A real authorization service stays authoritative: static config must not
    add grants behind its back."""
    monkeypatch.setenv("DEBUG_MODE", "false")
    monkeypatch.setenv("FEATURE_AGENT_PORTAL_ENABLED", "false")
    monkeypatch.setenv("ADMIN_USERS", "alice@example.org")
    monkeypatch.setenv("AUTH_GROUP_CHECK_URL", "https://auth.example.com/check")
    monkeypatch.setenv("AUTH_GROUP_CHECK_API_KEY", "key")
    config_manager.reload_configs()

    from atlas.core.auth import is_user_in_group

    patcher, _client = _patched_authorizer(False)
    with patcher:
        assert await is_user_in_group("alice@example.org", "admin") is False


def test_warns_when_no_authorization_source_is_configured(monkeypatch, caplog):
    """The silent-collapse case from issue #910 should say so at startup."""
    monkeypatch.setenv("FEATURE_AGENT_PORTAL_ENABLED", "false")

    with caplog.at_level(logging.WARNING, logger="atlas.modules.config.settings"):
        AppSettings(debug_mode=False, auth_group_check_url=None)
    assert "No authorization source is configured" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="atlas.modules.config.settings"):
        AppSettings(debug_mode=False, auth_group_check_url=None, ADMIN_USERS="alice@example.org")
    assert "No authorization source is configured" not in caplog.text


@pytest.mark.asyncio
async def test_users_group_short_circuit_is_case_and_whitespace_tolerant(monkeypatch):
    """``Users`` / `` users `` in a hand-edited ``groups`` list must still hit
    the default-users short-circuit instead of denying everyone (Copilot review
    on PR #911): group-name tolerance cannot stop at the static table."""
    _production_static_env(monkeypatch)
    config_manager.reload_configs()

    from atlas.core.auth import is_user_in_group

    assert await is_user_in_group("nobody@example.org", "Users") is True
    assert await is_user_in_group("nobody@example.org", " users ") is True


@pytest.mark.asyncio
async def test_static_config_ignored_when_authorizer_url_set_without_api_key(monkeypatch):
    """An endpoint configured without a usable API key must fail closed.

    ``AUTH_GROUP_CHECK_URL`` without ``AUTH_GROUP_CHECK_API_KEY`` -- a failed
    secret injection, a misspelled variable -- does not take the external
    branch. If static config were consulted there, a listed admin would keep
    admin exactly while the authoritative service is unreachable, which is the
    opposite of what "the endpoint is authoritative" promises.
    """
    monkeypatch.setenv("DEBUG_MODE", "false")
    monkeypatch.setenv("FEATURE_AGENT_PORTAL_ENABLED", "false")
    monkeypatch.delenv("SKIP_AUTHORIZATION_CHECKS", raising=False)
    monkeypatch.setenv("ADMIN_USERS", "alice@example.org")
    monkeypatch.setenv("AUTH_GROUP_CHECK_URL", "https://auth.example.com/check")
    monkeypatch.delenv("AUTH_GROUP_CHECK_API_KEY", raising=False)
    config_manager.reload_configs()

    from atlas.core.auth import is_user_in_group

    assert await is_user_in_group("alice@example.org", "admin") is False
    # The unrelated default is unchanged: everyone is still in ``users``.
    assert await is_user_in_group("alice@example.org", "users") is True
