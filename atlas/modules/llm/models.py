"""
Data models for LLM responses and related structures.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class LLMResponse:
    """Response from LLM call with metadata."""
    content: str
    tool_calls: Optional[List[Dict]] = None
    model_used: str = ""
    tokens_used: int = 0
    # Names of tool calls dropped for unparseable arguments while other calls in
    # the same response survived. The turn continues, so nothing else would tell
    # the user or the model that a call was discarded.
    dropped_tool_calls: Optional[List[str]] = None
    # Whether that drop was an output-limit truncation rather than unparseable
    # JSON. The user-facing warning says why, so it must not guess.
    dropped_tool_calls_truncated: bool = False

    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return self.tool_calls is not None and len(self.tool_calls) > 0


def split_provider(litellm_model: str) -> Tuple[str, str]:
    """Split a LiteLLM model string into (provider, model_suffix).

    Examples: ``openai/gpt-4o`` -> (``openai``, ``gpt-4o``);
    ``anthropic/claude-opus-4-7`` -> (``anthropic``, ``claude-opus-4-7``).
    When no prefix is present, provider is ``unknown``.

    Lives here (not in ``litellm_caller``) so ``litellm_streaming`` can import
    it without creating an import cycle back into the caller module.
    """
    if not litellm_model:
        return "unknown", ""
    if "/" in litellm_model:
        provider, suffix = litellm_model.split("/", 1)
        return provider, suffix
    return "unknown", litellm_model
