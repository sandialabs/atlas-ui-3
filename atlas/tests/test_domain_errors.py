"""Tests for domain errors module."""


from atlas.domain.errors import (
    DomainError,
    ValidationError,
    SessionError,
    MessageError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    LLMError,
    LLMServiceError,
    ToolError,
    ToolAuthorizationError,
    DataSourcePermissionError,
    LLMConfigurationError,
    SessionNotFoundError,
    PromptOverrideError,
    RateLimitError,
    LLMTimeoutError,
    LLMAuthenticationError,
)


class TestDomainError:
    """Test suite for DomainError base class."""

    def test_domain_error_with_message_only(self):
        """Test DomainError creation with message only."""
        message = "Something went wrong"
        error = DomainError(message)
        
        assert str(error) == message
        assert error.message == message
        assert error.code is None

    def test_domain_error_with_message_and_code(self):
        """Test DomainError creation with message and code."""
        message = "Something went wrong"
        code = "ERR_001"
        error = DomainError(message, code)
        
        assert str(error) == message
        assert error.message == message
        assert error.code == code

    def test_domain_error_inheritance(self):
        """Test that DomainError inherits from Exception."""
        error = DomainError("test")
        assert isinstance(error, Exception)

    def test_domain_error_with_empty_message(self):
        """Test DomainError with empty message."""
        error = DomainError("")
        assert error.message == ""
        assert str(error) == ""

    def test_domain_error_with_none_code(self):
        """Test DomainError with explicitly None code."""
        error = DomainError("test", None)
        assert error.code is None


class TestValidationError:
    """Test suite for ValidationError."""

    def test_validation_error_inheritance(self):
        """Test that ValidationError inherits from DomainError."""
        error = ValidationError("Invalid input")
        assert isinstance(error, DomainError)
        assert isinstance(error, Exception)

    def test_validation_error_with_code(self):
        """Test ValidationError with error code."""
        error = ValidationError("Invalid email format", "VALIDATION_001")
        assert error.message == "Invalid email format"
        assert error.code == "VALIDATION_001"


class TestSessionError:
    """Test suite for SessionError."""

    def test_session_error_inheritance(self):
        """Test that SessionError inherits from DomainError."""
        error = SessionError("Session expired")
        assert isinstance(error, DomainError)
        assert isinstance(error, Exception)


class TestMessageError:
    """Test suite for MessageError."""

    def test_message_error_inheritance(self):
        """Test that MessageError inherits from DomainError."""
        error = MessageError("Message processing failed")
        assert isinstance(error, DomainError)
        assert isinstance(error, Exception)


class TestAuthenticationError:
    """Test suite for AuthenticationError."""

    def test_authentication_error_inheritance(self):
        """Test that AuthenticationError inherits from DomainError."""
        error = AuthenticationError("Invalid credentials")
        assert isinstance(error, DomainError)
        assert isinstance(error, Exception)


class TestAuthorizationError:
    """Test suite for AuthorizationError."""

    def test_authorization_error_inheritance(self):
        """Test that AuthorizationError inherits from DomainError."""
        error = AuthorizationError("Access denied")
        assert isinstance(error, DomainError)
        assert isinstance(error, Exception)


class TestConfigurationError:
    """Test suite for ConfigurationError."""

    def test_configuration_error_inheritance(self):
        """Test that ConfigurationError inherits from DomainError."""
        error = ConfigurationError("Invalid configuration")
        assert isinstance(error, DomainError)
        assert isinstance(error, Exception)


class TestLLMError:
    """Test suite for LLMError."""

    def test_llm_error_inheritance(self):
        """Test that LLMError inherits from DomainError."""
        error = LLMError("LLM service failed")
        assert isinstance(error, DomainError)
        assert isinstance(error, Exception)


class TestLLMServiceError:
    """Test suite for LLMServiceError."""

    def test_llm_service_error_inheritance(self):
        """Test that LLMServiceError inherits from LLMError."""
        error = LLMServiceError("Service unavailable")
        assert isinstance(error, LLMError)
        assert isinstance(error, DomainError)
        assert isinstance(error, Exception)


class TestToolError:
    """Test suite for ToolError."""

    def test_tool_error_inheritance(self):
        """Test that ToolError inherits from DomainError."""
        error = ToolError("Tool execution failed")
        assert isinstance(error, DomainError)
        assert isinstance(error, Exception)


class TestToolAuthorizationError:
    """Test suite for ToolAuthorizationError."""

    def test_tool_authorization_error_inheritance(self):
        """Test that ToolAuthorizationError inherits from AuthorizationError."""
        error = ToolAuthorizationError("Tool access denied")
        assert isinstance(error, AuthorizationError)
        assert isinstance(error, DomainError)
        assert isinstance(error, Exception)


