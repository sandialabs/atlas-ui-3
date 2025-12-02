"""Integration tests for security check in orchestrator."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from backend.application.chat.orchestrator import ChatOrchestrator
from backend.core.security_check import (
    SecurityCheckService,
    SecurityCheckResponse,
    SecurityCheckResult,
)
from backend.domain.sessions.models import Session
from backend.domain.messages.models import Message, MessageRole
from backend.modules.config.config_manager import AppSettings


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
    publisher.publish_message = AsyncMock()
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
        
        # Security warning should have been published
        mock_event_publisher.publish_message.assert_called_once()
        call_args = mock_event_publisher.publish_message.call_args
        assert call_args.kwargs["message_type"] == "security_warning"
        assert call_args.kwargs["content"]["type"] == "input_blocked"

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
        
        # Warning should have been published
        assert mock_event_publisher.publish_message.call_count >= 1
        warning_calls = [
            call for call in mock_event_publisher.publish_message.call_args_list
            if call.kwargs.get("message_type") == "security_warning"
        ]
        assert len(warning_calls) > 0

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
