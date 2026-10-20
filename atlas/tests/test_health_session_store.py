"""/api/health with a shared session store (issue #1091).

The endpoint is unauthenticated, so it reports reachability only; the
detail (masked URL, prefix, counters) is behind the admin route.
"""

from unittest.mock import patch

import fakeredis
import pytest
from fastapi.testclient import TestClient
from main import app

from atlas.infrastructure.app_factory import app_factory
from atlas.infrastructure.redis import RedisClientManager, RedisSettings
from atlas.infrastructure.sessions.redis_repository import RedisSessionRepository


@pytest.fixture
def shared_store():
    server = fakeredis.FakeServer()
    manager = RedisClientManager(
        RedisSettings(url="redis://ops:topsecret@redis.internal:6379/0", key_prefix="prod-a"),
        client=fakeredis.FakeAsyncRedis(server=server),
    )
    repo = RedisSessionRepository(manager)
    settings = app_factory.config_manager.app_settings
    with patch.object(app_factory, "redis", manager), \
            patch.object(app_factory, "session_repository", repo), \
            patch.object(settings, "session_repository_type", "redis"):
        yield server


def test_health_without_a_shared_store_is_unchanged():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert "session_store" not in response.json()


def test_healthy_store_reports_only_reachability(shared_store):
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["session_store"]["status"] == "ok"
    assert set(body["session_store"]) <= {"backend", "status", "latency_ms"}
    assert "redis.internal" not in response.text
    assert "prod-a" not in response.text
    assert "topsecret" not in response.text


def test_unreachable_store_is_degraded_but_still_200(shared_store):
    shared_store.connected = False
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["session_store"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_admin_detail_includes_counters_with_the_password_masked(shared_store):
    report = await app_factory.session_store_health()
    assert report["url"] == "redis://ops:***@redis.internal:6379/0"
    assert report["key_prefix"] == "prod-a"
    assert "command_errors" in report and "live_sessions" in report
