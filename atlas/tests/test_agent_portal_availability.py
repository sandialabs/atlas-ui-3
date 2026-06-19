from types import SimpleNamespace
from unittest.mock import patch

from atlas.routes import agent_portal_availability as availability


def test_agent_portal_effective_flag_is_false_on_windows(monkeypatch):
    monkeypatch.setattr(availability.sys, "platform", "win32")

    settings = SimpleNamespace(feature_agent_portal_enabled=True)

    assert availability.is_agent_portal_supported() is False
    assert availability.is_agent_portal_enabled(settings) is False


def test_agent_portal_effective_flag_still_honors_config_on_linux(monkeypatch):
    monkeypatch.setattr(availability.sys, "platform", "linux")

    assert availability.is_agent_portal_enabled(SimpleNamespace(feature_agent_portal_enabled=True)) is True
    assert availability.is_agent_portal_enabled(SimpleNamespace(feature_agent_portal_enabled=False)) is False


def test_load_agent_portal_router_skips_linux_only_import_on_windows(monkeypatch):
    monkeypatch.setattr(availability.sys, "platform", "win32")

    with patch.object(availability.importlib, "import_module") as import_module:
        assert availability.load_agent_portal_router() is None

    import_module.assert_not_called()


def test_load_agent_portal_router_imports_when_supported(monkeypatch):
    monkeypatch.setattr(availability.sys, "platform", "linux")
    router = object()
    module = SimpleNamespace(router=router)

    with patch.object(availability.importlib, "import_module", return_value=module) as import_module:
        assert availability.load_agent_portal_router() is router

    import_module.assert_called_once_with("atlas.routes.agent_portal_routes")
