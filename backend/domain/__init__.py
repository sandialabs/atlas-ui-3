"""Domain layer - pure business models and logic."""

from .errors import (
    DomainError,
    ValidationError,
    SessionError,
    MessageError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    LLMError,
    ToolError
)

from .messages.models import (
    Message,
    MessageRole,
    MessageType,
    ToolCall,
    ToolResult,
    ConversationHistory
)

from .sessions.models import Session

__all__ = [
    # Errors
    "DomainError",
    "ValidationError",
    "SessionError",
    "MessageError",
    "AuthenticationError",
    "AuthorizationError",
    "ConfigurationError",
    "LLMError",
    "ToolError",
    # Messages
    "Message",
    "MessageRole",
    "MessageType",
    "ToolCall",
    "ToolResult",
    "ConversationHistory",
    # Sessions
    "Session",
]
