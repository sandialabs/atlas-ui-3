"""
Shared Models for LLM Mock Servers

This module contains common Pydantic models used by all LLM mock servers
to reduce code duplication and ensure consistency.
"""

from typing import List, Optional
from pydantic import BaseModel


class ChatMessage(BaseModel):
    """A single chat message with role and content."""
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    """Request model for OpenAI-compatible chat completions endpoint."""
    model: str
    messages: List[ChatMessage]
    max_tokens: Optional[int] = 1000
    temperature: Optional[float] = 0.7
    stream: Optional[bool] = False


class ChatCompletionChoice(BaseModel):
    """A single choice in a chat completion response."""
    index: int
    message: ChatMessage
    finish_reason: str


class ChatCompletionUsage(BaseModel):
    """Token usage statistics for a chat completion."""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    """Response model for OpenAI-compatible chat completions endpoint."""
    id: str
    object: str
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: ChatCompletionUsage
