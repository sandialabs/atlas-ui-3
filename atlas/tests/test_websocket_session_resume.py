"""The /ws resume handshake (issue #1091).

A client that sends ``resume_session`` is told its session id in a
``connection_session`` frame; naming a session it owns resumes it.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from main import app

from atlas.application.chat.service import ChatService
from atlas.domain.sessions.models import Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository

USER = "alice@example.com"


@pytest.fixture
def repo():
    return InMemorySessionRepository()


@pytest.fixture
def mock_app_factory(repo):
    with patch("main.app_factory") as factory:
        config = MagicMock()
        config.app_settings.debug_mode = False
        config.app_settings.auth_user_header = "X-User-Email"
        config.app_settings.feature_proxy_secret_enabled = False
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
            )

        factory.create_chat_service.side_effect = _service
        yield factory


def test_opt_in_without_an_id_reports_a_fresh_session(mock_app_factory):
    client = TestClient(app)
    with client.websocket_connect("/ws?resume_session=", headers={"X-User-Email": USER}) as ws:
        frame = ws.receive_json()
    assert frame["type"] == "connection_session"
    assert frame["resumed"] is False
    assert frame["session_id"]


def test_reconnect_resumes_an_owned_session(mock_app_factory, repo):
    import asyncio

    session = Session(id=uuid4(), user_email=USER, active=False)
    session.context["conversation_id"] = "conv-7"
    asyncio.run(repo.create(session))

    client = TestClient(app)
    with client.websocket_connect(
        f"/ws?resume_session={session.id}", headers={"X-User-Email": USER}
    ) as ws:
        frame = ws.receive_json()
        assert frame == {
            "type": "connection_session",
            "session_id": str(session.id),
            "resumed": True,
        }
        live = asyncio.run(repo.get(session.id))
        assert live is not session  # a detached copy
        assert live.active is True and live.context["conversation_id"] == "conv-7"


def test_another_users_session_is_not_resumed(mock_app_factory, repo):
    import asyncio

    session = Session(id=uuid4(), user_email="bob@example.com")
    asyncio.run(repo.create(session))

    client = TestClient(app)
    with client.websocket_connect(
        f"/ws?resume_session={session.id}", headers={"X-User-Email": USER}
    ) as ws:
        frame = ws.receive_json()
    assert frame["resumed"] is False
    assert frame["session_id"] != str(session.id)
