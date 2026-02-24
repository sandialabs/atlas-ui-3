"""
Error handling utilities - pure functions for exception handling patterns.

This module provides stateless utility functions for consistent error handling
across chat operations without maintaining any state.
"""

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from atlas.domain.errors import (
    LLMAuthenticationError,
    LLMServiceError,
    LLMTimeoutError,
    RateLimitError,
    ValidationError,
)
from atlas.domain.messages.models import MessageType

logger = logging.getLogger(__name__)

# Type hint for update callback
UpdateCallback = Callable[[Dict[str, Any]], Awaitable[None]]


async def safe_execute_with_tools(
    execution_func: Callable,
    *args,
    **kwargs
) -> Dict[str, Any]:
    """
    Safely execute tools mode with centralized exception handling.

    Pure function that wraps any execution function with error handling.
    """
    try:
        return await execution_func(*args, **kwargs)
    except ValidationError:
        raise  # Re-raise validation errors
    except Exception as e:
        logger.error(f"Error in tools mode execution: {e}", exc_info=True)
        return {
            "type": MessageType.ERROR.value,
            "message": f"Tools execution failed: {str(e)}"
        }


async def safe_get_tools_schema(
    tool_manager,
    selected_tools: List[str]
) -> List[Dict[str, Any]]:
    """
    Safely get tools schema with error handling.

    Pure function that handles tool schema retrieval errors.
    """
    if not tool_manager:
        raise ValidationError("Tool manager not configured")

    try:
        tools_schema = tool_manager.get_tools_schema(selected_tools)
        logger.info(f"Got {len(tools_schema)} tool schemas for selected tools: {selected_tools}")
        return tools_schema
    except Exception as e:
        logger.error(f"Error getting tools schema: {e}", exc_info=True)
        raise ValidationError(f"Failed to get tools schema: {str(e)}")


def classify_llm_error(error: Exception) -> Tuple[type, str, str]:
    """
    Classify LLM errors and return appropriate error type, user message, and log message.

    Returns:
        Tuple of (error_class, user_message, log_message).

    NOTE: user_message MUST NOT contain raw exception details or sensitive data.
    """
    error_str = str(error)
    error_type_name = type(error).__name__

    # Check for rate limiting errors
    if "RateLimitError" in error_type_name or "rate limit" in error_str.lower() or "high traffic" in error_str.lower():
        user_msg = "The AI service is experiencing high traffic. Please try again in a moment."
        log_msg = f"Rate limit error: {error_str}"
        return (RateLimitError, user_msg, log_msg)

    # Check for timeout errors
    if "timeout" in error_str.lower() or "timed out" in error_str.lower():
        user_msg = "The AI service request timed out. Please try again."
        log_msg = f"Timeout error: {error_str}"
        return (LLMTimeoutError, user_msg, log_msg)

    # Check for authentication/authorization errors
    if any(keyword in error_str.lower() for keyword in ["unauthorized", "authentication", "invalid api key", "invalid_api_key", "api key"]):
        user_msg = "There was an authentication issue with the AI service. Please contact your administrator."
        log_msg = f"Authentication error: {error_str}"
        return (LLMAuthenticationError, user_msg, log_msg)

    # Generic LLM service error (non-validation)
    user_msg = "The AI service encountered an error. Please try again or contact support if the issue persists."
    log_msg = f"LLM error: {error_str}"
    return (LLMServiceError, user_msg, log_msg)


async def safe_call_llm_with_tools(
    llm_caller,
    model: str,
    messages: List[Dict[str, str]],
    tools_schema: List[Dict[str, Any]],
    data_sources: Optional[List[str]] = None,
    user_email: Optional[str] = None,
    tool_choice: str = "auto",
    temperature: float = 0.7,
):
    """
    Safely call LLM with tools and error handling.

    Pure function that handles LLM calling errors with proper classification.
    """
    try:
        if data_sources and user_email:
            llm_response = await llm_caller.call_with_rag_and_tools(
                model, messages, data_sources, tools_schema, user_email, tool_choice, temperature=temperature
            )
            logger.info(f"LLM response received with RAG and tools for user {user_email}, has_tool_calls: {llm_response.has_tool_calls()}")
        else:
            llm_response = await llm_caller.call_with_tools(
                model, messages, tools_schema, tool_choice, temperature=temperature, user_email=user_email
            )
            # Log metadata at INFO level, content only at DEBUG
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("LLM response received with tools only, llm_response: %s", llm_response)
            else:
                # Check if llm_response has the expected attributes before logging
                has_tool_calls = llm_response.has_tool_calls() if hasattr(llm_response, 'has_tool_calls') else False
                content_length = len(llm_response.content) if hasattr(llm_response, 'content') else 0
                model_used = getattr(llm_response, 'model_used', 'unknown')
                logger.info(
                    f"LLM response received with tools only, has_tool_calls: {has_tool_calls}, "
                    f"content_length: {content_length}, model: {model_used}"
                )
        return llm_response
    except Exception as e:
        # Classify the error and raise appropriate error type
        error_class, user_msg, log_msg = classify_llm_error(e)
        logger.error(log_msg, exc_info=True)
        raise error_class(user_msg)


