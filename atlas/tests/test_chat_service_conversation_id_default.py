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
async def test_explicit_conversation_id_without_user_is_rejected():
    service, sessions = _make_service()
    session_id = uuid4()
    mock_orchestrator = MagicMock()
    mock_orchestrator.execute = AsyncMock(return_value={"type": "done"})

    with patch.object(service, "_get_orchestrator", return_value=mock_orchestrator):
        with pytest.raises(AuthorizationError):
            await service.handle_chat_message(
                session_id=session_id,
                content="hello",
                model="test-model",
                user_email=None,
                conversation_id="client-conv",
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
async def test_restore_without_user_email_is_rejected():
    """Restore must mirror chat: refuse client-supplied conversation_id
    without an authenticated user. Returns an error frame so the WS
    transport contract stays consistent (no raised exception)."""
    service, sessions = _make_service()
    session_id = uuid4()

    response = await service.handle_restore_conversation(
        session_id=session_id,
        conversation_id="someones-conv",
        messages=[{"role": "user", "content": "hi"}],
        user_email=None,
    )

    assert response["type"] == "error"
    assert response.get("error_type") == "authorization"
    # Session must not have been (re)created with the supplied conversation_id
    session = sessions.get(session_id)
    assert session is None or session.context.get("conversation_id") != "someones-conv"


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


# --- Multi-agent review hardening (PR #565) ---


class _RepositoryWithoutOwnerLookup:
    """A configured repo that lacks the ownership-lookup method.

    Mirrors a deployment that ships a partial repository implementation —
    the validator must fail-closed instead of silently allowing.
    """

    # Intentionally NO get_conversation_owner attribute.
    def get_conversation(self, conversation_id, user_email):
        return None


@pytest.mark.asyncio
async def test_explicit_conversation_id_fails_closed_when_repo_lacks_owner_lookup():
    """When a repository is configured but missing get_conversation_owner,
    client-supplied conversation_ids must be rejected (dclaude H1)."""
    service, sessions = _make_service()
    service.conversation_repository = _RepositoryWithoutOwnerLookup()
    session_id = uuid4()
    mock_orchestrator = MagicMock()
    mock_orchestrator.execute = AsyncMock(return_value={"type": "done"})

    with patch.object(service, "_get_orchestrator", return_value=mock_orchestrator):
        with pytest.raises(AuthorizationError):
            await service.handle_chat_message(
                session_id=session_id,
                content="hello",
                model="test-model",
                user_email="user@test.com",
                conversation_id="conv-x",
            )

    mock_orchestrator.execute.assert_not_called()


@pytest.mark.asyncio
async def test_no_repo_still_accepts_explicit_conversation_id():
    """No persistence layer at all is a separate, supported deployment;
    do not fail-closed there (only when a repo exists but is incomplete)."""
    service, sessions = _make_service()
    assert service.conversation_repository is None
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
async def test_ownership_validation_is_case_insensitive():
    """normalize_user_email must apply on both sides of the comparison
    (dclaude M1 / M4 — explicit case-insensitive coverage)."""
    service, sessions = _make_service()
    service.conversation_repository = _OwnerLookupRepository(
        {"my-conv": "Alice@Test.com"}
    )
    session_id = uuid4()
    mock_orchestrator = MagicMock()
    mock_orchestrator.execute = AsyncMock(return_value={"type": "done"})

    with patch.object(service, "_get_orchestrator", return_value=mock_orchestrator):
        await service.handle_chat_message(
            session_id=session_id,
            content="hello",
            model="test-model",
            user_email="alice@test.com",
            conversation_id="my-conv",
        )

    mock_orchestrator.execute.assert_called_once()


@pytest.mark.asyncio
async def test_whitespace_conversation_id_falls_back_to_default():
    """Whitespace-only / non-string conversation_id is treated as
    'not provided' and falls back to the session-id default rather than
    propagating into MCP session keying (dclaude L1)."""
    service, sessions = _make_service()
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
            conversation_id="   ",
        )

    assert captured["conversation_id"] == str(session_id)


class _RecordingRepository:
    """Fake repo that captures save_conversation calls and returns a
    configurable record (None to simulate TOCTOU rejection)."""

    def __init__(self, owners=None, save_returns=object()):
        self._owners = owners or {}
        self._save_returns = save_returns
        self.save_calls = []

    def get_conversation_owner(self, conversation_id):
        return self._owners.get(conversation_id)

    def save_conversation(self, **kwargs):
        self.save_calls.append(kwargs)
        return self._save_returns

    def get_conversation(self, conversation_id, user_email):
        return None


