import pytest

from modules.config.manager import config_manager


def test_is_user_in_group_debug_admin(monkeypatch):
    # Enable debug mode so test user is treated as admin per core.auth logic
    monkeypatch.setenv("DEBUG_MODE", "true")
    config_manager.reload_configs()

    from core.auth import is_user_in_group  # import after reload to use env

    test_user = config_manager.app_settings.test_user
    admin_group = config_manager.app_settings.admin_group

    assert is_user_in_group(test_user, admin_group) is True
