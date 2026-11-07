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
