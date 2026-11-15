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



@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, ["dark", "light"]),
        ("", ["dark", "light"]),
        ("windows-xp-luna", ["dark", "light", "windows-xp-luna"]),
        (" LIGHT, dark,win95, WIN95, unknown, https://evil.test", ["dark", "light", "win95"]),
        ("bad-id,also-bad", ["dark", "light"]),
    ],
)
def test_theme_allowlist_defaults_safely_and_filters_unknown_ids(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("ATLAS_ALLOWED_THEMES", raising=False)
    else:
        monkeypatch.setenv("ATLAS_ALLOWED_THEMES", raw)
    settings = AppSettings(_env_file=None)
    assert list(settings.allowed_theme_ids) == expected


@pytest.mark.parametrize("path", ["/api/config/shell", "/api/config"])
def test_both_config_endpoints_publish_only_approved_themes(monkeypatch, path):
    app_settings = app_factory.get_config_manager().app_settings
    monkeypatch.setattr(app_settings, "atlas_allowed_themes", "windows-xp-luna,unknown,win95")
    response = TestClient(app).get(path, headers=_headers())
    assert response.status_code == 200
    assert response.json()["features"]["allowed_themes"] == [
        "dark", "light", "windows-xp-luna", "win95"
    ]


def test_backend_theme_catalog_matches_generated_frontend(tmp_path):
    import json
    import re
    from pathlib import Path
    from atlas.core.theme_policy import RETRO_THEME_IDS

    source = Path(__file__).parents[2] / "frontend/src/themes/retroThemeModes.generated.js"
    text = source.read_text()
    found = re.search(r"Object\.freeze\((\{.*?\})\)", text, re.S)
    assert found is not None
    assert frozenset(json.loads(found.group(1))) == RETRO_THEME_IDS
