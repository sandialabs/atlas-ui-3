"""Stored team-prompt pin state (issue #1169, third review).

A cleared pin must stay cleared: a session that still holds the old pin --
however its save interleaves with the clear -- cannot write it back. Covers
the pure merge rule, the repository on DuckDB, and the clear/save race on
PostgreSQL with independent engines standing in for replicas.
"""

import os
import threading
import uuid

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import sessionmaker

from alembic import command
from atlas.domain.conversation_prompt_pin import (
    KEEP_STORED_PIN,
    PIN_EPOCH_METADATA_KEY,
    PIN_HISTORY_METADATA_KEY,
    PIN_METADATA_KEY,
    reconcile_prompt_pin,
)
from atlas.modules.chat_history.database import reset_engine
from atlas.modules.chat_history.schema_migration import default_alembic_ini

PG_URL = os.environ.get("ATLAS_TEST_POSTGRES_URL")
USER = "member@example.com"
PIN = {"prompt_id": "p1", "version_id": "v1", "version_number": 1, "title": "T"}
HISTORY = [{**PIN, "pinned_at": "2026-01-01T00:00:00+00:00", "from_message_index": 0}]
MESSAGES = [
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "reply"},
]


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


def test_stale_epoch_drops_the_pin_and_keeps_the_stored_epoch():
    stored = {PIN_EPOCH_METADATA_KEY: 2, PIN_HISTORY_METADATA_KEY: HISTORY}
    merged = reconcile_prompt_pin(stored, {"agent_mode": False, PIN_METADATA_KEY: PIN, PIN_EPOCH_METADATA_KEY: 1})
    assert PIN_METADATA_KEY not in merged
    assert merged[PIN_EPOCH_METADATA_KEY] == 2
    assert merged[PIN_HISTORY_METADATA_KEY] == HISTORY  # history the save lacked is kept
    assert merged["agent_mode"] is False


def test_current_epoch_keeps_the_pin():
    merged = reconcile_prompt_pin({PIN_EPOCH_METADATA_KEY: 2}, {PIN_METADATA_KEY: PIN, PIN_EPOCH_METADATA_KEY: 2})
    assert merged[PIN_METADATA_KEY] == PIN and merged[PIN_EPOCH_METADATA_KEY] == 2


def test_keep_stored_carries_the_stored_pin_through():
    stored = {PIN_METADATA_KEY: PIN, PIN_HISTORY_METADATA_KEY: HISTORY, PIN_EPOCH_METADATA_KEY: 3}
    merged = reconcile_prompt_pin(stored, {"workspace_id": None, KEEP_STORED_PIN: True})
    assert merged == {"workspace_id": None, **stored}
    # Nothing stored: nothing invented, and the marker never reaches the record.
    assert reconcile_prompt_pin(None, {KEEP_STORED_PIN: True}) == {}


@pytest.mark.parametrize("bad_epoch", [None, -1, "3", True, 1.5])
def test_malformed_epochs_read_as_zero(bad_epoch):
    merged = reconcile_prompt_pin({PIN_EPOCH_METADATA_KEY: bad_epoch}, {PIN_METADATA_KEY: PIN})
    assert merged[PIN_METADATA_KEY] == PIN and PIN_EPOCH_METADATA_KEY not in merged


def test_never_cleared_records_gain_no_epoch_key():
    """Saves of conversations that were never cleared keep their metadata shape."""
    assert reconcile_prompt_pin({"agent_mode": False}, {"agent_mode": True}) == {"agent_mode": True}


@pytest.fixture
def duck_store(tmp_path):
    from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database

    init_database(f"duckdb:///{tmp_path / 'pins.db'}")
    return ConversationRepository(get_session_factory())


def _save(store, conv, metadata, messages=MESSAGES):
    return store.save_conversation(conv, USER, "t", "m", messages, metadata=metadata)


def test_clear_bumps_the_epoch_and_a_stale_save_cannot_restore_the_pin(duck_store):
    conv = str(uuid.uuid4())
    _save(duck_store, conv, {PIN_METADATA_KEY: PIN, PIN_HISTORY_METADATA_KEY: HISTORY, PIN_EPOCH_METADATA_KEY: 0})
    assert duck_store.clear_prompt_pin(conv, USER) == {PIN_METADATA_KEY: PIN}
    meta = duck_store.get_conversation_metadata(conv, USER)
    assert PIN_METADATA_KEY not in meta and meta[PIN_EPOCH_METADATA_KEY] == 1
    assert meta[PIN_HISTORY_METADATA_KEY] == HISTORY

    # The open session saves what it still holds: pin and the old epoch.
    _save(duck_store, conv, {PIN_METADATA_KEY: PIN, PIN_EPOCH_METADATA_KEY: 0}, MESSAGES * 2)
    meta = duck_store.get_conversation_metadata(conv, USER)
    assert PIN_METADATA_KEY not in meta and meta[PIN_EPOCH_METADATA_KEY] == 1
    assert len(duck_store.get_conversation(conv, USER)["messages"]) == 4  # the turn itself is kept

    # A session that saw the clear may pin again.
    _save(duck_store, conv, {PIN_METADATA_KEY: PIN, PIN_EPOCH_METADATA_KEY: 1}, MESSAGES * 3)
    assert duck_store.get_conversation_metadata(conv, USER)[PIN_METADATA_KEY] == PIN


