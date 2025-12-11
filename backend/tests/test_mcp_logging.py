"""Tests for MCP server logging functionality.

These tests verify that:
1. Log handlers are properly created and attached to MCP clients
2. Log messages are filtered based on configured LOG_LEVEL
3. Log messages are forwarded to the UI callback when provided
4. Backend logger receives MCP server logs
"""

import logging
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from backend.modules.mcp_tools.client import MCPToolManager


class MockLogMessage:
    """Mock LogMessage from fastmcp.client.logging."""
    def __init__(self, level: str, msg: str, extra: dict = None):
        self.level = level
        self.data = {
            'msg': msg,
            'extra': extra or {}
        }


@pytest.mark.asyncio
class TestMCPLogging:
    """Tests for MCP logging functionality."""

    async def test_log_handler_respects_log_level_info(self):
        """Test that log handler filters out DEBUG logs when level is INFO."""
        # Create manager with no log callback (just testing filtering)
        with patch.dict('os.environ', {'LOG_LEVEL': 'INFO'}):
            manager = MCPToolManager()
            
            # Get the handler for a mock server
            log_handler = manager._create_log_handler("test_server")
            
            # Mock the backend logger
            with patch('backend.modules.mcp_tools.client.logger') as mock_logger:
                # Send a DEBUG log (should be filtered out)
                debug_msg = MockLogMessage('debug', 'This is a debug message')
                await log_handler(debug_msg)
                
                # Logger should not be called for DEBUG when level is INFO
                mock_logger.log.assert_not_called()
                
                # Send an INFO log (should pass through)
                info_msg = MockLogMessage('info', 'This is an info message')
                await log_handler(info_msg)
                
                # Logger should be called for INFO
                assert mock_logger.log.call_count == 1
                call_args = mock_logger.log.call_args
                assert call_args[0][0] == logging.INFO
                assert 'This is an info message' in call_args[0][1]

    async def test_log_handler_respects_log_level_warning(self):
        """Test that log handler filters out INFO logs when level is WARNING."""
        with patch.dict('os.environ', {'LOG_LEVEL': 'WARNING'}):
            manager = MCPToolManager()
            log_handler = manager._create_log_handler("test_server")
            
            with patch('backend.modules.mcp_tools.client.logger') as mock_logger:
                # Send an INFO log (should be filtered out)
                info_msg = MockLogMessage('info', 'This is an info message')
                await log_handler(info_msg)
                
                # Logger should not be called for INFO when level is WARNING
                mock_logger.log.assert_not_called()
                
                # Send a WARNING log (should pass through)
                warn_msg = MockLogMessage('warning', 'This is a warning')
                await log_handler(warn_msg)
                
                # Logger should be called for WARNING
                assert mock_logger.log.call_count == 1
                call_args = mock_logger.log.call_args
                assert call_args[0][0] == logging.WARNING

    async def test_log_handler_forwards_to_callback(self):
        """Test that log handler forwards messages to UI callback."""
        # Create a mock callback
        mock_callback = AsyncMock()
        
        with patch.dict('os.environ', {'LOG_LEVEL': 'INFO'}):
            manager = MCPToolManager(log_callback=mock_callback)
            log_handler = manager._create_log_handler("test_server")
            
            with patch('backend.modules.mcp_tools.client.logger'):
                # Send a log message
                msg = MockLogMessage('info', 'Test message', {'key': 'value'})
                await log_handler(msg)
                
                # Callback should be called with correct parameters
                mock_callback.assert_called_once()
                call_args = mock_callback.call_args[0]
                assert call_args[0] == "test_server"  # server_name
                assert call_args[1] == "info"  # level
                assert call_args[2] == "Test message"  # message
                assert call_args[3] == {'key': 'value'}  # extra

    async def test_log_handler_maps_log_levels_correctly(self):
        """Test that MCP log levels are correctly mapped to Python logging levels."""
        manager = MCPToolManager()
        log_handler = manager._create_log_handler("test_server")
        
        # Test mapping of different log levels
        test_cases = [
            ('debug', logging.DEBUG),
            ('info', logging.INFO),
            ('notice', logging.INFO),
            ('warning', logging.WARNING),
            ('warn', logging.WARNING),
            ('error', logging.ERROR),
            ('alert', logging.CRITICAL),
            ('critical', logging.CRITICAL),
            ('emergency', logging.CRITICAL),
        ]
        
        with patch('backend.modules.mcp_tools.client.logger') as mock_logger:
            for mcp_level, expected_python_level in test_cases:
                mock_logger.reset_mock()
                msg = MockLogMessage(mcp_level, f'Test {mcp_level} message')
                await log_handler(msg)
                
                # Check that the correct Python log level was used
                call_args = mock_logger.log.call_args
                assert call_args[0][0] == expected_python_level, \
                    f"MCP level '{mcp_level}' should map to Python level {expected_python_level}"

    async def test_set_log_callback(self):
        """Test that log callback can be set after initialization."""
        manager = MCPToolManager()
        
        # Initially no callback
        assert manager._log_callback is None
        
        # Set a callback
        mock_callback = AsyncMock()
        manager.set_log_callback(mock_callback)
        
        assert manager._log_callback is mock_callback
        
        # Test that it's used
        log_handler = manager._create_log_handler("test_server")
        
        with patch('backend.modules.mcp_tools.client.logger'):
            msg = MockLogMessage('info', 'Test message')
            await log_handler(msg)
            
            mock_callback.assert_called_once()

    async def test_log_handler_handles_errors_gracefully(self):
        """Test that log handler doesn't crash if callback raises an exception."""
        # Create a callback that raises an exception
        mock_callback = AsyncMock(side_effect=Exception("Callback error"))
        
        manager = MCPToolManager(log_callback=mock_callback)
        log_handler = manager._create_log_handler("test_server")
        
        with patch('backend.modules.mcp_tools.client.logger') as mock_logger:
            # Send a log message - should not raise despite callback error
            msg = MockLogMessage('info', 'Test message')
            await log_handler(msg)
            
            # Backend logger should still be called
            mock_logger.log.assert_called_once()
            # Warning about callback failure should be logged
            mock_logger.warning.assert_called_once()

    async def test_log_handler_includes_server_context(self):
        """Test that log handler includes server name in backend logs."""
        manager = MCPToolManager()
        log_handler = manager._create_log_handler("my_test_server")
        
        with patch('backend.modules.mcp_tools.client.logger') as mock_logger:
            msg = MockLogMessage('info', 'Test message')
            await log_handler(msg)
            
            # Check that server name is in the log message
            call_args = mock_logger.log.call_args
            log_message = call_args[0][1]
            assert 'my_test_server' in log_message
            assert 'Test message' in log_message
