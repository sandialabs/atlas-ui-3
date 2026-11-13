"""The WebSocket window between admission and the run's task (#1128).

Once ``admit_run`` grants a run, it holds a cluster-wide claim: the
conversation lock and one of the user's slots. Until the handler attaches the
run's task, nothing would ever finish the run, and the lease heartbeat keeps
renewing a live record. A failure in that window (seeding the run's files,
sending ``run_started``, a cancelled socket) must therefore mark the run
failed, which releases the claim on every replica.

These tests drive the real ``/ws`` frame loop against a Redis coordinator on
fakeredis and read the shared keys afterwards.

The socket is an ``AsgiWebSocketSession``, not Starlette's ``TestClient``
(issue #1203). A handler that answers a frame and goes back to listening --
which is what every refused turn does -- leaves a blocking ``receive_json()``
waiting forever for a frame nobody will send; here every wait has a deadline
and reports the frames that did arrive.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import main
import pytest

from atlas.application.chat.runs import RunStatus, get_run_registry, reset_run_registry
from atlas.application.chat.runs.coordination import set_run_coordinator
from atlas.infrastructure.redis import RedisClientManager, RedisSettings
from atlas.infrastructure.redis.run_coordinator import RedisRunCoordinator
from atlas.tests.asgi_websocket_session import AsgiWebSocketSession, WebSocketDeadlineError

USER = "guard@example.com"
CONV = "conv-guard"


@pytest.fixture
def server():
    return fakeredis.FakeServer()


@pytest.fixture
def coordinator(server):
    coord = RedisRunCoordinator(
        RedisClientManager(
            RedisSettings(url="redis://fake/0", key_prefix="t"),
            client=fakeredis.FakeAsyncRedis(server=server),
        ),
        replica_id="replica-a",
    )
    set_run_coordinator(coord)
    reset_run_registry()
    yield coord
    reset_run_registry()
    set_run_coordinator(None)


@pytest.fixture
def mock_app_factory():
    with patch("main.app_factory") as mock_factory:
        mock_config = MagicMock()
        mock_config.app_settings.test_user = None
        mock_config.app_settings.debug_mode = False
        mock_config.app_settings.auth_user_header = "X-User-Email"
        mock_config.app_settings.auth_user_header_type = "header"
        mock_config.app_settings.auth_aws_expected_alb_arn = ""
        mock_config.app_settings.auth_aws_region = "us-east-1"
        mock_config.app_settings.feature_proxy_secret_enabled = False
        mock_config.app_settings.feature_websocket_origin_check_enabled = False
        mock_config.app_settings.max_concurrent_runs_per_user = 3
        mock_factory.get_config_manager.return_value = mock_config

        chat_service = MagicMock()
        chat_service.handle_chat_message = AsyncMock(return_value={})
        chat_service.end_session = AsyncMock()
        chat_service.session_repository.get = AsyncMock(return_value=None)
        chat_service.session_repository.delete = AsyncMock()
        mock_factory.create_chat_service.return_value = chat_service
        yield mock_factory


def _chat_frame():
    # Agent mode + server save mode + a tool: what makes a turn a tracked run.
    return {
        "type": "chat",
        "content": "work",
        "agent_mode": True,
        "save_mode": "server",
        "selected_tools": ["atlas_sleep"],
        "conversation_id": CONV,
    }


def _shared_keys(coord, server):
    sync = fakeredis.FakeRedis(server=server, decode_responses=True)
    return (
        sync.zcard(coord.keys.user(USER)),
        sync.get(coord.keys.conversation(CONV)),
    )


def _connect(**kwargs):
    return AsgiWebSocketSession(main.app, "/ws", headers={"X-User-Email": USER}, **kwargs)


async def _wait_released(coord, server, timeout=5.0):
    """The release is scheduled on the app's loop; poll the shared keys."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if _shared_keys(coord, server) == (0, None):
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"claim still held: {_shared_keys(coord, server)}")


def _only_record():
    records = get_run_registry().records_for_user(USER)
    assert len(records) == 1, records
    return records[0]


async def _run_until_the_handler_fails(frame, injected, **kwargs):
    """Send one chat frame; the handler fails inside the admission window.

    The handler must end by raising ``injected`` itself. A handler that closes
    the socket, returns, raises anything else, or simply keeps listening is a
    failure of the test, each reported as what it was. Returns the frames the
    handler sent first.
    """
    async with _connect(**kwargs) as ws:
        await ws.send_json(frame)
        failure = await ws.run_until_app_exits()
    sent = [f.get("type") for f in ws.frames]
    if failure is None:
        raise AssertionError(
            "the handler ended without the injected failure "
            f"(close code {ws.closed_by_app}); frames sent: {sent}"
        )
    if failure is not injected:
        raise AssertionError(
            f"the handler failed with {failure!r}, not the injected failure; frames sent: {sent}"
        ) from failure
    return ws.frames


async def test_seeding_failure_after_admission_releases_the_claim(
    mock_app_factory, coordinator, server, monkeypatch
):
    injected = RuntimeError("seeding blew up")

    async def boom(*_args, **_kwargs):
        raise injected

    monkeypatch.setattr(main, "_seed_run_session_files", boom)
    frames = await _run_until_the_handler_fails(_chat_frame(), injected)
    assert "run_started" not in [f["type"] for f in frames]

    record = _only_record()
    assert record.status == RunStatus.FAILED
    assert record.task is None
    await _wait_released(coordinator, server)
    shared = fakeredis.FakeRedis(server=server, decode_responses=True).hgetall(
        coordinator.keys.run(record.run_id)
    )
    assert shared["status"] == "failed"
    assert shared["lease_expires_at"] == "0"


