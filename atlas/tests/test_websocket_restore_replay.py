"""Transport-level coverage for the restore_conversation replay block (#957).

The buffer semantics and the in-flight record are covered by
``test_run_stream_replay.py``, and the full mid-stream scenario by the PR
validation script -- but the ``/ws`` frame loop itself is what decides who
gets a replay. These tests drive the real endpoint: an owner reopening a
conversation whose run has an open segment receives exactly one
``token_stream`` frame with ``replay: true``, tagged with the run's ids; a
connection naming another user's conversation id receives none.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from main import app

from atlas.application.chat.runs import get_run_registry, reset_run_registry

OWNER = "owner@example.com"
OTHER = "other@example.com"
CONVERSATION_ID = "conv-with-streaming-run"


@pytest.fixture
def mock_app_factory():
    """The same minimal factory the auth-header tests use."""
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
        mock_chat_service.handle_restore_conversation = AsyncMock(
            return_value={"type": "conversation_restored"}
        )
        mock_chat_service.end_session = AsyncMock()
        mock_chat_service.session_repository.get = AsyncMock(return_value=None)
        mock_factory.create_chat_service.return_value = mock_chat_service
        yield mock_factory


@pytest.fixture
def streaming_run(mock_app_factory):
    reset_run_registry()
    registry = get_run_registry()
    run = registry.start(conversation_id=CONVERSATION_ID, user_email=OWNER)
    registry.note_stream_token(run.run_id, "the answer so far", True, False)
    yield registry, run
    registry.cancel(run.run_id, OWNER)
    reset_run_registry()


def _connect(client, email):
    return client.websocket_connect("/ws", headers={"X-User-Email": email})


def _restore(websocket, conversation_id=CONVERSATION_ID):
    websocket.send_json({
        "type": "restore_conversation",
        "conversation_id": conversation_id,
        "messages": [],
    })


def test_restore_replays_the_open_segment_to_the_owner(streaming_run):
    _, run = streaming_run
    client = TestClient(app)

    with _connect(client, OWNER) as websocket:
        _restore(websocket)
        response = websocket.receive_json()
        replay = websocket.receive_json()

    assert response["type"] == "conversation_restored"
    assert replay["type"] == "token_stream"
    assert replay["replay"] is True
    assert replay["token"] == "the answer so far"
    assert replay["is_first"] is True
    assert replay["is_last"] is False
    assert replay["run_id"] == run.run_id
    assert replay["conversation_id"] == CONVERSATION_ID


def test_restore_sends_no_replay_to_a_non_owner(streaming_run):
    client = TestClient(app)

    with _connect(client, OTHER) as websocket:
        _restore(websocket)
        first = websocket.receive_json()
        # A second restore yields exactly one more frame -- the response --
        # proving no replay frame followed the first: the replay resolve is
        # ownership-checked, so the stranger's lookup found nothing.
        _restore(websocket)
        second = websocket.receive_json()

    assert first["type"] == "conversation_restored"
    assert second["type"] == "conversation_restored"


def test_restore_sends_no_replay_once_the_run_has_ended(streaming_run):
    registry, run = streaming_run
    registry.set_status(run.run_id, registry.get(run.run_id).status.COMPLETED)
    client = TestClient(app)

    with _connect(client, OWNER) as websocket:
        _restore(websocket)
        first = websocket.receive_json()
        _restore(websocket)
        second = websocket.receive_json()

    # A terminal run's buffer is cleared with the turn: there is nothing left
    # to replay, and the stored record is what the client loads instead.
    assert first["type"] == "conversation_restored"
    assert second["type"] == "conversation_restored"
