"""Tests for the DuckDB secondary-index workaround.

DuckDB's ART indexes can silently stop matching rows that are present in the
file, which surfaces as empty conversations and missing custom prompts. The
workaround drops those indexes on the DuckDB backend only -- PostgreSQL keeps
them. See ``atlas.core.duckdb_indexes``.
"""

import pytest
from sqlalchemy import (
    Column,
    Index,
    MetaData,
    String,
    Table,
    create_engine,
    inspect,
)

from atlas.core.duckdb_indexes import drop_duckdb_secondary_indexes


@pytest.fixture
def metadata():
    md = MetaData()
    Table(
        "widgets",
        md,
        Column("id", String(36), primary_key=True),
        Column("owner", String(255), nullable=False, index=True),
        Column("updated_at", String(64), nullable=False),
        Index("ix_widgets_owner_updated", "owner", "updated_at"),
    )
    return md


def _duckdb_index_names(engine):
    """Read the index list from DuckDB directly.

    duckdb-engine cannot reflect indexes -- ``inspect().get_indexes()`` always
    returns an empty list -- so asserting through it would pass vacuously.
    """
    with engine.connect() as conn:
        return sorted(
            row[0]
            for row in conn.exec_driver_sql(
                "select index_name from duckdb_indexes()"
            ).fetchall()
        )


def test_drops_declared_indexes_on_duckdb(tmp_path, metadata):
    engine = create_engine(f"duckdb:///{tmp_path / 'widgets.db'}")
    metadata.create_all(engine)
    assert _duckdb_index_names(engine) == [
        "ix_widgets_owner",
        "ix_widgets_owner_updated",
    ]

    dropped = drop_duckdb_secondary_indexes(engine, metadata)

    assert dropped == 2
    assert _duckdb_index_names(engine) == []


def test_rows_remain_queryable_after_dropping(tmp_path, metadata):
    engine = create_engine(f"duckdb:///{tmp_path / 'widgets.db'}")
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "insert into widgets values ('w1', 'someone', '2026-01-01')"
        )

    drop_duckdb_secondary_indexes(engine, metadata)

    with engine.connect() as conn:
        found = conn.exec_driver_sql(
            "select count(*) from widgets where owner = 'someone'"
        ).scalar()
    assert found == 1


def test_is_a_no_op_on_other_backends(tmp_path, metadata):
    """Only DuckDB is affected; every other dialect keeps its indexes."""
    engine = create_engine(f"sqlite:///{tmp_path / 'widgets.db'}")
    metadata.create_all(engine)

    dropped = drop_duckdb_secondary_indexes(engine, metadata)

    assert dropped == 0
    assert inspect(engine).get_indexes("widgets"), "non-DuckDB indexes must survive"


def test_init_database_leaves_no_indexes(tmp_path, monkeypatch):
    """The chat-history startup path drops the indexes it just created."""
    from atlas.modules.chat_history import database as chat_db

    monkeypatch.setattr(chat_db, "_engine", None)
    monkeypatch.setattr(chat_db, "_session_factory", None)
    engine = chat_db.init_database(f"duckdb:///{tmp_path / 'chat.db'}")

    assert _duckdb_index_names(engine) == []
