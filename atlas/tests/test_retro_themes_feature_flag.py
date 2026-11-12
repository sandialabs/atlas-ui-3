"""FEATURE_RETRO_THEMES_ENABLED: run-time switch for the retro theme gallery.

Issue #1119 / PR #1175 review: admins can turn the Retro Design System themes
on or off at run time (this setting) as well as at build time
(VITE_FEATURE_RETRO_THEMES). The frontend reads ``features.retro_themes`` from
both config endpoints; the shell endpoint matters most because it is what the
UI has on first paint.
"""

import pytest
from main import app
from starlette.testclient import TestClient

from atlas.infrastructure.app_factory import app_factory
from atlas.modules.config.config_manager import AppSettings, config_manager


def _headers():
    return {"X-User-Email": config_manager.app_settings.test_user}


def test_defaults_off(monkeypatch):
    monkeypatch.delenv("FEATURE_RETRO_THEMES_ENABLED", raising=False)
    assert AppSettings(_env_file=None).feature_retro_themes_enabled is False


@pytest.mark.parametrize("raw, expected", [("true", True), ("1", True), ("false", False), ("0", False)])
def test_reads_environment(monkeypatch, raw, expected):
    monkeypatch.setenv("FEATURE_RETRO_THEMES_ENABLED", raw)
    assert AppSettings(_env_file=None).feature_retro_themes_enabled is expected


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("path", ["/api/config/shell", "/api/config"])
def test_config_endpoints_expose_flag(monkeypatch, enabled, path):
    app_settings = app_factory.get_config_manager().app_settings
    monkeypatch.setattr(app_settings, "feature_retro_themes_enabled", enabled, raising=False)
    resp = TestClient(app).get(path, headers=_headers())
    assert resp.status_code == 200
    assert resp.json()["features"]["retro_themes"] is enabled
