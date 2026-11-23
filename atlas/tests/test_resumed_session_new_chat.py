"""A chat frame for a new conversation never lands in a resumed session's old one (#1217).

New Chat clicked while the socket is down loses its ``reset_session`` frame.
The reconnect resumes the session, which still names the old conversation. The
first server-mode chat frame that names no conversation is a new chat and gets
a fresh conversation id; one that names the conversation continues it.
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
from atlas.domain.errors import SessionStoreUnavailableError
from atlas.domain.sessions.models import Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository

USER = "alice@example.com"
OLD = "conv-old"
MARKER = "resumed_conversation_unconfirmed"


class _StoredConversations:
    """Just enough of the conversation store: ``OLD`` is saved, nothing else is."""

    def get_conversation_owner(self, conversation_id):
        return USER if conversation_id == OLD else None


@pytest.fixture
def repo():
    return InMemorySessionRepository()


@pytest.fixture
def seen():
    """What each turn that reached the service looked like."""
    return []


@pytest.fixture
def mock_app_factory(repo, seen):
    reset_run_registry()

    async def fake_turn(self, session_id, **kwargs):
        session = await repo.get(session_id)
        context = session.context if session is not None else {}
        seen.append({
            "session_conversation": context.get("conversation_id"),
            "frame_conversation": kwargs.get("conversation_id"),
        })
        return {}

    with patch("main.app_factory") as factory, patch.object(
        ChatService, "handle_chat_message", fake_turn
    ):
        config = MagicMock()
        config.app_settings.debug_mode = False
        config.app_settings.auth_user_header = "X-User-Email"
        config.app_settings.feature_proxy_secret_enabled = False
        config.app_settings.feature_websocket_origin_check_enabled = False
        config.app_settings.max_concurrent_runs_per_user = 3
        config.app_settings.feature_chat_history_enabled = True
        factory.get_config_manager.return_value = config

        def _service(_connection):
            tool_manager = MagicMock()
            tool_manager.release_sessions = AsyncMock()
            return ChatService(
                llm=MagicMock(),
                tool_manager=tool_manager,
                connection=MagicMock(),
                config_manager=MagicMock(),
                session_repository=repo,
                conversation_repository=_StoredConversations(),
            )

        factory.create_chat_service.side_effect = _service
        yield factory
    reset_run_registry()


def _previous_session(repo, conversation_id=OLD):
    session = Session(id=uuid4(), user_email=USER, active=False)
    session.context["conversation_id"] = conversation_id
    asyncio.run(repo.create(session))
    return session


def _connect(session):
    return TestClient(app).websocket_connect(
        f"/ws?resume_session={session.id}", headers={"X-User-Email": USER}
    )


def _chat(ws, seen, expected, **frame):
    ws.send_json({"type": "chat", "content": "hello", "model": "m", **frame})
    # Bounded wait for the turn task to have started.
    for _ in range(250):
        if len(seen) >= expected:
            return
        time.sleep(0.02)


def _marker(repo, session):
    live = asyncio.run(repo.get(session.id))
    return bool(live.context.get(MARKER))


def _new_conversation(turn):
    return turn["frame_conversation"] not in (None, OLD)


def test_new_chat_frame_after_resume_leaves_old_conversation(mock_app_factory, repo, seen):
    session = _previous_session(repo)
    with _connect(session) as ws:
        assert ws.receive_json()["resumed"] is True
        _chat(ws, seen, 1)
        assert not _marker(repo, session)
    assert len(seen) == 1
    assert _new_conversation(seen[0])


def test_followup_naming_the_conversation_still_lands_in_it(mock_app_factory, repo, seen):
    session = _previous_session(repo)
    with _connect(session) as ws:
        assert ws.receive_json()["resumed"] is True
        _chat(ws, seen, 1, conversation_id=OLD)
        assert not _marker(repo, session)
    assert seen[0]["frame_conversation"] == OLD


def test_rule_applies_to_the_first_frame_only(mock_app_factory, repo, seen):
    session = _previous_session(repo)
    with _connect(session) as ws:
        assert ws.receive_json()["resumed"] is True
        _chat(ws, seen, 1, conversation_id=OLD)
        # An id-less frame later is an ordinary turn on the session's own
        # conversation: no fresh id is minted for it.
        _chat(ws, seen, 2)
    assert len(seen) == 2
    assert seen[1]["frame_conversation"] in (None, OLD)


def test_off_record_first_frame_keeps_the_rule_armed(mock_app_factory, repo, seen):
    session = _previous_session(repo)
    with _connect(session) as ws:
        assert ws.receive_json()["resumed"] is True
        _chat(ws, seen, 1, save_mode="none", incognito=True)
        assert _marker(repo, session)
        _chat(ws, seen, 2)
    assert seen[0]["frame_conversation"] in (None, OLD)
    assert _new_conversation(seen[1])


def test_steer_frame_without_a_run_leaves_the_marker(mock_app_factory, repo, seen):
    session = _previous_session(repo)
    with _connect(session) as ws:
        assert ws.receive_json()["resumed"] is True
        ws.send_json({
            "type": "chat", "content": "more", "model": "m",
            "steer_only": True, "run_id": str(uuid4()), "request_id": "r1",
        })
        assert ws.receive_json()["error_type"] == "steer_run_finished"
        assert _marker(repo, session)
    assert seen == []


def test_refused_admission_does_not_touch_the_session(mock_app_factory, repo, seen):
    session = _previous_session(repo)
    with patch("main.admit_run", AsyncMock(side_effect=ConcurrencyLimitError("too many runs"))):
        with _connect(session) as ws:
            assert ws.receive_json()["resumed"] is True
            ws.send_json({"type": "chat", "content": "hello", "model": "m"})
            frame = ws.receive_json()
            assert frame["error_type"] == "run_limit_exceeded"
            assert _marker(repo, session)
            live = asyncio.run(repo.get(session.id))
            assert live.context["conversation_id"] == OLD
    assert seen == []


def test_session_store_outage_answers_the_frame(mock_app_factory, repo, seen):
    session = _previous_session(repo)
    with _connect(session) as ws:
        assert ws.receive_json()["resumed"] is True
        with patch.object(
            repo, "get", AsyncMock(side_effect=SessionStoreUnavailableError("store down"))
        ):
            ws.send_json({"type": "chat", "content": "hello", "model": "m"})
            frame = ws.receive_json()
        assert frame["type"] == "error"
        assert frame["error_type"] == "domain"
        # The socket survived: the next frame is served once the store is back.
        _chat(ws, seen, 1)
    assert _new_conversation(seen[0])


def test_unsaved_new_chat_is_not_marked_on_resume(mock_app_factory, repo, seen):
    """A New Chat awaiting its first message (files may be attached) carries on."""
    session = _previous_session(repo, conversation_id="conv-not-saved-yet")
    with _connect(session) as ws:
        assert ws.receive_json()["resumed"] is True
        assert not _marker(repo, session)
        _chat(ws, seen, 1)
    assert seen[0]["frame_conversation"] in (None, "conv-not-saved-yet")


def test_fresh_connection_chat_is_untouched(mock_app_factory, repo, seen):
    with TestClient(app).websocket_connect("/ws", headers={"X-User-Email": USER}) as ws:
        _chat(ws, seen, 1)
    assert len(seen) == 1
    assert seen[0]["frame_conversation"] is None


class _Service:
    def __init__(self, repository):
        self.conversation_repository = repository


class _Failing:
    def get_conversation_owner(self, _conversation_id):
        raise RuntimeError("store down")


class _NoOwnerLookup:
    pass


class _OtherOwner:
    def get_conversation_owner(self, _conversation_id):
        return "bob@example.com"


@pytest.mark.parametrize("repository,expected", [
    (None, False),
    (_StoredConversations(), True),
    (_Failing(), True),
    (_NoOwnerLookup(), True),
    (_OtherOwner(), True),
])
def test_stored_check_fails_closed(repository, expected):
    stored = asyncio.run(_conversation_may_be_stored(_Service(repository), OLD))
    assert stored is expected


def test_stored_check_false_for_an_unknown_conversation():
    assert asyncio.run(_conversation_may_be_stored(_Service(_StoredConversations()), "other")) is False
