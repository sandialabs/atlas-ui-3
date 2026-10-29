"""Regression tests for issue #1013: MCP sessions released on the right manager.

There were two ``MCPToolManager`` instances: the DI-managed runtime manager
built by ``app_factory`` (the one that serves chat) and a package-level
singleton in ``atlas.modules.mcp_tools``. Disconnect cleanup, run teardown and
conversation restore released on the singleton, so the live manager's
per-conversation sessions and per-user clients leaked for the life of the
process.

These tests pin:
- the package-level singletons are gone (``mcp_tool_manager``, ``llm_caller``);
- every release path targets the chat service's own ``tool_manager``;
- a released conversation's entries are actually gone from the live
  manager's ``_session_manager`` and ``_user_clients``, with the underlying
  clients closed; and
- the restore path releases over a real WebSocket connection.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import atlas.modules.llm as llm_package
import atlas.modules.mcp_tools as mcp_tools_package
from atlas.core.user_identity import normalize_user_email
from atlas.domain.sessions.models import Session
from atlas.modules.mcp_tools.client import MCPToolManager
from atlas.modules.mcp_tools.session_manager import ManagedSession, MCPSessionManager

USER = "alice@example.com"
CONVERSATION_ID = "conv-1013"


# --- the legacy package-level singletons are gone ------------------------------


def test_mcp_tools_package_exports_no_manager_instance():
    """``atlas.modules.mcp_tools`` must not build a manager at import time."""
    assert not hasattr(mcp_tools_package, "mcp_tool_manager")


def test_llm_package_exports_no_caller_instance():
    """``atlas.modules.llm`` had the same unused import-time instance."""
    assert not hasattr(llm_package, "llm_caller")


# --- a live manager with a session and a per-user client to release ------------


class _FakeSessionRepo:
    """Minimal session repository standing in for the real one."""

    def __init__(self, session):
        self._session = session
        self.deleted = []

    async def get(self, session_id):
        return self._session

    async def create(self, session):
        return session

    async def update(self, session):
        return session

    async def delete(self, session_id):
        self.deleted.append(session_id)


def _live_manager_with_entries() -> SimpleNamespace:
    """A real MCPToolManager holding one session and one cached client.

    Built via ``__new__`` (the established pattern in
    ``test_mcp_per_user_http_clients.py``) so no config is read; the release
    path only touches the session manager and the user-client cache.
    """
    manager = MCPToolManager.__new__(MCPToolManager)
    manager._session_manager = MCPSessionManager()
    manager._user_clients = {}
    manager._user_clients_lock = asyncio.Lock()

    user_key = normalize_user_email(USER)
    session_key = (user_key, CONVERSATION_ID, "server-a")
    client_key = (user_key, "server-a", CONVERSATION_ID)

    mcp_client = MagicMock()
    mcp_client.__aexit__ = AsyncMock(return_value=False)
    managed = ManagedSession(mcp_client)
    managed._opened = True
    manager._session_manager._sessions[session_key] = managed
    manager._session_manager._conv_index[CONVERSATION_ID] = {session_key}

    http_client = MagicMock()
    http_client.__aexit__ = AsyncMock(return_value=False)
    manager._user_clients[client_key] = http_client

    return SimpleNamespace(
        manager=manager,
        session_key=session_key,
        client_key=client_key,
        mcp_client=mcp_client,
        http_client=http_client,
    )


def _assert_released(entries: SimpleNamespace) -> None:
    """The conversation's entries are gone and both clients were closed."""
    manager = entries.manager
    assert entries.session_key not in manager._session_manager._sessions
    assert CONVERSATION_ID not in manager._session_manager._conv_index
    assert entries.client_key not in manager._user_clients
    # Closed, not merely dropped from the dicts.
    assert entries.mcp_client.__aexit__.await_count >= 1
    assert entries.http_client.__aexit__.await_count >= 1


def _assert_intact(entries: SimpleNamespace) -> None:
    """The conversation's entries are still held by the runtime manager."""
    manager = entries.manager
    assert entries.session_key in manager._session_manager._sessions
    assert CONVERSATION_ID in manager._session_manager._conv_index
    assert entries.client_key in manager._user_clients


def _service_for(manager):
    """A ChatService whose tool_manager is the live runtime manager."""
    from atlas.application.chat.service import ChatService

    session = Session(id=uuid4(), user_email=USER)
    session.context["conversation_id"] = CONVERSATION_ID
    repo = _FakeSessionRepo(session)

    return ChatService(
        llm=MagicMock(),
        tool_manager=manager,
        connection=MagicMock(),
        config_manager=MagicMock(),
        session_repository=repo,
    )


