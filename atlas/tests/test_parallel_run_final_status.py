"""A run's terminal status comes from its own turn, not the latest one (#1014).

The ``/ws`` frame loop builds a fresh ``turn_failure`` list for every chat
frame. ``handle_chat`` records a swallowed domain failure (rate limit,
timeout, ...) in that list, and ``handle_chat_guarded`` reads it when the run
ends to decide between ``completed`` and ``failed``. With parallel runs a
newer chat frame rebinds the loop variable while an older run is still in
flight, so the guard must read the list it was started with. Otherwise a run
that died on a rate limit is recorded as ``completed`` -- or a run that
succeeded is recorded as ``failed`` with another turn's error.

These tests drive the real frame loop with two runs on one socket and finish
them out of order.
"""

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient
from main import app

from atlas.application.chat.runs import RunStatus, get_run_registry, reset_run_registry
from atlas.domain.errors import RateLimitError

USER = "parallel@example.com"
CONV_A = "conv-run-a"
CONV_B = "conv-run-b"


@pytest.fixture
def gates():
    """One gate per conversation: the run's turn blocks until it is opened,
    then fails or succeeds as the test chose."""
    return {
        CONV_A: {"go": threading.Event(), "fail": False},
        CONV_B: {"go": threading.Event(), "fail": False},
    }


@pytest.fixture
def mock_app_factory(gates):
    from unittest.mock import AsyncMock, MagicMock, patch

    async def handle_chat_message(**kwargs):
        gate = gates[kwargs["conversation_id"]]
        # The app runs on TestClient's own loop thread; a threading.Event
        # polled with a short sleep is what lets the test thread release it.
        while not gate["go"].is_set():
            await asyncio.sleep(0.01)
        if gate["fail"]:
            raise RateLimitError(f"rate limited in {kwargs['conversation_id']}")
        return {}

    with patch('main.app_factory') as mock_factory:
        mock_config = MagicMock()
        mock_config.app_settings.test_user = None
        mock_config.app_settings.debug_mode = False
        mock_config.app_settings.auth_user_header = 'X-User-Email'
        mock_config.app_settings.auth_user_header_type = 'header'
        mock_config.app_settings.auth_aws_expected_alb_arn = ''
        mock_config.app_settings.auth_aws_region = 'us-east-1'
        mock_config.app_settings.feature_proxy_secret_enabled = False
        mock_config.app_settings.feature_websocket_origin_check_enabled = False
        mock_config.app_settings.max_concurrent_runs_per_user = 3
        mock_factory.get_config_manager.return_value = mock_config

        mock_chat_service = MagicMock()
        mock_chat_service.handle_chat_message = AsyncMock(side_effect=handle_chat_message)
        mock_chat_service.end_session = AsyncMock()
        mock_chat_service.session_repository.get = AsyncMock(return_value=None)
        mock_chat_service.session_repository.delete = AsyncMock()
        mock_factory.create_chat_service.return_value = mock_chat_service
        yield mock_factory


@pytest.fixture
def registry():
    reset_run_registry()
    yield get_run_registry()
    reset_run_registry()


def _chat_frame(conversation_id):
    # Agent mode + server save mode + a tool: what makes a turn a tracked run.
    return {
        "type": "chat",
        "content": f"work in {conversation_id}",
        "agent_mode": True,
        "save_mode": "server",
        "selected_tools": ["atlas_sleep"],
        "conversation_id": conversation_id,
    }


def _receive(websocket):
    """Next frame that is not a ``run_status`` broadcast (those interleave
    with everything and are checked through the registry instead)."""
    while True:
        frame = websocket.receive_json()
        if frame.get("type") != "run_status":
            return frame


def _start(websocket, conversation_id):
    websocket.send_json(_chat_frame(conversation_id))
    reply = _receive(websocket)
    assert reply["type"] == "run_started", reply
    assert reply["conversation_id"] == conversation_id
    return reply["run_id"]


def _wait_terminal(registry, run_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = registry.get(run_id)
        if record is not None and record.status in (
            RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED
        ):
            return record
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} never reached a terminal status")


def test_older_run_that_fails_after_a_newer_one_starts_is_recorded_failed(
    mock_app_factory, registry, gates
):
    gates[CONV_A]["fail"] = True
    client = TestClient(app)

    with client.websocket_connect("/ws", headers={"X-User-Email": USER}) as ws:
        run_a = _start(ws, CONV_A)
        # The second frame rebinds the loop's per-turn state while A runs.
        run_b = _start(ws, CONV_B)

        gates[CONV_A]["go"].set()
        error = _receive(ws)
        assert error["type"] == "error"
        assert error["error_type"] == "rate_limit"
        assert error["conversation_id"] == CONV_A

        record_a = _wait_terminal(registry, run_a)
        assert record_a.status == RunStatus.FAILED
        assert record_a.error == "rate_limit"

        gates[CONV_B]["go"].set()
        record_b = _wait_terminal(registry, run_b)

    assert record_b.status == RunStatus.COMPLETED
    assert record_b.error is None


def test_older_run_that_succeeds_is_not_charged_with_a_newer_runs_failure(
    mock_app_factory, registry, gates
):
    gates[CONV_B]["fail"] = True
    client = TestClient(app)

    with client.websocket_connect("/ws", headers={"X-User-Email": USER}) as ws:
        run_a = _start(ws, CONV_A)
        run_b = _start(ws, CONV_B)

        # B fails first, filling the list the loop variable now points at.
        gates[CONV_B]["go"].set()
        error = _receive(ws)
        assert error["error_type"] == "rate_limit"
        assert error["conversation_id"] == CONV_B
        record_b = _wait_terminal(registry, run_b)
        assert record_b.status == RunStatus.FAILED
        assert record_b.error == "rate_limit"

        gates[CONV_A]["go"].set()
        record_a = _wait_terminal(registry, run_a)

    assert record_a.status == RunStatus.COMPLETED
    assert record_a.error is None
