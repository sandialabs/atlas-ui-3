"""Integration test for MCP sampling functionality."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.domain.messages.models import ToolCall
from atlas.modules.mcp_tools.client import MCPToolManager

# Project root needed for PYTHONPATH so STDIO servers can import atlas.mcp_shared
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _stdio_env() -> dict:
    """Build env dict with PYTHONPATH for STDIO subprocess."""
    env = dict(os.environ)
    env["PYTHONPATH"] = _PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return env


class TestSamplingIntegration:
    """Integration tests for MCP sampling."""

    @pytest.mark.asyncio
    async def test_sampling_handler_basic(self):
        """Test that sampling handler can be created and configured."""
        manager = MCPToolManager()

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
            # Verify routing is set up with composite key (server_name, tool_call.id)
            routing_key = ("test_server", "test_tool_call_1")
            assert routing_key in manager._sampling_routing
            routing = manager._sampling_routing[routing_key]
            assert routing.server_name == "test_server"
            assert routing.tool_call == tool_call
            assert routing.update_cb == update_cb

        # Verify routing is cleaned up
        assert routing_key not in manager._sampling_routing

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
        with patch('atlas.modules.llm.litellm_caller.LiteLLMCaller') as mock_llm_class:
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

    async def _call_tool(self, tool_name: str, arguments: dict):
        import sys
        from pathlib import Path

        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport

        server_path = Path(__file__).parent.parent / "mcp" / "sampling_demo" / "main.py"
        transport = StdioTransport(
            command=sys.executable,
            args=[str(server_path)],
            env=_stdio_env(),
        )
        client = Client(transport)
        async with client:
            return await client.call_tool(tool_name, arguments)

    @pytest.mark.asyncio
    async def test_summarize_text_tool(self):
        result = await self._call_tool(
            "summarize_text",
            {"text": "Long text that needs summarization."},
        )
        assert "summarization" in result.content[0].text.lower()

    @pytest.mark.asyncio
    async def test_analyze_sentiment_tool(self):
        result = await self._call_tool(
            "analyze_sentiment",
            {"text": "I love this product!"},
        )
        assert "positive" in result.content[0].text.lower()

    @pytest.mark.asyncio
    async def test_generate_code_tool(self):
        result = await self._call_tool(
            "generate_code",
            {"description": "calculate fibonacci numbers", "language": "Python"},
        )
        assert "NotImplementedError" in result.content[0].text

    @pytest.mark.asyncio
    async def test_creative_story_tool(self):
        result = await self._call_tool(
            "creative_story",
            {"prompt": "a robot learning to paint"},
        )
        assert "Story prompt" in result.content[0].text

    @pytest.mark.asyncio
    async def test_multi_turn_conversation_tool(self):
        result = await self._call_tool(
            "multi_turn_conversation",
            {"topic": "artificial intelligence"},
        )
        text = result.content[0].text
        assert "Discussion on artificial intelligence" in text
        assert "Initial Response" in text
        assert "Follow-up" in text

    @pytest.mark.asyncio
    async def test_research_question_tool(self):
        result = await self._call_tool(
            "research_question",
            {"question": "What are the benefits of renewable energy?"},
        )
        text = result.content[0].text
        assert "Research Question" in text
        assert "Analysis" in text
        assert "Answer" in text

    @pytest.mark.asyncio
    async def test_translate_and_explain_tool(self):
        result = await self._call_tool(
            "translate_and_explain",
            {"text": "Hello, how are you?", "target_language": "Spanish"},
        )
        text = result.content[0].text
        assert "Translation to Spanish" in text
        assert "Translation Notes" in text

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tool_name", "arguments", "expected_text"),
        [
            (
                "summarize_text",
                {"text": "Long text that needs summarization.", "summary": "Client summary"},
                "Client summary",
            ),
            (
                "analyze_sentiment",
                {"text": "I love this product!", "analysis": "Custom sentiment"},
                "Custom sentiment",
            ),
            (
                "generate_code",
                {
                    "description": "calculate fibonacci numbers",
                    "language": "Python",
                    "generated_code": "print('client code')",
                },
                "print('client code')",
            ),
            (
                "creative_story",
                {"prompt": "a robot learning to paint", "story": "Client story"},
                "Client story",
            ),
            (
                "multi_turn_conversation",
                {
                    "topic": "artificial intelligence",
                    "initial_response": "Client initial",
                    "follow_up_response": "Client follow-up",
                },
                "Client follow-up",
            ),
            (
                "research_question",
                {
                    "question": "What are the benefits of renewable energy?",
                    "breakdown": "Client breakdown",
                    "answer": "Client answer",
                },
                "Client answer",
            ),
            (
                "translate_and_explain",
                {
                    "text": "Hello, how are you?",
                    "target_language": "Spanish",
                    "translation": "Hola, ¿cómo estás?",
                    "explanation": "Client explanation",
                },
                "Client explanation",
            ),
        ],
    )
    async def test_tools_accept_client_supplied_generated_fields(
        self, tool_name, arguments, expected_text
    ):
        result = await self._call_tool(tool_name, arguments)
        assert result.content[0].text == expected_text or expected_text in result.content[0].text
