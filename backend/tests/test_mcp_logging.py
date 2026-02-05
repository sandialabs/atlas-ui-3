"""Tests for MCP server logging functionality.

These tests verify that:
1. Log handlers are properly created and attached to MCP clients
2. Log messages are filtered based on configured LOG_LEVEL
3. Log messages are forwarded to the UI callback when provided
4. Backend logger receives MCP server logs
"""

import asyncio
import logging
import pytest
from unittest.mock import patch, AsyncMock
from backend.modules.mcp_tools.client import MCPToolManager, MCP_TO_PYTHON_LOG_LEVEL


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

    async def test_log_level_mapping(self):
        """Test that MCP log levels are mapped correctly to Python logging levels."""
        assert MCP_TO_PYTHON_LOG_LEVEL['debug'] == logging.DEBUG
        assert MCP_TO_PYTHON_LOG_LEVEL['info'] == logging.INFO
        assert MCP_TO_PYTHON_LOG_LEVEL['notice'] == logging.INFO
        assert MCP_TO_PYTHON_LOG_LEVEL['warning'] == logging.WARNING
        assert MCP_TO_PYTHON_LOG_LEVEL['warn'] == logging.WARNING
        assert MCP_TO_PYTHON_LOG_LEVEL['error'] == logging.ERROR
        assert MCP_TO_PYTHON_LOG_LEVEL['alert'] == logging.CRITICAL
        assert MCP_TO_PYTHON_LOG_LEVEL['critical'] == logging.CRITICAL
        assert MCP_TO_PYTHON_LOG_LEVEL['emergency'] == logging.CRITICAL

    async def test_log_handler_forwards_to_callback(self):
        """Test that log handler forwards messages to UI callback."""
        # Create a mock callback
        mock_callback = AsyncMock()
        
        with patch.dict('os.environ', {'LOG_LEVEL': 'DEBUG'}):
            manager = MCPToolManager(log_callback=mock_callback)
            log_handler = manager._create_log_handler("test_server")
            
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

    async def test_log_handler_filters_by_level(self):
        """Test that log handler respects min_log_level filtering."""
        mock_callback = AsyncMock()
        
        # Set minimum level to WARNING
        with patch.dict('os.environ', {'LOG_LEVEL': 'WARNING'}):
            manager = MCPToolManager(log_callback=mock_callback)
            manager._min_log_level = logging.WARNING  # Ensure it's set
            log_handler = manager._create_log_handler("test_server")
            
            # Send a DEBUG log (should be filtered out)
            debug_msg = MockLogMessage('debug', 'Debug message')
            await log_handler(debug_msg)
            
            # Callback should NOT be called for DEBUG when level is WARNING
            mock_callback.assert_not_called()
            
            # Send an INFO log (should also be filtered out)
            info_msg = MockLogMessage('info', 'Info message')
            await log_handler(info_msg)
            
            # Still should not be called
            mock_callback.assert_not_called()
            
            # Send a WARNING log (should pass through)
            warn_msg = MockLogMessage('warning', 'Warning message')
            await log_handler(warn_msg)
            
            # Now callback should be called
            mock_callback.assert_called_once()

    async def test_set_log_callback(self):
        """Test that log callback can be set after initialization."""
        manager = MCPToolManager()
        
        # Initially no callback
        assert manager._default_log_callback is None
        
        # Set a callback
        mock_callback = AsyncMock()
        manager.set_log_callback(mock_callback)
        
        assert manager._default_log_callback is mock_callback
        
        # Test that it's used
        log_handler = manager._create_log_handler("test_server")
        
        msg = MockLogMessage('info', 'Test message')
        await log_handler(msg)
        
        mock_callback.assert_called_once()

    async def test_log_handler_handles_callback_errors_gracefully(self):
        """Test that log handler doesn't crash if callback raises an exception."""
        # Create a callback that raises an exception
        mock_callback = AsyncMock(side_effect=Exception("Callback error"))
        
        manager = MCPToolManager(log_callback=mock_callback)
        log_handler = manager._create_log_handler("test_server")
        
        # Send a log message - should not raise despite callback error
        msg = MockLogMessage('info', 'Test message')
        # This should not raise an exception
        await log_handler(msg)
        
        # Verify the callback was attempted
        mock_callback.assert_called_once()

    async def test_request_scoped_callback_overrides_default(self):
        """Request-scoped callback should override the default callback.

        This is the core mechanism preventing cross-user log leakage when MCPToolManager
        is shared across multiple websocket connections.
        """
        default_cb = AsyncMock()
        request_cb = AsyncMock()

        manager = MCPToolManager(log_callback=default_cb)
        log_handler = manager._create_log_handler("test_server")

        msg = MockLogMessage('info', 'Scoped message')

        async with manager._use_log_callback(request_cb):
            await log_handler(msg)

        request_cb.assert_called_once()
        default_cb.assert_not_called()

    async def test_request_scoped_callbacks_are_isolated_across_tasks(self):
        """Two concurrent tasks should not receive each other's MCP logs."""
        cb_a = AsyncMock()
        cb_b = AsyncMock()
        manager = MCPToolManager()
        log_handler = manager._create_log_handler("test_server")

        async def run_with(cb, text):
            async with manager._use_log_callback(cb):
                await log_handler(MockLogMessage('info', text))

        await asyncio.gather(
            run_with(cb_a, "message-a"),
            run_with(cb_b, "message-b"),
        )

        cb_a.assert_called_once()
        cb_b.assert_called_once()

