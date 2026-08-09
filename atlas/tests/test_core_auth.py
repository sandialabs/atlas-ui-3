
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
async def test_is_user_in_group_skip_authorization_checks_grants_any_group(monkeypatch):
    """SKIP_AUTHORIZATION_CHECKS (with DEBUG_MODE=true) should authorize any
    user for any group, without needing ADMIN_TEST_USER configured."""
    monkeypatch.setenv("DEBUG_MODE", "true")
    monkeypatch.setenv("SKIP_AUTHORIZATION_CHECKS", "true")
    config_manager.reload_configs()
    try:
        from atlas.core.auth import is_user_in_group

        assert await is_user_in_group("someone-not-in-any-mock-table@example.com", "admin") is True
        assert await is_user_in_group("someone-not-in-any-mock-table@example.com", "mcp_advanced") is True
    finally:
        monkeypatch.delenv("SKIP_AUTHORIZATION_CHECKS", raising=False)
        config_manager.reload_configs()


def test_skip_authorization_checks_requires_debug_mode(monkeypatch):
    """Startup must refuse SKIP_AUTHORIZATION_CHECKS=true without DEBUG_MODE=true."""
    monkeypatch.setenv("DEBUG_MODE", "false")
    monkeypatch.setenv("SKIP_AUTHORIZATION_CHECKS", "true")
    monkeypatch.setenv("FEATURE_AGENT_PORTAL_ENABLED", "false")
    with pytest.raises(ValueError, match="SKIP_AUTHORIZATION_CHECKS"):
        AppSettings()
