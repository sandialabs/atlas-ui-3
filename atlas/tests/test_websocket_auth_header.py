"""
Tests for WebSocket authentication using the configured authentication header.

These tests validate that the backend correctly extracts the user email from the
configured authentication header (default: X-User-Email) for WebSocket connections,
which is critical for the production authentication flow where the reverse proxy
sets this header. The tests also ensure that fallback mechanisms (query parameter,
test user from config) work as expected, and that the header takes precedence when
both are present.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from main import app


@pytest.fixture
def mock_app_factory():
    """Mock app factory to avoid initializing full application."""
    with patch('main.app_factory') as mock_factory:
        # Mock config manager
        mock_config = MagicMock()
        mock_config.app_settings.test_user = 'test@test.com'
        mock_config.app_settings.debug_mode = False
        mock_config.app_settings.auth_user_header = 'X-User-Email'
        mock_config.app_settings.feature_proxy_secret_enabled = False
        mock_factory.get_config_manager.return_value = mock_config

        # Mock chat service
        mock_chat_service = MagicMock()
        mock_chat_service.handle_chat_message = AsyncMock(return_value={})
        mock_chat_service.handle_attach_file = AsyncMock(return_value={'type': 'file_attach', 'success': True})
        mock_chat_service.end_session = MagicMock()
        mock_factory.create_chat_service.return_value = mock_chat_service

        yield mock_factory


def test_websocket_uses_x_user_email_header(mock_app_factory):
    """Test that WebSocket connection uses X-User-Email header for authentication."""
    client = TestClient(app)

    # Connect with X-User-Email header
    with client.websocket_connect("/ws", headers={"X-User-Email": "alice@example.com"}) as websocket:
        # Send a test message
        websocket.send_json({"type": "attach_file", "s3_key": "users/alice@example.com/test.txt"})

        # Verify that the connection was created with the correct user from header
        # The user_email should be extracted from X-User-Email header
        call_args = mock_app_factory.create_chat_service.call_args
        connection_adapter = call_args[0][0]  # First positional argument

        # The connection adapter should have been created with alice@example.com
        assert connection_adapter.user_email == "alice@example.com"


def test_websocket_rejects_unauthenticated_in_production(mock_app_factory):
    """Test that WebSocket rejects connections without auth header in production mode."""
    from starlette.websockets import WebSocketDisconnect

    # Ensure debug_mode is False (production)
    mock_app_factory.get_config_manager.return_value.app_settings.debug_mode = False

    client = TestClient(app)

    # Connect without header - should be rejected with 1008 (Policy Violation)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws"):
            pass

    assert exc_info.value.code == 1008
    assert "Authentication required" in exc_info.value.reason


def test_websocket_rejects_query_param_in_production(mock_app_factory):
    """Test that WebSocket ignores query param auth in production mode."""
    from starlette.websockets import WebSocketDisconnect

    # Ensure debug_mode is False (production)
    mock_app_factory.get_config_manager.return_value.app_settings.debug_mode = False

    client = TestClient(app)

    # Connect with query param but no header - should be rejected in production
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws?user=bob@example.com"):
            pass

    assert exc_info.value.code == 1008
    assert "Authentication required" in exc_info.value.reason


@pytest.fixture
def mock_app_factory_debug_mode():
    """Mock app factory with debug mode enabled."""
    with patch('main.app_factory') as mock_factory:
        # Mock config manager with debug_mode=True
        mock_config = MagicMock()
        mock_config.app_settings.test_user = 'test@test.com'
        mock_config.app_settings.debug_mode = True  # Debug mode enabled
        mock_config.app_settings.auth_user_header = 'X-User-Email'
        mock_config.app_settings.feature_proxy_secret_enabled = False
        mock_factory.get_config_manager.return_value = mock_config

        # Mock chat service
        mock_chat_service = MagicMock()
        mock_chat_service.handle_chat_message = AsyncMock(return_value={})
        mock_chat_service.handle_attach_file = AsyncMock(return_value={'type': 'file_attach', 'success': True})
        mock_chat_service.end_session = MagicMock()
        mock_factory.create_chat_service.return_value = mock_chat_service

        yield mock_factory


def test_websocket_fallback_to_query_param_debug_mode(mock_app_factory_debug_mode):
    """Test that WebSocket falls back to query param in debug mode only."""
    client = TestClient(app)

    # Connect without header but with query param - should work in debug mode
    with client.websocket_connect("/ws?user=bob@example.com") as websocket:
        # Send a test message
        websocket.send_json({"type": "attach_file", "s3_key": "users/bob@example.com/test.txt"})

        # Get the chat service instance
        call_args = mock_app_factory_debug_mode.create_chat_service.call_args
        connection_adapter = call_args[0][0]

        # Should use query param in debug mode
        assert connection_adapter.user_email == "bob@example.com"


def test_websocket_fallback_to_test_user_debug_mode(mock_app_factory_debug_mode):
    """Test that WebSocket falls back to test user in debug mode only."""
    client = TestClient(app)

    # Connect without header or query param - should work in debug mode
    with client.websocket_connect("/ws") as websocket:
        # Send a test message
        websocket.send_json({"type": "attach_file", "s3_key": "users/test@test.com/test.txt"})

        # Get the chat service instance
        call_args = mock_app_factory_debug_mode.create_chat_service.call_args
        connection_adapter = call_args[0][0]

        # Should use test user from config in debug mode
        assert connection_adapter.user_email == "test@test.com"


def test_websocket_header_takes_precedence_over_query_param(mock_app_factory):
    """Test that X-User-Email header takes precedence over query parameter."""
    client = TestClient(app)

    # Connect with both header and query param (header should win)
    with client.websocket_connect(
        "/ws?user=wrong@example.com",
        headers={"X-User-Email": "correct@example.com"}
    ) as websocket:
        # Send a test message
        websocket.send_json({"type": "attach_file", "s3_key": "users/correct@example.com/test.txt"})

        # Get the chat service instance
        call_args = mock_app_factory.create_chat_service.call_args
        connection_adapter = call_args[0][0]

        # Should use header, not query param
        assert connection_adapter.user_email == "correct@example.com"
