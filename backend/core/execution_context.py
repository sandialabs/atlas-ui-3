"""Execution context tracking for clear log paths.

Provides utilities to track and log execution flow through the system,
making it easy to understand chains of LLM calls, tool executions, and file operations.
"""

import logging
from contextvars import ContextVar
from typing import Any, Dict, Optional
from uuid import uuid4
from enum import Enum

# Context variables for tracking execution flow
conversation_id_var: ContextVar[Optional[str]] = ContextVar("conversation_id", default=None)
request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


class LogCategory(str, Enum):
    """Categories for different types of log entries."""
    CHAT = "CHAT"
    LLM = "LLM"
    TOOL = "TOOL"
    FILE = "FILE"
    RAG = "RAG"
    SYSTEM = "SYSTEM"


class ExecutionPhase(str, Enum):
    """Phases of execution for tracking flow."""
    CHAT_START = "chat_start"
    MESSAGE_RECEIVED = "message_received"
    LLM_CALL = "llm_call"
    TOOL_EXECUTION = "tool_execution"
    TOOL_APPROVAL = "tool_approval"
    FILE_OPERATION = "file_operation"
    SYNTHESIS = "synthesis"
    RESPONSE_SENT = "response_sent"
    ERROR = "error"
    CHAT_END = "chat_end"


def set_conversation_context(conversation_id: str, request_id: Optional[str] = None):
    """Set the conversation and request context for logging."""
    conversation_id_var.set(conversation_id)
    request_id_var.set(request_id or str(uuid4()))


def get_conversation_context() -> Dict[str, Optional[str]]:
    """Get current conversation and request context."""
    return {
        "conversation_id": conversation_id_var.get(),
        "request_id": request_id_var.get()
    }


def clear_context():
    """Clear the execution context."""
    conversation_id_var.set(None)
    request_id_var.set(None)


def log_execution(
    logger: logging.Logger,
    level: int,
    message: str,
    category: LogCategory,
    phase: ExecutionPhase,
    **extra_fields
):
    """Log with execution context and structured fields.
    
    Args:
        logger: Logger instance to use
        level: Logging level (logging.INFO, etc.)
        message: Log message
        category: Log category (CHAT, LLM, TOOL, etc.)
        phase: Execution phase
        **extra_fields: Additional structured fields to include
    """
    context = get_conversation_context()
    
    # Create structured extra fields
    extra = {
        "log_category": category.value,
        "execution_phase": phase.value,
        **{f"exec_{k}": v for k, v in extra_fields.items()}
    }
    
    # Add context if available
    if context["conversation_id"]:
        extra["conversation_id"] = context["conversation_id"]
    if context["request_id"]:
        extra["request_id"] = context["request_id"]
    
    # Log with extra fields
    logger.log(level, message, extra=extra)


def log_chat_event(logger: logging.Logger, message: str, phase: ExecutionPhase, **extra):
    """Log a chat-related event."""
    log_execution(logger, logging.INFO, message, LogCategory.CHAT, phase, **extra)


def log_llm_call(
    logger: logging.Logger,
    model: str,
    message_count: int,
    has_tools: bool = False,
    has_rag: bool = False,
    **extra
):
    """Log an LLM call with structured information."""
    msg = f"LLM call: model={model}, messages={message_count}"
    if has_tools:
        msg += ", with_tools=true"
    if has_rag:
        msg += ", with_rag=true"
    
    log_execution(
        logger, logging.INFO, msg, LogCategory.LLM, ExecutionPhase.LLM_CALL,
        model=model, message_count=message_count, has_tools=has_tools, has_rag=has_rag,
        **extra
    )


def log_llm_response(
    logger: logging.Logger,
    model: str,
    content_length: int,
    tool_calls_count: int = 0,
    tokens_used: Optional[int] = None,
    **extra
):
    """Log an LLM response with structured information."""
    msg = f"LLM response: model={model}, content_len={content_length}"
    if tool_calls_count > 0:
        msg += f", tool_calls={tool_calls_count}"
    if tokens_used:
        msg += f", tokens={tokens_used}"
    
    log_execution(
        logger, logging.INFO, msg, LogCategory.LLM, ExecutionPhase.LLM_CALL,
        model=model, content_length=content_length, tool_calls_count=tool_calls_count,
        tokens_used=tokens_used, **extra
    )


def log_tool_execution(
    logger: logging.Logger,
    tool_name: str,
    status: str = "started",
    result_summary: Optional[str] = None,
    error: Optional[str] = None,
    **extra
):
    """Log a tool execution event."""
    msg = f"Tool {status}: {tool_name}"
    if result_summary:
        msg += f" - {result_summary}"
    if error:
        msg += f" - ERROR: {error}"
    
    level = logging.ERROR if error else logging.INFO
    log_execution(
        logger, level, msg, LogCategory.TOOL, ExecutionPhase.TOOL_EXECUTION,
        tool_name=tool_name, status=status, result_summary=result_summary,
        error=error, **extra
    )


def log_file_operation(
    logger: logging.Logger,
    operation: str,
    filename: str,
    status: str = "success",
    error: Optional[str] = None,
    **extra
):
    """Log a file operation event."""
    msg = f"File {operation}: {filename} - {status}"
    if error:
        msg += f" - ERROR: {error}"
    
    level = logging.ERROR if error else logging.INFO
    log_execution(
        logger, level, msg, LogCategory.FILE, ExecutionPhase.FILE_OPERATION,
        operation=operation, filename=filename, status=status, error=error,
        **extra
    )


def log_rag_operation(
    logger: logging.Logger,
    operation: str,
    source: str,
    result_count: Optional[int] = None,
    **extra
):
    """Log a RAG operation event."""
    msg = f"RAG {operation}: source={source}"
    if result_count is not None:
        msg += f", results={result_count}"
    
    log_execution(
        logger, logging.INFO, msg, LogCategory.RAG, ExecutionPhase.LLM_CALL,
        operation=operation, source=source, result_count=result_count, **extra
    )
