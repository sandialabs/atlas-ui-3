from fastapi import FastAPI
from starlette.testclient import TestClient

from core.rate_limit_middleware import RateLimitMiddleware
from modules.config import config_manager


def test_rate_limit_blocks_after_threshold():
    # Configure very low limits via ConfigManager
    settings = config_manager.app_settings
    orig_rpm = settings.rate_limit_rpm
    orig_window = settings.rate_limit_window_seconds
    orig_per_path = settings.rate_limit_per_path

    settings.rate_limit_rpm = 2
    settings.rate_limit_window_seconds = 60
    settings.rate_limit_per_path = False

    try:
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware)

        @app.get("/ping")
        def ping():
            return {"ok": True}

        client = TestClient(app)

        # First two requests should pass
        r1 = client.get("/ping")
        assert r1.status_code == 200
        r2 = client.get("/ping")
        assert r2.status_code == 200

        # Third request within the window should be rate-limited
        r3 = client.get("/ping")
        assert r3.status_code == 429
        data = r3.json()
        assert "detail" in data
        assert "Retry-After" in r3.headers
    finally:
        # Restore original settings to avoid side effects on other tests
        settings.rate_limit_rpm = orig_rpm
        settings.rate_limit_window_seconds = orig_window
        settings.rate_limit_per_path = orig_per_path
