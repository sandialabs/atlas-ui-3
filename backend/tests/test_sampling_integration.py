"""Integration test for MCP sampling functionality."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from modules.mcp_tools.client import MCPToolManager, _SamplingRoutingContext
from domain.messages.models import ToolCall


class TestSamplingIntegration:
    """Integration tests for MCP sampling."""

    @pytest.mark.asyncio
    async def test_sampling_handler_basic(self):
        """Test that sampling handler can be created and configured."""
        manager = MCPToolManager()
        
        # Create a mock tool call
        tool_call = ToolCall(
            id="test_tool_call_1",
            name="test_tool",
            arguments={}
        )
        
        # Create a sampling handler
        handler = manager._create_sampling_handler("test_server")
        
        # Verify handler is callable
        assert callable(handler)

    @pytest.mark.asyncio
    async def test_sampling_context_manager(self):
        """Test the sampling context manager."""
        manager = MCPToolManager()
        
        # Create a mock tool call and update callback
        tool_call = ToolCall(
            id="test_tool_call_1",
            name="test_tool",
            arguments={}
        )
        
        update_cb = AsyncMock()
        
        # Use the context manager
        async with manager._use_sampling_context("test_server", tool_call, update_cb):
            # Verify routing is set up
            from modules.mcp_tools.client import _SAMPLING_ROUTING
            assert "test_server" in _SAMPLING_ROUTING
            routing = _SAMPLING_ROUTING["test_server"]
            assert routing.server_name == "test_server"
            assert routing.tool_call == tool_call
            assert routing.update_cb == update_cb
        
        # Verify routing is cleaned up
        assert "test_server" not in _SAMPLING_ROUTING

    @pytest.mark.asyncio
    async def test_sampling_handler_with_routing(self):
        """Test sampling handler with routing context."""
        manager = MCPToolManager()
        
        # Create a mock tool call
        tool_call = ToolCall(
            id="test_tool_call_1",
            name="test_tool",
            arguments={}
        )
        
        update_cb = AsyncMock()
        
        # Mock the LLM caller - patch where it's imported in the handler
        with patch('modules.llm.litellm_caller.LiteLLMCaller') as mock_llm_class:
            mock_llm_instance = AsyncMock()
            mock_llm_instance.call_plain = AsyncMock(return_value="Mocked LLM response")
            mock_llm_class.return_value = mock_llm_instance
            
            # Set up routing context
            async with manager._use_sampling_context("test_server", tool_call, update_cb):
                handler = manager._create_sampling_handler("test_server")
                
                # Create mock sampling params
                mock_params = MagicMock()
                mock_params.systemPrompt = "You are helpful"
                mock_params.temperature = 0.7
                mock_params.maxTokens = 500
                mock_params.modelPreferences = None
                
                # Call the handler
                result = await handler(
                    messages=["Test message"],
                    params=mock_params
                )
                
                # Verify result
                assert result.text == "Mocked LLM response"
                
                # Verify LLM was called correctly
                mock_llm_instance.call_plain.assert_called_once()
                call_args = mock_llm_instance.call_plain.call_args
                assert call_args.kwargs.get('temperature') == 0.7
                assert call_args.kwargs.get('max_tokens') == 500

    @pytest.mark.asyncio
    async def test_sampling_without_routing_context(self):
        """Test that sampling fails without routing context."""
        manager = MCPToolManager()
        
        handler = manager._create_sampling_handler("test_server")
        
        # Try to call handler without routing context
        with pytest.raises(Exception, match="No routing context"):
            await handler(messages=["Test"], params=None)
