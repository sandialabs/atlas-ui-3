"""Migration 004 for the prompt library (issue #1169).

Alembic has no DuckDB dialect, so the upgrade path is exercised on SQLite (as
in test_schema_migration.py) and, when ``ATLAS_TEST_POSTGRES_URL`` points at a
server, on real PostgreSQL. Concurrency is covered in
test_prompt_library_concurrency.py.
"""

import os
import uuid
from datetime import datetime, timezone

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, make_url, text
from sqlalchemy.orm import sessionmaker

from alembic import command
from atlas.modules.chat_history.database import reset_engine
from atlas.modules.chat_history.models import Base
from atlas.modules.chat_history.prompt_library_repository import PromptLibraryRepository
from atlas.modules.chat_history.schema_migration import default_alembic_ini

LIBRARY_TABLES = {
    "prompt_library_prompts",
    "prompt_library_versions",
    "prompt_library_grants",
    "prompt_library_audit_events",
}
PG_URL = os.environ.get("ATLAS_TEST_POSTGRES_URL")


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


def _alembic(db_url, monkeypatch):
    monkeypatch.setenv("CHAT_HISTORY_DB_URL", db_url)
    return Config(str(default_alembic_ini()))


def _migrate(cfg, db_url, action, revision):
    """Run an Alembic command on a connection we own.

    Handing env.py a connection (as the schema migration runner does) also
    keeps it from calling logging.fileConfig, which would disable every
    logger already created in this pytest process and break later
    log-capturing tests.
    """
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            action(cfg, revision)
    finally:
        cfg.attributes.pop("connection", None)
        engine.dispose()


def _seed_legacy_rows(engine):
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO user_prompts (id, user_email, title, content, created_at, updated_at) "
                "VALUES (:id, :u, :t, :c, :n, :n)"
            ),
            {"id": "p-legacy", "u": "alice@example.com", "t": "Legacy", "c": "legacy body", "n": now},
        )
        conn.execute(
            text(
                "INSERT INTO user_workspaces (id, user_email, name, description, config_json, created_at, updated_at) "
                "VALUES (:id, :u, :n2, NULL, :cfg, :n, :n)"
            ),
            {"id": "w-1", "u": "alice@example.com", "n2": "Work",
             "cfg": '{"active_prompt_key": "userprompt:p-legacy"}', "n": now},
        )


def _assert_upgrade_preserves_and_matches(db_url, monkeypatch):
    cfg = _alembic(db_url, monkeypatch)
    _migrate(cfg, db_url, command.upgrade, "003")
    engine = create_engine(db_url)
    try:
        assert not LIBRARY_TABLES & set(inspect(engine).get_table_names())
        _seed_legacy_rows(engine)

        _migrate(cfg, db_url, command.upgrade, "head")
        assert LIBRARY_TABLES <= set(inspect(engine).get_table_names())
        with engine.connect() as conn:
            row = conn.execute(text("SELECT title, content FROM user_prompts WHERE id='p-legacy'")).one()
            assert tuple(row) == ("Legacy", "legacy body")
            assert conn.execute(text("SELECT count(*) FROM user_workspaces")).scalar() == 1
            # The migrated schema matches the models for the new tables.
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        library_diffs = [d for d in diff if any(t in repr(d) for t in LIBRARY_TABLES)]
        assert library_diffs == []
        unique = {c["name"] for c in inspect(engine).get_unique_constraints("prompt_library_grants")}
        assert "uq_prompt_library_grant" in unique
        columns = {c["name"] for c in inspect(engine).get_columns("prompt_library_prompts")}
        assert {"draft_revision", "sharing_revision"} <= columns

        # The repository works against the migrated schema.
        repo = PromptLibraryRepository(sessionmaker(bind=engine, expire_on_commit=False))
        prompt = repo.create_prompt("alice@example.com", "T", None, "body")
        version = repo.publish_version(prompt["id"], "alice@example.com", None, 1)
        assert version["version_number"] == 1

        # Downgrade removes only the library tables.
        _migrate(cfg, db_url, command.downgrade, "003")
        tables = set(inspect(engine).get_table_names())
        assert not LIBRARY_TABLES & tables and "user_prompts" in tables
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM user_prompts")).scalar() == 1
    finally:
        engine.dispose()


def test_migration_is_additive_and_preserves_existing_data_sqlite(tmp_path, monkeypatch):
    _assert_upgrade_preserves_and_matches(f"sqlite:///{tmp_path / 'm.db'}", monkeypatch)


@pytest.fixture
def pg_database():
    if not PG_URL:
        pytest.skip("ATLAS_TEST_POSTGRES_URL not set")
    admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    name = f"atlas_lib_{uuid.uuid4().hex[:10]}"
    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {name}"))
    url = make_url(PG_URL).set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        admin.dispose()


def test_migration_on_postgresql(pg_database, monkeypatch):
    _assert_upgrade_preserves_and_matches(pg_database, monkeypatch)
