"""The /ws frame loop for reopened-run approvals (issue #1122).

Below the transport the approval decision and the projection are covered by
``test_reopened_run_approval.py``. This drives the real websocket endpoint:
the first ``tool_approval_response`` resolves the waiting call and resumes
the run, a duplicate gets ``tool_approval_settled`` instead of an
unknown-approval error, an unknown response gets the error that names it,
and ``list_runs`` reports the run without replaying a card.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from main import app

from atlas.application.chat.approval_manager import get_approval_manager
from atlas.application.chat.runs import RunStatus, get_run_registry, reset_run_registry
from atlas.application.chat.runs.execution_state import (
    MemoryExecutionStore,
    invocation_id,
    reset_execution_store,
    set_execution_store,
)

USER = "owner@example.com"
CONV = "conv-ws-approval"


@pytest.fixture
def mock_app_factory():
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
        mock_chat_service.handle_chat_message = AsyncMock(return_value={})
        mock_chat_service.end_session = AsyncMock()
        mock_chat_service.session_repository.get = AsyncMock(return_value=None)
        mock_factory.create_chat_service.return_value = mock_chat_service
        yield mock_factory


@pytest.fixture
def paused_run():
    """A run waiting on one approval, as the executor leaves it."""
    reset_run_registry()
    store = MemoryExecutionStore()
    set_execution_store(store)
    registry = get_run_registry()
    run = registry.start(conversation_id=CONV, user_email=USER)
    registry.set_status(run.run_id, RunStatus.RUNNING)
    inv = invocation_id(run.run_id, 0, "call_0")
    frame = {
        "type": "tool_approval_request", "tool_call_id": "call_0", "tool_name": "search",
        "arguments": {"q": "x"}, "run_id": run.run_id, "conversation_id": CONV,
        "invocation_id": inv, "step": 0,
    }
    registry.note_event(run.run_id, frame)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(store.apply_event({**frame, "user_email": USER}))
    request = get_approval_manager().create_approval_request(
        "call_0", "search", {"q": "x"}, user_email=USER, invocation_id=inv, run_id=run.run_id,
    )
    yield registry, run, inv, request
    get_approval_manager().cleanup_request(request.storage_key)
    registry.cancel(run.run_id, USER)
    reset_run_registry()
    reset_execution_store()
    asyncio.set_event_loop(None)
    loop.close()


def _until(ws, wanted):
    """Frames up to and including the first of type ``wanted``."""
    frames = []
    for _ in range(20):
        frame = ws.receive_json()
        frames.append(frame)
        if frame.get("type") == wanted:
            return frames
    raise AssertionError(f"no {wanted} frame in {frames}")


def test_duplicate_response_is_settled_and_list_runs_replays_no_card(mock_app_factory, paused_run):
    registry, run, inv, request = paused_run
    response = {
        "type": "tool_approval_response", "tool_call_id": "call_0", "invocation_id": inv,
        "run_id": run.run_id, "conversation_id": CONV, "approved": True, "arguments": {"q": "x"},
    }
    client = TestClient(app)
    with client.websocket_connect("/ws", headers={"X-User-Email": USER}) as ws:
        ws.send_json(response)
        ws.send_json({"type": "list_runs"})
        frames = _until(ws, "runs_snapshot")
        # Accepted: no reply frame for the approval itself, and no card replay.
        assert not [f for f in frames if f.get("type") in ("error", "tool_approval_request")]
        assert request.future.done() and request.future.result()["approved"] is True
        snapshot = frames[-1]["runs"]
        assert [r["run_id"] for r in snapshot] == [run.run_id]
        assert snapshot[0]["status"] == "running"
        assert registry.get(run.run_id).pending_requests == []

        # The same response again: settled, not "No matching pending tool approval".
        ws.send_json(response)
        settled = _until(ws, "tool_approval_settled")[-1]
        assert settled["invocation_id"] == inv
        assert settled["approved"] is True
        assert settled["status"] == "approved"

        # A response for an invocation nobody is waiting on names itself.
        ws.send_json({**response, "invocation_id": invocation_id(run.run_id, 9, "call_0")})
        error = _until(ws, "error")[-1]
        assert error["error_type"] == "unknown_tool_approval"
        assert error["invocation_id"] == invocation_id(run.run_id, 9, "call_0")