async def safe_execute_single_tool(
    tool_execution_func: Callable,
    tool_call,
    session_context: Dict[str, Any],
    tool_manager,
    update_callback: Optional[UpdateCallback] = None
):
    """
    Safely execute a single tool with comprehensive error handling.

    Pure function that wraps tool execution with error handling.
    """
    try:
        return await tool_execution_func(
            tool_call=tool_call,
            session_context=session_context,
            tool_manager=tool_manager,
            update_callback=update_callback
        )
    except Exception as e:
        logger.error(f"Error executing tool {tool_call.function.name}: {e}")

        # Send error notification if callback available
        if update_callback:
            try:
                await update_callback({
                    "type": "tool_error",
                    "tool_call_id": tool_call.id,
                    "tool_name": tool_call.function.name,
                    "error": str(e)
                })
            except Exception:
                pass  # Don't let notification errors compound the problem

        # Return error result instead of raising
        from atlas.domain.messages.models import ToolResult
        return ToolResult(
            tool_call_id=tool_call.id,
            content=f"Tool execution failed: {str(e)}",
            success=False,
            error=str(e)
        )


async def safe_file_operation(
    file_operation_func: Callable,
    *args,
    **kwargs
) -> Any:
    """
    Safely execute file operations with error handling.

    Pure function that wraps file operations with error handling.
    """
    try:
        return await file_operation_func(*args, **kwargs)
    except Exception as e:
        logger.error(f"Error in file operation: {e}", exc_info=True)
        # Return original context if operation fails
        if args and isinstance(args[0], dict):
            return args[0]  # Return original session_context
        return None


async def safe_llm_call(
    llm_call_func: Callable,
    *args,
    **kwargs
) -> Any:
    """
    Safely execute LLM calls with error handling.

    Pure function that wraps LLM calls with error handling.
    """
    try:
        return await llm_call_func(*args, **kwargs)
    except Exception as e:
        logger.error(f"Error in LLM call: {e}", exc_info=True)
        raise ValidationError(f"LLM call failed: {str(e)}")


def safe_sync_operation(
    operation_func: Callable,
    *args,
    **kwargs
) -> Any:
    """
    Safely execute synchronous operations with error handling.

    Pure function that wraps sync operations with error handling.
    """
    try:
        return operation_func(*args, **kwargs)
    except Exception as e:
        logger.error(f"Error in sync operation: {e}", exc_info=True)
        return None


def create_error_response(
    error_message: str,
    error_type: str = "error"
) -> Dict[str, str]:
    """
    Create standardized error response.

    Pure function that creates consistent error responses.
    """
    return {
        "type": error_type,
        "message": str(error_message)
    }


def create_validation_error_response(
    validation_message: str
) -> Dict[str, str]:
    """
    Create standardized validation error response.

    Pure function that creates consistent validation error responses.
    """
    return {
        "type": MessageType.ERROR.value,
        "message": f"Validation error: {validation_message}"
    }


def log_and_suppress_error(
    operation_name: str,
    error: Exception,
    level: str = "warning"
) -> None:
    """
    Log an error and suppress it for non-critical operations.

    Pure function that provides consistent error logging.
    """
    log_func = getattr(logger, level, logger.warning)
    log_func(f"Non-fatal error in {operation_name}: {error}")


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


def should_retry_operation(
    error: Exception,
    retry_count: int,
    max_retries: int = 3
) -> bool:
    """
    Determine if an operation should be retried based on error type.

    Pure function that implements retry logic.
    """
    if retry_count >= max_retries:
        return False

    # Don't retry validation errors
    if isinstance(error, ValidationError):
        return False

    # Retry for other types of errors
    return True


async def with_retry(
    operation_func: Callable,
    max_retries: int = 3,
    *args,
    **kwargs
) -> Any:
    """
    Execute operation with retry logic.

    Pure function that provides retry capability for operations.
    """
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            return await operation_func(*args, **kwargs)
        except Exception as e:
            last_error = e
            if not should_retry_operation(e, attempt, max_retries):
                break
            logger.warning(f"Operation failed (attempt {attempt + 1}/{max_retries + 1}): {e}")

    # If we get here, all retries failed
    if last_error is not None:
        raise last_error
    raise RuntimeError("Operation failed: no retry attempts were made")


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
