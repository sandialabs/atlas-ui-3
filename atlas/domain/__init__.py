"""Domain layer - pure business models and logic."""

from .errors import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    DomainError,
    LLMError,
    MessageError,
    SessionError,
    ToolError,
    ValidationError,
)
from .messages.models import ConversationHistory, Message, MessageRole, MessageType, ToolCall, ToolResult
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
