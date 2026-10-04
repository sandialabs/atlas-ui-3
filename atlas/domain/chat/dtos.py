"""Data Transfer Objects for chat operations."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
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
    agent_mode: bool = False
    temperature: float = 0.7
    agent_max_steps: int = 10
    agent_loop_strategy: Optional[str] = None
    files: Optional[Dict[str, Any]] = None
    # When set, rewind to this user message (0-based ordinal) before running the
    # turn: history is truncated at that prompt and ``content`` replaces it.
    rewind_to_user_index: Optional[int] = None
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
