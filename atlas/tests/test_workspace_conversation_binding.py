"""Tests for issue #829: re-enabling a conversation's workspace on reload.

The active workspace id travels with each chat turn (sent as
``workspace_id`` on the WebSocket chat frame), is stashed in
``session.context["workspace_id"]``, and is persisted in the conversation's
metadata on save so that reopening the conversation from history can re-bind
it to its workspace.

These tests cover:
- ``handle_chat_message`` captures ``workspace_id`` into session context.
- ``_save_conversation`` persists ``workspace_id`` in conversation metadata.
- A null/missing ``workspace_id`` round-trips as null (no crash, no stale id).
- ``get_conversation`` returns the stored ``workspace_id`` in metadata so the
  frontend restore path can read it.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from atlas.application.chat.service import ChatService
from atlas.domain.messages.models import Message, MessageRole
from atlas.modules.chat_history import (
    ConversationRepository,
    get_session_factory,
    init_database,
)
from atlas.modules.chat_history.database import reset_engine
from atlas.modules.config.config_manager import config_manager

TEST_USER = config_manager.app_settings.test_user


@pytest.fixture(autouse=True)
def _clean_engine():
    """Reset the global engine before and after each test."""
    reset_engine()
    yield
    reset_engine()


@pytest.fixture
def repo(tmp_path):
    """Create a ConversationRepository backed by a temp DuckDB."""
    db_url = f"duckdb:///{tmp_path / 'test_ws_binding.db'}"
    init_database(db_url)
    factory = get_session_factory()
    return ConversationRepository(factory)


def _make_service(repo):
    """Build a ChatService with an in-memory session repo and a real
    conversation repository, so a turn's save actually reaches the store."""
    sessions = {}

    async def _get(session_id):
        return sessions.get(session_id)

    async def _create(session):
        sessions[session.id] = session

    async def _update(session):
        sessions[session.id] = session

    mock_session_repo = MagicMock()
    mock_session_repo.get = AsyncMock(side_effect=_get)
    mock_session_repo.create = AsyncMock(side_effect=_create)
    mock_session_repo.update = AsyncMock(side_effect=_update)

    service = ChatService(
        llm=MagicMock(),
        tool_manager=MagicMock(),
        connection=MagicMock(),
        config_manager=MagicMock(),
        session_repository=mock_session_repo,
        conversation_repository=repo,
    )
    return service, sessions


def _stub_orchestrator(service, sessions, session_id):
    """Replace the orchestrator so handle_chat_message returns without calling
    an LLM, while still letting _commit_turn persist the turn."""
    async def fake_execute(**kwargs):
        # Add an assistant reply so the turn has something to save.
        session = sessions[session_id]
        session.history.add_message(
            Message(role=MessageRole.ASSISTANT, content="reply")
        )
        return {"type": "done"}

    mock_orchestrator = MagicMock()
    mock_orchestrator.execute = AsyncMock(side_effect=fake_execute)
    return patch.object(service, "_get_orchestrator", return_value=mock_orchestrator)


@pytest.mark.asyncio
async def test_handle_chat_message_captures_workspace_id(repo):
    """workspace_id sent on the chat frame lands in session.context."""
    service, sessions = _make_service(repo)
    session_id = uuid4()

    with _stub_orchestrator(service, sessions, session_id):
        await service.handle_chat_message(
            session_id=session_id,
            content="hello",
            model="test-model",
            user_email=TEST_USER,
            workspace_id="ws-work",
        )

    session = sessions[session_id]
    assert session.context.get("workspace_id") == "ws-work"


@pytest.mark.asyncio
async def test_handle_chat_message_strips_blank_workspace_id(repo):
    """A blank/whitespace workspace_id is normalized to None."""
    service, sessions = _make_service(repo)
    session_id = uuid4()

    with _stub_orchestrator(service, sessions, session_id):
        await service.handle_chat_message(
            session_id=session_id,
            content="hello",
            model="test-model",
            user_email=TEST_USER,
            workspace_id="  ",
        )

    session = sessions[session_id]
    assert session.context.get("workspace_id") is None


@pytest.mark.asyncio
async def test_save_persists_workspace_id_in_metadata(repo):
    """A turn sent with a workspace_id persists it in conversation metadata."""
    service, sessions = _make_service(repo)
    session_id = uuid4()

    with _stub_orchestrator(service, sessions, session_id):
        await service.handle_chat_message(
            session_id=session_id,
            content="hello",
            model="test-model",
            user_email=TEST_USER,
            workspace_id="ws-work",
        )

    session = sessions[session_id]
    conv_id = session.context.get("conversation_id")
    saved = repo.get_conversation(conv_id, TEST_USER)
    assert saved is not None
    assert saved["metadata"].get("workspace_id") == "ws-work"
    assert saved["metadata"].get("agent_mode") is False


@pytest.mark.asyncio
async def test_save_persists_null_workspace_id(repo):
    """A turn without a workspace persists null, not a stale id from a prior
    turn on the same session."""
    service, sessions = _make_service(repo)
    session_id = uuid4()

    # First turn carries a workspace.
    with _stub_orchestrator(service, sessions, session_id):
        await service.handle_chat_message(
            session_id=session_id,
            content="hello",
            model="test-model",
            user_email=TEST_USER,
            workspace_id="ws-work",
        )
    session = sessions[session_id]
    conv_id = session.context.get("conversation_id")

    # Second turn on the same conversation drops the workspace.
    with _stub_orchestrator(service, sessions, session_id):
        await service.handle_chat_message(
            session_id=session_id,
            content="again",
            model="test-model",
            user_email=TEST_USER,
            conversation_id=conv_id,
            workspace_id=None,
        )

    saved = repo.get_conversation(conv_id, TEST_USER)
    assert saved is not None
    assert saved["metadata"].get("workspace_id") is None


@pytest.mark.asyncio
async def test_save_without_workspace_id_arg_defaults_null(repo):
    """Omitting workspace_id entirely (older clients) persists null rather
    than raising."""
    service, sessions = _make_service(repo)
    session_id = uuid4()

    with _stub_orchestrator(service, sessions, session_id):
        await service.handle_chat_message(
            session_id=session_id,
            content="hello",
            model="test-model",
            user_email=TEST_USER,
        )

    session = sessions[session_id]
    conv_id = session.context.get("conversation_id")
    saved = repo.get_conversation(conv_id, TEST_USER)
    assert saved is not None
    assert saved["metadata"].get("workspace_id") is None