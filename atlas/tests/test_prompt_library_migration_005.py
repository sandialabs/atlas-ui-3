"""Migration 005: pin markers (legacy compatibility) and audit deduplication.

Exercised on SQLite (Alembic has no DuckDB dialect) and, with
``ATLAS_TEST_POSTGRES_URL``, on PostgreSQL: the backfill marks exactly the
conversations that might hold a pin, existing audit rows keep one occurrence,
the schema matches the models, and the downgrade restores 004.
"""

import json
import os
import uuid
from datetime import datetime, timezone

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, make_url, text

from alembic import command
from atlas.modules.chat_history.database import reset_engine
from atlas.modules.chat_history.models import Base
from atlas.modules.chat_history.schema_migration import default_alembic_ini

PG_URL = os.environ.get("ATLAS_TEST_POSTGRES_URL")
NEW_TABLES = {"conversation_prompt_pin_markers", "prompt_library_audit_events"}


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


def _migrate(db_url, monkeypatch, action, revision):
    monkeypatch.setenv("CHAT_HISTORY_DB_URL", db_url)
    cfg = Config(str(default_alembic_ini()))
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            action(cfg, revision)
    finally:
        engine.dispose()


CONVERSATIONS = {
    "pinned": json.dumps({"prompt_library": {"prompt_id": "p", "version_id": "v"}, "prompt_library_pin_revision": 1}),
    "cleared": json.dumps({"prompt_library_history": [{"prompt_id": "p", "version_id": "v"}],
                           "prompt_library_pin_revision": 2}),
    "damaged-with-pin": '{"prompt_library": {"prompt_id": "p", "vers',
    "damaged-plain": "{not json",
    "plain": json.dumps({"workspace_id": "w"}),
    "empty": None,
    "used-then-wiped": json.dumps({}),  # named only by a "use" audit event
}
EXPECTED_MARKED = {"pinned", "cleared", "damaged-with-pin", "used-then-wiped"}


def _seed(engine):
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        for cid, meta in CONVERSATIONS.items():
            conn.execute(text(
                "INSERT INTO conversations (id, user_email, title, model, created_at, updated_at, message_count, "
                "metadata_json) VALUES (:id, 'u@example.com', 't', 'm', :n, :n, 0, :meta)"
            ), {"id": cid, "n": now, "meta": meta})
        for i, (kind, details) in enumerate([
            ("use", {"conversation_id": "used-then-wiped"}),
            ("use", {"conversation_id": "deleted-conversation"}),
            ("use", "not json"),
            ("share", {"groups_added": ["eng"]}),
        ]):
            conn.execute(text(
                "INSERT INTO prompt_library_audit_events (id, prompt_id, event_type, actor_email, details_json, "
                "created_at) VALUES (:id, 'p', :kind, 'a@example.com', :d, :n)"
            ), {"id": f"e{i}", "kind": kind, "d": details if isinstance(details, str) else json.dumps(details), "n": now})


def _check(db_url, monkeypatch):
    _migrate(db_url, monkeypatch, command.upgrade, "004")
    engine = create_engine(db_url)
    try:
        _seed(engine)
        _migrate(db_url, monkeypatch, command.upgrade, "head")
        with engine.connect() as conn:
            marked = {r[0] for r in conn.execute(text("SELECT conversation_id FROM conversation_prompt_pin_markers"))}
            assert marked == EXPECTED_MARKED
            rows = conn.execute(text(
                "SELECT occurrences, dedup_key, last_occurred_at FROM prompt_library_audit_events"
            )).all()
            assert len(rows) == 4 and all(r[0] == 1 and r[1] is None and r[2] is None for r in rows)
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        assert [d for d in diff if any(t in repr(d) for t in NEW_TABLES)] == []

        _migrate(db_url, monkeypatch, command.downgrade, "004")
        tables = set(inspect(engine).get_table_names())
        assert "conversation_prompt_pin_markers" not in tables
        columns = {c["name"] for c in inspect(engine).get_columns("prompt_library_audit_events")}
        assert not {"occurrences", "dedup_key", "last_occurred_at"} & columns
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM prompt_library_audit_events")).scalar() == 4
            assert conn.execute(text("SELECT count(*) FROM conversations")).scalar() == len(CONVERSATIONS)
    finally:
        engine.dispose()


def test_migration_005_sqlite(tmp_path, monkeypatch):
    _check(f"sqlite:///{tmp_path / 'm.db'}", monkeypatch)


def test_migration_005_postgresql(monkeypatch):
    if not PG_URL:
        pytest.skip("ATLAS_TEST_POSTGRES_URL not set")
    admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    name = f"atlas_m005_{uuid.uuid4().hex[:10]}"
    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {name}"))
    try:
        _check(make_url(PG_URL).set(database=name).render_as_string(hide_password=False), monkeypatch)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        admin.dispose()
