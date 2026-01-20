"""Integration test for error flow from LLM to WebSocket."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from domain.errors import RateLimitError, LLMTimeoutError, LLMAuthenticationError


class TestErrorFlowIntegration:
    """Test that errors flow correctly from LLM through to error responses."""

    @pytest.mark.asyncio
    async def test_rate_limit_error_flow(self):
        """Test that rate limit errors result in proper user-friendly messages."""
        from application.chat.utilities.error_handler import safe_call_llm_with_tools
        
        # Mock LLM caller that raises a rate limit error
        mock_llm = MagicMock()
        mock_llm.call_with_tools = AsyncMock(
            side_effect=Exception("RateLimitError: We're experiencing high traffic right now! Please try again soon.")
        )
        
        # Call should raise our custom RateLimitError
        with pytest.raises(RateLimitError) as exc_info:
            await safe_call_llm_with_tools(
                llm_caller=mock_llm,
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                tools_schema=[],
            )
        
        # Verify the error message is user-friendly
        error_msg = str(exc_info.value.message if hasattr(exc_info.value, 'message') else exc_info.value)
        assert "high traffic" in error_msg.lower()
        assert "try again" in error_msg.lower()
        # Should NOT contain technical details
        assert "RateLimitError:" not in error_msg

    @pytest.mark.asyncio
    async def test_timeout_error_flow(self):
        """Test that timeout errors result in proper user-friendly messages."""
        from application.chat.utilities.error_handler import safe_call_llm_with_tools
        
        # Mock LLM caller that raises a timeout error
        mock_llm = MagicMock()
        mock_llm.call_with_tools = AsyncMock(
            side_effect=Exception("Request timed out after 60 seconds")
        )
        
        # Call should raise our custom LLMTimeoutError
        with pytest.raises(LLMTimeoutError) as exc_info:
            await safe_call_llm_with_tools(
                llm_caller=mock_llm,
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                tools_schema=[],
            )
        
        # Verify the error message is user-friendly
        error_msg = str(exc_info.value.message if hasattr(exc_info.value, 'message') else exc_info.value)
        assert "timeout" in error_msg.lower() or "timed out" in error_msg.lower()
        assert "try again" in error_msg.lower()

    @pytest.mark.asyncio
    async def test_authentication_error_flow(self):
        """Test that authentication errors result in proper user-friendly messages."""
        from application.chat.utilities.error_handler import safe_call_llm_with_tools
        
        # Mock LLM caller that raises an auth error
        mock_llm = MagicMock()
        mock_llm.call_with_tools = AsyncMock(
            side_effect=Exception("Invalid API key provided")
        )
        
        # Call should raise our custom LLMAuthenticationError
        with pytest.raises(LLMAuthenticationError) as exc_info:
            await safe_call_llm_with_tools(
                llm_caller=mock_llm,
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                tools_schema=[],
            )
        
        # Verify the error message is user-friendly
        error_msg = str(exc_info.value.message if hasattr(exc_info.value, 'message') else exc_info.value)
        assert "authentication" in error_msg.lower()
        assert "administrator" in error_msg.lower()
        # Should NOT contain the actual API key reference
        assert "API key" not in error_msg and "api key" not in error_msg.lower()

    @pytest.mark.asyncio
    async def test_successful_llm_call(self):
        """Test that successful LLM calls work normally."""
        from application.chat.utilities.error_handler import safe_call_llm_with_tools
        from interfaces.llm import LLMResponse
        
        # Mock successful LLM response
        mock_response = LLMResponse(
            content="Test response",
            model_used="test-model"
        )
        
        mock_llm = MagicMock()
        mock_llm.call_with_tools = AsyncMock(return_value=mock_response)
        
        # Call should succeed
        result = await safe_call_llm_with_tools(
            llm_caller=mock_llm,
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
            tools_schema=[],
        )
        
        assert result == mock_response
        assert result.content == "Test response"
