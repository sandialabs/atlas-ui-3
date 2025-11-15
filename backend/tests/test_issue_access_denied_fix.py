"""
Integration test demonstrating the fix for the access denied issue.

This test simulates the exact scenario from the issue:
- A file belongs to user 'agarlan@sandia.gov'
- WebSocket connection is authenticated as 'agarlan@sandia.gov' via X-User-Email header
- Attaching the file should succeed (not fail with "Access denied")
"""

import base64
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def mock_components():
    """Mock all components needed for the test."""
    with patch('main.app_factory') as mock_factory:
        # Mock config
        mock_config = MagicMock()
        mock_config.app_settings.test_user = 'test@test.com'
        mock_config.app_settings.auth_user_header = 'X-User-Email'
        mock_factory.get_config_manager.return_value = mock_config
        
        # Mock file manager with S3 client
        mock_file_manager = MagicMock()
        mock_s3_client = MagicMock()
        
        # Simulate a file that belongs to agarlan@sandia.gov
        async def mock_get_file(user_email, s3_key):
            """Mock S3 get_file that enforces user prefix check."""
            # This is the actual check from s3_client.py line 185
            if not s3_key.startswith(f"users/{user_email}/"):
                raise Exception("Access denied to file")
            
            # If user matches, return file metadata
            return {
                "key": s3_key,
                "filename": "mypdf.pdf",
                "content_base64": base64.b64encode(b"test content").decode(),
                "content_type": "application/pdf",
                "size": 100,
                "etag": "test-etag"
            }
        
        mock_s3_client.get_file = AsyncMock(side_effect=mock_get_file)
        mock_file_manager.s3_client = mock_s3_client
        
        # Mock chat service
        mock_chat_service = MagicMock()
        mock_chat_service.handle_attach_file = AsyncMock(return_value={
            'type': 'file_attach',
            'success': True,
            'filename': 'mypdf.pdf'
        })
        mock_chat_service.end_session = MagicMock()
        mock_factory.create_chat_service.return_value = mock_chat_service
        
        yield {
            'factory': mock_factory,
            'config': mock_config,
            'file_manager': mock_file_manager,
            'chat_service': mock_chat_service
        }


def test_issue_scenario_fixed_with_correct_user(mock_components):
    """
    Test the exact scenario from the issue, demonstrating the fix.
    
    Before fix:
    - WebSocket would use test@test.com (from fallback)
    - Attempting to access users/agarlan@sandia.gov/generated/file.pdf would fail
    - Error: "Access denied: test@test.com attempted to access users/agarlan@sandia.gov/..."
    
    After fix:
    - WebSocket uses agarlan@sandia.gov (from X-User-Email header)
    - Accessing users/agarlan@sandia.gov/generated/file.pdf succeeds
    """
    client = TestClient(app)
    
    # Simulate the production scenario: reverse proxy sets X-User-Email header
    actual_user = "agarlan@sandia.gov"

    # Connect with X-User-Email header (as set by reverse proxy)
    with client.websocket_connect("/ws", headers={"X-User-Email": actual_user}):
        # Verify the connection was created with the correct user
        call_args = mock_components['factory'].create_chat_service.call_args
        connection_adapter = call_args[0][0]
        
        # This should be the actual user, not test@test.com
        assert connection_adapter.user_email == actual_user, (
            f"Expected user to be {actual_user}, but got {connection_adapter.user_email}. "
            "This would cause 'Access denied' errors when accessing user's files."
        )


def test_issue_scenario_would_fail_without_header():
    """
    Demonstrate that without the header, the old behavior (test user fallback) occurs.
    This test shows why the issue existed in the first place.
    """
    with patch('main.app_factory') as mock_factory:
        # Mock config
        mock_config = MagicMock()
        mock_config.app_settings.test_user = 'test@test.com'
        mock_config.app_settings.auth_user_header = 'X-User-Email'
        mock_factory.get_config_manager.return_value = mock_config
        
        # Mock chat service
        mock_chat_service = MagicMock()
        mock_chat_service.end_session = MagicMock()
        mock_factory.create_chat_service.return_value = mock_chat_service
        
        client = TestClient(app)
        
        # Connect WITHOUT X-User-Email header (simulating old behavior or dev mode)
        with client.websocket_connect("/ws"):
            call_args = mock_factory.create_chat_service.call_args
            connection_adapter = call_args[0][0]
            
            # Without header, it falls back to test user
            assert connection_adapter.user_email == 'test@test.com', (
                "Without X-User-Email header, should fall back to test user"
            )
            
            # This would cause access denied when trying to access:
            # users/agarlan@sandia.gov/generated/file.pdf
            # because connection is authenticated as test@test.com


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
