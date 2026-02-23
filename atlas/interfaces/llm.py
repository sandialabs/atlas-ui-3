"""LLM interface protocols."""

from typing import AsyncGenerator, Dict, List, Optional, Protocol, Union, runtime_checkable

from atlas.modules.llm.models import LLMResponse as LLMResponse


@runtime_checkable
class LLMProtocol(Protocol):
    """Protocol for LLM interactions."""

    async def call_plain(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        user_email: Optional[str] = None,
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
        user_email: Optional[str] = None,
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

    def stream_plain(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        user_email: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream plain LLM response token-by-token."""
        ...

    def stream_with_tools(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        tools_schema: List[Dict],
        tool_choice: str = "auto",
        temperature: float = 0.7,
        user_email: Optional[str] = None,
    ) -> AsyncGenerator[Union[str, LLMResponse], None]:
        """Stream LLM with tools. Yields str chunks then final LLMResponse."""
        ...

    def stream_with_rag(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        data_sources: List[str],
        user_email: str,
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """Stream LLM response with RAG integration."""
        ...

    def stream_with_rag_and_tools(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        data_sources: List[str],
        tools_schema: List[Dict],
        user_email: str,
        tool_choice: str = "auto",
        temperature: float = 0.7,
    ) -> AsyncGenerator[Union[str, LLMResponse], None]:
        """Stream LLM with both RAG and tools."""
        ...
