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
                assert result.content.text == "Mocked LLM response"
                
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


class TestSamplingDemoTools:
    """Integration tests for sampling_demo MCP server tools."""

    @pytest.mark.asyncio
    async def test_summarize_text_tool(self):
        """Test summarize_text tool with basic sampling."""
        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport
        from mcp.types import CreateMessageResult, TextContent
        from pathlib import Path
        import sys

        # Create mock sampling handler
        async def mock_sampling_handler(messages, params=None, context=None):
            # Verify basic sampling call
            assert len(messages) > 0
            return CreateMessageResult(
                role="assistant",
                content=TextContent(type="text", text="This is a concise summary of the text."),
                model="test-model"
            )

        # Get absolute path to the sampling demo server
        server_path = Path(__file__).parent.parent / "mcp" / "sampling_demo" / "main.py"

        # Use StdioTransport explicitly
        transport = StdioTransport(
            command=sys.executable,
            args=[str(server_path)]
        )
        client = Client(transport, sampling_handler=mock_sampling_handler)

        async with client:
            result = await client.call_tool(
                "summarize_text",
                {"text": "Long text that needs summarization..."}
            )

            # Verify tool returns the sampled text
            assert "summary" in result.content[0].text.lower()

    @pytest.mark.asyncio
    async def test_analyze_sentiment_tool(self):
        """Test analyze_sentiment tool with system prompt and low temperature."""
        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport
        from mcp.types import CreateMessageResult, TextContent
        from pathlib import Path
        import sys

        captured_params = {}

        async def mock_sampling_handler(messages, params=None, context=None):
            # Capture params to verify system prompt and temperature
            if params:
                captured_params['system_prompt'] = getattr(params, 'systemPrompt', None)
                captured_params['temperature'] = getattr(params, 'temperature', None)

            return CreateMessageResult(
                role="assistant",
                content=TextContent(
                    type="text",
                    text="Positive sentiment - the text expresses enthusiasm and satisfaction."
                ),
                model="test-model"
            )

        server_path = Path(__file__).parent.parent / "mcp" / "sampling_demo" / "main.py"
        transport = StdioTransport(
            command=sys.executable,
            args=[str(server_path)]
        )
        client = Client(transport, sampling_handler=mock_sampling_handler)

        async with client:
            result = await client.call_tool(
                "analyze_sentiment",
                {"text": "I love this product!"}
            )

            # Verify tool used system prompt and low temperature
            assert captured_params.get('system_prompt') is not None
            assert "sentiment" in captured_params['system_prompt'].lower()
            assert captured_params.get('temperature') == 0.3
            assert "sentiment" in result.content[0].text.lower()

    @pytest.mark.asyncio
    async def test_generate_code_tool(self):
        """Test generate_code tool with model preferences."""
        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport
        from mcp.types import CreateMessageResult, TextContent
        from pathlib import Path
        import sys

        captured_params = {}

        async def mock_sampling_handler(messages, params=None, context=None):
            # Capture model preferences
            if params:
                captured_params['model_preferences'] = getattr(params, 'modelPreferences', None)
                captured_params['max_tokens'] = getattr(params, 'maxTokens', None)
                captured_params['temperature'] = getattr(params, 'temperature', None)

            return CreateMessageResult(
                role="assistant",
                content=TextContent(
                    type="text",
                    text="def fibonacci(n):\n    if n <= 1:\n        return n\n    return fibonacci(n-1) + fibonacci(n-2)"
                ),
                model="test-model"
            )

        server_path = Path(__file__).parent.parent / "mcp" / "sampling_demo" / "main.py"
        transport = StdioTransport(
            command=sys.executable,
            args=[str(server_path)]
        )
        client = Client(transport, sampling_handler=mock_sampling_handler)

        async with client:
            result = await client.call_tool(
                "generate_code",
                {
                    "description": "calculate fibonacci numbers",
                    "language": "Python"
                }
            )

            # Verify model preferences were set
            assert captured_params.get('model_preferences') is not None
            # ModelPreferences can be an object or list, just verify it exists
            assert captured_params['model_preferences'] is not None
            # Verify reasonable parameters for code generation
            assert captured_params.get('max_tokens') == 1000
            assert captured_params.get('temperature') == 0.7
            assert "def" in result.content[0].text or "fibonacci" in result.content[0].text.lower()

    @pytest.mark.asyncio
    async def test_creative_story_tool(self):
        """Test creative_story tool with high temperature."""
        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport
        from mcp.types import CreateMessageResult, TextContent
        from pathlib import Path
        import sys

        captured_params = {}

        async def mock_sampling_handler(messages, params=None, context=None):
            # Capture temperature to verify high value for creativity
            if params:
                captured_params['temperature'] = getattr(params, 'temperature', None)
                captured_params['max_tokens'] = getattr(params, 'maxTokens', None)

            return CreateMessageResult(
                role="assistant",
                content=TextContent(
                    type="text",
                    text="Once upon a time, in a world of circuits and code, there lived a robot who dreamed of painting..."
                ),
                model="test-model"
            )

        server_path = Path(__file__).parent.parent / "mcp" / "sampling_demo" / "main.py"
        transport = StdioTransport(
            command=sys.executable,
            args=[str(server_path)]
        )
        client = Client(transport, sampling_handler=mock_sampling_handler)

        async with client:
            result = await client.call_tool(
                "creative_story",
                {"prompt": "a robot learning to paint"}
            )

            # Verify high temperature for creativity
            assert captured_params.get('temperature') == 0.9
            assert captured_params.get('max_tokens') == 500
            # Story should be present
            assert len(result.content[0].text) > 0

    @pytest.mark.asyncio
    async def test_multi_turn_conversation_tool(self):
        """Test multi_turn_conversation tool with SamplingMessage objects."""
        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport
        from mcp.types import CreateMessageResult, TextContent, SamplingMessage
        from pathlib import Path
        import sys

        call_count = 0

        async def mock_sampling_handler(messages, params=None, context=None):
            nonlocal call_count
            call_count += 1

            # Verify messages include SamplingMessage objects for multi-turn
            if call_count == 1:
                # First turn - initial question
                assert len(messages) == 1
                return CreateMessageResult(
                    role="assistant",
                    content=TextContent(
                        type="text",
                        text="Key aspects to consider: history, current state, and future trends."
                    ),
                    model="test-model"
                )
            else:
                # Second turn - should have conversation history
                assert len(messages) >= 3  # User, Assistant, User
                return CreateMessageResult(
                    role="assistant",
                    content=TextContent(
                        type="text",
                        text="The most important point is understanding the historical context."
                    ),
                    model="test-model"
                )

        server_path = Path(__file__).parent.parent / "mcp" / "sampling_demo" / "main.py"
        transport = StdioTransport(
            command=sys.executable,
            args=[str(server_path)]
        )
        client = Client(transport, sampling_handler=mock_sampling_handler)

        async with client:
            result = await client.call_tool(
                "multi_turn_conversation",
                {"topic": "artificial intelligence"}
            )

            # Verify two sampling calls were made
            assert call_count == 2
            # Result should contain both turns
            text = result.content[0].text
            assert "Discussion" in text or "Initial Response" in text

    @pytest.mark.asyncio
    async def test_research_question_tool(self):
        """Test research_question tool with multi-step agentic workflow."""
        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport
        from mcp.types import CreateMessageResult, TextContent
        from pathlib import Path
        import sys

        call_count = 0
        captured_calls = []

        async def mock_sampling_handler(messages, params=None, context=None):
            nonlocal call_count
            call_count += 1

            # Capture each call for verification
            captured_calls.append({
                'messages': messages,
                'params': params
            })

            if call_count == 1:
                # First call: break down question
                return CreateMessageResult(
                    role="assistant",
                    content=TextContent(
                        type="text",
                        text="1. What are renewable energy sources?\n2. What are their benefits?\n3. What are the challenges?"
                    ),
                    model="test-model"
                )
            else:
                # Second call: comprehensive answer
                return CreateMessageResult(
                    role="assistant",
                    content=TextContent(
                        type="text",
                        text="Renewable energy sources include solar, wind, and hydro. Benefits include sustainability, reduced emissions, and energy independence."
                    ),
                    model="test-model"
                )

        server_path = Path(__file__).parent.parent / "mcp" / "sampling_demo" / "main.py"
        transport = StdioTransport(
            command=sys.executable,
            args=[str(server_path)]
        )
        client = Client(transport, sampling_handler=mock_sampling_handler)

        async with client:
            result = await client.call_tool(
                "research_question",
                {"question": "What are the benefits of renewable energy?"}
            )

            # Verify two-step research process
            assert call_count == 2
            # First call should be about breaking down the question
            assert "break down" in str(captured_calls[0]['messages']).lower()
            # Second call should reference the breakdown
            assert len(str(captured_calls[1]['messages'])) > len(str(captured_calls[0]['messages']))
            # Result should contain analysis and answer
            text = result.content[0].text
            assert "Research Question" in text or "Analysis" in text or "Answer" in text

    @pytest.mark.asyncio
    async def test_translate_and_explain_tool(self):
        """Test translate_and_explain tool with sequential sampling workflow."""
        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport
        from mcp.types import CreateMessageResult, TextContent
        from pathlib import Path
        import sys

        call_count = 0

        async def mock_sampling_handler(messages, params=None, context=None):
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                # First call: translation
                return CreateMessageResult(
                    role="assistant",
                    content=TextContent(
                        type="text",
                        text="Hola, ¿cómo estás?"
                    ),
                    model="test-model"
                )
            else:
                # Second call: explanation
                return CreateMessageResult(
                    role="assistant",
                    content=TextContent(
                        type="text",
                        text="Translation uses informal 'tú' form. 'Cómo estás' is the standard greeting in Spanish."
                    ),
                    model="test-model"
                )

        server_path = Path(__file__).parent.parent / "mcp" / "sampling_demo" / "main.py"
        transport = StdioTransport(
            command=sys.executable,
            args=[str(server_path)]
        )
        client = Client(transport, sampling_handler=mock_sampling_handler)

        async with client:
            result = await client.call_tool(
                "translate_and_explain",
                {
                    "text": "Hello, how are you?",
                    "target_language": "Spanish"
                }
            )

            # Verify sequential workflow (two calls)
            assert call_count == 2
            # Result should contain both translation and explanation
            text = result.content[0].text
            assert "Translation" in text or "Hola" in text
            assert "Notes" in text or "explain" in text.lower() or "form" in text.lower()
