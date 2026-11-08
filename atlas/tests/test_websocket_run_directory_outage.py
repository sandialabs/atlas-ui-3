"""WebSocket turns while the shared run directory cannot be read (issue #1128).

A directory lookup that fails is not "no other replica owns this
conversation": the turn is refused with ``run_control_unavailable`` and never
admitted, so a Redis outage cannot start a second turn beside a run another
replica is executing, or one under another user's run.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from main import app

from atlas.application.chat.runs import reset_run_registry
from atlas.application.chat.runs.commands import stop_addressed_run
from atlas.application.chat.runs.control_bus import set_run_control_bus

USER = "owner@example.com"
CONV = "conv-elsewhere"


class _UnreadableDirectory:
    replica_id = "replica-b"

    async def owner_of(self, run_id, *, include_terminal=False):
        raise TimeoutError("redis timed out")

    async def run_for_conversation(self, conversation_id):
        raise TimeoutError("redis timed out")


@pytest.fixture
def chat_service():
    with patch("main.app_factory") as mock_factory:
        cfg = MagicMock()
        cfg.app_settings.test_user = None
        cfg.app_settings.debug_mode = False
        cfg.app_settings.auth_user_header = "X-User-Email"
        cfg.app_settings.auth_user_header_type = "header"
        cfg.app_settings.auth_aws_expected_alb_arn = ""
        cfg.app_settings.auth_aws_region = "us-east-1"
        cfg.app_settings.feature_proxy_secret_enabled = False
        cfg.app_settings.feature_websocket_origin_check_enabled = False
        cfg.app_settings.max_concurrent_runs_per_user = 3
        mock_factory.get_config_manager.return_value = cfg

        service = MagicMock()
        service.handle_chat_message = AsyncMock(return_value={})
        service.end_session = AsyncMock()
        service.session_repository.get = AsyncMock(return_value=None)
        service.steering_classification_refusal = AsyncMock(return_value=None)
        mock_factory.create_chat_service.return_value = service
        reset_run_registry()
        set_run_control_bus(_UnreadableDirectory())
        yield service
        set_run_control_bus(None)
        reset_run_registry()


def _connect(client):
    return client.websocket_connect("/ws", headers={"X-User-Email": USER})


def _first_error(ws):
    frame = ws.receive_json()
    while frame.get("type") != "error":
        frame = ws.receive_json()
    return frame


def test_turn_naming_a_conversation_is_refused_not_admitted(chat_service):
    with _connect(TestClient(app)) as ws:
        for _ in range(2):  # a plain turn, and the same retried
            ws.send_json({"type": "chat", "content": "synthetic", "model": "m", "conversation_id": CONV})
            frame = _first_error(ws)
            assert frame["error_type"] == "run_control_unavailable", frame
            assert frame["conversation_id"] == CONV and "run_id" not in frame
    chat_service.handle_chat_message.assert_not_awaited()


def test_stop_naming_a_run_reports_the_outage(chat_service):
    with _connect(TestClient(app)) as ws:
        ws.send_json({"type": "agent_control", "action": "stop", "run_id": "run-elsewhere",
                      "conversation_id": CONV})
        frame = _first_error(ws)
    assert frame["error_type"] == "run_control_unavailable" and frame["run_id"] == "run-elsewhere"


class _RemoteOwnerBus:
    """A directory naming another replica as the conversation's owner, and a
    bus that records what it was asked to send."""

    replica_id = "replica-b"

    def __init__(self):
        self.sent = []

    async def owner_of(self, run_id, *, include_terminal=False):
        return None

    async def run_for_conversation(self, conversation_id):
        return {"run_id": "run-elsewhere", "conversation_id": conversation_id,
                "replica": "replica-a", "generation": 1, "user_email": USER}

    async def send(self, owner_replica, command, run_id, actor, args=None, **kwargs):
        from atlas.application.chat.runs.control_bus import RedisRunControlBus

        self.sent.append({"command": command, "args": args, **kwargs})
        # The real bus decides; it refuses an off-record command unwritten.
        if kwargs.get("off_record"):
            return await RedisRunControlBus.send(self, owner_replica, command, run_id, actor, args, **kwargs)
        return {"ok": True, "code": "ok", "message": "", "payload": {}}


@pytest.mark.parametrize("off_record", [{"incognito": True}, {"save_mode": "local"}])
def test_off_record_steer_to_another_replica_is_refused(chat_service, off_record):
    bus = _RemoteOwnerBus()
    set_run_control_bus(bus)
    with _connect(TestClient(app)) as ws:
        ws.send_json({"type": "chat", "content": "private words", "model": "m",
                      "conversation_id": CONV, **off_record})
        frame = _first_error(ws)
    assert frame["error_type"] == "off_record" and frame["steering"] is True, frame
    assert frame["run_id"] == "run-elsewhere"
    assert [s["off_record"] for s in bus.sent] == [True]
    chat_service.handle_chat_message.assert_not_awaited()


def test_saved_steer_to_another_replica_is_sent(chat_service):
    bus = _RemoteOwnerBus()
    set_run_control_bus(bus)
    with _connect(TestClient(app)) as ws:
        ws.send_json({"type": "chat", "content": "go left", "model": "m", "conversation_id": CONV})
        frame = ws.receive_json()
        while frame.get("update_type") != "steering_queued":
            frame = ws.receive_json()
    assert bus.sent[0]["off_record"] is False and bus.sent[0]["args"]["content"] == "go left"


class _Registry:
    """This replica holds only the conversation's follow-up run."""

    def __init__(self, follow_up=True):
        self.follow_up = MagicMock(run_id="follow-up") if follow_up else None
        self.cancelled = []

    def get_for_user(self, run_id, user_email):
        return self.follow_up if run_id == "follow-up" else None

    def active_for_conversation(self, conversation_id, user_email):
        return self.follow_up if conversation_id == CONV else None

    def stoppable_for_conversation(self, conversation_id, user_email):
        return [self.follow_up] if self.follow_up is not None and conversation_id == CONV else []

    def cancel(self, run_id, user_email):
        self.cancelled.append(run_id)
        return True