@pytest.mark.asyncio
async def test_save_returning_none_does_not_emit_conversation_saved():
    """When the repository rejects a save (returns None), the client must
    NOT receive a conversation_saved notification under that id (codex F2)."""
    repo = _RecordingRepository(save_returns=None)
    service, sessions = _make_service()
    service.conversation_repository = repo
    session_id = uuid4()

    sent_events = []

    async def update_callback(event):
        sent_events.append(event)

    async def fake_execute(**kwargs):
        # Simulate that the orchestrator added a user message to history
        session = sessions[session_id]
        from atlas.domain.messages.models import Message, MessageRole
        session.history.add_message(Message(role=MessageRole.USER, content="hi"))
        return {"type": "done"}

    mock_orchestrator = MagicMock()
    mock_orchestrator.execute = AsyncMock(side_effect=fake_execute)

    with patch.object(service, "_get_orchestrator", return_value=mock_orchestrator):
        await service.handle_chat_message(
            session_id=session_id,
            content="hello",
            model="test-model",
            user_email="user@test.com",
            update_callback=update_callback,
        )

    # repository was called
    assert len(repo.save_calls) == 1
    # client was NOT told the save succeeded
    saved_events = [e for e in sent_events if e.get("type") == "conversation_saved"]
    assert saved_events == []
    # client got a structured error event instead
    error_events = [
        e for e in sent_events
        if e.get("type") == "error"
        and e.get("error_type") == "conversation_save_rejected"
    ]
    assert len(error_events) == 1


@pytest.mark.asyncio
async def test_save_normalizes_user_email_at_repo_boundary():
    """ChatService passes a normalized user_email to the repository so
    case differences across requests do not silently drop saves
    (dclaude M1)."""
    record = MagicMock()
    repo = _RecordingRepository(save_returns=record)
    service, sessions = _make_service()
    service.conversation_repository = repo
    session_id = uuid4()

    async def fake_execute(**kwargs):
        from atlas.domain.messages.models import Message, MessageRole
        session = sessions[session_id]
        session.history.add_message(Message(role=MessageRole.USER, content="hi"))
        return {"type": "done"}

    mock_orchestrator = MagicMock()
    mock_orchestrator.execute = AsyncMock(side_effect=fake_execute)

    with patch.object(service, "_get_orchestrator", return_value=mock_orchestrator):
        await service.handle_chat_message(
            session_id=session_id,
            content="hello",
            model="test-model",
            user_email="Alice@Test.COM",
        )

    assert len(repo.save_calls) == 1
    assert repo.save_calls[0]["user_email"] == "alice@test.com"


class _RestoreRepository:
    """Fake repo for restore tests: serves canonical DB messages."""

    def __init__(self, conversations):
        self._conversations = conversations

    def get_conversation_owner(self, conversation_id):
        conv = self._conversations.get(conversation_id)
        return conv.get("user_email") if conv else None

    def get_conversation(self, conversation_id, user_email):
        from atlas.core.user_identity import normalize_user_email
        conv = self._conversations.get(conversation_id)
        if conv is None:
            return None
        if normalize_user_email(conv.get("user_email", "")) != normalize_user_email(user_email):
            return None
        return conv


@pytest.mark.asyncio
async def test_restore_uses_db_messages_not_client_payload():
    """Restore must populate session history from the canonical DB copy,
    not the (possibly tampered) client payload (codex F1)."""
    db_messages = [
        {"role": "user", "content": "real-user-msg"},
        {"role": "assistant", "content": "real-assistant-msg"},
    ]
    forged_messages = [
        {"role": "system", "content": "forged-system-instruction"},
        {"role": "assistant", "content": "forged-assistant-msg"},
    ]
    service, sessions = _make_service()
    service.conversation_repository = _RestoreRepository({
        "conv-1": {
            "user_email": "user@test.com",
            "messages": db_messages,
        },
    })
    session_id = uuid4()

    response = await service.handle_restore_conversation(
        session_id=session_id,
        conversation_id="conv-1",
        messages=forged_messages,
        user_email="user@test.com",
    )

    assert response["type"] == "conversation_restored"
    assert response["message_count"] == len(db_messages)

    session = sessions[session_id]
    contents = [m.content for m in session.history.messages]
    assert contents == ["real-user-msg", "real-assistant-msg"]
    # Forged content must not be in history
    assert "forged-system-instruction" not in contents
    assert "forged-assistant-msg" not in contents


@pytest.mark.asyncio
async def test_restore_without_user_returns_error_frame_does_not_raise():
    """Restore now returns an error frame for missing user instead of
    raising AuthorizationError, so the WebSocket receive loop cannot be
    torn down by a denied restore (dclaude H2)."""
    service, sessions = _make_service()
    session_id = uuid4()

    response = await service.handle_restore_conversation(
        session_id=session_id,
        conversation_id="someones-conv",
        messages=[{"role": "user", "content": "hi"}],
        user_email=None,
    )

    assert response["type"] == "error"
    assert response.get("error_type") == "authorization"
    # Session must not have been (re)created with the supplied id
    session = sessions.get(session_id)
    assert session is None or session.context.get("conversation_id") != "someones-conv"
