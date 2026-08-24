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

import threading
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
async def test_handle_chat_message_ignores_blank_workspace_id(repo):
    """A blank/whitespace workspace_id is ignored, leaving no binding.

    Asserts the key is *absent* rather than falsy: `.get(...) is None` would
    hold simply because nothing was ever written, so it would pass even if the
    ignore logic were deleted.
    """
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
    assert "workspace_id" not in session.context


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
async def test_save_without_workspace_id_arg_leaves_context_unset(repo):
    """Omitting workspace_id entirely (older clients) writes nothing to the
    session and persists null, rather than raising.

    Asserts the key is absent, not merely falsy, so the UNSET branch is really
    what is under test.
    """
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
    assert "workspace_id" not in session.context
    conv_id = session.context.get("conversation_id")
    saved = repo.get_conversation(conv_id, TEST_USER)
    assert saved is not None
    assert saved["metadata"].get("workspace_id") is None


@pytest.mark.asyncio
async def test_omitted_workspace_id_does_not_clear_an_existing_binding(repo):
    """An omitted field is not an explicit null.

    A client that never sends ``workspace_id`` (the CLI, a script, an older
    bundle) must leave an existing binding alone rather than stripping it on
    its next turn.
    """
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

    # Second turn omits the field entirely.
    with _stub_orchestrator(service, sessions, session_id):
        await service.handle_chat_message(
            session_id=session_id,
            content="again",
            model="test-model",
            user_email=TEST_USER,
            conversation_id=conv_id,
        )

    assert session.context.get("workspace_id") == "ws-work"
    saved = repo.get_conversation(conv_id, TEST_USER)
    assert saved["metadata"].get("workspace_id") == "ws-work"


@pytest.mark.asyncio
async def test_explicit_null_workspace_id_still_clears_the_binding(repo):
    """An explicit null is honoured -- only omission is a no-op."""
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

    with _stub_orchestrator(service, sessions, session_id):
        await service.handle_chat_message(
            session_id=session_id,
            content="again",
            model="test-model",
            user_email=TEST_USER,
            conversation_id=conv_id,
            workspace_id=None,
        )

    assert session.context.get("workspace_id") is None


@pytest.mark.asyncio
async def test_oversized_workspace_id_is_rejected(repo):
    """A client-supplied id is length-bounded before it reaches metadata."""
    service, sessions = _make_service(repo)
    session_id = uuid4()

    with _stub_orchestrator(service, sessions, session_id):
        await service.handle_chat_message(
            session_id=session_id,
            content="hello",
            model="test-model",
            user_email=TEST_USER,
            workspace_id="w" * 5000,
        )

    # Key absence, not falsiness: `.get(...) is None` would hold simply because
    # nothing was ever written, so it would pass with the length check removed.
    assert "workspace_id" not in sessions[session_id].context


@pytest.mark.asyncio
async def test_restore_seeds_workspace_id_from_stored_metadata(repo):
    """Restoring a conversation carries its stored binding into the new
    session, so a client that never sends the field re-persists it."""
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
    conv_id = sessions[session_id].context.get("conversation_id")
    saved = repo.get_conversation(conv_id, TEST_USER)

    # A fresh session restores the conversation.
    new_session_id = uuid4()
    await service.handle_restore_conversation(
        session_id=new_session_id,
        conversation_id=conv_id,
        messages=saved["messages"],
        user_email=TEST_USER,
    )

    assert sessions[new_session_id].context.get("workspace_id") == "ws-work"

    # A follow-up turn that omits workspace_id keeps the binding intact.
    with _stub_orchestrator(service, sessions, new_session_id):
        await service.handle_chat_message(
            session_id=new_session_id,
            content="again",
            model="test-model",
            user_email=TEST_USER,
            conversation_id=conv_id,
        )

    reloaded = repo.get_conversation(conv_id, TEST_USER)
    assert reloaded["metadata"].get("workspace_id") == "ws-work"


