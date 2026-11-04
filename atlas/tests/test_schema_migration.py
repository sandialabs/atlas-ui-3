"""Schema migration runner used by the Kubernetes init container (issue #1129).

The PostgreSQL advisory-lock path (concurrent pods, one upgrade) needs a real
server and is exercised by test/pr-validation/test_pr1165_k8s_staging.sh; these
tests cover the database-independent behaviour on SQLite (Alembic has no DuckDB dialect).
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from atlas.modules.chat_history import schema_migration
from atlas.modules.chat_history.database import init_database, reset_engine
from atlas.modules.chat_history.schema_migration import (
    MigrationRefused,
    current_revisions,
    default_alembic_ini,
    head_revisions,
    upgrade_to_head,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _revisions(db_url: str) -> set:
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            return current_revisions(connection)
    finally:
        engine.dispose()


def test_default_ini_is_the_repository_alembic_ini(monkeypatch):
    monkeypatch.delenv("ATLAS_ALEMBIC_INI", raising=False)
    assert default_alembic_ini() == REPO_ROOT / "alembic.ini"
    monkeypatch.setenv("ATLAS_ALEMBIC_INI", "/elsewhere/alembic.ini")
    assert default_alembic_ini() == Path("/elsewhere/alembic.ini")


def test_upgrades_an_empty_database_to_head_then_changes_nothing(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'fresh.db'}"
    heads = head_revisions(default_alembic_ini())
    assert heads

    assert upgrade_to_head(db_url, wait_seconds=0) == set()
    assert _revisions(db_url) == heads

    # A second pod (or a scale-up) finds the schema at head.
    assert upgrade_to_head(db_url, wait_seconds=0) == heads
    assert _revisions(db_url) == heads


def test_refuses_a_database_created_by_create_all(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    try:
        init_database(db_url)
    finally:
        reset_engine()
    with pytest.raises(MigrationRefused, match="no alembic_version table"):
        upgrade_to_head(db_url, wait_seconds=0)
    assert _revisions(db_url) == set()


def test_leaves_a_newer_schema_alone_on_rollback(tmp_path, caplog):
    db_url = f"sqlite:///{tmp_path / 'newer.db'}"
    upgrade_to_head(db_url, wait_seconds=0)
    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.exec_driver_sql("UPDATE alembic_version SET version_num = '999_from_a_newer_release'")
    engine.dispose()

    with caplog.at_level("WARNING", logger="atlas.schema_migration"):
        assert upgrade_to_head(db_url, wait_seconds=0) == {"999_from_a_newer_release"}
    assert _revisions(db_url) == {"999_from_a_newer_release"}
    assert "newer than this image's head" in caplog.text


def test_refuses_without_a_database_url(monkeypatch):
    for name in ("CHAT_HISTORY_DB_URL", "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(MigrationRefused, match="No database configured"):
        upgrade_to_head(wait_seconds=0)


def test_refuses_a_missing_alembic_ini(tmp_path):
    with pytest.raises(MigrationRefused, match="alembic.ini not found"):
        upgrade_to_head(f"sqlite:///{tmp_path / 'x.db'}", ini_path=tmp_path / "missing.ini", wait_seconds=0)


def test_retries_an_unreachable_database_until_the_deadline(monkeypatch):
    sleeps = []
    clock = iter([0.0, 0.0, 1.0, 2.0, 3.0, 99.0])
    # Replace the module's clock only: the driver uses time.monotonic too.
    monkeypatch.setattr(schema_migration, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    with pytest.raises(OperationalError):
        upgrade_to_head(
            "postgresql+psycopg://atlas:x@127.0.0.1:1/atlas?connect_timeout=1",
            wait_seconds=10,
            sleep=sleeps.append,
        )
    assert sleeps == [1.0, 2.0, 4.0, 8.0]


def test_main_exit_codes(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAT_HISTORY_DB_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    assert schema_migration.main(["--wait-seconds", "0"]) == 0

    legacy = tmp_path / "legacy-cli.db"
    try:
        init_database(f"sqlite:///{legacy}")
    finally:
        reset_engine()
    monkeypatch.setenv("CHAT_HISTORY_DB_URL", f"sqlite:///{legacy}")
    assert schema_migration.main(["--wait-seconds", "0"]) == 2
