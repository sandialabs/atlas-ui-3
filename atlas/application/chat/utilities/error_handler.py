"""
Error handling utilities - pure functions for exception handling patterns.

This module provides stateless utility functions for consistent error handling
across chat operations without maintaining any state.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from atlas.domain.errors import (
    CONTEXT_WINDOW_KEYWORDS,
    AuthenticationError,
    AuthorizationError,
    ContextWindowExceededError,
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMMalformedToolCallError,
    LLMServiceError,
    LLMTimeoutError,
    RateLimitError,
    ValidationError,
)
from atlas.domain.messages.models import MessageType

logger = logging.getLogger(__name__)

async def safe_get_tools_schema(
    tool_manager,
    selected_tools: List[str],
    user_email: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Safely get tools schema with error handling.

    Pure function that handles tool schema retrieval errors.
    """
    if not tool_manager:
        raise ValidationError("Tool manager not configured")

    try:
        tools_schema = tool_manager.get_tools_schema(selected_tools, user_email)
        logger.info(f"Got {len(tools_schema)} tool schemas for selected tools: {selected_tools}")
        return tools_schema
    except Exception as e:
        logger.error(f"Error getting tools schema: {e}", exc_info=True)
        raise ValidationError(f"Failed to get tools schema: {str(e)}")


# Mirrors the error_type strings the WebSocket handler in atlas/main.py sends,
# so clients can branch on one vocabulary regardless of which path raised.
# Ordered most specific first: lookup walks it, so a subclass resolves to its
# nearest listed ancestor rather than falling through to "unexpected".
_ERROR_TYPE_BY_CLASS = (
    (RateLimitError, "rate_limit"),
    (LLMTimeoutError, "timeout"),
    (LLMAuthenticationError, "authentication"),
    (AuthenticationError, "authentication"),
    (AuthorizationError, "authorization"),
    (ContextWindowExceededError, "context_window_exceeded"),
    (ValidationError, "validation"),
    (LLMBadRequestError, "bad_request"),
    (LLMMalformedToolCallError, "malformed_tool_call"),
    (LLMServiceError, "domain"),
)


def error_type_for(error_class: type) -> str:
    """Map a domain error class to the ``error_type`` string sent to clients."""
    if not isinstance(error_class, type):
        return "unexpected"
    for known, error_type in _ERROR_TYPE_BY_CLASS:
        if issubclass(error_class, known):
            return error_type
    return "unexpected"


def classify_llm_error(error: Exception) -> Tuple[type, str, str]:
    """
    Classify LLM errors and return appropriate error type, user message, and log message.

    Returns:
        Tuple of (error_class, user_message, log_message).

    NOTE: user_message MUST NOT contain raw exception details or sensitive data.
    """
    # Errors that already carry a specific, user-safe message must not be
    # re-generalized here: str(error) is that message, so keyword matching
    # below would classify the message rather than the original failure.
    if isinstance(error, LLMBadRequestError):
        return (LLMBadRequestError, error.message, f"LLM rejected the request: {error.message}")
    if isinstance(error, (AuthenticationError, AuthorizationError)):
        # E.g. "not a member of the selected LiteLLM team" or "please sign in
        # again": raised by Atlas itself with a message meant for the user.
        return (type(error), error.message, f"{type(error).__name__}: {error.message}")
    if isinstance(error, LLMMalformedToolCallError):
        return (
            LLMMalformedToolCallError,
            error.message,
            f"Model returned an unusable tool call: {error.message}",
        )

    error_str = str(error)
    error_type_name = type(error).__name__

    # Check for rate limiting errors
    if "RateLimitError" in error_type_name or "rate limit" in error_str.lower() or "high traffic" in error_str.lower():
        user_msg = "The LLM service is experiencing high traffic. Please try again in a moment."
        log_msg = f"Rate limit error: {error_str}"
        return (RateLimitError, user_msg, log_msg)

    # Check for timeout errors
    if "timeout" in error_str.lower() or "timed out" in error_str.lower():
        user_msg = "The LLM service request timed out. Please try again."
        log_msg = f"Timeout error: {error_str}"
        return (LLMTimeoutError, user_msg, log_msg)

    # Check for authentication/authorization errors
    if any(keyword in error_str.lower() for keyword in ["unauthorized", "authentication", "invalid api key", "invalid_api_key", "api key"]):
        user_msg = "There was an authentication issue with the LLM service. Please contact your administrator."
        log_msg = f"Authentication error: {error_str}"
        return (LLMAuthenticationError, user_msg, log_msg)

    # Check for context window exceeded errors
    if isinstance(error, ContextWindowExceededError) or "ContextWindowExceeded" in error_type_name or any(
        kw in error_str.lower() for kw in CONTEXT_WINDOW_KEYWORDS
    ):
        user_msg = "Your conversation is too long for this model's context window. Please start a new conversation or switch to a model with a larger context window."
        log_msg = f"Context window exceeded: {error_str}"
        return (ContextWindowExceededError, user_msg, log_msg)

    # Generic LLM service error (non-validation)
    user_msg = "The LLM service encountered an error. Please try again or contact support if the issue persists."
    log_msg = f"LLM error: {error_str}"
    return (LLMServiceError, user_msg, log_msg)


def handle_chat_message_error(
    error: Exception,
    context: str = "chat message handling"
) -> Dict[str, str]:
    """
    Handle chat message errors with consistent logging and response.

    Pure function that provides standard chat error handling.
    """
    logger.error(f"Error in {context}: {error}", exc_info=True)
    return {
        "type": MessageType.ERROR.value,
        "message": str(error)
    }


def sanitize_kwargs_for_logging(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize kwargs for safe logging by replacing large objects with summaries.

    Pure function that creates a sanitized copy for logging purposes.
    Used to prevent large file contents from cluttering logs.
    """
    try:
        sanitized_kwargs = dict(kwargs)
        if "files" in sanitized_kwargs and isinstance(sanitized_kwargs["files"], dict):
            sanitized_kwargs["files"] = list(sanitized_kwargs["files"].keys())
        return sanitized_kwargs
    except Exception:
        return {k: ("<error sanitizing>") for k in kwargs.keys()}
