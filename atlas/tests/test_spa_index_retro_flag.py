"""index.html carries the live FEATURE_RETRO_THEMES_ENABLED for first paint (PR #1175)."""

from atlas.core.spa_index import BOOTSTRAP_TAG, render_spa_index

INDEX = (
    '<!doctype html><html><head>'
    '<script src="/theme-bootstrap.js" data-retro-themes="true"></script>'
    '<script type="module" src="/assets/index.js"></script></head></html>'
)


def test_stamps_disabled_flag(tmp_path):
    path = tmp_path / "index.html"
    path.write_text(INDEX)
    html = render_spa_index(path, False)
    assert '<script src="/theme-bootstrap.js" data-retro-runtime="false" data-retro-themes="true">' in html
    assert html.count("data-retro-runtime") == 1


def test_stamps_enabled_flag(tmp_path):
    path = tmp_path / "index.html"
    path.write_text(INDEX)
    assert 'data-retro-runtime="true"' in render_spa_index(path, True)


def test_page_without_bootstrap_tag_is_unchanged(tmp_path):
    path = tmp_path / "index.html"
    path.write_text("<html><body>custom</body></html>")
    assert render_spa_index(path, False) == "<html><body>custom</body></html>"
    assert BOOTSTRAP_TAG not in path.read_text()


def test_spa_routes_serve_stamped_index(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import atlas.main as main

    if not main.static_dir.exists():
        import pytest

        pytest.skip("no frontend build to serve")
    settings = main.app_factory.get_config_manager().app_settings
    monkeypatch.setattr(settings, "feature_retro_themes_enabled", False)
    client = TestClient(main.app)
    for route in ("/", "/admin/logview"):
        response = client.get(route, headers={"X-User-Email": "test@test.com"})
        assert response.status_code == 200
        if BOOTSTRAP_TAG in response.text:
            assert 'data-retro-runtime="false"' in response.text


def test_theme_bootstrap_script_is_served():
    from fastapi.testclient import TestClient

    import atlas.main as main

    if not (main.static_dir / "theme-bootstrap.js").is_file():
        import pytest

        pytest.skip("no frontend build to serve")
    # Like index.html and /assets, the script sits behind AuthMiddleware in
    # production mode (the auth proxy injects the header on every request).
    response = TestClient(main.app).get("/theme-bootstrap.js", headers={"X-User-Email": "test@test.com"})
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "data-retro-runtime" in response.text