def _refuse_run_started(monkeypatch, injected):
    from starlette.websockets import WebSocket

    real_send = WebSocket.send_json
    refused = []

    async def refuse_run_started(self, data, *args, **kwargs):
        if isinstance(data, dict) and data.get("type") == "run_started":
            refused.append(data)
            raise injected
        return await real_send(self, data, *args, **kwargs)

    monkeypatch.setattr(WebSocket, "send_json", refuse_run_started)
    return refused


async def test_run_started_send_failure_releases_the_claim(
    mock_app_factory, coordinator, server, monkeypatch
):
    """The other await in the window: the ``run_started`` frame cannot be sent."""
    injected = RuntimeError("socket gone")
    refused = _refuse_run_started(monkeypatch, injected)
    frames = await _run_until_the_handler_fails(_chat_frame(), injected)

    # The failure came from the one send this test refuses, for the run the
    # registry admitted, and that frame never reached the client.
    record = _only_record()
    assert [f["run_id"] for f in refused] == [record.run_id]
    assert "run_started" not in [f["type"] for f in frames]
    assert record.status == RunStatus.FAILED
    await _wait_released(coordinator, server)
    # The conversation is free for the next turn on any replica.
    mock_app_factory.create_chat_service.return_value.handle_chat_message.assert_not_called()


async def test_a_turn_that_never_reaches_the_window_is_a_bounded_failure(
    mock_app_factory, coordinator, monkeypatch
):
    """Why this file has deadlines (#1203): a refused turn is not the failure.

    When admission refuses the turn the handler sends an error frame and goes
    back to waiting for the client, so ``run_started`` is never attempted and
    nothing is ever raised. A client blocked in ``receive_json()`` would wait
    forever; this one fails in bounded time, names the error frame it did
    get, and is not the injected ``RuntimeError``.
    """
    from atlas.application.chat.runs.coordination import RunCoordinationUnavailableError

    async def down(**_kwargs):
        raise RunCoordinationUnavailableError("ConnectionError")

    monkeypatch.setattr(coordinator, "claim", down)
    injected = RuntimeError("socket gone")
    refused = _refuse_run_started(monkeypatch, injected)

    with pytest.raises(WebSocketDeadlineError) as stalled:
        await _run_until_the_handler_fails(_chat_frame(), injected, deadline=1.0)
    assert not isinstance(stalled.value, RuntimeError)
    assert "'error'" in str(stalled.value), "the report lists the frames received"
    assert "websocket_endpoint" in str(stalled.value), "and where the handler is waiting"
    assert refused == []


async def test_successful_turn_holds_then_releases_the_claim(mock_app_factory, coordinator, server):
    """Control: with nothing failing, the claim is held while the run is live
    and released when it completes."""
    async with _connect() as ws:
        await ws.send_json(_chat_frame())
        frame = await ws.receive_until(
            lambda f: f["type"] == "run_started", describe="the run_started frame"
        )
        record = get_run_registry().get(frame["run_id"])
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5
        while not record.is_terminal and loop.time() < deadline:
            await asyncio.sleep(0.02)
    assert record.status == RunStatus.COMPLETED
    await _wait_released(coordinator, server)


async def test_a_coordinator_outage_refuses_the_turn(mock_app_factory, coordinator, monkeypatch):
    """Fail closed on the WebSocket: an error frame, no run, no untracked turn."""
    from atlas.application.chat.runs.coordination import RunCoordinationUnavailableError

    async def down(**_kwargs):
        raise RunCoordinationUnavailableError("ConnectionError")

    monkeypatch.setattr(coordinator, "claim", down)
    async with _connect() as ws:
        await ws.send_json(_chat_frame())
        frame = await ws.receive_until(
            lambda f: f["type"] != "run_status", describe="the frame answering the turn"
        )
    assert frame["type"] == "error"
    assert frame["error_type"] == "run_coordination_unavailable"
    assert get_run_registry().records_for_user(USER) == []
    mock_app_factory.create_chat_service.return_value.handle_chat_message.assert_not_called()


async def test_any_other_admission_refusal_refuses_the_turn(mock_app_factory, coordinator, monkeypatch):
    """A future RunRegistryError subclass is refused, never run untracked."""
    from atlas.application.chat.runs.admission_errors import RunRegistryError

    class _NewRefusal(RunRegistryError):
        pass

    async def refuse(**_kwargs):
        raise _NewRefusal("internal detail: shard 7")

    monkeypatch.setattr(main, "admit_run", refuse)
    async with _connect() as ws:
        await ws.send_json(_chat_frame())
        frame = await ws.receive_until(
            lambda f: f["type"] != "run_status", describe="the frame answering the turn"
        )
    assert frame["type"] == "error"
    assert frame["error_type"] == "run_not_admitted"
    assert "shard 7" not in frame["message"] and frame["message"]
    mock_app_factory.create_chat_service.return_value.handle_chat_message.assert_not_called()