@pytest.mark.asyncio
async def test_malformed_workspace_id_leaves_existing_binding(repo):
    """A malformed id is ignored, not persisted as a cleared binding.

    Otherwise a single bad frame would drop the binding and later well-formed
    turns could not recover it.
    """
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

    for bad in (12345, {"id": "x"}, "  ", "w" * 5000):
        with _stub_orchestrator(service, sessions, session_id):
            await service.handle_chat_message(
                session_id=session_id,
                content="again",
                model="test-model",
                user_email=TEST_USER,
                conversation_id=conv_id,
                workspace_id=bad,
            )
        assert session.context.get("workspace_id") == "ws-work", f"clobbered by {bad!r}"

    saved = repo.get_conversation(conv_id, TEST_USER)
    assert saved["metadata"].get("workspace_id") == "ws-work"


@pytest.mark.asyncio
async def test_rehydrate_on_reconnect_preserves_workspace_binding(repo):
    """A mid-conversation reconnect must not save a null over the stored
    binding: the rehydrate path seeds the session from stored metadata."""
    service, sessions = _make_service(repo)
    first_session = uuid4()

    with _stub_orchestrator(service, sessions, first_session):
        await service.handle_chat_message(
            session_id=first_session,
            content="hello",
            model="test-model",
            user_email=TEST_USER,
            workspace_id="ws-work",
        )
    conv_id = sessions[first_session].context.get("conversation_id")

    # The client reconnects: a brand-new session continues the same conversation
    # and (being e.g. the CLI) does not send workspace_id.
    reconnected = uuid4()
    with _stub_orchestrator(service, sessions, reconnected):
        await service.handle_chat_message(
            session_id=reconnected,
            content="after reconnect",
            model="test-model",
            user_email=TEST_USER,
            conversation_id=conv_id,
        )

    assert sessions[reconnected].context.get("workspace_id") == "ws-work"
    saved = repo.get_conversation(conv_id, TEST_USER)
    assert saved["metadata"].get("workspace_id") == "ws-work"


class TestWebSocketChatFrameWorkspaceId:
    """The transport layer must forward the omitted-vs-null distinction.

    ``data.get("workspace_id")`` alone turns an omitted field into an explicit
    null before the service's ``UNSET`` branch can be reached, so the binding
    would still be cleared for any client that does not send the field. These
    drive the real endpoint rather than the service in isolation.
    """

    @pytest.fixture
    def ws_client(self):
        from fastapi.testclient import TestClient
        from main import app

        with patch("main.app_factory") as mock_factory:
            mock_config = MagicMock()
            mock_config.app_settings.test_user = TEST_USER
            mock_config.app_settings.debug_mode = True
            mock_config.app_settings.auth_user_header = "X-User-Email"
            mock_config.app_settings.feature_proxy_secret_enabled = False
            mock_factory.get_config_manager.return_value = mock_config

            chat_service = MagicMock()
            chat_service.handle_chat_message = AsyncMock(return_value={})
            chat_service.end_session = AsyncMock()
            chat_service.session_repository.get = AsyncMock(return_value=None)
            mock_factory.create_chat_service.return_value = chat_service

            yield TestClient(app), chat_service

    @staticmethod
    def _sent_workspace_id(chat_service):
        chat_service.handle_chat_message.assert_awaited()
        return chat_service.handle_chat_message.await_args.kwargs["workspace_id"]

    def _send(self, ws_client, frame):
        """Send one chat frame and return the workspace_id the service saw.

        Waits on an Event signalled by the mock rather than polling: this is the
        only coverage of the atlas/main.py wiring, and a busy-wait that times out
        under CI load would surface as a misleading "not awaited" failure.
        """
        client, chat_service = ws_client
        received = threading.Event()

        async def _capture(**kwargs):
            received.set()
            return {}

        chat_service.handle_chat_message.side_effect = _capture

        with client.websocket_connect(
            "/ws", headers={"X-User-Email": TEST_USER}
        ) as websocket:
            websocket.send_json({"type": "chat", "content": "hi", "model": "m", **frame})
            assert received.wait(timeout=10), (
                "the server never invoked handle_chat_message for the chat frame"
            )
        return self._sent_workspace_id(chat_service)

    def test_omitted_workspace_id_forwards_unset(self, ws_client):
        """No field on the frame -> UNSET, so the service leaves the binding."""
        from atlas.application.chat.service import UNSET

        assert self._send(ws_client, {}) is UNSET

    def test_explicit_null_forwards_none(self, ws_client):
        """An explicit null still reaches the service as None, so it clears."""
        assert self._send(ws_client, {"workspace_id": None}) is None

    def test_value_forwards_unchanged(self, ws_client):
        assert self._send(ws_client, {"workspace_id": "ws-work"}) == "ws-work"


