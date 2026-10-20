"""Shared Redis client lifecycle and configuration (issue #1091)."""

import socket
from types import SimpleNamespace

import fakeredis
import pytest

from atlas.infrastructure.redis import RedisClientManager, RedisSettings, redact_redis_url
from atlas.modules.config.settings import AppSettings


def _app_settings(**overrides):
    values = dict(
        redis_url="redis://redis:6379/0",
        redis_key_prefix="atlas",
        redis_max_connections=7,
        redis_pool_timeout=2.0,
        redis_socket_timeout=3.0,
        redis_connect_timeout=1.5,
        redis_health_check_interval=15,
        redis_tls_ca_certs=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_settings_come_from_app_settings():
    settings = RedisSettings.from_app_settings(_app_settings())
    assert settings.url == "redis://redis:6379/0"
    assert settings.max_connections == 7
    assert settings.pool_timeout == 2.0
    assert settings.connect_timeout == 1.5


def test_missing_url_fails_loudly():
    with pytest.raises(ValueError, match="REDIS_URL is required"):
        RedisSettings.from_app_settings(_app_settings(redis_url=None))


def test_unknown_scheme_is_rejected():
    with pytest.raises(ValueError, match="redis://, rediss://"):
        RedisSettings.from_app_settings(_app_settings(redis_url="http://redis:6379"))


def test_app_settings_validate_redis_bounds(monkeypatch):
    monkeypatch.setenv("REDIS_MAX_CONNECTIONS", "0")
    with pytest.raises(Exception):
        AppSettings()


def test_app_settings_defaults_need_no_redis():
    settings = AppSettings()
    assert settings.session_repository_type == "memory"
    assert settings.redis_max_connections == 20
    assert settings.session_ttl_seconds == 86400


def test_pool_is_bounded_and_blocking():
    import redis.asyncio as redis_asyncio

    manager = RedisClientManager(RedisSettings.from_app_settings(_app_settings()))
    pool = manager.client.connection_pool
    assert isinstance(pool, redis_asyncio.BlockingConnectionPool)
    assert pool.max_connections == 7
    assert pool.timeout == 2.0
    kwargs = pool.connection_kwargs
    assert kwargs["socket_timeout"] == 3.0
    assert kwargs["socket_connect_timeout"] == 1.5


def test_tls_url_uses_ssl_connections_with_ca_bundle():
    manager = RedisClientManager(
        RedisSettings.from_app_settings(
            _app_settings(redis_url="rediss://u:p@redis:6380/0", redis_tls_ca_certs="/ca.pem")
        )
    )
    pool = manager.client.connection_pool
    assert "SSL" in pool.connection_class.__name__
    assert pool.connection_kwargs["ssl_ca_certs"] == "/ca.pem"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("redis://redis:6379/0", "redis://redis:6379/0"),
        ("rediss://user:secret@redis:6380/1", "rediss://user:***@redis:6380/1"),
        ("redis://:secret@redis/0", "redis://:***@redis/0"),
        ("redis://redis:6379/0?password=secret", "redis://redis:6379/0?password=***"),
        (
            "rediss://redis/0?username=ops&password=secret&ssl_cert_reqs=required",
            "rediss://redis/0?username=***&password=***&ssl_cert_reqs=required",
        ),
    ],
)
def test_passwords_never_reach_logs(url, expected):
    assert redact_redis_url(url) == expected


def test_keys_are_namespaced():
    manager = RedisClientManager(
        RedisSettings(url="redis://x", key_prefix="prod-a"), client=fakeredis.FakeAsyncRedis()
    )
    assert manager.key("session", "abc") == "prod-a:session:abc"


def _unused_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_start_raises_when_redis_is_unreachable():
    """Startup must fail, not fall back to process-local sessions."""
    manager = RedisClientManager(
        RedisSettings(
            url=f"redis://:topsecret@127.0.0.1:{_unused_port()}/0",
            connect_timeout=0.5,
            socket_timeout=0.5,
            pool_timeout=0.5,
        )
    )
    with pytest.raises(RuntimeError, match="unreachable") as excinfo:
        await manager.start()
    assert "topsecret" not in str(excinfo.value)
    assert manager.stats.command_errors == 1
    await manager.close()
    await manager.close()  # idempotent


@pytest.mark.asyncio
async def test_start_and_health_against_a_server():
    server = fakeredis.FakeServer()
    manager = RedisClientManager(
        RedisSettings(url="redis://fake/0"), client=fakeredis.FakeAsyncRedis(server=server)
    )
    await manager.start()
    report = await manager.health()
    assert report["status"] == "ok"
    assert report["max_connections"] == 20
    assert "latency_ms" in report

    server.connected = False
    report = await manager.health()
    assert report["status"] == "unavailable"
    assert report["command_errors"] == 1
    await manager.close()
