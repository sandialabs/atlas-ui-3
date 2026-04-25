"""Regression test for issue #543.

Ensures ChatService.handle_chat_message populates
``session.context["conversation_id"]`` before dispatching to the
orchestrator, even when the client didn't send one (e.g. the first
message of a brand-new conversation).

Without this default, MCP tool calls in that first turn fall into the
``async with client:`` per-call fallback in ``MCPToolManager.call_tool``
because ``conversation_id`` is ``None``. That tears down the MCP session
(POST ... DELETE) after every tool call, which breaks stateful MCP
servers that key per-tool state on ``Context.session_id``.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from atlas.application.chat.service import ChatService
from atlas.domain.errors import AuthorizationError


def _make_service():
    # Mimic a minimal in-memory session repo so handle_chat_message can
    # create and then look up a session.
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
    )
    return service, sessions


class _OwnerLookupRepository:
    def __init__(self, owners):
        self._owners = owners

    def get_conversation_owner(self, conversation_id):
        return self._owners.get(conversation_id)


@pytest.mark.asyncio
async def test_first_message_defaults_conversation_id_to_session_id():
    """First message with no client-sent conversation_id should default to str(session_id)."""
    service, sessions = _make_service()
    session_id = uuid4()

    captured = {}

    async def fake_execute(**kwargs):
        # Grab the live session context at dispatch time
        session = sessions[session_id]
        captured["conversation_id"] = session.context.get("conversation_id")
        return {"type": "done"}

    mock_orchestrator = MagicMock()
    mock_orchestrator.execute = AsyncMock(side_effect=fake_execute)

    with patch.object(service, "_get_orchestrator", return_value=mock_orchestrator):
        await service.handle_chat_message(
            session_id=session_id,
            content="hello",
            model="test-model",
            user_email="test@test.com",
        )

    assert captured["conversation_id"] == str(session_id), (
        "handle_chat_message must populate session.context['conversation_id'] "
        "with str(session_id) when the client doesn't send one, so that MCP "
        "tool calls reuse a persistent session via MCPSessionManager."
    )


@pytest.mark.asyncio
async def test_explicit_conversation_id_wins_over_default():
    """An explicit conversation_id from the client must be preserved."""
    service, sessions = _make_service()
    session_id = uuid4()
    explicit_conv_id = "conv-abc-123"

    captured = {}

    async def fake_execute(**kwargs):
        session = sessions[session_id]
        captured["conversation_id"] = session.context.get("conversation_id")
        return {"type": "done"}

    mock_orchestrator = MagicMock()
    mock_orchestrator.execute = AsyncMock(side_effect=fake_execute)

    with patch.object(service, "_get_orchestrator", return_value=mock_orchestrator):
        await service.handle_chat_message(
            session_id=session_id,
            content="hello",
            model="test-model",
            user_email="test@test.com",
            conversation_id=explicit_conv_id,
        )

    assert captured["conversation_id"] == explicit_conv_id


@pytest.mark.asyncio
async def test_explicit_conversation_id_owned_by_other_user_is_rejected():
    service, sessions = _make_service()
    service.conversation_repository = _OwnerLookupRepository(
        {"victim-conv": "victim@test.com"}
    )
    session_id = uuid4()
    mock_orchestrator = MagicMock()
    mock_orchestrator.execute = AsyncMock(return_value={"type": "done"})

    with patch.object(service, "_get_orchestrator", return_value=mock_orchestrator):
        with pytest.raises(AuthorizationError):
            await service.handle_chat_message(
                session_id=session_id,
                content="hello",
                model="test-model",
                user_email="attacker@test.com",
                conversation_id="victim-conv",
            )

    mock_orchestrator.execute.assert_not_called()
    assert sessions[session_id].context.get("conversation_id") is None


@pytest.mark.asyncio
async def test_explicit_conversation_id_owned_by_user_is_allowed():
    service, sessions = _make_service()
    service.conversation_repository = _OwnerLookupRepository(
        {"own-conv": "user@test.com"}
    )
    session_id = uuid4()

    captured = {}

    async def fake_execute(**kwargs):
        session = sessions[session_id]
        captured["conversation_id"] = session.context.get("conversation_id")
        return {"type": "done"}

    mock_orchestrator = MagicMock()
    mock_orchestrator.execute = AsyncMock(side_effect=fake_execute)

    with patch.object(service, "_get_orchestrator", return_value=mock_orchestrator):
        await service.handle_chat_message(
            session_id=session_id,
            content="hello",
            model="test-model",
            user_email="user@test.com",
            conversation_id="own-conv",
        )

    assert captured["conversation_id"] == "own-conv"


@pytest.mark.asyncio
async def test_default_does_not_overwrite_existing_context_value():
    """If session.context already has a conversation_id, don't clobber it."""
    service, sessions = _make_service()
    session_id = uuid4()

    # Pre-create session with an existing conversation_id
    session = await service.create_session(session_id, "test@test.com")
    session.context["conversation_id"] = "existing-conv-id"

    captured = {}

    async def fake_execute(**kwargs):
        captured["conversation_id"] = session.context.get("conversation_id")
        return {"type": "done"}

    mock_orchestrator = MagicMock()
    mock_orchestrator.execute = AsyncMock(side_effect=fake_execute)

    with patch.object(service, "_get_orchestrator", return_value=mock_orchestrator):
        await service.handle_chat_message(
            session_id=session_id,
            content="hello",
            model="test-model",
            user_email="test@test.com",
        )

    assert captured["conversation_id"] == "existing-conv-id"
