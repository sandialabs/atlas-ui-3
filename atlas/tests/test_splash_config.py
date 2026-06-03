"""Tests for the splash screen configuration endpoint.

The splash screen message body is defined in a markdown file (default
``splash-screen.md``). Additional presentation settings come from the JSON
config file, and whether the splash screen is shown is controlled solely by the
``FEATURE_SPLASH_SCREEN_ENABLED`` env var (the ``enabled`` field in the config
file is ignored).
"""

import json

import pytest
from main import app
from atlas.routes import config_routes
from starlette.testclient import TestClient


@pytest.fixture
def isolated_splash(tmp_path, monkeypatch):
    """Point splash config/markdown lookups at a tmp dir starting empty."""
    config_manager = config_routes.app_factory.get_config_manager()
    monkeypatch.setattr(
        config_routes,
        "_search_config_paths",
        lambda cm, filename: [tmp_path / filename],
    )
    return tmp_path, config_manager


def _set_enabled(monkeypatch, config_manager, value):
    monkeypatch.setattr(
        config_manager.app_settings, "feature_splash_screen_enabled", value
    )


def test_splash_disabled_when_feature_flag_off(isolated_splash, monkeypatch):
    tmp_path, config_manager = isolated_splash
    _set_enabled(monkeypatch, config_manager, False)

    client = TestClient(app)
    resp = client.get("/api/splash", headers={"X-User-Email": "test@test.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["markdown"] == ""


def test_splash_loads_markdown_and_settings(isolated_splash, monkeypatch):
    tmp_path, config_manager = isolated_splash
    _set_enabled(monkeypatch, config_manager, True)
    monkeypatch.setattr(
        config_manager.app_settings, "splash_config_file", "splash-config.json"
    )
    monkeypatch.setattr(
        config_manager.app_settings, "splash_screen_file", "splash-screen.md"
    )

    (tmp_path / "splash-config.json").write_text(
        json.dumps(
            {
                "title": "Hello",
                "require_accept": True,
                "accept_button_text": "OK",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "splash-screen.md").write_text(
        "## Policy\n\nPlease read this.", encoding="utf-8"
    )

    client = TestClient(app)
    resp = client.get("/api/splash", headers={"X-User-Email": "test@test.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["title"] == "Hello"
    assert data["require_accept"] is True
    assert data["accept_button_text"] == "OK"
    assert "Please read this." in data["markdown"]
    # The legacy 'messages' field is no longer produced.
    assert "messages" not in data


def test_splash_ignores_enabled_field_in_config_file(isolated_splash, monkeypatch):
    """The config file's 'enabled' field must not override the env var."""
    tmp_path, config_manager = isolated_splash
    _set_enabled(monkeypatch, config_manager, True)
    monkeypatch.setattr(
        config_manager.app_settings, "splash_config_file", "splash-config.json"
    )
    monkeypatch.setattr(
        config_manager.app_settings, "splash_screen_file", "splash-screen.md"
    )

    (tmp_path / "splash-config.json").write_text(
        json.dumps({"enabled": False, "title": "Hi"}), encoding="utf-8"
    )

    client = TestClient(app)
    resp = client.get("/api/splash", headers={"X-User-Email": "test@test.com"})
    assert resp.status_code == 200
    data = resp.json()
    # Env var is the single source of truth, so it stays enabled.
    assert data["enabled"] is True
    assert data["title"] == "Hi"


def test_splash_missing_markdown_returns_empty_string(isolated_splash, monkeypatch):
    tmp_path, config_manager = isolated_splash
    _set_enabled(monkeypatch, config_manager, True)
    monkeypatch.setattr(
        config_manager.app_settings, "splash_screen_file", "splash-screen.md"
    )

    client = TestClient(app)
    resp = client.get("/api/splash", headers={"X-User-Email": "test@test.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["markdown"] == ""
