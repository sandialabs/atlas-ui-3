"""LLM module for the chat backend.

This module provides:
- LLM calling interface for various interaction modes
- Response models and data structures
- CLI tools for testing LLM interactions
"""

from .litellm_caller import LiteLLMCaller
from .models import LLMResponse

# Create default instance
llm_caller = LiteLLMCaller()

__all__ = [
    "LiteLLMCaller",
    "LLMResponse",
    "llm_caller",
]
