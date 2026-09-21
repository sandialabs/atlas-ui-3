"""Endpoint-level refusal of a foreign in-flight conversation id (#884).

The registry-level ownership lookups are covered by
``test_run_in_flight_conversation.py``, and the full two-user scenario by the
PR validation script -- but both run below the transport. This exercises the
``/ws`` frame loop itself: a chat frame naming a conversation another user's
run is executing under must be refused with an ``authorization`` error before
a run is admitted, because in that window the conversation is not stored and
whoever saves first wins the id. The same frame from the run's own owner is
not refused; it meets the ordinary conversation-busy guard.
"""

import pytest
from fastapi.testclient import TestClient
from main import _run_title_from_frame, app

from atlas.application.chat.runs import get_run_registry, reset_run_registry

OWNER = "owner@example.com"
OTHER = "other@example.com"
CONVERSATION_ID = "conv-owned-by-owner"


def test_run_title_from_frame_handles_non_string_content():
    """Titles come from the first text part; anything else yields no title."""
    assert _run_title_from_frame({"content": "  hello  "}) == "hello"
    assert _run_title_from_frame({
        "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
            {"type": "text", "text": "  second part  "},
        ]
    }) == "second part"
    assert _run_title_from_frame({"content": [{"type": "image_url"}]}) is None
    assert _run_title_from_frame({"content": 42}) is None
    assert _run_title_from_frame({}) is None

OWNER = "owner@example.com"
OTHER = "other@example.com"
CONVERSATION_ID = "conv-owned-by-owner"


@pytest.fixture
def mock_app_factory():
    """The same minimal factory the auth-header tests use."""
    from unittest.mock import AsyncMock, MagicMock, patch

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
def registry_with_foreign_run():
    reset_run_registry()
    registry = get_run_registry()
    run = registry.start(conversation_id=CONVERSATION_ID, user_email=OWNER)
    yield registry
    registry.cancel(run.run_id, OWNER)
    reset_run_registry()


def _connect(client, email):
    return client.websocket_connect("/ws", headers={"X-User-Email": email})


def test_a_foreign_in_flight_conversation_id_is_refused(
    mock_app_factory, registry_with_foreign_run
):
    client = TestClient(app)

    with _connect(client, OTHER) as websocket:
        websocket.send_json({
            "type": "chat",
            "content": "hello",
            "conversation_id": CONVERSATION_ID,
        })
        reply = websocket.receive_json()

    assert reply["type"] == "error"
    assert reply["error_type"] == "authorization"
    assert reply["conversation_id"] == CONVERSATION_ID
    # Refused before admission: no run exists for the attacker.
    assert registry_with_foreign_run.active_for_user(OTHER) == []


def test_a_padded_foreign_id_is_refused_the_same_way(
    mock_app_factory, registry_with_foreign_run
):
    """Normalization happens before the guard, so padding cannot slip past."""
    client = TestClient(app)

    with _connect(client, OTHER) as websocket:
        websocket.send_json({
            "type": "chat",
            "content": "hello",
            "conversation_id": f"  {CONVERSATION_ID}  ",
        })
        reply = websocket.receive_json()

    assert reply["error_type"] == "authorization"
    assert reply["conversation_id"] == CONVERSATION_ID


def test_the_owner_meets_the_busy_guard_not_the_ownership_refusal(
    mock_app_factory, registry_with_foreign_run
):
    client = TestClient(app)

    with _connect(client, OWNER) as websocket:
        websocket.send_json({
            "type": "chat",
            "content": "hello again",
            "conversation_id": CONVERSATION_ID,
        })
        reply = websocket.receive_json()

    # The run's own user is never refused as foreign; the frame proceeds to
    # the ordinary steering/busy routing for a conversation that already has
    # a run.
    assert reply["error_type"] == "conversation_busy"


def test_a_differently_cased_owner_email_is_still_the_owner(
    mock_app_factory, registry_with_foreign_run
):
    """The registry compares emails the way the conversation repository does:
    case-insensitively. A proxy handing the socket ``Owner@Example.com`` when
    the run was admitted under ``owner@example.com`` must not read as foreign
    -- and a stranger with a case-variant of someone else's email must not
    read as the owner."""
    client = TestClient(app)

    with _connect(client, OWNER.upper()) as websocket:
        websocket.send_json({
            "type": "chat",
            "content": "hello again",
            "conversation_id": CONVERSATION_ID,
        })
        reply = websocket.receive_json()

    assert reply["error_type"] == "conversation_busy"

    with _connect(client, OWNER.upper() + ".not") as websocket:
        websocket.send_json({
            "type": "chat",
            "content": "hello",
            "conversation_id": CONVERSATION_ID,
        })
        reply = websocket.receive_json()

    assert reply["error_type"] == "authorization"


def test_multimodal_content_does_not_crash_run_admission(
    mock_app_factory, registry_with_foreign_run
):
    """A multimodal turn carries a list as ``content``; admission must title
    the run from its first text part, not raise on ``str.strip``."""
    client = TestClient(app)
    registry = registry_with_foreign_run

    with _connect(client, OWNER) as websocket:
        websocket.send_json({
            "type": "chat",
            "agent_mode": True,
            "save_mode": "server",
            "selected_tools": ["atlas_sleep"],
            "content": [
                {"type": "text", "text": "summarize this image"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
            ],
            # A different conversation: not owned by the seeded run, so the
            # frame is admitted as a fresh run rather than refused or steered.
            "conversation_id": "conv-fresh-multimodal",
        })
        reply = websocket.receive_json()

    assert reply["type"] == "run_started"
    assert reply["title"] == "summarize this image"
    record = registry.get(reply["run_id"])
    assert record is not None
    assert record.title == "summarize this image"
