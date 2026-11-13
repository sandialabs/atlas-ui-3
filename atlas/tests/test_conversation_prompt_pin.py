"""Stored team-prompt pin state (issue #1169).

Every pin change -- set, re-pin, explicit clear, owner DELETE -- is a
compare-and-set on ``prompt_library_pin_revision`` under the conversation's
row lock, and a conversation save never writes the pin of an existing record.
Covers the pure rules, the repository on DuckDB, and races on PostgreSQL with
independent engines standing in for replicas.
"""

import json
import os
import threading
import time
import uuid

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import sessionmaker

from alembic import command
from atlas.domain.conversation_prompt_pin import (
    PIN_HISTORY_METADATA_KEY,
    PIN_METADATA_KEY,
    PIN_REVISION_METADATA_KEY,
    apply_pin_change,
    carry_stored_pin,
    new_record_pin,
    pin_revision,
)
from atlas.domain.errors import ConversationMetadataUnreadableError
from atlas.modules.chat_history.database import reset_engine
from atlas.modules.chat_history.schema_migration import default_alembic_ini

PG_URL = os.environ.get("ATLAS_TEST_POSTGRES_URL")
USER = "member@example.com"
V1 = {"prompt_id": "p1", "version_id": "v1", "version_number": 1, "title": "T"}
V2 = {"prompt_id": "p1", "version_id": "v2", "version_number": 2, "title": "T"}
OTHER = {"prompt_id": "p2", "version_id": "w1", "version_number": 1, "title": "U"}
HISTORY = [{**V1, "pinned_at": "2026-01-01T00:00:00+00:00", "from_message_index": 0}]
MESSAGES = [
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "reply"},
]


def entry(pin):
    return {**(pin or {"prompt_id": None, "version_id": None}), "pinned_at": "t", "from_message_index": 0}


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


# --- Pure rules -------------------------------------------------------------


def test_a_save_of_an_existing_record_never_changes_the_pin():
    stored = {PIN_METADATA_KEY: V2, PIN_HISTORY_METADATA_KEY: HISTORY, PIN_REVISION_METADATA_KEY: 4}
    for incoming in (
        {"agent_mode": True, PIN_METADATA_KEY: V1, PIN_REVISION_METADATA_KEY: 9},  # stale pin, bigger number
        {"agent_mode": True},  # a session that never saw the pin
        {"agent_mode": True, PIN_HISTORY_METADATA_KEY: []},
    ):
        assert carry_stored_pin(stored, incoming) == {"agent_mode": True, **stored}


def test_a_new_record_takes_the_creating_sessions_pin():
    incoming = {"agent_mode": False, PIN_METADATA_KEY: V1, PIN_HISTORY_METADATA_KEY: HISTORY, PIN_REVISION_METADATA_KEY: 1}
    assert new_record_pin(incoming) == incoming
    assert new_record_pin({"agent_mode": False}) == {"agent_mode": False}  # no pin keys invented


def test_compare_and_set_applies_only_on_the_expected_revision():
    stored = {PIN_METADATA_KEY: V1, PIN_HISTORY_METADATA_KEY: HISTORY, PIN_REVISION_METADATA_KEY: 1}
    status, after = apply_pin_change(stored, 1, V2, entry(V2))
    assert status == "ok" and after[PIN_METADATA_KEY] == V2 and after[PIN_REVISION_METADATA_KEY] == 2
    assert [h["version_id"] for h in after[PIN_HISTORY_METADATA_KEY]] == ["v1", "v2"]
    status, same = apply_pin_change(stored, 0, V2, entry(V2))
    assert status == "conflict" and same is stored
    status, same = apply_pin_change(stored, 1, V1, entry(V1))
    assert status == "unchanged" and same is stored  # no revision churn
    status, cleared = apply_pin_change(stored, 1, None, entry(None))
    assert status == "ok" and PIN_METADATA_KEY not in cleared and cleared[PIN_REVISION_METADATA_KEY] == 2
    status, forced = apply_pin_change(stored, None, None, None)  # owner DELETE
    assert status == "ok" and forced[PIN_HISTORY_METADATA_KEY] == HISTORY