class _StopBus:
    """A directory with canned entries; records the stops it is asked to send."""

    replica_id = "replica-b"

    def __init__(self, by_run=None, by_conversation=None, answer=None):
        self.by_run = by_run or {}
        self.by_conversation = by_conversation
        self.answer = answer
        self.lookups = []
        self.sent = []

    async def owner_of(self, run_id, *, include_terminal=False):
        self.lookups.append(("run", run_id))
        return self.by_run.get(run_id)

    async def run_for_conversation(self, conversation_id):
        self.lookups.append(("conversation", conversation_id))
        return self.by_conversation

    async def stoppable_for_conversation(self, conversation_id, user_email):
        self.lookups.append(("conversation", conversation_id))
        return [self.by_conversation] if self.by_conversation else []

    async def send(self, owner_replica, command, run_id, actor, args=None, **kwargs):
        self.sent.append((owner_replica, command, run_id))
        return self.answer


def _entry(run_id):
    return {"run_id": run_id, "conversation_id": CONV, "replica": "replica-a",
            "generation": 1, "user_email": USER}


async def _stop(registry, data, bus):
    set_run_control_bus(bus)
    try:
        ws = MagicMock()
        ws.send_json = AsyncMock()
        await stop_addressed_run(ws, registry, USER, data)
    finally:
        set_run_control_bus(None)
    return ws


async def test_stop_naming_a_remote_parent_stops_it_and_the_local_follow_up():
    ok = {"ok": True, "code": "ok", "message": "", "payload": {"run": {"run_id": "parent"}}}
    bus = _StopBus(by_run={"parent": _entry("parent")}, answer=ok)
    registry = _Registry()
    ws = await _stop(registry, {"run_id": "parent", "conversation_id": CONV}, bus)
    # The named run and the conversation's live follow-up (rows S31, S32).
    assert bus.sent == [("replica-a", "stop", "parent")] and registry.cancelled == ["follow-up"]
    ws.send_json.assert_awaited_once_with({"type": "run_status", "run": {"run_id": "parent"}})


async def test_stop_naming_an_unknown_run_falls_back_to_the_local_follow_up():
    bus = _StopBus()
    registry = _Registry()
    await _stop(registry, {"run_id": "gone", "conversation_id": CONV}, bus)
    # Nobody owns "gone": the conversation's run here is stopped locally.
    assert bus.lookups == [("run", "gone"), ("conversation", CONV)] and bus.sent == []
    assert registry.cancelled == ["follow-up"]


async def test_stop_by_conversation_only_is_routed_when_nothing_is_local():
    answer = {"ok": False, "code": "command_timeout", "message": "", "payload": {}}
    bus = _StopBus(by_conversation=_entry("r9"), answer=answer)
    registry = _Registry(follow_up=False)
    ws = await _stop(registry, {"conversation_id": CONV}, bus)
    assert bus.sent == [("replica-a", "stop", "r9")] and registry.cancelled == []
    assert ws.send_json.await_args.args[0]["run_id"] == "r9"


async def test_local_run_named_by_id_is_not_routed():
    bus = _StopBus()
    registry = _Registry()
    await _stop(registry, {"run_id": "follow-up", "conversation_id": CONV}, bus)
    assert bus.lookups == [] and registry.cancelled == ["follow-up"]


async def test_stop_to_an_owner_that_does_not_answer_still_stops_the_local_follow_up():
    unreachable = {"ok": False, "code": "owner_unavailable", "message": "", "payload": {}}
    bus = _StopBus(by_run={"parent": _entry("parent")}, answer=unreachable)
    registry = _Registry()
    ws = await _stop(registry, {"run_id": "parent", "conversation_id": CONV}, bus)
    # The parent's owner never answered: the conversation's run held here is
    # stopped rather than nothing, and the client is not told of an outage.
    assert bus.sent == [("replica-a", "stop", "parent")]
    assert registry.cancelled == ["follow-up"] and ws.send_json.await_count == 0


async def test_conversation_stop_to_an_owner_that_does_not_answer_is_reported():
    unreachable = {"ok": False, "code": "owner_unavailable", "message": "", "payload": {}}
    bus = _StopBus(by_conversation=_entry("r9"), answer=unreachable)
    set_run_control_bus(bus)
    try:
        ws = MagicMock()
        ws.send_json = AsyncMock()
        await stop_addressed_run(ws, _Registry(follow_up=False), USER, {"conversation_id": CONV})
    finally:
        set_run_control_bus(None)
    # The run is known to be elsewhere and cannot be reached: the client keeps
    # its running state, with the run id to stop later.
    frame = ws.send_json.await_args.args[0]
    assert frame["error_type"] == "run_control_unavailable" and frame["run_id"] == "r9"
