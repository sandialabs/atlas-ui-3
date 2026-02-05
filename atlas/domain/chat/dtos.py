"""Data Transfer Objects for chat operations."""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from uuid import UUID


@dataclass
class ChatRequest:
    """
    Request DTO for chat operations.
    
    Contains all parameters needed for different chat modes (plain, tools, RAG, agent).
    """
    session_id: UUID
    content: str
    model: str
    user_email: Optional[str] = None
    selected_tools: Optional[List[str]] = None
    selected_prompts: Optional[List[str]] = None
    selected_data_sources: Optional[List[str]] = None
    only_rag: bool = False
    tool_choice_required: bool = False
    agent_mode: bool = False
    temperature: float = 0.7
    agent_max_steps: int = 30
    agent_loop_strategy: Optional[str] = None
    files: Optional[Dict[str, Any]] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResponse:
    """
    Response DTO for chat operations.
    
    Contains the result of a chat interaction.
    """
    type: str
    message: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for API response."""
        return {
            "type": self.type,
            "message": self.message,
            **self.metadata
        }


@dataclass
class LLMMessage:
    """
    Type-safe message format for LLM interactions.
    
    Normalizes message structure across different chat modes.
    """
    role: str  # "user", "assistant", "system", "tool"
    content: str
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for LLM API."""
        result = {"role": self.role, "content": self.content}
        if self.name:
            result["name"] = self.name
        if self.tool_calls:
            result["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LLMMessage":
        """Create from dictionary format."""
        return cls(
            role=data["role"],
            content=data.get("content", ""),
            name=data.get("name"),
            tool_calls=data.get("tool_calls"),
            tool_call_id=data.get("tool_call_id"),
        )
