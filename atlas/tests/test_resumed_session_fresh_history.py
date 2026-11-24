"""A fresh conversation id minted after a resume starts with empty history (#1217).

The backstop no longer resets the session, so what the resumed session still
carries must not reach the model or be saved under the new conversation.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from main import _RESUMED_CONVERSATION_KEY, _resume_connection_session, app

from atlas.application.chat.runs import reset_run_registry
from atlas.application.chat.service import ChatService
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository

USER = "alice@example.com"
OLD = "conv-old"


class _Store:
    """Conversation store holding OLD; records every save."""

    def __init__(self):
        self.saved = []

    def get_conversation_owner(self, conversation_id):
        return USER if conversation_id == OLD else None

    def get_conversation(self, conversation_id, user_email):
        return None

    def save_conversation(self, **kwargs):
        self.saved.append(kwargs)
        return {"id": kwargs["conversation_id"]}


async def _setup():
    repo = InMemorySessionRepository()
    store = _Store()
    service = ChatService(
        llm=MagicMock(),
        tool_manager=MagicMock(),
        connection=MagicMock(),
        config_manager=MagicMock(),
        session_repository=repo,
        conversation_repository=store,
    )
    service.connection_id = str(uuid4())
    previous = Session(id=uuid4(), user_email=USER, active=False)
    previous.context["conversation_id"] = OLD
    for role, text in ((MessageRole.USER, "old question"), (MessageRole.ASSISTANT, "old answer")):
        previous.history.add_message(Message(role=role, content=text))
    await repo.create(previous)
    return repo, store, service, previous


async def _run_turn(service, session_id, conversation_id):
    seen = {}

    async def fake_execute(**kwargs):
        session = await service.session_repository.get(session_id)
        seen["history"] = [m.content for m in session.history.messages]
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content="new answer"))
        return {"type": "done"}

    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(side_effect=fake_execute)
    with patch.object(service, "_get_orchestrator", return_value=orchestrator):
        await service.handle_chat_message(
            session_id=session_id,
            content="new question",
            model="m",
            user_email=USER,
            conversation_id=conversation_id,
        )
    return seen


@pytest.mark.asyncio
async def test_fresh_id_turn_sends_no_old_messages_to_the_model():
    repo, store, service, previous = await _setup()
    resumed = await _resume_connection_session(service, str(previous.id), USER)
    assert resumed is not None and resumed.context.get(_RESUMED_CONVERSATION_KEY)

    seen = await _run_turn(service, previous.id, str(uuid4()))

    assert "old question" not in seen["history"]
    assert "old answer" not in seen["history"]


@pytest.mark.asyncio
async def test_fresh_id_turn_saves_only_its_own_messages():
    repo, store, service, previous = await _setup()
    await _resume_connection_session(service, str(previous.id), USER)
    fresh = str(uuid4())

    await _run_turn(service, previous.id, fresh)

    assert store.saved, "the new conversation should have been saved"
    saved = store.saved[-1]
    assert saved["conversation_id"] == fresh
    texts = [m["content"] if isinstance(m, dict) else m.content for m in saved["messages"]]
    assert "old question" not in texts and "old answer" not in texts
    assert all(call["conversation_id"] != OLD for call in store.saved)


# -- the same sequence through the WebSocket endpoint and the real service ----
#
# handle_chat_message and the save path are not replaced here; only the model
# loop is (an orchestrator that answers, so a turn leaves its exchange in the
# history as a real one does).


@pytest.fixture
def wired(monkeypatch):
    """A /ws endpoint backed by the real ChatService, an in-memory session
    store and a recording conversation store."""
    reset_run_registry()
    repo = InMemorySessionRepository()
    store = _Store()
    seen = []

    async def execute(**kwargs):
        session = await repo.get(kwargs.get("session_id"))
        seen.append([m.content for m in session.history.messages])
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content="answer"))
        return {"type": "done"}

    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(side_effect=execute)
    monkeypatch.setattr(ChatService, "_get_orchestrator", lambda self, *a, **k: orchestrator)

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
                config_manager=MagicMock(), session_repository=repo, conversation_repository=store,
            )

        factory.create_chat_service.side_effect = _service
        yield repo, store, seen
    reset_run_registry()


def _wait(predicate, seconds=10.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _off_record_then_saved(repo, store, seen, saved_extra):
    previous = Session(id=uuid4(), user_email=USER, active=False)
    # A conversation reopened from history and then left idle: stored, but this
    # session has not committed a turn of its own, so it carries no save floor.
    previous.context["conversation_id"] = OLD
    asyncio.run(repo.create(previous))
    with TestClient(app).websocket_connect(
        f"/ws?resume_session={previous.id}", headers={"X-User-Email": USER}
    ) as ws:
        assert ws.receive_json()["resumed"] is True
        ws.send_json({"type": "chat", "content": "secret", "model": "m",
                      "save_mode": "none", "incognito": True})
        assert _wait(lambda: len(seen) >= 1)
        time.sleep(0.5)  # let the off-record turn commit and its run end
        ws.send_json({"type": "chat", "content": "kept", "model": "m",
                      "save_mode": "server", **saved_extra})
        assert _wait(lambda: len(seen) >= 2)
        _wait(lambda: bool(store.saved), 5.0)


def _assert_saved_empty_start(store, seen):
    # The mock model loop adds only its answer; whatever is in the history
    # when the saved turn starts is what an earlier exchange left behind.
    assert seen[1] == [], f"the model saw earlier exchanges: {seen[1]}"
    assert store.saved, "the new conversation was not saved"
    saved = store.saved[-1]
    assert saved["conversation_id"] != OLD
    assert [m["content"] for m in saved["messages"]] == ["answer"]


def test_off_record_then_saved_plain_chat_saves_an_empty_start(wired):
    repo, store, seen = wired
    _off_record_then_saved(repo, store, seen, {})
    _assert_saved_empty_start(store, seen)


def test_off_record_then_saved_agent_mode_saves_an_empty_start(wired):
    repo, store, seen = wired
    _off_record_then_saved(repo, store, seen, {"agent_mode": True, "selected_tools": []})
    _assert_saved_empty_start(store, seen)
