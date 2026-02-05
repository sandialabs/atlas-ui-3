"""Tests for error classification and user-friendly error messages."""

from atlas.application.chat.utilities.error_handler import classify_llm_error
from atlas.domain.errors import RateLimitError, LLMTimeoutError, LLMAuthenticationError, LLMServiceError


class TestErrorClassification:
    """Test error classification for LLM errors."""

    def test_classify_rate_limit_error_by_type_name(self):
        """Test classification of rate limit errors by exception type name."""
        # Create a custom exception class to test type name detection
        class RateLimitError(Exception):
            pass
        
        error = RateLimitError("Some error message")
        
        from atlas.domain.errors import RateLimitError as DomainRateLimitError
        error_class, user_msg, log_msg = classify_llm_error(error)
        
        assert error_class == DomainRateLimitError
        assert "high traffic" in user_msg.lower()
        assert "try again" in user_msg.lower()
        assert "rate limit" in log_msg.lower()

    def test_classify_rate_limit_error_by_message_content(self):
        """Test classification of rate limit errors by message content."""
        error = Exception("We're experiencing high traffic right now! Please try again soon.")
        
        error_class, user_msg, log_msg = classify_llm_error(error)
        
        assert error_class == RateLimitError
        assert "high traffic" in user_msg.lower()
        assert "try again" in user_msg.lower()

    def test_classify_rate_limit_error_alternative_message(self):
        """Test classification of rate limit errors with alternative wording."""
        error = Exception("Rate limit exceeded for this API key")
        
        error_class, user_msg, log_msg = classify_llm_error(error)
        
        assert error_class == RateLimitError
        assert "try again" in user_msg.lower()

    def test_classify_timeout_error(self):
        """Test classification of timeout errors."""
        error = Exception("Request timed out after 30 seconds")
        
        error_class, user_msg, log_msg = classify_llm_error(error)
        
        assert error_class == LLMTimeoutError
        assert "timeout" in user_msg.lower() or "timed out" in user_msg.lower()
        assert "try again" in user_msg.lower()

    def test_classify_authentication_error(self):
        """Test classification of authentication errors."""
        error = Exception("Invalid API key provided")
        
        error_class, user_msg, log_msg = classify_llm_error(error)
        
        assert error_class == LLMAuthenticationError
        assert "authentication" in user_msg.lower()
        assert "administrator" in user_msg.lower()

    def test_classify_unauthorized_error(self):
        """Test classification of unauthorized errors."""
        error = Exception("Unauthorized access")
        
        error_class, user_msg, log_msg = classify_llm_error(error)
        
        assert error_class == LLMAuthenticationError
        assert "authentication" in user_msg.lower()

    def test_classify_generic_llm_error(self):
        """Test classification of generic LLM errors."""
        error = Exception("Something went wrong with the model")
        
        error_class, user_msg, log_msg = classify_llm_error(error)
        
        assert error_class == LLMServiceError
        assert "error" in user_msg.lower()
        assert "try again" in user_msg.lower() or "contact support" in user_msg.lower()

    def test_error_messages_are_user_friendly(self):
        """Test that all error messages are user-friendly (no technical details)."""
        test_errors = [
            Exception("RateLimitError: Rate limit exceeded"),
            Exception("Request timeout after 60s"),
            Exception("Invalid API key: abc123"),
            Exception("Unknown model error"),
        ]
        
        for error in test_errors:
            _, user_msg, _ = classify_llm_error(error)
            
            # User messages should be helpful and not expose technical details
            assert len(user_msg) > 20  # Should be a complete sentence
            # Technical details should not appear in user message
            technical_substrings = ["RateLimitError:", "abc123", "stack trace"]
            for technical in technical_substrings:
                assert technical not in user_msg, f"User message should not contain technical detail: {technical}"
            assert user_msg[0].isupper()  # Starts with capital letter
            assert user_msg.endswith(".")  # Ends with period

    def test_log_messages_contain_error_details(self):
        """Test that log messages contain error details for debugging."""
        error = Exception("RateLimitError: We're experiencing high traffic")
        
        _, _, log_msg = classify_llm_error(error)
        
        # Log message should contain the actual error for debugging
        assert "high traffic" in log_msg.lower()
        assert len(log_msg) > 10
