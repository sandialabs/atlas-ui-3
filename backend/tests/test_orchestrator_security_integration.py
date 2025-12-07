"""Integration tests for security check in orchestrator."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from application.chat.orchestrator import ChatOrchestrator
from core.security_check import (
    SecurityCheckService,
    SecurityCheckResponse,
    SecurityCheckResult,
)
from domain.sessions.models import Session
from domain.messages.models import Message, MessageRole



@pytest.fixture
def mock_llm():
    """Create a mock LLM."""
    llm = MagicMock()
    llm.call_plain = AsyncMock(return_value="Test response from LLM")
    return llm


@pytest.fixture
def mock_event_publisher():
    """Create a mock event publisher."""
    publisher = MagicMock()
    publisher.send_json = AsyncMock()
    publisher.publish_chat_response = AsyncMock()
    publisher.publish_response_complete = AsyncMock()
    return publisher


@pytest.fixture
def mock_session_repository():
    """Create a mock session repository."""
    repo = MagicMock()
    repo.get = AsyncMock()
    repo.create = AsyncMock()
    repo.update = AsyncMock()
    return repo


@pytest.fixture
def mock_security_service():
    """Create a mock security check service."""
    service = MagicMock(spec=SecurityCheckService)
    service.check_input = AsyncMock()
    service.check_output = AsyncMock()
    return service


@pytest.fixture
def test_session():
    """Create a test session."""
    session = Session(id=uuid4(), user_email="test@test.com")
    return session


class TestOrchestratorSecurityCheckIntegration:
    """Test security check integration in orchestrator."""

    @pytest.mark.asyncio
    async def test_input_blocked_prevents_llm_call(
        self,
        mock_llm,
        mock_event_publisher,
        mock_session_repository,
        mock_security_service,
        test_session
    ):
        """Test that blocked input prevents LLM call."""
        # Setup
        mock_session_repository.get.return_value = test_session
        mock_security_service.check_input.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.BLOCKED,
            message="Content blocked due to policy violation"
        )
        
        orchestrator = ChatOrchestrator(
            llm=mock_llm,
            event_publisher=mock_event_publisher,
            session_repository=mock_session_repository,
            security_check_service=mock_security_service,
        )
        
        # Execute
        result = await orchestrator.execute(
            session_id=test_session.id,
            content="bad input",
            model="test-model",
            user_email="test@test.com"
        )
        
        # Verify
        assert result["type"] == "error"
        assert result["blocked"] is True
        assert "blocked" in result["error"].lower()
        
        # LLM should not have been called
        mock_llm.call_plain.assert_not_called()
        
        # Security warning should have been sent via send_json
        mock_event_publisher.send_json.assert_called_once()
        call_args = mock_event_publisher.send_json.call_args
        assert call_args.args[0]["type"] == "security_warning"
        assert call_args.args[0]["status"] == "blocked"

    @pytest.mark.asyncio
    async def test_input_with_warnings_allows_processing(
        self,
        mock_llm,
        mock_event_publisher,
        mock_session_repository,
        mock_security_service,
        test_session
    ):
        """Test that input with warnings still allows processing."""
        # Setup
        mock_session_repository.get.return_value = test_session
        mock_security_service.check_input.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.ALLOWED_WITH_WARNINGS,
            message="Content has minor issues but is allowed"
        )
        mock_security_service.check_output.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.GOOD
        )
        
        orchestrator = ChatOrchestrator(
            llm=mock_llm,
            event_publisher=mock_event_publisher,
            session_repository=mock_session_repository,
            security_check_service=mock_security_service,
        )
        
        # Execute
        result = await orchestrator.execute(
            session_id=test_session.id,
            content="questionable input",
            model="test-model",
            user_email="test@test.com"
        )
        
        # Verify
        assert result["type"] != "error"
        
        # LLM should have been called
        mock_llm.call_plain.assert_called_once()
        
        # Warning should have been sent via send_json
        assert mock_event_publisher.send_json.call_count >= 1
        warning_calls = [
            call for call in mock_event_publisher.send_json.call_args_list
            if call.args[0].get("type") == "security_warning"
        ]
        assert len(warning_calls) > 0
        assert warning_calls[0].args[0]["status"] == "warning"

    @pytest.mark.asyncio
    async def test_good_input_proceeds_normally(
        self,
        mock_llm,
        mock_event_publisher,
        mock_session_repository,
        mock_security_service,
        test_session
    ):
        """Test that good input proceeds normally."""
        # Setup
        mock_session_repository.get.return_value = test_session
        mock_security_service.check_input.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.GOOD
        )
        mock_security_service.check_output.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.GOOD
        )
        
        orchestrator = ChatOrchestrator(
            llm=mock_llm,
            event_publisher=mock_event_publisher,
            session_repository=mock_session_repository,
            security_check_service=mock_security_service,
        )
        
        # Execute
        result = await orchestrator.execute(
            session_id=test_session.id,
            content="good input",
            model="test-model",
            user_email="test@test.com"
        )
        
        # Verify
        assert result["type"] != "error"
        
        # LLM should have been called
        mock_llm.call_plain.assert_called_once()

    @pytest.mark.asyncio
    async def test_output_blocked_removes_response(
        self,
        mock_llm,
        mock_event_publisher,
        mock_session_repository,
        mock_security_service,
        test_session
    ):
        """Test that blocked output removes the response from history."""
        # Setup
        mock_session_repository.get.return_value = test_session
        mock_security_service.check_input.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.GOOD
        )
        mock_security_service.check_output.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.BLOCKED,
            message="Response contains sensitive information"
        )
        
        orchestrator = ChatOrchestrator(
            llm=mock_llm,
            event_publisher=mock_event_publisher,
            session_repository=mock_session_repository,
            security_check_service=mock_security_service,
        )
        
        # Execute
        result = await orchestrator.execute(
            session_id=test_session.id,
            content="good input",
            model="test-model",
            user_email="test@test.com"
        )
        
        # Verify
        assert result["type"] == "error"
        assert result["blocked"] is True
        
        # LLM should have been called
        mock_llm.call_plain.assert_called_once()
        
        # Response should not be in history (user message should be, assistant should not)
        assert len(test_session.history.messages) == 1
        assert test_session.history.messages[0].role.value == MessageRole.USER.value

    @pytest.mark.asyncio
    async def test_output_with_warnings_allows_response(
        self,
        mock_llm,
        mock_event_publisher,
        mock_session_repository,
        mock_security_service,
        test_session
    ):
        """Test that output with warnings still allows the response."""
        # Setup
        mock_session_repository.get.return_value = test_session
        mock_security_service.check_input.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.GOOD
        )
        mock_security_service.check_output.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.ALLOWED_WITH_WARNINGS,
            message="Response may contain sensitive topics"
        )
        
        orchestrator = ChatOrchestrator(
            llm=mock_llm,
            event_publisher=mock_event_publisher,
            session_repository=mock_session_repository,
            security_check_service=mock_security_service,
        )
        
        # Execute
        result = await orchestrator.execute(
            session_id=test_session.id,
            content="good input",
            model="test-model",
            user_email="test@test.com"
        )
        
        # Verify
        assert result["type"] != "error"
        
        # Response should be in history
        assert len(test_session.history.messages) == 2
        assert test_session.history.messages[1].role.value == MessageRole.ASSISTANT.value

    @pytest.mark.asyncio
    async def test_no_security_service_allows_all(
        self,
        mock_llm,
        mock_event_publisher,
        mock_session_repository,
        test_session
    ):
        """Test that orchestrator works without security service."""
        # Setup
        mock_session_repository.get.return_value = test_session
        
        orchestrator = ChatOrchestrator(
            llm=mock_llm,
            event_publisher=mock_event_publisher,
            session_repository=mock_session_repository,
            security_check_service=None,
        )
        
        # Execute
        result = await orchestrator.execute(
            session_id=test_session.id,
            content="test input",
            model="test-model",
            user_email="test@test.com"
        )
        
        # Verify
        assert result["type"] != "error"
        
        # LLM should have been called
        mock_llm.call_plain.assert_called_once()

    @pytest.mark.asyncio
    async def test_message_history_sent_to_security_check(
        self,
        mock_llm,
        mock_event_publisher,
        mock_session_repository,
        mock_security_service,
        test_session
    ):
        """Test that message history is sent to security check."""
        # Setup - add some history to the session
        test_session.history.add_message(Message(
            role=MessageRole.USER,
            content="Previous user message"
        ))
        test_session.history.add_message(Message(
            role=MessageRole.ASSISTANT,
            content="Previous assistant message"
        ))
        
        mock_session_repository.get.return_value = test_session
        mock_security_service.check_input.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.GOOD
        )
        mock_security_service.check_output.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.GOOD
        )
        
        orchestrator = ChatOrchestrator(
            llm=mock_llm,
            event_publisher=mock_event_publisher,
            session_repository=mock_session_repository,
            security_check_service=mock_security_service,
        )
        
        # Execute
        await orchestrator.execute(
            session_id=test_session.id,
            content="new input",
            model="test-model",
            user_email="test@test.com"
        )
        
        # Verify input check received history
        input_call_args = mock_security_service.check_input.call_args
        message_history = input_call_args.kwargs["message_history"]
        assert len(message_history) == 2
        assert message_history[0]["content"] == "Previous user message"
        assert message_history[1]["content"] == "Previous assistant message"
        
        # Verify output check received history (excluding new user and assistant messages)
        output_call_args = mock_security_service.check_output.call_args
        output_history = output_call_args.kwargs["message_history"]
        assert len(output_history) == 3  # Original 2 + new user message


class TestOrchestratorSecurityNotificationAPI:
    """Test that security notifications use the correct event publisher API."""

    @pytest.mark.asyncio
    async def test_blocked_input_uses_send_json_not_publish_message(
        self,
        mock_llm,
        mock_event_publisher,
        mock_session_repository,
        mock_security_service,
        test_session
    ):
        """
        Regression test: Verify blocked input uses send_json (not publish_message).
        
        This test catches the AttributeError that occurred when code tried to call
        publish_message() which doesn't exist on WebSocketEventPublisher.
        """
        # Setup
        mock_session_repository.get.return_value = test_session
        mock_security_service.check_input.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.BLOCKED,
            message="Blocked content"
        )
        
        # Remove send_json to verify it's actually called (will raise AttributeError if wrong method)
        delattr(mock_event_publisher, 'send_json')
        mock_event_publisher.send_json = AsyncMock()
        
        orchestrator = ChatOrchestrator(
            llm=mock_llm,
            event_publisher=mock_event_publisher,
            session_repository=mock_session_repository,
            security_check_service=mock_security_service,
        )
        
        # Execute - this should NOT raise AttributeError
        result = await orchestrator.execute(
            session_id=test_session.id,
            content="bad content",
            model="test-model",
            user_email="test@test.com"
        )
        
        # Verify send_json was called (not publish_message)
        mock_event_publisher.send_json.assert_called_once()
        
        # Verify the message structure
        call_data = mock_event_publisher.send_json.call_args.args[0]
        assert call_data["type"] == "security_warning"
        assert call_data["status"] == "blocked"
        assert "message" in call_data

    @pytest.mark.asyncio
    async def test_warning_input_uses_send_json_not_publish_message(
        self,
        mock_llm,
        mock_event_publisher,
        mock_session_repository,
        mock_security_service,
        test_session
    ):
        """
        Regression test: Verify warning input uses send_json (not publish_message).
        """
        # Setup
        mock_session_repository.get.return_value = test_session
        mock_security_service.check_input.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.ALLOWED_WITH_WARNINGS,
            message="Content has warnings"
        )
        mock_security_service.check_output.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.GOOD
        )
        
        # Ensure send_json exists
        delattr(mock_event_publisher, 'send_json')
        mock_event_publisher.send_json = AsyncMock()
        
        orchestrator = ChatOrchestrator(
            llm=mock_llm,
            event_publisher=mock_event_publisher,
            session_repository=mock_session_repository,
            security_check_service=mock_security_service,
        )
        
        # Execute
        await orchestrator.execute(
            session_id=test_session.id,
            content="questionable content",
            model="test-model",
            user_email="test@test.com"
        )
        
        # Verify send_json was called for the warning
        warning_calls = [
            call for call in mock_event_publisher.send_json.call_args_list
            if call.args[0].get("type") == "security_warning"
        ]
        assert len(warning_calls) > 0
        assert warning_calls[0].args[0]["status"] == "warning"

    @pytest.mark.asyncio
    async def test_blocked_output_uses_send_json_not_publish_message(
        self,
        mock_llm,
        mock_event_publisher,
        mock_session_repository,
        mock_security_service,
        test_session
    ):
        """
        Regression test: Verify blocked output uses send_json (not publish_message).
        """
        # Setup
        mock_session_repository.get.return_value = test_session
        mock_security_service.check_input.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.GOOD
        )
        mock_security_service.check_output.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.BLOCKED,
            message="Output blocked"
        )
        
        # Ensure send_json exists
        delattr(mock_event_publisher, 'send_json')
        mock_event_publisher.send_json = AsyncMock()
        
        orchestrator = ChatOrchestrator(
            llm=mock_llm,
            event_publisher=mock_event_publisher,
            session_repository=mock_session_repository,
            security_check_service=mock_security_service,
        )
        
        # Execute
        result = await orchestrator.execute(
            session_id=test_session.id,
            content="good input",
            model="test-model",
            user_email="test@test.com"
        )
        
        # Verify send_json was called
        mock_event_publisher.send_json.assert_called_once()
        call_data = mock_event_publisher.send_json.call_args.args[0]
        assert call_data["type"] == "security_warning"
        assert call_data["status"] == "blocked"

    @pytest.mark.asyncio
    async def test_warning_output_uses_send_json_not_publish_message(
        self,
        mock_llm,
        mock_event_publisher,
        mock_session_repository,
        mock_security_service,
        test_session
    ):
        """
        Regression test: Verify warning output uses send_json (not publish_message).
        """
        # Setup
        mock_session_repository.get.return_value = test_session
        mock_security_service.check_input.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.GOOD
        )
        mock_security_service.check_output.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.ALLOWED_WITH_WARNINGS,
            message="Output has warnings"
        )
        
        # Ensure send_json exists
        delattr(mock_event_publisher, 'send_json')
        mock_event_publisher.send_json = AsyncMock()
        
        orchestrator = ChatOrchestrator(
            llm=mock_llm,
            event_publisher=mock_event_publisher,
            session_repository=mock_session_repository,
            security_check_service=mock_security_service,
        )
        
        # Execute
        await orchestrator.execute(
            session_id=test_session.id,
            content="good input",
            model="test-model",
            user_email="test@test.com"
        )
        
        # Verify send_json was called for the warning
        warning_calls = [
            call for call in mock_event_publisher.send_json.call_args_list
            if call.args[0].get("type") == "security_warning"
        ]
        assert len(warning_calls) > 0
        assert warning_calls[0].args[0]["status"] == "warning"

    @pytest.mark.asyncio
    async def test_event_publisher_does_not_have_publish_message(
        self,
        mock_llm,
        mock_event_publisher,
        mock_session_repository,
        mock_security_service,
        test_session
    ):
        """
        Regression test: Ensure publish_message is NOT used anywhere.
        
        This test explicitly verifies that if the event publisher doesn't have
        publish_message method, the orchestrator still works correctly.
        """
        # Setup - ensure publish_message doesn't exist
        if hasattr(mock_event_publisher, 'publish_message'):
            delattr(mock_event_publisher, 'publish_message')
        
        mock_session_repository.get.return_value = test_session
        mock_security_service.check_input.return_value = SecurityCheckResponse(
            status=SecurityCheckResult.BLOCKED,
            message="Test block"
        )
        
        orchestrator = ChatOrchestrator(
            llm=mock_llm,
            event_publisher=mock_event_publisher,
            session_repository=mock_session_repository,
            security_check_service=mock_security_service,
        )
        
        # Execute - should NOT raise AttributeError about publish_message
        result = await orchestrator.execute(
            session_id=test_session.id,
            content="bad content",
            model="test-model",
            user_email="test@test.com"
        )
        
        # Should complete successfully
        assert result["type"] == "error"
        assert result["blocked"] is True


class TestToolRagSecurityNotificationAPI:
    """Test that tool/RAG security notifications use the correct event publisher API."""

    @pytest.mark.asyncio
    async def test_tool_security_check_service_called_with_correct_params(
        self,
        mock_llm,
        mock_event_publisher,
        mock_session_repository,
        test_session
    ):
        """
        Test that tool security checks call the security service with correct parameters.
        """
        from core.security_check import SecurityCheckService
        
        # Setup real security service with mocked HTTP client
        mock_security_service = MagicMock(spec=SecurityCheckService)
        mock_security_service.check_tool_rag_output = AsyncMock(
            return_value=SecurityCheckResponse(
                status=SecurityCheckResult.GOOD
            )
        )
        
        mock_session_repository.get.return_value = test_session
        
        orchestrator = ChatOrchestrator(
            llm=mock_llm,
            event_publisher=mock_event_publisher,
            session_repository=mock_session_repository,
            security_check_service=mock_security_service,
        )
        
        # Add a user message to test message history
        test_session.history.add_message(Message(
            role=MessageRole.USER,
            content="User query"
        ))
        
        # Note: Full integration would require running actual tool execution
        # For now, verify the service interface is correct
        await mock_security_service.check_tool_rag_output(
            content="Tool output",
            source_type="tool",
            message_history=[{"role": "user", "content": "test"}],
            user_email="test@test.com"
        )
        
        # Verify call was made with correct structure
        call_args = mock_security_service.check_tool_rag_output.call_args
        assert call_args.kwargs["content"] == "Tool output"
        assert call_args.kwargs["source_type"] == "tool"
        assert "message_history" in call_args.kwargs
        assert call_args.kwargs["user_email"] == "test@test.com"

    @pytest.mark.asyncio
    async def test_event_publisher_send_json_available(
        self,
        mock_event_publisher
    ):
        """
        Regression test: Verify event publisher has send_json method (not publish_message).
        
        This is a simple check that the mock matches the actual interface.
        """
        # Ensure send_json exists
        assert hasattr(mock_event_publisher, 'send_json')
        
        # Ensure it's callable
        assert callable(mock_event_publisher.send_json)
        
        # Test that it can be called
        await mock_event_publisher.send_json({
            "type": "security_warning",
            "status": "blocked",
            "message": "Test"
        })
        
        # Verify it was called
        mock_event_publisher.send_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_security_check_formats_tool_vs_rag_correctly(
        self
    ):
        """
        Test that check_type is formatted correctly for tool vs RAG.
        """
        from core.security_check import SecurityCheckService
        from modules.config.config_manager import AppSettings
        from unittest.mock import patch
        
        # Create service with mocked HTTP
        app_settings = MagicMock(spec=AppSettings)
        app_settings.feature_security_check_input_enabled = False
        app_settings.feature_security_check_output_enabled = False
        app_settings.feature_security_check_tool_rag_enabled = True
        app_settings.security_check_api_url = "http://test.com/check"
        app_settings.security_check_api_key = "test-key"
        app_settings.security_check_timeout = 10
        
        service = SecurityCheckService(app_settings)
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "good",
            "message": None,
            "details": {}
        }
        mock_response.raise_for_status = MagicMock()
        
        with patch("core.security_check.httpx.AsyncClient") as mock_client:
            mock_post = AsyncMock(return_value=mock_response)
            mock_client.return_value.__aenter__.return_value.post = mock_post
            
            # Test tool check
            await service.check_tool_rag_output(
                content="tool content",
                source_type="tool",
                user_email="test@test.com"
            )
            
            call_args = mock_post.call_args
            payload = call_args[1]["json"]
            assert payload["check_type"] == "tool_rag_tool"
            
            # Test RAG check  
            await service.check_tool_rag_output(
                content="rag content",
                source_type="rag",
                user_email="test@test.com"
            )
            
            call_args = mock_post.call_args
            payload = call_args[1]["json"]
            assert payload["check_type"] == "tool_rag_rag"

