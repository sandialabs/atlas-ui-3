
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
    """Startup must refuse SKIP_AUTHORIZATION_CHECKS=true without DEBUG_MODE=true."""
    monkeypatch.setenv("DEBUG_MODE", "false")
    monkeypatch.setenv("SKIP_AUTHORIZATION_CHECKS", "true")
    monkeypatch.setenv("FEATURE_AGENT_PORTAL_ENABLED", "false")
    with pytest.raises(ValueError, match="SKIP_AUTHORIZATION_CHECKS"):
        AppSettings()
