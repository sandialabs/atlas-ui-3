"""WebSocket wiring for context compaction and the context meter (issue #877)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from main import app

from atlas.application.chat.runs import get_run_registry, reset_run_registry
from atlas.domain.errors import ValidationError

USER = "owner@example.com"
CONV = "conv-compact"


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
        service.report_context_usage = AsyncMock(return_value=None)
        service.end_session = AsyncMock()
        service.session_repository.get = AsyncMock(return_value=None)
        mock_factory.create_chat_service.return_value = service
        reset_run_registry()
        yield service
        reset_run_registry()


def _connect(client):
    return client.websocket_connect("/ws", headers={"X-User-Email": USER})


def test_compact_runs_as_a_checked_turn(chat_service):
    chat_service.handle_chat_message = AsyncMock(side_effect=ValidationError(
        "This conversation was saved under CUI and cannot be continued under UUR.",
        code="conversation_classification",
    ))
    with _connect(TestClient(app)) as ws:
        ws.send_json({
            "type": "compact_context", "model": "m", "conversation_id": f"  {CONV} ",
            "focus": "decisions", "compliance_level_filter": "UUR", "auto_compact": False,
        })
        frame = ws.receive_json()
    assert frame["error_type"] == "conversation_classification"
    kwargs = chat_service.handle_chat_message.await_args.kwargs
    assert kwargs["compact_only"] is True
    assert kwargs["content"] == ""
    assert kwargs["compact_focus"] == "decisions"
    assert kwargs["compliance_level"] == "UUR"
    assert kwargs["conversation_id"] == CONV
    assert kwargs["user_email"] == USER
    assert kwargs["auto_compact"] is False


def test_compact_refused_while_a_run_is_active(chat_service):
    get_run_registry().start(conversation_id=CONV, user_email=USER, steering=None, title="t")
    with _connect(TestClient(app)) as ws:
        ws.send_json({"type": "compact_context", "model": "m", "conversation_id": CONV})
        frame = ws.receive_json()
    assert frame["type"] == "context_compaction"
    assert frame["status"] == "failed"
    chat_service.handle_chat_message.assert_not_awaited()


def test_compact_refused_for_another_users_conversation(chat_service):
    from atlas.domain.errors import AuthorizationError
    chat_service.validate_conversation_id_owner = MagicMock(side_effect=AuthorizationError("not yours"))
    with _connect(TestClient(app)) as ws:
        ws.send_json({"type": "compact_context", "model": "m", "conversation_id": CONV})
        frame = ws.receive_json()
    assert frame["type"] == "error"
    chat_service.handle_chat_message.assert_not_awaited()


def test_usage_request_is_forwarded(chat_service):
    async def report(session_id, model, user_email, auto_compact=None, conversation_id=None):
        return None

    chat_service.report_context_usage = AsyncMock(side_effect=report)
    with _connect(TestClient(app)) as ws:
        ws.send_json({"type": "context_usage_request", "model": "m", "auto_compact": True,
                      "conversation_id": CONV})
        # A follow-up frame the server answers proves the request was handled.
        ws.send_json({"type": "compact_context", "model": "m", "conversation_id": CONV})
    args = chat_service.report_context_usage.await_args
    assert args.args[1:] == ("m", USER)
    assert args.kwargs == {"auto_compact": True, "conversation_id": CONV}


def test_chat_forwards_auto_toggle(chat_service):
    with _connect(TestClient(app)) as ws:
        ws.send_json({"type": "chat", "content": "hi", "model": "m", "auto_compact": False})
        ws.send_json({"type": "compact_context", "model": "m"})
    calls = [c.kwargs for c in chat_service.handle_chat_message.await_args_list]
    assert calls and calls[0]["auto_compact"] is False


def test_chat_refused_while_compacting(chat_service):
    import asyncio

    release = asyncio.Event()

    async def handle(**kwargs):
        if kwargs.get("compact_only"):
            await release.wait()
        return {}

    chat_service.handle_chat_message = AsyncMock(side_effect=handle)
    with _connect(TestClient(app)) as ws:
        ws.send_json({"type": "compact_context", "model": "m", "conversation_id": CONV})
        ws.send_json({"type": "chat", "content": "hi", "model": "m", "conversation_id": CONV})
        frame = ws.receive_json()
        assert frame["error_type"] == "compaction_in_progress"
        ws.send_json({"type": "stop_streaming"})
    calls = [c.kwargs for c in chat_service.handle_chat_message.await_args_list]
    assert all(c.get("compact_only") for c in calls)


def test_refusal_ends_pending_compaction(chat_service):
    chat_service.handle_chat_message = AsyncMock(side_effect=ValidationError("no level"))
    with _connect(TestClient(app)) as ws:
        ws.send_json({"type": "compact_context", "model": "m", "conversation_id": CONV})
        first, second = ws.receive_json(), ws.receive_json()
    assert first["type"] == "error"
    assert second == {"type": "context_compaction", "status": "failed", "trigger": "manual",
                      "silent": True, "conversation_id": CONV}


def test_usage_requests_are_throttled(chat_service):
    with _connect(TestClient(app)) as ws:
        for _ in range(3):
            ws.send_json({"type": "context_usage_request", "model": "m", "conversation_id": CONV})
        ws.send_json({"type": "compact_context", "model": "m", "conversation_id": CONV})
    assert chat_service.report_context_usage.await_count == 1
