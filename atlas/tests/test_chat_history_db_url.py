"""Tests for assembling chat_history_db_url from individual DB_* env vars.

Covers the behavior added for issue #581: support DB_HOST, DB_PORT, DB_NAME,
DB_USER, DB_PASSWORD, DB_DRIVER as an alternative to a full CHAT_HISTORY_DB_URL,
and the driver normalization added for issue #991: get_engine rewrites bare
postgresql:// URLs to postgresql+psycopg:// so the psycopg (v3) driver is used
on every install path.
"""

from pathlib import Path

import pytest

from atlas.modules.chat_history import database as chat_history_database
from atlas.modules.config.config_manager import (
    AppSettings,
    build_db_url_from_parts,
    normalize_postgres_driver_url,
)


@pytest.fixture(autouse=True)
def _isolate_db_env(monkeypatch):
    """Strip every DB_* / CHAT_HISTORY_DB_URL env var so the host shell can't leak in."""
    for var in (
        "CHAT_HISTORY_DB_URL",
        "DB_DRIVER",
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def _settings():
    # _env_file=None prevents pydantic-settings from reading the repo's .env file
    # (which would otherwise inject CHAT_HISTORY_DB_URL during local test runs).
    return AppSettings(_env_file=None)


class TestChatHistoryDbUrlAssembly:
    def test_default_when_nothing_set(self):
        settings = _settings()
        assert settings.chat_history_db_url == "duckdb:///data/chat_history.db"

    def test_explicit_url_wins_over_parts(self, monkeypatch):
        monkeypatch.setenv("CHAT_HISTORY_DB_URL", "postgresql://explicit:pw@db.example.com:5432/explicit")
        monkeypatch.setenv("DB_HOST", "ignored.example.com")
        monkeypatch.setenv("DB_NAME", "ignored")
        monkeypatch.setenv("DB_USER", "ignored")
        monkeypatch.setenv("DB_PASSWORD", "ignored")
        settings = _settings()
        assert settings.chat_history_db_url == "postgresql://explicit:pw@db.example.com:5432/explicit"

    def test_assembles_full_postgres_url(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "db.example.com")
        monkeypatch.setenv("DB_PORT", "5432")
        monkeypatch.setenv("DB_NAME", "atlas_chat_history")
        monkeypatch.setenv("DB_USER", "atlas")
        monkeypatch.setenv("DB_PASSWORD", "secret")
        settings = _settings()
        assert (
            settings.chat_history_db_url
            == "postgresql://atlas:secret@db.example.com:5432/atlas_chat_history"
        )

    def test_default_driver_is_postgres(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "db")
        monkeypatch.setenv("DB_NAME", "x")
        settings = _settings()
        assert settings.chat_history_db_url.startswith("postgresql://")

    def test_custom_driver(self, monkeypatch):
        monkeypatch.setenv("DB_DRIVER", "postgresql+psycopg")
        monkeypatch.setenv("DB_HOST", "db")
        monkeypatch.setenv("DB_NAME", "x")
        settings = _settings()
        assert settings.chat_history_db_url == "postgresql+psycopg://db/x"

    def test_omits_port_when_unset(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "db")
        monkeypatch.setenv("DB_NAME", "atlas")
        monkeypatch.setenv("DB_USER", "atlas")
        monkeypatch.setenv("DB_PASSWORD", "pw")
        settings = _settings()
        assert settings.chat_history_db_url == "postgresql://atlas:pw@db/atlas"

    def test_omits_credentials_when_user_unset(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "db")
        monkeypatch.setenv("DB_NAME", "atlas")
        settings = _settings()
        assert settings.chat_history_db_url == "postgresql://db/atlas"

    def test_url_encodes_special_chars_in_password(self, monkeypatch):
        # '@', ':', '/', and '#' would otherwise corrupt the URL
        monkeypatch.setenv("DB_HOST", "db")
        monkeypatch.setenv("DB_NAME", "atlas")
        monkeypatch.setenv("DB_USER", "atlas")
        monkeypatch.setenv("DB_PASSWORD", "p@ss:w/rd#1")
        settings = _settings()
        assert (
            settings.chat_history_db_url
            == "postgresql://atlas:p%40ss%3Aw%2Frd%231@db/atlas"
        )

    def test_url_encodes_special_chars_in_user(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "db")
        monkeypatch.setenv("DB_NAME", "atlas")
        monkeypatch.setenv("DB_USER", "user@domain")
        monkeypatch.setenv("DB_PASSWORD", "pw")
        settings = _settings()
        assert (
            settings.chat_history_db_url == "postgresql://user%40domain:pw@db/atlas"
        )

    def test_only_host_set_uses_default_db_name_omitted(self, monkeypatch):
        # Edge case: DB_HOST alone is enough to opt in; URL has no db path segment
        monkeypatch.setenv("DB_HOST", "db")
        settings = _settings()
        assert settings.chat_history_db_url == "postgresql://db"

    def test_only_db_name_assumes_localhost(self, monkeypatch):
        monkeypatch.setenv("DB_NAME", "atlas")
        settings = _settings()
        assert settings.chat_history_db_url == "postgresql://localhost/atlas"

    def test_shared_builder_for_alembic_env(self):
        expected_password = "p%40ss%3Aw%2Frd"
        assert (
            build_db_url_from_parts(
                db_driver="postgresql",
                db_host="db.example.com",
                db_port="5432",
                db_name="atlas",
                db_user="user@domain",
                db_password="p@ss:w/rd",
            )
            == f"postgresql://user%40domain:{expected_password}@db.example.com:5432/atlas"
        )


class TestChatHistoryDbUrlPrecedenceFromAllSources:
    """Precedence guard must fire for every pydantic-settings source, not just os.environ.

    Regression coverage for the case where CHAT_HISTORY_DB_URL is supplied via an
    .env file or directly as a constructor kwarg while DB_* parts are also present —
    the explicit URL must still win.
    """

    def test_url_from_env_file_wins_over_parts(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "CHAT_HISTORY_DB_URL=postgresql://envfile:pw@db.example.com:5432/from_envfile\n"
        )
        # DB_* parts on the process env would otherwise trigger assembly.
        monkeypatch.setenv("DB_HOST", "ignored.example.com")
        monkeypatch.setenv("DB_NAME", "ignored")
        monkeypatch.setenv("DB_USER", "ignored")
        monkeypatch.setenv("DB_PASSWORD", "ignored")

        settings = AppSettings(_env_file=str(env_file))
        assert (
            settings.chat_history_db_url
            == "postgresql://envfile:pw@db.example.com:5432/from_envfile"
        )

    def test_url_from_init_kwarg_wins_over_parts(self, monkeypatch):
        # The field's validation_alias is "CHAT_HISTORY_DB_URL", so init kwargs must
        # use the alias name (pydantic v2 behavior without populate_by_name).
        monkeypatch.setenv("DB_HOST", "ignored.example.com")
        monkeypatch.setenv("DB_NAME", "ignored")
        monkeypatch.setenv("DB_USER", "ignored")
        monkeypatch.setenv("DB_PASSWORD", "ignored")

        settings = AppSettings(
            _env_file=None,
            CHAT_HISTORY_DB_URL="postgresql://kwarg:pw@db.example.com:5432/from_kwarg",
        )
        assert (
            settings.chat_history_db_url
            == "postgresql://kwarg:pw@db.example.com:5432/from_kwarg"
        )

    def test_default_url_does_not_count_as_explicit(self, monkeypatch):
        # Sanity check: the static default must NOT register as explicitly provided,
        # otherwise the parts path would never run.
        monkeypatch.setenv("DB_HOST", "db")
        monkeypatch.setenv("DB_NAME", "atlas")
        settings = _settings()
        assert settings.chat_history_db_url == "postgresql://db/atlas"


class TestPostgresDriverInstalled:
    """The driver SQLAlchemy picks for a postgresql URL must be installed (issue #988).

    ``get_engine`` names the psycopg (v3) driver on bare ``postgresql://`` URLs
    (issue #991), so the driver no longer depends on the installed SQLAlchemy
    version. ``get_engine`` loads the driver module without connecting, so a
    missing driver fails here instead of at chat-history startup.
    """

    @pytest.fixture(autouse=True)
    def _fresh_engine(self):
        chat_history_database.reset_engine()
        yield
        chat_history_database.reset_engine()

    @pytest.mark.parametrize(
        ("scheme", "expected_driver"),
        [
            # A bare postgresql:// URL is normalized to postgresql+psycopg://
            # before the engine is created, whatever SQLAlchemy is installed.
            ("postgresql", "psycopg"),
            ("postgresql+psycopg", "psycopg"),
            ("postgresql+psycopg2", "psycopg2"),
        ],
    )
    def test_chat_history_engine_loads_driver(self, scheme, expected_driver):
        url = build_db_url_from_parts(
            db_driver=scheme, db_host="db.example.com", db_name="atlas", db_user="atlas", db_password="pw"
        )
        engine = chat_history_database.get_engine(url)
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == expected_driver
        # Assert the rewritten URL, not just the loaded dialect: on SQLAlchemy
        # 2.1 a bare postgresql:// URL loads psycopg even without the
        # normalization, so the drivername is what fails if get_engine stops
        # rewriting (issue #991).
        assert engine.url.drivername == f"postgresql+{expected_driver}"

    def test_explicit_url_from_env_is_normalized(self):
        # The CHAT_HISTORY_DB_URL path: app_factory hands the setting straight
        # to init_database/get_engine, so a bare URL the operator wrote must be
        # normalized too, not just assembled DB_* parts.
        engine = chat_history_database.get_engine(
            "postgresql://atlas:pw@db.example.com:5432/atlas_chat_history"
        )
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg"
        assert engine.url.drivername == "postgresql+psycopg"


class TestExternalPoolerConnectArgs:
    """CHAT_HISTORY_DB_DISABLE_POOLING keeps psycopg 3 statements unnamed.

    psycopg (v3) auto-prepares statements after a few executions, which
    transaction-mode PgBouncer below 1.21 cannot route. When an external pooler
    does the pooling, the engine passes ``prepare_threshold=None`` so no named
    statements are created. psycopg2 has no such setting and would reject the
    keyword, so it must only reach the psycopg 3 driver.
    """

    @pytest.mark.parametrize(
        ("db_url", "expect_prepare_threshold"),
        [
            ("postgresql://atlas:pw@db.example.com:5432/atlas", True),
            ("postgresql+psycopg://atlas:pw@db.example.com:5432/atlas", True),
            ("postgresql+psycopg2://atlas:pw@db.example.com:5432/atlas", False),
        ],
    )
    def test_disable_pooling_disables_auto_prepare_for_psycopg3(self, db_url, expect_prepare_threshold):
        url = normalize_postgres_driver_url(db_url)
        kwargs = chat_history_database._postgres_engine_kwargs(
            url, chat_history_database.PoolSettings(disable_pooling=True)
        )
        assert kwargs["poolclass"] is chat_history_database.NullPool
        connect_args = kwargs["connect_args"]
        assert connect_args["connect_timeout"] == chat_history_database.PoolSettings().connect_timeout
        if expect_prepare_threshold:
            assert connect_args["prepare_threshold"] is None
        else:
            assert "prepare_threshold" not in connect_args

    def test_pooled_engine_keeps_auto_prepare(self):
        url = normalize_postgres_driver_url("postgresql://atlas:pw@db.example.com:5432/atlas")
        kwargs = chat_history_database._postgres_engine_kwargs(url, chat_history_database.PoolSettings())
        assert "prepare_threshold" not in kwargs.get("connect_args", {})
        assert "poolclass" not in kwargs


class TestAlembicEnvNormalization:
    """alembic/env.py builds its own URL and must normalize it like the app.

    Guards the env.py wiring for issue #991: the URL override is passed through
    ``normalize_postgres_driver_url`` before it reaches ``sqlalchemy.url``. The
    end-to-end offline migration proof for both URL sources (DB_* parts and an
    explicit CHAT_HISTORY_DB_URL) lives in
    test/pr-validation/test_pr991_psycopg_driver.sh.
    """

    def test_env_py_passes_url_through_normalization(self):
        env_source = (Path(__file__).resolve().parents[2] / "alembic" / "env.py").read_text(encoding="utf-8")
        assert "normalize_postgres_driver_url(db_url)" in env_source


class TestNormalizePostgresDriverUrl:
    """Only bare postgresql:// URLs are rewritten to name the psycopg driver (issue #991).

    A bare ``postgresql://`` URL picks SQLAlchemy's default driver, which changed
    from psycopg2 to psycopg (v3) between SQLAlchemy 2.0 and 2.1 -- so ``uv sync``
    from the lockfile and the Docker/pip install paths could disagree. Naming the
    driver in the URL makes the choice independent of the installed version.
    """

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (
                "postgresql://atlas:pw@db.example.com:5432/atlas_chat_history",
                "postgresql+psycopg://atlas:pw@db.example.com:5432/atlas_chat_history",
            ),
            ("postgresql://db", "postgresql+psycopg://db"),
            # Anything after the scheme (path, query string) survives the rewrite.
            (
                "postgresql://db/atlas?sslmode=require",
                "postgresql+psycopg://db/atlas?sslmode=require",
            ),
            # URLs that already name a driver pass through untouched.
            ("postgresql+psycopg://db/atlas", "postgresql+psycopg://db/atlas"),
            (
                "postgresql+psycopg2://atlas:pw@db/atlas",
                "postgresql+psycopg2://atlas:pw@db/atlas",
            ),
            (
                "postgresql+asyncpg://atlas:pw@db/atlas",
                "postgresql+asyncpg://atlas:pw@db/atlas",
            ),
            # Only the exact bare scheme is rewritten; legacy "postgres://" is
            # not something SQLAlchemy accepts, so it is not rewritten either.
            ("postgres://db/atlas", "postgres://db/atlas"),
            # Non-postgres URLs are untouched.
            ("duckdb:///data/chat_history.db", "duckdb:///data/chat_history.db"),
            ("sqlite:///data/chat_history.db", "sqlite:///data/chat_history.db"),
        ],
    )
    def test_rewrites_only_bare_postgresql_urls(self, url, expected):
        assert normalize_postgres_driver_url(url) == expected
