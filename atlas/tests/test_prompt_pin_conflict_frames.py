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


# --- Review finding 5: a blocking pin is recoverable from the browser -----------


@pytest.fixture
def blocked_service(conflicting_service):
    from atlas.domain.conversation_prompt_pin import PromptPinBlockedError
    from atlas.domain.errors import AuthorizationError

    async def blocked(**kwargs):
        conversation_id = kwargs.get("conversation_id")
        if conversation_id == "conv-plain":
            raise AuthorizationError("The selected team prompt is not available. Choose another prompt.")
        raise PromptPinBlockedError(
            f"pinned ... (DELETE /api/prompts/pins/{conversation_id})", conversation_id,
            "damaged" if conversation_id == "conv-damaged" else "unavailable",
        )

    conflicting_service.handle_chat_message = AsyncMock(side_effect=blocked)
    return conflicting_service


def test_a_blocking_pin_frame_offers_the_owner_clear_for_its_conversation(blocked_service):
    client = TestClient(app)
    with client.websocket_connect("/ws", headers={"X-User-Email": USER}) as ws:
        ws.send_json({"type": "chat", "content": "hi", "model": "m", "conversation_id": "conv-a"})
        unavailable = _error_frame(ws)
        ws.send_json({"type": "chat", "content": "hi", "model": "m", "conversation_id": "conv-damaged"})
        damaged = _error_frame(ws)
        ws.send_json({"type": "chat", "content": "hi", "model": "m", "conversation_id": "conv-plain"})
        plain = _error_frame(ws)
    assert unavailable["error_type"] == "authorization"  # unchanged for existing clients
    assert unavailable["recovery"] == "clear_team_prompt" and unavailable["pin_problem"] == "unavailable"
    assert unavailable["conversation_id"] == "conv-a" and unavailable["run_id"]
    assert "DELETE /api/prompts/pins/conv-a" in unavailable["message"]  # the CLI text stays
    assert damaged["pin_problem"] == "damaged" and damaged["conversation_id"] == "conv-damaged"
    # A refused *new* selection (no stored pin) offers no clear: choosing another prompt is the way out.
    assert "recovery" not in plain


@pytest.mark.asyncio
async def test_only_a_stored_pin_raises_the_recoverable_refusal():
    from atlas.application.chat import prompt_library_turn as library_turn
    from atlas.domain.conversation_prompt_pin import PromptPinBlockedError
    from atlas.domain.errors import AuthorizationError

    context = {"conversation_id": "conv-1", library_turn.PIN_CONTEXT_KEY: dict(PIN)}
    assert isinstance(library_turn._pin_refusal(context, True, None, True), PromptPinBlockedError)
    not_stored = library_turn._pin_refusal(context, True, None, False)  # an unsaved / incognito session
    assert type(not_stored) is AuthorizationError
    new_selection = library_turn._pin_refusal(context, False, "Not available.", True)
    assert type(new_selection) is AuthorizationError and new_selection.message == "Not available."


# --- Operational monitoring: distinct pin-failure metrics -----------------


def test_pin_conflict_emits_distinct_metric(conflicting_service):
    with patch("main.log_metric") as metrics:
        with TestClient(app).websocket_connect(
            "/ws", headers={"X-User-Email": USER}
        ) as ws:
            ws.send_json({
                "type": "chat",
                "content": "hi",
                "model": "m",
                "conversation_id": "conv-a",
                "library_prompt": None,
                "library_prompt_revision": 1,
            })
            frame = _error_frame(ws)

    assert frame["error_type"] == "prompt_pin_conflict"
    metrics.assert_any_call(
        "error", USER, error_type="prompt_pin_conflict"
    )


def test_blocked_pin_emits_recoverable_metric(blocked_service):
    with patch("main.log_metric") as metrics:
        with TestClient(app).websocket_connect(
            "/ws", headers={"X-User-Email": USER}
        ) as ws:
            ws.send_json({
                "type": "chat",
                "content": "hi",
                "model": "m",
                "conversation_id": "conv-a",
            })
            frame = _error_frame(ws)

    assert frame["recovery"] == "clear_team_prompt"
    metrics.assert_any_call(
        "error", USER,
        error_type="prompt_pin_blocked",
        pin_problem="unavailable",
    )


def test_regular_authorization_error_keeps_its_metric(blocked_service):
    with patch("main.log_metric") as metrics:
        with TestClient(app).websocket_connect(
            "/ws", headers={"X-User-Email": USER}
        ) as ws:
            ws.send_json({
                "type": "chat",
                "content": "hi",
                "model": "m",
                "conversation_id": "conv-plain",
            })
            frame = _error_frame(ws)

    assert frame["error_type"] == "authorization"
    assert "recovery" not in frame
    metrics.assert_any_call(
        "error", USER, error_type="authorization"
    )
