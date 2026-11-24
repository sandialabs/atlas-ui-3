"""A chat for a new conversation never lands in a resumed session's old one (#1217).

New Chat clicked while the socket is down loses its ``reset_session`` frame.
The reconnect resumes the session, which still names the old conversation. The
guard decides once, at resume: a session whose carried conversation may be
stored starts its next chat in a fresh conversation. There is no per-frame
state, so every row of the lifecycle table in the PR is a plain end-to-end
sequence here, driven through the real endpoint, turn and save path (only the
model loop is replaced).

| Row | At resume                                      | Result                                  |
|-----|------------------------------------------------|-----------------------------------------|
| 1   | no conversation store                          | session unchanged                       |
| 2   | store answers "not stored"                     | session unchanged                       |
| 3   | stored, normal session                         | fresh id, empty history, floor reset    |
| 4   | stored, off-record session                     | same as row 3 (its history never resumes)|
| 5   | store cannot answer / foreign owner            | treated as stored (row 3)               |
| 6   | adopting the session fails                     | not resumed, a fresh session is used    |
| 7   | not a resume (fresh connection)                | store never consulted                   |
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from main import _conversation_may_be_stored, app

from atlas.application.chat.runs import reset_run_registry
from atlas.application.chat.runs.registry import ConcurrencyLimitError
from atlas.application.chat.service import ChatService
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import SAVE_FLOOR_CONTEXT_KEY, Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository

USER = "alice@example.com"
OLD = "conv-old"


class _Store:
    """Conversation store holding OLD; records lookups and saves."""

    def __init__(self, owner=USER, fail=False):
        self.owner = owner
        self.fail = fail
        self.lookups = []
        self.saved = []

    def get_conversation_owner(self, conversation_id):
        self.lookups.append(conversation_id)
        if self.fail:
            raise RuntimeError("store down")
        return self.owner if conversation_id == OLD else None

    def get_conversation(self, conversation_id, user_email):
        return None

    def save_conversation(self, **kwargs):
        self.saved.append(kwargs)
        return {"id": kwargs["conversation_id"]}


class _Wired:
    def __init__(self, repo, store, seen):
        self.repo, self.store, self.seen = repo, store, seen


@pytest.fixture
def wired(monkeypatch):
    """The /ws endpoint on the real service; ``make`` picks the conversation store."""
    reset_run_registry()
    repo = InMemorySessionRepository()
    seen = []

    async def execute(**kwargs):
        session = await repo.get(kwargs.get("session_id"))
        seen.append({
            "conversation": session.context.get("conversation_id"),
            "history": [m.content for m in session.history.messages],
        })
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content="answer"))
        return {"type": "done"}

    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(side_effect=execute)
    monkeypatch.setattr(ChatService, "_get_orchestrator", lambda self, *a, **k: orchestrator)

    state = {"store": _Store()}
    with patch("main.app_factory") as factory:
        config = MagicMock()
        config.app_settings.debug_mode = False
        config.app_settings.auth_user_header = "X-User-Email"
        config.app_settings.feature_proxy_secret_enabled = False
        config.app_settings.feature_websocket_origin_check_enabled = False
        config.app_settings.max_concurrent_runs_per_user = 3
        config.app_settings.feature_chat_history_enabled = True
        factory.get_config_manager.return_value = config

        def _service(_connection):
            return ChatService(
                llm=MagicMock(), tool_manager=MagicMock(), connection=MagicMock(),
                config_manager=MagicMock(), session_repository=repo,
                conversation_repository=state["store"],
            )

        factory.create_chat_service.side_effect = _service
        wired_ = _Wired(repo, state["store"], seen)

        def use(store):
            state["store"] = store
            wired_.store = store

        wired_.use = use
        yield wired_
    reset_run_registry()


def _previous(repo, *, incognito=False, history=()):
    session = Session(id=uuid4(), user_email=USER, active=False)
    session.context["conversation_id"] = OLD
    if incognito:
        session.context["incognito"] = True
    for text in history:
        session.history.add_message(Message(role=MessageRole.USER, content=text))
    asyncio.run(repo.create(session))
    return session


def _connect(session):
    return TestClient(app).websocket_connect(
        f"/ws?resume_session={session.id}", headers={"X-User-Email": USER}
    )


def _wait(predicate, seconds=10.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _send(ws, w, content, expected_turns, **frame):
    ws.send_json({"type": "chat", "content": content, "model": "m", "save_mode": "server", **frame})
    assert _wait(lambda: len(w.seen) >= expected_turns), "the turn never started"
    time.sleep(0.6)  # let the turn commit and its run end before the next frame


def _saved_ids(w):
    return [call["conversation_id"] for call in w.store.saved]


# -- rows 1-2: nothing to protect ----------------------------------------------


def test_unstored_new_chat_carries_on(wired):
    previous = _previous(wired.repo)
    wired.use(_Store(owner=None))
    with _connect(previous) as ws:
        assert ws.receive_json()["resumed"] is True
        _send(ws, wired, "hello", 1)
    assert wired.seen[0]["conversation"] == OLD


def test_without_a_store_the_session_carries_on(wired):
    previous = _previous(wired.repo, history=["earlier"])
    wired.use(None)
    with _connect(previous) as ws:
        assert ws.receive_json()["resumed"] is True
        _send(ws, wired, "hello", 1)
    assert wired.seen[0]["conversation"] == OLD
    assert wired.seen[0]["history"] == ["earlier"]


# -- row 3: the normal case -----------------------------------------------------


def test_stored_chat_gets_a_new_conversation(wired):
    previous = _previous(wired.repo, history=["old question"])
    with _connect(previous) as ws:
        assert ws.receive_json()["resumed"] is True
        _send(ws, wired, "hello", 1)
    turn = wired.seen[0]
    assert turn["conversation"] not in (None, OLD)
    assert turn["history"] == []
    assert _saved_ids(wired) == [turn["conversation"]]
    assert OLD not in _saved_ids(wired)


def test_naming_the_old_conversation_continues_it(wired):
    previous = _previous(wired.repo)
    with _connect(previous) as ws:
        assert ws.receive_json()["resumed"] is True
        _send(ws, wired, "hello", 1, conversation_id=OLD)
    assert wired.seen[0]["conversation"] == OLD
    assert _saved_ids(wired) == [OLD]


def test_save_floor_is_reset_for_the_new_chat(wired):
    previous = _previous(wired.repo)
    # A floor left over from this session's earlier life must not hide the
    # first messages of the new conversation from the save.
    previous.context[SAVE_FLOOR_CONTEXT_KEY] = {"floor": 4, "locked": False}
    with _connect(previous) as ws:
        assert ws.receive_json()["resumed"] is True
        _send(ws, wired, "hello", 1)
    assert wired.store.saved, "the new conversation was not saved"
    assert [m["content"] for m in wired.store.saved[-1]["messages"]] == ["answer"]


def test_refused_admission_keeps_the_guard(wired):
    previous = _previous(wired.repo)
    with _connect(previous) as ws:
        assert ws.receive_json()["resumed"] is True
        with patch("main.admit_run", AsyncMock(side_effect=ConcurrencyLimitError("busy"))):
            ws.send_json({"type": "chat", "content": "no", "model": "m", "save_mode": "server"})
            assert ws.receive_json()["error_type"] == "run_limit_exceeded"
        _send(ws, wired, "hello", 1)
    assert wired.seen[0]["conversation"] not in (None, OLD)
    assert OLD not in _saved_ids(wired)


def test_agent_frames_never_fall_back(wired):
    previous = _previous(wired.repo)
    agent = {"agent_mode": True, "selected_tools": []}
    with _connect(previous) as ws:
        assert ws.receive_json()["resumed"] is True
        _send(ws, wired, "first", 1, **agent)
        _send(ws, wired, "second", 2, **agent)
    first, second = wired.seen[0]["conversation"], wired.seen[1]["conversation"]
    # An id-less agent frame mints its own id (the client adopts it from
    # run_started); what matters is that none can fall back to the old one.
    assert first not in (None, OLD)
    assert second not in (None, OLD)
    assert OLD not in _saved_ids(wired)


def test_off_record_then_saved_starts_empty(wired):
    previous = _previous(wired.repo, history=["old question"])
    with _connect(previous) as ws:
        assert ws.receive_json()["resumed"] is True
        ws.send_json({"type": "chat", "content": "secret", "model": "m",
                      "save_mode": "none", "incognito": True})
        assert _wait(lambda: len(wired.seen) >= 1)
        time.sleep(0.5)
        _send(ws, wired, "kept", 2)
    assert wired.seen[1]["conversation"] not in (None, OLD)
    assert wired.store.saved, "the new conversation was not saved"
    assert [m["content"] for m in wired.store.saved[-1]["messages"]] == ["answer"]


# -- row 4: a session whose last turn was off-record ------------------------------


def test_off_record_session_is_guarded_too(wired):
    previous = _previous(wired.repo, incognito=True, history=["old question", "private note"])
    with _connect(previous) as ws:
        assert ws.receive_json()["resumed"] is True
        _send(ws, wired, "kept", 1)
    turn = wired.seen[0]
    assert turn["conversation"] not in (None, OLD)
    # The shared store never holds an off-record session's messages, so it
    # resumed empty; nothing of the old thread can reach the new conversation.
    assert turn["history"] == []
    assert wired.store.saved, "the new conversation was not saved"
    saved = wired.store.saved[-1]
    assert saved["conversation_id"] == turn["conversation"] != OLD
    assert [m["content"] for m in saved["messages"]] == ["answer"]


# -- row 5: fail closed -------------------------------------------------------------


class _NoOwnerLookup:
    pass


@pytest.mark.parametrize("store", [_Store(fail=True), _NoOwnerLookup(), _Store(owner="bob@example.com")])
def test_unverifiable_stores_count_as_stored(store):
    class _Service:
        conversation_repository = store

    assert asyncio.run(_conversation_may_be_stored(_Service(), OLD)) is True


def test_no_store_and_unknown_conversation_are_not_stored():
    class _None:
        conversation_repository = None

    class _Empty:
        conversation_repository = _Store(owner=None)

    assert asyncio.run(_conversation_may_be_stored(_None(), OLD)) is False
    assert asyncio.run(_conversation_may_be_stored(_Empty(), OLD)) is False
    assert asyncio.run(_conversation_may_be_stored(_Empty(), "")) is False


def test_failing_store_still_guards_the_session(wired):
    previous = _previous(wired.repo)
    wired.use(_Store(fail=True))
    with _connect(previous) as ws:
        assert ws.receive_json()["resumed"] is True
        _send(ws, wired, "hello", 1)
    assert wired.seen[0]["conversation"] not in (None, OLD)


# -- row 6: adopting fails ----------------------------------------------------------


def test_failed_adoption_is_not_a_resume(wired):
    previous = _previous(wired.repo)
    with patch.object(ChatService, "persist_session", AsyncMock(return_value=False)):
        with _connect(previous) as ws:
            frame = ws.receive_json()
    assert frame["resumed"] is False
    assert asyncio.run(wired.repo.get(previous.id)).context["conversation_id"] == OLD


# -- row 7: only a resume consults the store -----------------------------------------


def test_fresh_connection_never_consults_the_store(wired):
    with TestClient(app).websocket_connect("/ws", headers={"X-User-Email": USER}) as ws:
        _send(ws, wired, "hello", 1)
    assert wired.store.lookups == []


def test_resume_consults_the_store_once(wired):
    previous = _previous(wired.repo)
    with _connect(previous) as ws:
        assert ws.receive_json()["resumed"] is True
        _send(ws, wired, "one", 1)
        _send(ws, wired, "two", 2)
    assert wired.store.lookups.count(OLD) == 1