@pytest.mark.asyncio
async def test_switching_conversations_in_one_session_does_not_leak_the_binding(repo):
    """A session that switches to an unbound conversation must not re-bind it.

    The seeding added for the reconnect case makes this reachable: a truthy-only
    assignment would leave conversation A's workspace in ``session.context``, and
    ``_save_conversation`` would then stamp it onto conversation B, which the
    user never bound to any workspace.
    """
    service, sessions = _make_service(repo)
    session_id = uuid4()

    # Conversation A is bound to a workspace.
    with _stub_orchestrator(service, sessions, session_id):
        await service.handle_chat_message(
            session_id=session_id,
            content="first",
            model="test-model",
            user_email=TEST_USER,
            workspace_id="ws-work",
        )
    session = sessions[session_id]
    conv_a = session.context.get("conversation_id")

    # Conversation B was saved with no workspace at all.
    conv_b = "conv-unbound"
    repo.save_conversation(
        conversation_id=conv_b,
        user_email=TEST_USER,
        title="Unbound",
        model="test-model",
        messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}],
        metadata={"agent_mode": False, "workspace_id": None},
    )

    # The same long-lived session switches to B without sending workspace_id.
    with _stub_orchestrator(service, sessions, session_id):
        await service.handle_chat_message(
            session_id=session_id,
            content="second",
            model="test-model",
            user_email=TEST_USER,
            conversation_id=conv_b,
        )

    assert session.context.get("workspace_id") is None
    saved_b = repo.get_conversation(conv_b, TEST_USER)
    assert saved_b["metadata"].get("workspace_id") is None, "B inherited A's workspace"

    # A keeps its own binding.
    saved_a = repo.get_conversation(conv_a, TEST_USER)
    assert saved_a["metadata"].get("workspace_id") == "ws-work"


@pytest.mark.asyncio
async def test_unknown_conversation_id_does_not_inherit_the_previous_binding(repo):
    """Switching to a conversation the store has never seen starts unbound.

    The rehydrate path returns early for an unknown id (the normal first-turn
    case). If the carried binding were only cleared after that return, the new
    conversation would be saved with the previous conversation's workspace.
    """
    service, sessions = _make_service(repo)
    session_id = uuid4()

    with _stub_orchestrator(service, sessions, session_id):
        await service.handle_chat_message(
            session_id=session_id,
            content="first",
            model="test-model",
            user_email=TEST_USER,
            workspace_id="ws-work",
        )
    session = sessions[session_id]

    # A conversation id the store has never seen, with no workspace_id sent.
    with _stub_orchestrator(service, sessions, session_id):
        await service.handle_chat_message(
            session_id=session_id,
            content="brand new",
            model="test-model",
            user_email=TEST_USER,
            conversation_id="conv-never-stored",
        )

    # The binding is what this pins: it is what _save_conversation would stamp
    # onto the new conversation. (The save itself is not asserted here -- this
    # session still holds the previous conversation's messages, since the
    # rehydrate path deliberately leaves history alone for an id the store does
    # not have, and re-saving them under a second id trips a primary-key
    # constraint. That behavior predates this change and is unrelated to it.)
    assert session.context.get("workspace_id") is None, "inherited the previous binding"
