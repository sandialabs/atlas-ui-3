"""
Data models for LLM responses and related structures.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class LLMResponse:
    """Response from LLM call with metadata."""
    content: str
    tool_calls: Optional[List[Dict]] = None
    model_used: str = ""
    tokens_used: int = 0
    
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return self.tool_calls is not None and len(self.tool_calls) > 0