@pytest.mark.parametrize("value, expected", [(None, 0), (-1, 0), ("3", 0), (True, 0), (1.5, 0), (7, 7)])
def test_revisions_read_defensively(value, expected):
    assert pin_revision({PIN_REVISION_METADATA_KEY: value}) == expected


def test_the_earlier_epoch_key_reads_as_the_revision():
    assert pin_revision({"prompt_library_pin_epoch": 3}) == 3
    status, after = apply_pin_change({"prompt_library_pin_epoch": 3}, 3, V1, entry(V1))
    assert status == "ok" and after == {PIN_METADATA_KEY: V1, PIN_HISTORY_METADATA_KEY: [entry(V1)], PIN_REVISION_METADATA_KEY: 4}


# --- Repository on DuckDB ----------------------------------------------------


@pytest.fixture
def duck_store(tmp_path):
    from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database

    init_database(f"duckdb:///{tmp_path / 'pins.db'}")
    return ConversationRepository(get_session_factory())


def _save(store, conv, metadata, messages=MESSAGES):
    return store.save_conversation(conv, USER, "t", "m", messages, metadata=metadata)


def _meta(store, conv):
    return store.get_conversation_metadata(conv, USER)


def test_pin_changes_are_compare_and_set_and_saves_cannot_undo_them(duck_store):
    conv = str(uuid.uuid4())
    _save(duck_store, conv, {PIN_METADATA_KEY: V1, PIN_HISTORY_METADATA_KEY: HISTORY, PIN_REVISION_METADATA_KEY: 1})
    ok = duck_store.change_prompt_pin(conv, USER, 1, V2, entry(V2))
    assert ok["status"] == "ok" and ok["state"]["revision"] == 2 and ok["state"]["pin"] == V2
    stale = duck_store.change_prompt_pin(conv, USER, 1, OTHER, entry(OTHER))
    assert stale["status"] == "conflict" and stale["state"]["pin"] == V2
    # A session still holding v1 saves its turn: the stored pin is untouched.
    _save(duck_store, conv, {PIN_METADATA_KEY: V1, PIN_REVISION_METADATA_KEY: 1}, MESSAGES * 2)
    assert _meta(duck_store, conv)[PIN_METADATA_KEY] == V2
    # A session that never saw any pin saves: still untouched.
    _save(duck_store, conv, {"agent_mode": True}, MESSAGES * 3)
    meta = _meta(duck_store, conv)
    assert meta[PIN_METADATA_KEY] == V2 and meta[PIN_REVISION_METADATA_KEY] == 2 and meta["agent_mode"] is True
    assert len(duck_store.get_conversation(conv, USER)["messages"]) == 6  # the turns themselves are kept


def test_owner_clear_is_unconditional_and_owner_scoped(duck_store):
    conv = str(uuid.uuid4())
    _save(duck_store, conv, {PIN_METADATA_KEY: V1, PIN_HISTORY_METADATA_KEY: HISTORY, PIN_REVISION_METADATA_KEY: 5})
    assert duck_store.clear_prompt_pin(conv, "someone@example.com") is None
    assert duck_store.change_prompt_pin(conv, "someone@example.com", 5, None, entry(None)) is None
    assert duck_store.clear_prompt_pin(str(uuid.uuid4()), USER) is None
    assert duck_store.clear_prompt_pin(conv, USER) == {PIN_METADATA_KEY: V1}
    meta = _meta(duck_store, conv)
    assert PIN_METADATA_KEY not in meta and meta[PIN_REVISION_METADATA_KEY] == 6
    assert meta[PIN_HISTORY_METADATA_KEY] == HISTORY
    assert duck_store.clear_prompt_pin(conv, USER) == {}  # nothing to clear: no churn
    assert _meta(duck_store, conv)[PIN_REVISION_METADATA_KEY] == 6