# --- disconnect cleanup --------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_releases_on_service_tool_manager():
    """``cleanup_disconnected_session`` must release on the chat service's manager.

    The stale behaviour released on a package-level singleton, so the entries
    on the runtime manager survived the disconnect and leaked.
    """
    from main import cleanup_disconnected_session

    entries = _live_manager_with_entries()
    service = _service_for(entries.manager)
    session_id = uuid4()

    await cleanup_disconnected_session(service, session_id, USER, {"task": None})

    _assert_released(entries)


@pytest.mark.asyncio
async def test_cleanup_release_failure_does_not_break_teardown():
    """A release error is swallowed; ``end_session`` still runs."""
    from main import cleanup_disconnected_session

    entries = _live_manager_with_entries()
    entries.manager.release_sessions = AsyncMock(side_effect=RuntimeError("boom"))
    service = _service_for(entries.manager)
    session_id = uuid4()

    # Must not raise, and the session must still be ended.
    await cleanup_disconnected_session(service, session_id, USER, {"task": None})
    assert service.session_repository._session.active is False


# --- finished-run teardown -----------------------------------------------------


@pytest.mark.asyncio
async def test_release_finished_run_releases_on_service_tool_manager():
    """``_release_finished_run`` must release on the chat service's manager.

    Per-run teardown is the documented #884 contract: a conversation's MCP
    sessions and per-user clients are owned by the runs executing against
    them, so the last run of a conversation releases them. Pinned here so the
    behaviour stays visible (stateful MCP servers start each turn fresh).
    """
    from main import _release_finished_run

    entries = _live_manager_with_entries()
    service = _service_for(entries.manager)
    session_id = uuid4()

    registry = MagicMock()
    registry.active_for_conversation.return_value = None

    await _release_finished_run(
        service,
        registry,
        "run-1",
        session_id,
        CONVERSATION_ID,
        USER,
        connection_session_id=None,
    )

    _assert_released(entries)


@pytest.mark.asyncio
async def test_release_finished_run_keeps_sessions_while_a_run_owns_the_conversation():
    """No release while another run still owns the conversation (issue #884)."""
    from main import _release_finished_run

    entries = _live_manager_with_entries()
    service = _service_for(entries.manager)
    session_id = uuid4()

    registry = MagicMock()
    registry.active_for_conversation.return_value = MagicMock()  # a live run

    await _release_finished_run(
        service,
        registry,
        "run-1",
        session_id,
        CONVERSATION_ID,
        USER,
        connection_session_id=None,
    )

    _assert_intact(entries)


# --- the shared helper ---------------------------------------------------------


@pytest.mark.asyncio
async def test_helper_releases_through_tool_manager():
    """The helper releases on ``chat_service.tool_manager``, not any global."""
    from main import _release_conversation_mcp_sessions

    manager = MagicMock()
    manager.release_sessions = AsyncMock()
    service = _service_for(manager)

    await _release_conversation_mcp_sessions(service, CONVERSATION_ID, USER)

    manager.release_sessions.assert_awaited_once_with(CONVERSATION_ID, user_email=USER)


@pytest.mark.asyncio
async def test_helper_tolerates_a_manager_without_release_sessions():
    """A tool manager without ``release_sessions`` is skipped, not fatal."""
    from main import _release_conversation_mcp_sessions

    service = _service_for(MagicMock(spec=["unrelated"]))
    await _release_conversation_mcp_sessions(service, CONVERSATION_ID, USER)


@pytest.mark.asyncio
async def test_helper_tolerates_a_missing_tool_manager():
    """A chat service built without a tool manager is skipped, not fatal."""
    from main import _release_conversation_mcp_sessions

    from atlas.application.chat.service import ChatService

    service = ChatService(
        llm=MagicMock(),
        tool_manager=None,
        connection=MagicMock(),
        config_manager=MagicMock(),
    )
    await _release_conversation_mcp_sessions(service, CONVERSATION_ID, USER)


# --- restore, over a real WebSocket --------------------------------------------
#
# The restore release lives inline in ``websocket_endpoint``, so these tests
# drive the real handler: a resumed session whose context holds a previous
# conversation id, then a ``restore_conversation`` frame. A MagicMock release
# would silently swallow a broken path (the handler wraps it in try/except),
# so the service's tool manager is an AsyncMock and the test asserts the
# await directly.


class _RestoreRepo:
    """In-memory session repository for the restore round trips."""

    def __init__(self):
        self._sessions = {}

    async def get(self, session_id):
        return self._sessions.get(str(session_id))

    async def create(self, session):
        self._sessions[str(session.id)] = session
        return session

    async def update(self, session):
        self._sessions[str(session.id)] = session
        return session


class _RunRegistryMock:
    """Run registry double: configurable ownership, everything else empty."""

    def __init__(self, active_conversations=()):
        self._active = set(active_conversations)

    def set_max_concurrent_runs_per_user(self, _limit):
        return None

    max_concurrent_runs_per_user = 3

    def add_listener(self, _user_email, _callback):
        return lambda: None

    def snapshot_for_user(self, _user_email):
        return []

    def active_for_user(self, _user_email):
        return []

    def active_for_conversation(self, conversation_id, _user_email):
        return MagicMock() if conversation_id in self._active else None

    def pending_requests_for_conversation(self, _conversation_id, _user_email):
        return []


