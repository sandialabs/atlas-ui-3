"""
Tests for WebSocket authentication using the configured authentication header.

These tests validate that the backend correctly extracts the user email from the
configured authentication header (default: X-User-Email) for WebSocket connections,
which is critical for the production authentication flow where the reverse proxy
sets this header. The tests also ensure that fallback mechanisms (query parameter,
test user from config) work as expected, and that the header takes precedence when
both are present.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

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


def test_websocket_fallback_to_query_param(mock_app_factory):
    """Test that WebSocket falls back to query parameter if header not present."""
    client = TestClient(app)
    
    # Connect without header but with query param
    with client.websocket_connect("/ws?user=bob@example.com") as websocket:
        # Send a test message
        websocket.send_json({"type": "attach_file", "s3_key": "users/bob@example.com/test.txt"})
        
        # Get the chat service instance
        call_args = mock_app_factory.create_chat_service.call_args
        connection_adapter = call_args[0][0]
        
        # Should use query param
        assert connection_adapter.user_email == "bob@example.com"


def test_websocket_fallback_to_test_user(mock_app_factory):
    """Test that WebSocket falls back to test user if neither header nor query param present."""
    client = TestClient(app)
    
    # Connect without header or query param
    with client.websocket_connect("/ws") as websocket:
        # Send a test message
        websocket.send_json({"type": "attach_file", "s3_key": "users/test@test.com/test.txt"})
        
        # Get the chat service instance
        call_args = mock_app_factory.create_chat_service.call_args
        connection_adapter = call_args[0][0]
        
        # Should use test user from config
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
