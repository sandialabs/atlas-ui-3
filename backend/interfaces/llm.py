"""LLM interface protocols."""

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from domain.messages.models import ToolCall


class LLMResponse:
    """Response from LLM."""
    def __init__(
        self,
        content: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        model_used: Optional[str] = None
    ):
        self.content = content
        self.tool_calls = tool_calls
        self.model_used = model_used
    
    def has_tool_calls(self) -> bool:
        """Check if response has tool calls."""
        return bool(self.tool_calls)


@runtime_checkable
class LLMProtocol(Protocol):
    """Protocol for LLM interactions."""
    
    async def call_plain(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> str:
        """Plain LLM call without tools or RAG."""
        ...
    
    async def call_with_tools(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        tools_schema: List[Dict],
        tool_choice: str = "auto",
        temperature: float = 0.7,
    ) -> LLMResponse:
        """LLM call with tool support."""
        ...
    
    async def call_with_rag(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        data_sources: List[str],
        user_email: str,
        temperature: float = 0.7,
    ) -> str:
        """LLM call with RAG integration."""
        ...
    
    async def call_with_rag_and_tools(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        data_sources: List[str],
        tools_schema: List[Dict],
        user_email: str,
        tool_choice: str = "auto",
        temperature: float = 0.7,
    ) -> LLMResponse:
        """LLM call with both RAG and tools."""
        ...