@pytest.fixture
def mock_app_factory():
    """``main.app_factory`` mocked, handing back a real ChatService."""
    from atlas.application.chat.service import ChatService

    repo = _RestoreRepo()
    with patch("main.app_factory") as factory:
        config = MagicMock()
        config.app_settings.debug_mode = False
        config.app_settings.auth_user_header = "X-User-Email"
        config.app_settings.feature_proxy_secret_enabled = False
        factory.get_config_manager.return_value = config

        created = []

        def _service(_connection):
            tool_manager = MagicMock()
            tool_manager.release_sessions = AsyncMock()
            service = ChatService(
                llm=MagicMock(),
                tool_manager=tool_manager,
                connection=MagicMock(),
                config_manager=MagicMock(),
                session_repository=repo,
            )
            created.append(service)
            return service

        factory.create_chat_service.side_effect = _service
        with patch("main.get_run_registry", return_value=_RunRegistryMock()):
            yield SimpleNamespace(factory=factory, repo=repo, created=created)


def _restorable_session(conv_id):
    """A resumable session whose context still points at ``conv_id``."""
    session = Session(id=uuid4(), user_email=USER, active=False)
    session.context["conversation_id"] = conv_id
    return session


def _read_terminal_frame(ws, max_frames=10):
    """Read frames until the restore answers with a non-streaming frame."""
    for _ in range(max_frames):
        frame = ws.receive_json()
        if frame.get("type") not in ("token_stream", "intermediate_update"):
            return frame
    raise AssertionError("restore never answered with a terminal frame")


def test_restore_releases_the_previous_conversation(mock_app_factory):
    """Restoring another conversation releases the old conversation's sessions.

    Issue #1013: this path used to release on the package-level singleton, so
    the live manager's entries survived the conversation switch.
    """
    from main import app

    session = _restorable_session("conv-old")
    mock_app_factory.repo._sessions[str(session.id)] = session

    client = TestClient(app)
    with client.websocket_connect(f"/ws?resume_session={session.id}", headers={"X-User-Email": USER}) as ws:
        assert ws.receive_json()["type"] == "connection_session"
        ws.send_json(
            {
                "type": "restore_conversation",
                "conversation_id": "conv-new",
                "messages": [],
            }
        )
        _read_terminal_frame(ws)

        tool_manager = mock_app_factory.created[-1].tool_manager
        tool_manager.release_sessions.assert_awaited_once_with("conv-old", user_email=USER)


def test_restore_keeps_sessions_while_a_run_owns_the_old_conversation(mock_app_factory):
    """No release when a run still owns the old conversation (issue #884)."""
    from main import app

    session = _restorable_session("conv-old")
    mock_app_factory.repo._sessions[str(session.id)] = session

    registry = _RunRegistryMock(active_conversations={"conv-old"})
    with patch("main.get_run_registry", return_value=registry):
        client = TestClient(app)
        with client.websocket_connect(f"/ws?resume_session={session.id}", headers={"X-User-Email": USER}) as ws:
            assert ws.receive_json()["type"] == "connection_session"
            ws.send_json(
                {
                    "type": "restore_conversation",
                    "conversation_id": "conv-new",
                    "messages": [],
                }
            )
            _read_terminal_frame(ws)

            tool_manager = mock_app_factory.created[-1].tool_manager
            # Asserted inside the connection: the restore block itself must
            # not release while the run owns the old conversation. (After the
            # restore succeeds the disconnect teardown legitimately releases
            # conv-new -- the connection now holds it and nothing runs there.)
            tool_manager.release_sessions.assert_not_called()


# --- guard against the old bug shape -------------------------------------------


@pytest.mark.asyncio
async def test_reset_session_release_reaches_the_runtime_manager():
    """``handle_reset_session`` releases on the same manager (regression net).

    The service already did the right thing (issue #1013's contrast case);
    pinning it here so the runtime manager stays the only release target.
    """
    from atlas.application.chat.service import ChatService

    session = Session(id=uuid4(), user_email=USER)
    session.context["conversation_id"] = CONVERSATION_ID
    repo = _FakeSessionRepo(session)

    manager = MagicMock()
    manager.release_sessions = AsyncMock()

    service = ChatService(
        llm=MagicMock(),
        tool_manager=manager,
        connection=MagicMock(),
        config_manager=MagicMock(),
        session_repository=repo,
    )

    await service.handle_reset_session(session_id=session.id, user_email=USER, preserve_conversation_resources=False)

    manager.release_sessions.assert_awaited_once_with(CONVERSATION_ID, user_email=USER)
