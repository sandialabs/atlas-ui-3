
import pytest

from atlas.modules.config.config_manager import config_manager
from atlas.modules.config.settings import AppSettings


@pytest.mark.asyncio
async def test_is_user_in_group_debug_admin(monkeypatch):
    # Enable debug mode so test user is treated as admin per core.auth logic
    monkeypatch.setenv("DEBUG_MODE", "true")
    config_manager.reload_configs()

    from atlas.core.auth import is_user_in_group  # import after reload to use env

    test_user = config_manager.app_settings.test_user
    admin_group = config_manager.app_settings.admin_group

    assert await is_user_in_group(test_user, admin_group) is True


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
    monkeypatch.delenv("AUTH_GROUP_CHECK_URL", raising=False)
    monkeypatch.delenv("AUTH_GROUP_CHECK_API_KEY", raising=False)
    config_manager.reload_configs()

    from atlas.core.auth import is_user_in_group

    admin_test_user = config_manager.app_settings.admin_test_user
    admin_group = config_manager.app_settings.admin_group
    assert await is_user_in_group(admin_test_user, admin_group) is False


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