def test_clear_is_owner_scoped_and_bumps_even_without_a_pin(duck_store):
    conv = str(uuid.uuid4())
    _save(duck_store, conv, {"agent_mode": False})
    assert duck_store.clear_prompt_pin(conv, "someone@example.com") is None
    assert duck_store.get_conversation_metadata(conv, "someone@example.com") is None
    assert duck_store.clear_prompt_pin(str(uuid.uuid4()), USER) is None
    assert duck_store.clear_prompt_pin(conv, USER) == {}
    # A session may hold a pin it has not saved yet; its save is still refused.
    _save(duck_store, conv, {PIN_METADATA_KEY: PIN, PIN_EPOCH_METADATA_KEY: 0})
    assert PIN_METADATA_KEY not in duck_store.get_conversation_metadata(conv, USER)


def test_unknown_pin_state_saves_keep_the_stored_pin(duck_store):
    conv = str(uuid.uuid4())
    stored = {PIN_METADATA_KEY: PIN, PIN_HISTORY_METADATA_KEY: HISTORY, PIN_EPOCH_METADATA_KEY: 4}
    _save(duck_store, conv, stored)
    _save(duck_store, conv, {"agent_mode": True, KEEP_STORED_PIN: True})
    meta = duck_store.get_conversation_metadata(conv, USER)
    assert {k: meta[k] for k in stored} == stored
    assert KEEP_STORED_PIN not in meta and meta["agent_mode"] is True


# --- PostgreSQL: the clear and a stale save race -------------------------


@pytest.fixture
def pg_url(monkeypatch):
    if not PG_URL:
        pytest.skip("ATLAS_TEST_POSTGRES_URL not set")
    admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    name = f"atlas_pin_{uuid.uuid4().hex[:10]}"
    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {name}"))
    url = make_url(PG_URL).set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("CHAT_HISTORY_DB_URL", url)
    cfg = Config(str(default_alembic_ini()))
    engine = create_engine(url)
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn  # keeps env.py off logging.fileConfig
        command.upgrade(cfg, "head")
    engine.dispose()
    try:
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        admin.dispose()


def test_clear_and_stale_saves_race_across_replicas_postgresql(pg_url):
    from atlas.modules.chat_history import ConversationRepository

    engines = [create_engine(pg_url, pool_size=5) for _ in range(2)]
    try:
        clearer, saver = (ConversationRepository(sessionmaker(bind=e, expire_on_commit=False)) for e in engines)
        for attempt in range(15):
            conv = str(uuid.uuid4())
            _save(saver, conv, {PIN_METADATA_KEY: PIN, PIN_EPOCH_METADATA_KEY: 0})
            barrier = threading.Barrier(2)
            errors = []

            def clear(conv=conv, barrier=barrier):
                barrier.wait()
                clearer.clear_prompt_pin(conv, USER)

            def stale_save(n, conv=conv, barrier=barrier, errors=errors):
                barrier.wait()
                try:
                    # The session that has not seen the clear, saving a longer thread.
                    _save(
                        saver, conv,
                        {PIN_METADATA_KEY: PIN, PIN_HISTORY_METADATA_KEY: HISTORY, PIN_EPOCH_METADATA_KEY: 0},
                        MESSAGES * n,
                    )
                except Exception as e:  # collected for the assertion
                    errors.append(e)

            threads = [threading.Thread(target=clear), threading.Thread(target=stale_save, args=(2 + attempt,))]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
            assert errors == []
            meta = saver.get_conversation_metadata(conv, USER)
            # Whichever ran first, the clear wins: no pin, epoch 1.
            assert PIN_METADATA_KEY not in meta, f"pin restored on attempt {attempt}"
            assert meta[PIN_EPOCH_METADATA_KEY] == 1
    finally:
        for e in engines:
            e.dispose()


def test_a_save_waits_for_an_in_flight_clear_postgresql(pg_url):
    """Deterministic interleaving: the clear holds the row, a stale save arrives.

    Without the save's row lock it would read the pre-clear epoch, wait only
    at its UPDATE, and then write the old pin back over the clear.
    """
    import json
    import time

    from atlas.modules.chat_history import ConversationRepository

    engine = create_engine(pg_url)
    try:
        store = ConversationRepository(sessionmaker(bind=engine, expire_on_commit=False))
        conv = str(uuid.uuid4())
        _save(store, conv, {PIN_METADATA_KEY: PIN, PIN_EPOCH_METADATA_KEY: 0})
        done = {}
        with engine.connect() as clearing:
            tx = clearing.begin()
            raw = clearing.execute(
                text("SELECT metadata_json FROM conversations WHERE id = :id FOR UPDATE"), {"id": conv}
            ).scalar_one()

            def stale_save():
                # Its metadata differs from the stored copy (a history entry),
                # so the ORM really writes the column.
                done["result"] = _save(
                    store, conv,
                    {PIN_METADATA_KEY: PIN, PIN_HISTORY_METADATA_KEY: HISTORY, PIN_EPOCH_METADATA_KEY: 0},
                    MESSAGES * 2,
                )

            thread = threading.Thread(target=stale_save)
            thread.start()
            time.sleep(0.5)
            assert "result" not in done  # blocked on the clear's row lock
            meta = json.loads(raw)
            meta.pop(PIN_METADATA_KEY)
            meta[PIN_EPOCH_METADATA_KEY] = 1
            clearing.execute(
                text("UPDATE conversations SET metadata_json = :m WHERE id = :id"),
                {"m": json.dumps(meta), "id": conv},
            )
            tx.commit()
        thread.join(30)
        assert done["result"] is not None
        meta = store.get_conversation_metadata(conv, USER)
        assert PIN_METADATA_KEY not in meta and meta[PIN_EPOCH_METADATA_KEY] == 1
    finally:
        engine.dispose()
