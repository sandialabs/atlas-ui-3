"""
Data models for LLM responses and related structures.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class ReasoningToken:
    """Emitted during streaming for each reasoning token chunk (for real-time display)."""
    token: str


@dataclass
class ReasoningBlock:
    """Emitted during streaming when model reasoning is complete, before content begins."""
    content: str


@dataclass
class LLMResponse:
    """Response from LLM call with metadata."""
    content: str
    tool_calls: Optional[List[Dict]] = None
    model_used: str = ""
    tokens_used: int = 0
    reasoning_content: Optional[str] = None

    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return self.tool_calls is not None and len(self.tool_calls) > 0
