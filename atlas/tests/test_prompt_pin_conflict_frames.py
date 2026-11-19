"""Pin-conflict error frames carry their conversation's identity (issue #1169).

A ``prompt_pin_conflict`` error tells the client to adopt another window's
team-prompt selection. Without the conversation id (and run id) on the frame,
a client that has switched conversations while the turn was in flight would
apply that selection to whatever conversation is on screen. This drives the
``/ws`` frame loop with the chat service stubbed to raise the conflict.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from main import app

from atlas.application.chat.runs import reset_run_registry
from atlas.domain.conversation_prompt_pin import PromptPinConflictError

USER = "member@example.com"
PIN = {"prompt_id": "p1", "version_id": "v2", "version_number": 2, "title": "Team reviewer"}


@pytest.fixture
def conflicting_service():
    reset_run_registry()
    with patch("main.app_factory") as factory:
        config = MagicMock()
        config.app_settings.test_user = None
        config.app_settings.debug_mode = False
        config.app_settings.auth_user_header = "X-User-Email"
        config.app_settings.auth_user_header_type = "header"
        config.app_settings.auth_aws_expected_alb_arn = ""
        config.app_settings.auth_aws_region = "us-east-1"
        config.app_settings.feature_proxy_secret_enabled = False
        config.app_settings.feature_websocket_origin_check_enabled = False
        config.app_settings.max_concurrent_runs_per_user = 3
        factory.get_config_manager.return_value = config
        service = MagicMock()

        async def conflict(**kwargs):
            raise PromptPinConflictError(
                {"pin": PIN, "revision": 2}, conversation_id=kwargs.get("conversation_id")
            )

        service.handle_chat_message = AsyncMock(side_effect=conflict)
        service.end_session = AsyncMock()
        service.session_repository.get = AsyncMock(return_value=None)
        factory.create_chat_service.return_value = service
        yield service
    reset_run_registry()


def _error_frame(ws):
    while True:
        frame = ws.receive_json()
        if frame.get("type") == "error":
            return frame


def test_a_pin_conflict_frame_names_its_conversation_and_run(conflicting_service):
    client = TestClient(app)
    with client.websocket_connect("/ws", headers={"X-User-Email": USER}) as ws:
        ws.send_json({"type": "chat", "content": "hi", "model": "m", "conversation_id": "conv-a",
                      "library_prompt": None, "library_prompt_revision": 1})
        frame = _error_frame(ws)
    assert frame["error_type"] == "prompt_pin_conflict"
    assert frame["conversation_id"] == "conv-a"
    assert isinstance(frame.get("run_id"), str) and frame["run_id"]
    assert frame["prompt_library"] == PIN and frame["prompt_library_pin_revision"] == 2


def test_conflicts_for_two_conversations_stay_with_their_own_conversation(conflicting_service):
    client = TestClient(app)
    with client.websocket_connect("/ws", headers={"X-User-Email": USER}) as ws:
        ws.send_json({"type": "chat", "content": "a", "model": "m", "conversation_id": "conv-a",
                      "library_prompt": None, "library_prompt_revision": 1})
        first = _error_frame(ws)
        ws.send_json({"type": "chat", "content": "b", "model": "m", "conversation_id": "conv-b",
                      "library_prompt": None, "library_prompt_revision": 1})
        second = _error_frame(ws)
    assert first["conversation_id"] == "conv-a" and second["conversation_id"] == "conv-b"
    assert first["run_id"] != second["run_id"]


def test_the_conflict_names_the_conversation_it_concerns_over_the_envelope():
    """The error carries the conversation the pin belongs to; the run envelope
    never overwrites an id a frame already has (tag_event)."""
    from atlas.application.chat.runs.context import tag_event

    err = PromptPinConflictError({"pin": None, "revision": 3}, conversation_id="conv-pinned")
    assert err.conversation_id == "conv-pinned"
    frame = tag_event({"type": "error", "conversation_id": err.conversation_id}, "run-1", "conv-other")
    assert frame["conversation_id"] == "conv-pinned" and frame["run_id"] == "run-1"
