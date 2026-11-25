"""A navigation frame replayed after a reconnect runs once (#1223).

The client re-sends an unacknowledged ``reset_session`` / ``restore_conversation``
with the same ``request_id``. If the first copy had already completed, the
server answers again without repeating the work, so a second reset does not
start yet another conversation.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from main import app

from atlas.application.chat.runs import reset_run_registry
from atlas.application.chat.service import note_navigation_request

USER = "owner@example.com"


@pytest.fixture
def service():
    reset_run_registry()
    with patch("main.app_factory") as factory:
        config = MagicMock()
        settings = config.app_settings
        settings.test_user = None
        settings.debug_mode = False
        settings.auth_user_header = "X-User-Email"
        settings.auth_user_header_type = "header"
        settings.auth_aws_expected_alb_arn = ""
        settings.auth_aws_region = "us-east-1"
        settings.feature_proxy_secret_enabled = False
        settings.feature_websocket_origin_check_enabled = False
        settings.max_concurrent_runs_per_user = 3
        factory.get_config_manager.return_value = config
        chat = MagicMock()
        session = SimpleNamespace(context={})

        # The real handlers record the id on the session they persist.
        async def reset(**kwargs):
            note_navigation_request(session, kwargs.get("navigation_request_id"))
            return {"type": "session_reset"}

        async def restore(**kwargs):
            if chat.refuse_restore:
                return {"type": "error", "message": "nope"}
            note_navigation_request(session, kwargs.get("navigation_request_id"))
            return {"type": "conversation_restored", "conversation_id": "c1"}

        chat.refuse_restore = False
        chat.handle_reset_session = AsyncMock(side_effect=reset)
        chat.handle_restore_conversation = AsyncMock(side_effect=restore)
        chat.end_session = AsyncMock()
        chat.persist_session = AsyncMock()
        chat.session_repository.get = AsyncMock(return_value=session)
        factory.create_chat_service.return_value = chat
        yield chat
    reset_run_registry()


def _twice(frame):
    with TestClient(app).websocket_connect("/ws", headers={"X-User-Email": USER}) as ws:
        ws.send_json(frame)
        first = ws.receive_json()
        ws.send_json(frame)
        return first, ws.receive_json()


def test_replayed_reset_runs_once(service):
    first, second = _twice({"type": "reset_session", "request_id": "nav-9"})
    assert first["type"] == second["type"] == "session_reset"
    assert second["request_id"] == "nav-9"
    assert second["deduplicated"] is True
    assert service.handle_reset_session.await_count == 1


def test_replayed_restore_runs_once(service):
    frame = {"type": "restore_conversation", "conversation_id": "c1", "messages": [], "request_id": "nav-8"}
    _, second = _twice(frame)
    assert second["deduplicated"] is True
    assert second["conversation_id"] == "c1"
    assert service.handle_restore_conversation.await_count == 1


def test_refused_restore_is_not_remembered(service):
    service.refuse_restore = True
    frame = {"type": "restore_conversation", "conversation_id": "c1", "messages": [], "request_id": "nav-7"}
    first, second = _twice(frame)
    assert first["type"] == second["type"] == "error"
    assert service.handle_restore_conversation.await_count == 2


def test_frames_without_an_id_are_not_deduplicated(service):
    _, second = _twice({"type": "reset_session"})
    assert "deduplicated" not in second
    assert service.handle_reset_session.await_count == 2


@pytest.mark.asyncio
async def test_reset_records_the_id_in_the_session_it_persists():
    from uuid import uuid4

    from atlas.application.chat.service import NAVIGATION_REQUEST_IDS_KEY, ChatService
    from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository

    repo = InMemorySessionRepository()
    service = ChatService(
        llm=MagicMock(),
        tool_manager=MagicMock(),
        connection=MagicMock(),
        config_manager=MagicMock(),
        session_repository=repo,
    )
    session_id = uuid4()
    await service.create_session(session_id, USER)
    reply = await service.handle_reset_session(
        session_id=session_id, user_email=USER, navigation_request_id="nav-1"
    )
    assert reply["type"] == "session_reset"
    stored = await repo.get(session_id)
    assert stored.context[NAVIGATION_REQUEST_IDS_KEY] == ["nav-1"]
