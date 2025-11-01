"""Domain-level errors and exceptions."""

from typing import Optional


class DomainError(Exception):
    """Base domain error."""
    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.code = code


class ValidationError(DomainError):
    """Validation error."""
    pass


class SessionError(DomainError):
    """Session-related error."""
    pass


class MessageError(DomainError):
    """Message-related error."""
    pass


class AuthenticationError(DomainError):
    """Authentication error."""
    pass


class AuthorizationError(DomainError):
    """Authorization error."""
    pass


class ConfigurationError(DomainError):
    """Configuration error."""
    pass


class LLMError(DomainError):
    """LLM-related error."""
    pass


class ToolError(DomainError):
    """Tool execution error."""
    pass


class ToolAuthorizationError(AuthorizationError):
    """Raised when user is not authorized to use a specific tool."""
    pass


class DataSourcePermissionError(AuthorizationError):
    """Raised when user lacks permission to access a data source."""
    pass


class LLMConfigurationError(ConfigurationError):
    """Raised when LLM configuration is invalid or incomplete."""
    pass


class SessionNotFoundError(SessionError):
    """Raised when a session cannot be found."""
    pass


class PromptOverrideError(DomainError):
    """Raised when MCP prompt override fails."""
    pass
