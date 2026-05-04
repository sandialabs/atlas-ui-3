"""Tests for assembling chat_history_db_url from individual DB_* env vars.

Covers the behavior added for issue #581: support DB_HOST, DB_PORT, DB_NAME,
DB_USER, DB_PASSWORD, DB_DRIVER as an alternative to a full CHAT_HISTORY_DB_URL.
"""

import pytest

from atlas.modules.config.config_manager import AppSettings


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
