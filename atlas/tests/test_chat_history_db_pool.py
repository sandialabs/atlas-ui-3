"""Tests for the configurable PostgreSQL connection pool (issue #1091).

Every API process builds its own chat-history engine, so a multi-replica
deployment can open replicas x workers x (pool_size + max_overflow)
connections. These tests pin that the CHAT_HISTORY_DB_* settings reach the
engine, that the defaults keep the pre-#1091 pool bounds, and that DuckDB is
left alone. ``create_engine`` loads the driver without connecting, so no
database is needed.
"""

import pytest
from pydantic import ValidationError
from sqlalchemy.pool import NullPool, QueuePool

from atlas.modules.chat_history import database as chat_history_database
from atlas.modules.chat_history.database import PoolSettings, _postgres_engine_kwargs
from atlas.modules.config.config_manager import AppSettings

_PG_URL = "postgresql+psycopg://atlas:pw@db.example.com:5432/atlas"

_POOL_ENV_VARS = (
    "CHAT_HISTORY_DB_POOL_SIZE",
    "CHAT_HISTORY_DB_MAX_OVERFLOW",
    "CHAT_HISTORY_DB_POOL_TIMEOUT",
    "CHAT_HISTORY_DB_POOL_RECYCLE",
    "CHAT_HISTORY_DB_POOL_PRE_PING",
    "CHAT_HISTORY_DB_CONNECT_TIMEOUT",
    "CHAT_HISTORY_DB_DISABLE_POOLING",
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Strip pool env vars and give each test a fresh process-wide engine."""
    for var in _POOL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    chat_history_database.reset_engine()
    yield
    chat_history_database.reset_engine()


def _settings():
    # _env_file=None keeps the repo's .env out of the test.
    return AppSettings(_env_file=None)


class TestPoolSettingsFromAppSettings:
    def test_defaults_match_previous_pool_bounds(self):
        pool = PoolSettings.from_app_settings(_settings())
        assert pool == PoolSettings()
        assert (pool.pool_size, pool.max_overflow) == (5, 10)
        assert pool.max_connections_per_process == 15

    def test_env_vars_reach_pool_settings(self, monkeypatch):
        monkeypatch.setenv("CHAT_HISTORY_DB_POOL_SIZE", "3")
        monkeypatch.setenv("CHAT_HISTORY_DB_MAX_OVERFLOW", "0")
        monkeypatch.setenv("CHAT_HISTORY_DB_POOL_TIMEOUT", "2.5")
        monkeypatch.setenv("CHAT_HISTORY_DB_POOL_RECYCLE", "300")
        monkeypatch.setenv("CHAT_HISTORY_DB_POOL_PRE_PING", "false")
        monkeypatch.setenv("CHAT_HISTORY_DB_CONNECT_TIMEOUT", "4")
        monkeypatch.setenv("CHAT_HISTORY_DB_DISABLE_POOLING", "true")

        pool = PoolSettings.from_app_settings(_settings())

        assert pool == PoolSettings(
            pool_size=3,
            max_overflow=0,
            pool_timeout=2.5,
            pool_recycle=300,
            pool_pre_ping=False,
            connect_timeout=4,
            disable_pooling=True,
        )
        assert pool.max_connections_per_process is None

    @pytest.mark.parametrize(
        ("var", "value"),
        [
            ("CHAT_HISTORY_DB_POOL_SIZE", "0"),
            ("CHAT_HISTORY_DB_MAX_OVERFLOW", "-1"),
            ("CHAT_HISTORY_DB_POOL_TIMEOUT", "0"),
            ("CHAT_HISTORY_DB_POOL_RECYCLE", "-2"),
            ("CHAT_HISTORY_DB_CONNECT_TIMEOUT", "0"),
        ],
    )
    def test_out_of_range_values_fail_at_startup(self, monkeypatch, var, value):
        # An unbounded or zero-sized pool must not slip through silently.
        monkeypatch.setenv(var, value)
        with pytest.raises(ValidationError):
            _settings()


class TestPostgresEngine:
    def test_engine_uses_configured_pool(self):
        pool = PoolSettings(pool_size=2, max_overflow=3, pool_timeout=7, pool_recycle=600, pool_pre_ping=False)
        engine = chat_history_database.get_engine(_PG_URL, pool)

        assert isinstance(engine.pool, QueuePool)
        assert engine.pool.size() == 2
        assert engine.pool._max_overflow == 3
        assert engine.pool._timeout == 7
        assert engine.pool._recycle == 600
        assert engine.pool._pre_ping is False

    def test_default_engine_keeps_previous_bounds(self):
        engine = chat_history_database.get_engine(_PG_URL)

        assert engine.pool.size() == 5
        assert engine.pool._max_overflow == 10
        assert engine.pool._pre_ping is True

    def test_disable_pooling_uses_null_pool(self):
        engine = chat_history_database.get_engine(_PG_URL, PoolSettings(disable_pooling=True))
        assert isinstance(engine.pool, NullPool)

    @pytest.mark.parametrize("scheme", ["postgresql", "postgresql+psycopg", "postgresql+psycopg2"])
    def test_connect_timeout_is_passed_to_driver(self, scheme):
        # The bare scheme is what Compose and the docs use; it resolves to a
        # libpq driver (psycopg or psycopg2, depending on SQLAlchemy version).
        url = f"{scheme}://atlas:pw@db.example.com:5432/atlas"
        kwargs = _postgres_engine_kwargs(url, PoolSettings(connect_timeout=4))
        assert kwargs["connect_args"] == {"connect_timeout": 4}

    def test_connect_timeout_in_url_wins(self):
        kwargs = _postgres_engine_kwargs(f"{_PG_URL}?connect_timeout=30", PoolSettings(connect_timeout=4))
        assert "connect_args" not in kwargs

    def test_connect_timeout_only_for_libpq_drivers(self):
        # pg8000 is not libpq-based and rejects a connect_timeout argument.
        kwargs = _postgres_engine_kwargs("postgresql+pg8000://atlas:pw@db.example.com/atlas", PoolSettings())
        assert "connect_args" not in kwargs

    def test_existing_engine_warns_on_different_pool(self, caplog):
        first = chat_history_database.get_engine(_PG_URL, PoolSettings(pool_size=2))
        with caplog.at_level("WARNING", logger=chat_history_database.__name__):
            again = chat_history_database.get_engine(_PG_URL, PoolSettings(pool_size=2))
            assert not [r for r in caplog.records if r.levelname == "WARNING"]
            other = chat_history_database.get_engine(_PG_URL, PoolSettings(pool_size=9))
        assert first is again is other
        assert other.pool.size() == 2
        assert "ignoring new pool settings" in caplog.text

    def test_null_pool_omits_queue_pool_arguments(self):
        # NullPool rejects pool_size/max_overflow, so they must not be passed.
        kwargs = _postgres_engine_kwargs(_PG_URL, PoolSettings(disable_pooling=True))
        assert kwargs["poolclass"] is NullPool
        assert not {"pool_size", "max_overflow", "pool_timeout", "pool_recycle"} & kwargs.keys()


class TestDuckDbUnaffected:
    def test_duckdb_ignores_pool_settings(self, tmp_path):
        engine = chat_history_database.get_engine(
            f"duckdb:///{tmp_path / 'history.db'}", PoolSettings(pool_size=1, disable_pooling=True)
        )
        assert engine.dialect.name == "duckdb"
        assert not isinstance(engine.pool, NullPool)


class TestAppFactoryWiring:
    def test_app_factory_passes_configured_pool_to_init_database(self, monkeypatch):
        # Without this, dropping the pool argument in AppFactory would silently
        # fall back to the defaults while every other test still passed.
        import atlas.modules.chat_history as chat_history
        from atlas.infrastructure.app_factory import AppFactory

        captured = {}

        def fake_init_database(db_url=None, pool=None):
            captured["db_url"] = db_url
            captured["pool"] = pool

        monkeypatch.setenv("FEATURE_CHAT_HISTORY_ENABLED", "true")
        monkeypatch.setenv("CHAT_HISTORY_DB_URL", _PG_URL)
        monkeypatch.setenv("CHAT_HISTORY_DB_POOL_SIZE", "3")
        monkeypatch.setenv("CHAT_HISTORY_DB_MAX_OVERFLOW", "1")
        monkeypatch.setattr(chat_history, "init_database", fake_init_database)
        monkeypatch.setattr(chat_history, "get_session_factory", lambda: None)

        AppFactory()

        assert captured["db_url"] == _PG_URL
        assert captured["pool"].pool_size == 3
        assert captured["pool"].max_overflow == 1
