"""Tests for log level control of sensitive data logging.

These tests verify that:
1. User message content is only logged at DEBUG level, not INFO
2. LLM response content is only logged at DEBUG level, not INFO  
3. Non-sensitive metadata is always logged at INFO level
4. The LOG_LEVEL environment variable controls this behavior
"""

import logging
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from backend.application.chat.service import ChatService
from backend.modules.llm.litellm_caller import LiteLLMCaller


class TestLogLevelSensitiveData:
    """Tests for log level control of sensitive data."""

    def test_chat_service_info_level_excludes_content(self, caplog):
        """Test that INFO level logging excludes user message content."""
        # Create mock dependencies
        mock_llm = MagicMock()
        mock_tool_manager = MagicMock()
        mock_connection = MagicMock()
        mock_config = MagicMock()
        mock_session_repo = MagicMock()
        mock_session_repo.get = AsyncMock(return_value=None)
        
        service = ChatService(
            llm=mock_llm,
            tool_manager=mock_tool_manager,
            connection=mock_connection,
            config_manager=mock_config,
            session_repository=mock_session_repo
        )
        
        # Set log level to INFO
        with caplog.at_level(logging.INFO):
            # Create test session first
            import asyncio
            asyncio.run(service.create_session("test-session", "test@test.com"))
            
            # Clear logs from session creation
            caplog.clear()
            
            # Try to call handle_chat_message (it will fail but we only care about logs)
            try:
                asyncio.run(service.handle_chat_message(
                    session_id="test-session",
                    content="This is sensitive user input that should not be logged at INFO level",
                    model="test-model",
                    user_email="test@test.com"
                ))
            except Exception:
                pass  # We expect this to fail, we're just checking logs
        
        # Check that logs exist but don't contain the sensitive content
        log_messages = [record.message for record in caplog.records if record.levelno == logging.INFO]
        
        # Should have INFO log about the call
        assert any("handle_chat_message called" in msg for msg in log_messages), \
            "Should have INFO log about handle_chat_message call"
        
        # Should NOT contain the sensitive content at INFO level
        assert not any("sensitive user input" in msg for msg in log_messages), \
            "Should NOT log sensitive content at INFO level"
        
        # Should log metadata like content length
        assert any("content_length" in msg for msg in log_messages), \
            "Should log content_length metadata at INFO level"

    def test_chat_service_debug_level_includes_content(self, caplog):
        """Test that DEBUG level logging includes user message content."""
        # Create mock dependencies
        mock_llm = MagicMock()
        mock_tool_manager = MagicMock()
        mock_connection = MagicMock()
        mock_config = MagicMock()
        mock_session_repo = MagicMock()
        mock_session_repo.get = AsyncMock(return_value=None)
        
        service = ChatService(
            llm=mock_llm,
            tool_manager=mock_tool_manager,
            connection=mock_connection,
            config_manager=mock_config,
            session_repository=mock_session_repo
        )
        
        # Set log level to DEBUG
        with caplog.at_level(logging.DEBUG):
            # Create test session first
            import asyncio
            asyncio.run(service.create_session("test-session", "test@test.com"))
            
            # Clear logs from session creation
            caplog.clear()
            
            # Try to call handle_chat_message
            try:
                asyncio.run(service.handle_chat_message(
                    session_id="test-session",
                    content="This is sensitive user input",
                    model="test-model",
                    user_email="test@test.com"
                ))
            except Exception:
                pass  # We expect this to fail, we're just checking logs
        
        # Check that DEBUG logs include the content
        log_messages = [record.message for record in caplog.records if record.levelno == logging.DEBUG]
        
        # Should contain the sensitive content at DEBUG level
        assert any("sensitive user input" in msg for msg in log_messages), \
            "Should log sensitive content at DEBUG level"

    @pytest.mark.asyncio
    async def test_llm_caller_info_level_excludes_response_preview(self, caplog):
        """Test that INFO level logging excludes LLM response previews."""
        # Mock the acompletion call
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message = MagicMock()
        mock_response.choices[0].message.content = "This is a sensitive LLM response with user data"
        
        with patch('backend.modules.llm.litellm_caller.acompletion', return_value=mock_response):
            caller = LiteLLMCaller()
            
            # Mock the config to return test model
            with patch.object(caller, '_get_litellm_model_name', return_value='gpt-4'):
                with patch.object(caller, '_get_model_kwargs', return_value={}):
                    # Set log level to INFO
                    with caplog.at_level(logging.INFO):
                        result = await caller.call(
                            model_name="test-model",
                            messages=[{"role": "user", "content": "test"}],
                            temperature=0.7
                        )
        
        # Check logs
        log_messages = [record.message for record in caplog.records if record.levelno == logging.INFO]
        
        # Should have INFO log about the call
        assert any("Plain LLM call" in msg for msg in log_messages), \
            "Should have INFO log about LLM call"
        
        # Should NOT contain response preview at INFO level
        assert not any("sensitive LLM response" in msg for msg in log_messages), \
            "Should NOT log response preview at INFO level"
        
        # Should log response length instead
        assert any("response length" in msg for msg in log_messages), \
            "Should log response length at INFO level"

    @pytest.mark.asyncio
    async def test_llm_caller_debug_level_includes_response_preview(self, caplog):
        """Test that DEBUG level logging includes LLM response previews."""
        # Mock the acompletion call
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message = MagicMock()
        mock_response.choices[0].message.content = "This is a sensitive LLM response"
        
        with patch('backend.modules.llm.litellm_caller.acompletion', return_value=mock_response):
            caller = LiteLLMCaller()
            
            # Mock the config to return test model
            with patch.object(caller, '_get_litellm_model_name', return_value='gpt-4'):
                with patch.object(caller, '_get_model_kwargs', return_value={}):
                    # Set log level to DEBUG
                    with caplog.at_level(logging.DEBUG):
                        result = await caller.call(
                            model_name="test-model",
                            messages=[{"role": "user", "content": "test"}],
                            temperature=0.7
                        )
        
        # Check logs
        log_messages = [record.message for record in caplog.records if record.levelno == logging.DEBUG]
        
        # Should contain response preview at DEBUG level
        assert any("sensitive LLM response" in msg for msg in log_messages), \
            "Should log response preview at DEBUG level"

    def test_log_level_from_env(self):
        """Test that LOG_LEVEL environment variable is properly read."""
        with patch.dict('os.environ', {'LOG_LEVEL': 'WARNING'}):
            from backend.core.otel_config import OpenTelemetryConfig
            config = OpenTelemetryConfig()
            assert config.log_level == logging.WARNING, \
                "Should read LOG_LEVEL from environment"
        
        with patch.dict('os.environ', {'LOG_LEVEL': 'DEBUG'}):
            config = OpenTelemetryConfig()
            assert config.log_level == logging.DEBUG, \
                "Should read DEBUG level from environment"
        
        with patch.dict('os.environ', {'LOG_LEVEL': 'INFO'}):
            config = OpenTelemetryConfig()
            assert config.log_level == logging.INFO, \
                "Should read INFO level from environment"
