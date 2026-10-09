"""WebSocket wiring for conversation classification boundaries (issue #1042).

The rule lives in the service (test_conversation_classification_boundaries);
these pin that the transport hands it the client's level on restore, reports a
refusal with its own error type, and refuses a steer at another level instead
of injecting it into the running loop.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from main import app

from atlas.application.chat.runs import get_run_registry, reset_run_registry
from atlas.application.chat.service import UNSET
from atlas.domain.errors import ValidationError

USER = "owner@example.com"
CONV = "conv-classified"


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
        yield service
        reset_run_registry()


def _connect(client):
    return client.websocket_connect("/ws", headers={"X-User-Email": USER})


def test_restore_forwards_the_level_and_sends_only_the_refusal(chat_service):
    chat_service.handle_restore_conversation = AsyncMock(return_value={
        "type": "error",
        "message": "This conversation was saved under CUI and cannot be continued under UUR.",
        "error_type": "conversation_classification",
        "conversation_id": CONV,
    })
    with _connect(TestClient(app)) as ws:
        ws.send_json({
            "type": "restore_conversation", "conversation_id": CONV,
            "messages": [], "compliance_level_filter": "UUR",
        })
        frame = ws.receive_json()
    assert frame["error_type"] == "conversation_classification"
    assert chat_service.handle_restore_conversation.await_args.kwargs["compliance_level"] == "UUR"


def test_restore_without_a_level_forwards_unset(chat_service):
    chat_service.handle_restore_conversation = AsyncMock(
        return_value={"type": "conversation_restored"}
    )
    with _connect(TestClient(app)) as ws:
        ws.send_json({"type": "restore_conversation", "conversation_id": CONV, "messages": []})
        ws.receive_json()
    assert chat_service.handle_restore_conversation.await_args.kwargs["compliance_level"] is UNSET


def test_refused_turn_reports_the_classification_error_type(chat_service):
    chat_service.handle_chat_message = AsyncMock(side_effect=ValidationError(
        "This conversation was saved under CUI and cannot be continued under UUR.",
        code="conversation_classification",
    ))
    with _connect(TestClient(app)) as ws:
        ws.send_json({
            "type": "chat", "content": "synthetic", "model": "m",
            "conversation_id": CONV, "compliance_level_filter": "UUR",
        })
        frame = ws.receive_json()
        while frame.get("type") != "error":
            frame = ws.receive_json()
    assert frame["error_type"] == "conversation_classification"


def test_steer_at_another_level_is_refused_not_injected(chat_service):
    registry = get_run_registry()
    run = registry.start(conversation_id=CONV, user_email=USER)
    steering = MagicMock()
    steering.active = True
    run.steering = steering
    chat_service.steering_classification_refusal = AsyncMock(
        return_value="The running turn is working under CUI."
    )
    try:
        with _connect(TestClient(app)) as ws:
            ws.send_json({
                "type": "chat", "content": "synthetic steer", "model": "m",
                "conversation_id": CONV, "compliance_level_filter": "UUR",
            })
            frame = ws.receive_json()
            while frame.get("type") != "error":
                frame = ws.receive_json()
    finally:
        registry.cancel(run.run_id, USER)
    assert frame["error_type"] == "conversation_classification"
    assert frame["run_id"] == run.run_id
    # Marked as a steer refusal so the client does not end the running turn.
    assert frame["steering"] is True
    steering.queue.put_nowait.assert_not_called()
    args = chat_service.steering_classification_refusal.await_args.args
    assert args == (run.session_id, "UUR")
