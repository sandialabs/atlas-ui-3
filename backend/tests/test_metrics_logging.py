"""Tests for metrics logging functionality.

These tests verify that:
1. LLM calls are logged with [METRIC] prefix
2. Tool calls are logged with tool name only (no arguments)
3. Errors are logged with error type
4. File operations are logged
5. Sensitive data is not logged (prompts, messages, arguments, file names)
"""

import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from backend.modules.llm.litellm_caller import LiteLLMCaller
from backend.application.chat.utilities.tool_executor import execute_single_tool
from backend.application.chat.utilities.error_handler import classify_llm_error
from backend.routes.files_routes import upload_file, FileUploadRequest
from domain.messages.models import ToolCall, ToolResult
from domain.errors import LLMServiceError


class TestMetricsLogging:
    """Tests for metrics logging functionality."""

    @pytest.mark.asyncio
    async def test_llm_call_plain_logging(self, caplog):
        """Test that plain LLM calls are logged with metrics."""
        with caplog.at_level(logging.INFO):
            # Create mock config
            mock_config = MagicMock()
            mock_config.models = {
                "test-model": MagicMock(
                    model_name="test-model",
                    model_url="https://api.openai.com",
                    api_key="test-key",
                    max_tokens=1000,
                    temperature=0.7
                )
            }
            
            caller = LiteLLMCaller(llm_config=mock_config)
            
            # Mock the acompletion call
            with patch('backend.modules.llm.litellm_caller.acompletion') as mock_completion:
                mock_response = MagicMock()
                mock_response.choices = [MagicMock()]
                mock_response.choices[0].message.content = "Test response"
                mock_completion.return_value = mock_response
                
                messages = [{"role": "user", "content": "test message"}]
                await caller.call_plain("test-model", messages)
                
                # Check for metric logs
                assert any("[METRIC] LLM call initiated: type=plain" in record.message for record in caplog.records)
                assert any("[METRIC] LLM call completed: type=plain" in record.message for record in caplog.records)
                
                # Ensure no sensitive content is logged at INFO level
                # (messages content should not appear in INFO logs)
                info_logs = [record.message for record in caplog.records if record.levelno == logging.INFO]
                assert not any("test message" in log for log in info_logs)

    @pytest.mark.asyncio
    async def test_llm_call_with_tools_logging(self, caplog):
        """Test that LLM calls with tools are logged with metrics."""
        with caplog.at_level(logging.INFO):
            # Create mock config
            mock_config = MagicMock()
            mock_config.models = {
                "test-model": MagicMock(
                    model_name="test-model",
                    model_url="https://api.openai.com",
                    api_key="test-key",
                    max_tokens=1000,
                    temperature=0.7
                )
            }
            
            caller = LiteLLMCaller(llm_config=mock_config)
            
            # Mock the acompletion call
            with patch('backend.modules.llm.litellm_caller.acompletion') as mock_completion:
                mock_response = MagicMock()
                mock_response.choices = [MagicMock()]
                mock_response.choices[0].message.content = "Test response"
                mock_response.choices[0].message.tool_calls = None
                mock_completion.return_value = mock_response
                
                messages = [{"role": "user", "content": "test message"}]
                tools_schema = [{"type": "function", "function": {"name": "test_tool"}}]
                await caller.call_with_tools("test-model", messages, tools_schema)
                
                # Check for metric logs
                assert any("[METRIC] LLM call initiated: type=tools" in record.message for record in caplog.records)
                assert any("[METRIC] LLM call completed: type=tools" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_tool_call_logging_no_arguments(self, caplog):
        """Test that tool calls are logged with tool name only, not arguments."""
        with caplog.at_level(logging.INFO):
            # Create mock tool call
            mock_tool_call = MagicMock()
            mock_tool_call.id = "test-call-123"
            mock_tool_call.function.name = "test_tool"
            mock_tool_call.function.arguments = '{"secret_key": "should-not-be-logged"}'
            
            # Mock tool manager
            mock_tool_manager = MagicMock()
            mock_tool_manager.get_tools_schema = MagicMock(return_value=[
                {
                    "function": {
                        "name": "test_tool",
                        "parameters": {
                            "properties": {"secret_key": {"type": "string"}},
                            "required": ["secret_key"]
                        }
                    }
                }
            ])
            
            mock_result = ToolResult(
                tool_call_id="test-call-123",
                content="Result",
                success=True
            )
            mock_tool_manager.execute_tool = AsyncMock(return_value=mock_result)
            
            # Mock config manager to disable approval
            mock_config_manager = MagicMock()
            mock_config_manager.app_settings.force_tool_approval_globally = False
            mock_config_manager.tool_approvals_config.tools = {}
            
            session_context = {
                "session_id": "test-session",
                "user_email": "test@example.com"
            }
            
            # Patch the approval requirement check to skip approval
            with patch('backend.application.chat.utilities.tool_executor.requires_approval', return_value=(False, True, False)):
                result = await execute_single_tool(
                    tool_call=mock_tool_call,
                    session_context=session_context,
                    tool_manager=mock_tool_manager,
                    update_callback=None,
                    config_manager=mock_config_manager
                )
            
            # Check for metric logs with tool name
            assert any("[METRIC] Tool call initiated: tool_name=test_tool" in record.message for record in caplog.records)
            assert any("[METRIC] Tool call completed: tool_name=test_tool" in record.message for record in caplog.records)
            
            # Ensure sensitive arguments are not logged
            info_logs = [record.message for record in caplog.records if record.levelno == logging.INFO]
            assert not any("should-not-be-logged" in log for log in info_logs)
            assert not any("secret_key" in log for log in info_logs)

    def test_error_logging_with_type(self, caplog):
        """Test that errors are logged with error type."""
        with caplog.at_level(logging.INFO):
            # Test various error types
            test_error = Exception("Test error message")
            error_class, user_msg, log_msg = classify_llm_error(test_error)
            
            # Check for metric logs with error type
            assert any("[METRIC] Error occurred: error_type=Exception" in record.message for record in caplog.records)
            assert any("category=llm_error" in record.message for record in caplog.records)
            
            # Verify the error type is LLMServiceError (generic)
            assert error_class == LLMServiceError

    @pytest.mark.asyncio
    async def test_file_upload_logging_no_filename(self, caplog):
        """Test that file uploads are logged without file names."""
        with caplog.at_level(logging.INFO):
            # Create mock request
            request = FileUploadRequest(
                filename="secret_document.pdf",
                content_base64="dGVzdA==",  # "test" in base64
                content_type="application/pdf"
            )
            
            # Mock app_factory and file storage
            mock_storage = AsyncMock()
            mock_storage.upload_file = AsyncMock(return_value={
                "key": "test-key",
                "filename": "secret_document.pdf",
                "size": 1024,
                "content_type": "application/pdf",
                "last_modified": "2024-01-01T00:00:00Z",
                "etag": "test-etag",
                "tags": {},
                "user_email": "test@example.com"
            })
            
            with patch('backend.routes.files_routes.app_factory') as mock_factory:
                mock_factory.get_file_storage.return_value = mock_storage
                
                # Mock get_current_user dependency
                with patch('backend.routes.files_routes.get_current_user', return_value="test@example.com"):
                    result = await upload_file(request, "test@example.com")
            
            # Check for metric logs
            assert any("[METRIC] File upload initiated" in record.message for record in caplog.records)
            assert any("[METRIC] File upload completed" in record.message for record in caplog.records)
            
            # Ensure filename is not in metric logs
            info_logs = [record.message for record in caplog.records if record.levelno == logging.INFO and "[METRIC]" in record.message]
            assert not any("secret_document.pdf" in log for log in info_logs)
            
            # But content_type should be logged (not sensitive)
            assert any("content_type=application/pdf" in record.message for record in caplog.records)

    def test_metric_log_format_consistency(self, caplog):
        """Test that all metric logs follow consistent format."""
        with caplog.at_level(logging.INFO):
            # Test error logging format
            test_error = ValueError("test")
            classify_llm_error(test_error)
            
            # Check format: [METRIC] <action>: key=value, key=value
            metric_logs = [record.message for record in caplog.records if "[METRIC]" in record.message]
            
            for log in metric_logs:
                # All metric logs should start with [METRIC]
                assert log.startswith("[METRIC]")
                
                # Should contain key=value pairs
                assert ":" in log
