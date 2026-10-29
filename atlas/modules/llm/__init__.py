"""LLM module for the chat backend.

This module provides:
- LLM calling interface for various interaction modes
- Response models and data structures
- CLI tools for testing LLM interactions

``LiteLLMCaller`` instances are owned by the application factory
(``atlas.infrastructure.app_factory``); there is deliberately no
package-level instance here, for the same reason the ``mcp_tools`` package
dropped its singleton (issue #1013).
"""

from .litellm_caller import LiteLLMCaller
from .models import LLMResponse

__all__ = [
    "LiteLLMCaller",
    "LLMResponse",
]