def test_concurrent_changes_on_duckdb_never_both_apply(duck_store):
    """DuckDB has no row locks: a racing writer fails its commit or loses the
    compare-and-set; it never applies on top of the other's change."""
    conv = str(uuid.uuid4())
    _save(duck_store, conv, {PIN_METADATA_KEY: V1, PIN_REVISION_METADATA_KEY: 1})
    results, errors = [], []
    barrier = threading.Barrier(2)

    def change(pin):
        barrier.wait()
        try:
            results.append(duck_store.change_prompt_pin(conv, USER, 1, pin, entry(pin)))
        except Exception as e:  # DuckDB write-write conflict
            errors.append(e)

    threads = [threading.Thread(target=change, args=(p,)) for p in (V2, OTHER)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    applied = [r for r in results if r["status"] == "ok"]
    assert len(applied) == 1 and len(results) + len(errors) == 2
    meta = _meta(duck_store, conv)
    assert meta[PIN_METADATA_KEY] == applied[0]["state"]["pin"] and meta[PIN_REVISION_METADATA_KEY] == 2


@pytest.mark.parametrize("blob", ["{not json", "[1, 2]", '"text"', "42"])
def test_unreadable_metadata_raises_and_is_never_rewritten(duck_store, blob):
    from atlas.modules.chat_history.models import ConversationRecord

    conv = str(uuid.uuid4())
    _save(duck_store, conv, {PIN_METADATA_KEY: V1, PIN_REVISION_METADATA_KEY: 1})
    with duck_store._get_session() as session:
        session.get(ConversationRecord, conv).metadata_json = blob
        session.commit()
    with pytest.raises(ConversationMetadataUnreadableError):
        _meta(duck_store, conv)
    with pytest.raises(ConversationMetadataUnreadableError):
        duck_store.change_prompt_pin(conv, USER, 1, None, entry(None))
    with pytest.raises(ConversationMetadataUnreadableError):
        duck_store.clear_prompt_pin(conv, USER)
    record = duck_store.get_conversation(conv, USER)
    assert record["metadata_unreadable"] is True and record["metadata"] == {}
    with duck_store._get_session() as session:
        assert session.get(ConversationRecord, conv).metadata_json == blob


@pytest.mark.parametrize("blob", [None, "", "null", "{}"])
def test_genuinely_empty_metadata_reads_as_empty(duck_store, blob):
    from atlas.modules.chat_history.models import ConversationRecord

    conv = str(uuid.uuid4())
    _save(duck_store, conv, {"agent_mode": False})
    with duck_store._get_session() as session:
        session.get(ConversationRecord, conv).metadata_json = blob
        session.commit()
    assert _meta(duck_store, conv) == {}
    assert "metadata_unreadable" not in duck_store.get_conversation(conv, USER)
    assert duck_store.change_prompt_pin(conv, USER, 0, V1, entry(V1))["status"] == "ok"


# --- PostgreSQL: pin changes and saves race across replicas ------------------


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


@pytest.fixture
def replicas(pg_url):
    from atlas.modules.chat_history import ConversationRepository

    engines = [create_engine(pg_url, pool_size=5) for _ in range(2)]
    yield [ConversationRepository(sessionmaker(bind=e, expire_on_commit=False)) for e in engines]
    for e in engines:
        e.dispose()


def _race(calls):
    results, errors = [], []
    barrier = threading.Barrier(len(calls))

    def run(fn):
        barrier.wait()
        try:
            results.append(fn())
        except Exception as e:  # collected for the assertions
            errors.append(e)

    threads = [threading.Thread(target=run, args=(fn,)) for fn in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return results, errors


def test_two_replicas_selecting_different_versions_have_one_winner_postgresql(replicas):
    a, b = replicas
    for attempt in range(10):
        conv = str(uuid.uuid4())
        _save(a, conv, {PIN_METADATA_KEY: V1, PIN_REVISION_METADATA_KEY: 1})
        results, errors = _race([
            lambda conv=conv: a.change_prompt_pin(conv, USER, 1, V2, entry(V2)),
            lambda conv=conv: b.change_prompt_pin(conv, USER, 1, OTHER, entry(OTHER)),
        ])
        assert errors == []
        statuses = sorted(r["status"] for r in results)
        assert statuses == ["conflict", "ok"], f"attempt {attempt}: {statuses}"
        winner = next(r for r in results if r["status"] == "ok")["state"]["pin"]
        meta = _meta(a, conv)
        assert meta[PIN_METADATA_KEY] == winner and meta[PIN_REVISION_METADATA_KEY] == 2
        # The loser was told the winner's state, so it can adopt it.
        assert next(r for r in results if r["status"] == "conflict")["state"]["pin"] == winner


def test_a_pin_set_survives_racing_stale_saves_postgresql(replicas):
    a, b = replicas
    for attempt in range(10):
        conv = str(uuid.uuid4())
        _save(a, conv, {PIN_METADATA_KEY: V1, PIN_HISTORY_METADATA_KEY: HISTORY, PIN_REVISION_METADATA_KEY: 1})
        results, errors = _race([
            lambda conv=conv: a.change_prompt_pin(conv, USER, 1, V2, entry(V2)),
            # A session that only ever saw v1, saving a longer thread.
            lambda n=attempt, conv=conv: _save(b, conv, {PIN_METADATA_KEY: V1, PIN_REVISION_METADATA_KEY: 1}, MESSAGES * (2 + n)),
        ])
        assert errors == []
        meta = _meta(a, conv)
        assert meta[PIN_METADATA_KEY] == V2 and meta[PIN_REVISION_METADATA_KEY] == 2, f"attempt {attempt}"


def test_a_save_waits_for_an_in_flight_pin_change_postgresql(pg_url):
    """Deterministic interleaving: a pin change holds the row, a stale save
    arrives and must wait, then carry the *new* stored pin through."""
    from atlas.modules.chat_history import ConversationRepository

    engine = create_engine(pg_url)
    try:
        store = ConversationRepository(sessionmaker(bind=engine, expire_on_commit=False))
        conv = str(uuid.uuid4())
        _save(store, conv, {PIN_METADATA_KEY: V1, PIN_REVISION_METADATA_KEY: 1})
        done = {}
        with engine.connect() as changing:
            tx = changing.begin()
            raw = changing.execute(
                text("SELECT metadata_json FROM conversations WHERE id = :id FOR UPDATE"), {"id": conv}
            ).scalar_one()

            def stale_save():
                # A real metadata change (agent_mode), so the save writes the
                # column: identical JSON would be skipped by the ORM and could
                # not show a lost update.
                done["result"] = _save(
                    store, conv,
                    {"agent_mode": True, PIN_METADATA_KEY: V1, PIN_REVISION_METADATA_KEY: 1},
                    MESSAGES * 2,
                )

            thread = threading.Thread(target=stale_save)
            thread.start()
            time.sleep(0.5)
            assert "result" not in done  # blocked on the change's row lock
            meta = json.loads(raw)
            meta[PIN_METADATA_KEY] = V2
            meta[PIN_REVISION_METADATA_KEY] = 2
            changing.execute(
                text("UPDATE conversations SET metadata_json = :m WHERE id = :id"),
                {"m": json.dumps(meta), "id": conv},
            )
            tx.commit()
        thread.join(30)
        assert done["result"] is not None
        meta = _meta(store, conv)
        assert meta[PIN_METADATA_KEY] == V2 and meta[PIN_REVISION_METADATA_KEY] == 2
        assert meta["agent_mode"] is True  # the save itself still landed
    finally:
        engine.dispose()


def test_an_explicit_clear_and_a_stale_set_race_postgresql(replicas):
    a, b = replicas
    for attempt in range(10):
        conv = str(uuid.uuid4())
        _save(a, conv, {PIN_METADATA_KEY: V1, PIN_REVISION_METADATA_KEY: 1})
        results, errors = _race([
            lambda conv=conv: a.change_prompt_pin(conv, USER, 1, None, entry(None)),
            lambda conv=conv: b.change_prompt_pin(conv, USER, 1, V2, entry(V2)),
        ])
        assert errors == []
        assert sorted(r["status"] for r in results) == ["conflict", "ok"], f"attempt {attempt}"
        assert _meta(a, conv)[PIN_REVISION_METADATA_KEY] == 2
