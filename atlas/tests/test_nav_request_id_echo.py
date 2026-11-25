"""Replies to reset_session / restore_conversation echo the client's request_id (#1223).

A client that holds a navigation frame until the server answers it matches the
reply by ``request_id``. The frames themselves are unchanged otherwise.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from main import _echo_request_id, app

from atlas.application.chat.runs import reset_run_registry

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
        chat.handle_reset_session = AsyncMock(return_value={"type": "session_reset"})
        chat.handle_restore_conversation = AsyncMock(
            return_value={"type": "conversation_restored", "conversation_id": "c1"}
        )
        chat.end_session = AsyncMock()
        chat.session_repository.get = AsyncMock(return_value=None)
        factory.create_chat_service.return_value = chat
        yield chat
    reset_run_registry()


def _exchange(frame):
    with TestClient(app).websocket_connect("/ws", headers={"X-User-Email": USER}) as ws:
        ws.send_json(frame)
        return ws.receive_json()


def test_reset_reply_echoes_id(service):
    reply = _exchange({"type": "reset_session", "request_id": "nav-1"})
    assert reply == {"type": "session_reset", "request_id": "nav-1"}


def test_restore_reply_echoes_id(service):
    reply = _exchange(
        {"type": "restore_conversation", "conversation_id": "c1", "messages": [], "request_id": "nav-2"}
    )
    assert reply["type"] == "conversation_restored"
    assert reply["request_id"] == "nav-2"


def test_refused_restore_echoes_id(service):
    service.handle_restore_conversation.return_value = {"type": "error", "message": "nope"}
    reply = _exchange(
        {"type": "restore_conversation", "conversation_id": "c1", "messages": [], "request_id": "nav-3"}
    )
    assert reply["type"] == "error"
    assert reply["request_id"] == "nav-3"


def test_no_id_no_echo(service):
    assert "request_id" not in _exchange({"type": "reset_session"})


@pytest.mark.parametrize("bad", [None, 7, "", "x" * 65, {"a": 1}])
def test_unusable_ids_are_ignored(bad):
    reply = {"type": "session_reset"}
    _echo_request_id({"request_id": bad}, reply)
    assert "request_id" not in reply


def _real_service():
    from atlas.application.chat.service import ChatService
    from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository

    repo = InMemorySessionRepository()
    service = ChatService(
        llm=MagicMock(),
        tool_manager=MagicMock(),
        connection=MagicMock(),
        config_manager=MagicMock(),
        session_repository=repo,
    )
    return service


@pytest.mark.asyncio
async def test_reset_reports_a_failed_persist():
    from uuid import uuid4

    service = _real_service()
    session_id = uuid4()
    await service.create_session(session_id, USER)
    service.persist_session = AsyncMock(return_value=False)
    reply = await service.handle_reset_session(session_id=session_id, user_email=USER)
    assert reply["type"] == "error"
    assert reply["error_type"] == "domain"


@pytest.mark.asyncio
async def test_restore_reports_a_failed_persist():
    from uuid import uuid4

    service = _real_service()
    session_id = uuid4()
    await service.create_session(session_id, USER)
    service.persist_session = AsyncMock(return_value=False)
    reply = await service.handle_restore_conversation(
        session_id=session_id, conversation_id="c1", messages=[], user_email=USER
    )
    assert reply["type"] == "error"
    assert reply["error_type"] == "domain"


@pytest.mark.asyncio
async def test_successful_persist_still_acknowledges():
    from uuid import uuid4

    service = _real_service()
    session_id = uuid4()
    await service.create_session(session_id, USER)
    reply = await service.handle_reset_session(session_id=session_id, user_email=USER)
    assert reply["type"] == "session_reset"