class TestDataSourcePermissionError:
    """Test suite for DataSourcePermissionError."""

    def test_data_source_permission_error_inheritance(self):
        """Test that DataSourcePermissionError inherits from AuthorizationError."""
        error = DataSourcePermissionError("Data source access denied")
        assert isinstance(error, AuthorizationError)
        assert isinstance(error, DomainError)
        assert isinstance(error, Exception)


class TestLLMConfigurationError:
    """Test suite for LLMConfigurationError."""

    def test_llm_configuration_error_inheritance(self):
        """Test that LLMConfigurationError inherits from ConfigurationError."""
        error = LLMConfigurationError("Invalid LLM config")
        assert isinstance(error, ConfigurationError)
        assert isinstance(error, DomainError)
        assert isinstance(error, Exception)


class TestSessionNotFoundError:
    """Test suite for SessionNotFoundError."""

    def test_session_not_found_error_inheritance(self):
        """Test that SessionNotFoundError inherits from SessionError."""
        error = SessionNotFoundError("Session not found")
        assert isinstance(error, SessionError)
        assert isinstance(error, DomainError)
        assert isinstance(error, Exception)


class TestPromptOverrideError:
    """Test suite for PromptOverrideError."""

    def test_prompt_override_error_inheritance(self):
        """Test that PromptOverrideError inherits from DomainError."""
        error = PromptOverrideError("Prompt override failed")
        assert isinstance(error, DomainError)
        assert isinstance(error, Exception)


class TestRateLimitError:
    """Test suite for RateLimitError."""

    def test_rate_limit_error_inheritance(self):
        """Test that RateLimitError inherits from LLMError."""
        error = RateLimitError("Rate limit exceeded")
        assert isinstance(error, LLMError)
        assert isinstance(error, DomainError)
        assert isinstance(error, Exception)


class TestLLMTimeoutError:
    """Test suite for LLMTimeoutError."""

    def test_llm_timeout_error_inheritance(self):
        """Test that LLMTimeoutError inherits from LLMError."""
        error = LLMTimeoutError("Request timed out")
        assert isinstance(error, LLMError)
        assert isinstance(error, DomainError)
        assert isinstance(error, Exception)


class TestLLMAuthenticationError:
    """Test suite for LLMAuthenticationError."""

    def test_llm_authentication_error_inheritance(self):
        """Test that LLMAuthenticationError inherits from AuthenticationError."""
        error = LLMAuthenticationError("LLM authentication failed")
        assert isinstance(error, AuthenticationError)
        assert isinstance(error, DomainError)
        assert isinstance(error, Exception)


class TestErrorHierarchy:
    """Test suite for error hierarchy and relationships."""

    def test_all_errors_inherit_from_domain_error(self):
        """Test that all custom errors inherit from DomainError."""
        error_classes = [
            ValidationError,
            SessionError,
            MessageError,
            AuthenticationError,
            AuthorizationError,
            ConfigurationError,
            LLMError,
            LLMServiceError,
            ToolError,
            ToolAuthorizationError,
            DataSourcePermissionError,
            LLMConfigurationError,
            SessionNotFoundError,
            PromptOverrideError,
            RateLimitError,
            LLMTimeoutError,
            LLMAuthenticationError,
        ]
        
        for error_class in error_classes:
            error = error_class("test message")
            assert isinstance(error, DomainError)
            assert isinstance(error, Exception)

    def test_error_message_and_code_preservation(self):
        """Test that all error types preserve message and code correctly."""
        error_classes = [
            DomainError,
            ValidationError,
            SessionError,
            MessageError,
            AuthenticationError,
            AuthorizationError,
            ConfigurationError,
            LLMError,
            LLMServiceError,
            ToolError,
            ToolAuthorizationError,
            DataSourcePermissionError,
            LLMConfigurationError,
            SessionNotFoundError,
            PromptOverrideError,
            RateLimitError,
            LLMTimeoutError,
            LLMAuthenticationError,
        ]
        
        test_message = "Test error message"
        test_code = "TEST_001"
        
        for error_class in error_classes:
            error = error_class(test_message, test_code)
            assert error.message == test_message
            assert error.code == test_code
            assert str(error) == test_message

    def test_specific_inheritance_relationships(self):
        """Test specific inheritance relationships between error types."""
        # Test LLM-related errors
        assert issubclass(LLMServiceError, LLMError)
        assert issubclass(RateLimitError, LLMError)
        assert issubclass(LLMTimeoutError, LLMError)
        
        # Test authorization-related errors
        assert issubclass(ToolAuthorizationError, AuthorizationError)
        assert issubclass(DataSourcePermissionError, AuthorizationError)
        
        # Test authentication-related errors
        assert issubclass(LLMAuthenticationError, AuthenticationError)
        
        # Test configuration-related errors
        assert issubclass(LLMConfigurationError, ConfigurationError)
        
        # Test session-related errors
        assert issubclass(SessionNotFoundError, SessionError)