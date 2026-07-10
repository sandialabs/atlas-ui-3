"""Domain models for messages."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID, uuid4


# Message types that exist only so the UI can re-render a reloaded
# conversation. They are persisted (issue #684) but must never be replayed
# back to the LLM as conversation turns.
DISPLAY_ONLY_MESSAGE_TYPES = frozenset({"tool_call", "agent_intermediate"})


class MessageRole(Enum):
    """Message role enumeration."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class MessageType(Enum):
    """Message type enumeration."""
    CHAT = "chat"
    CHAT_RESPONSE = "chat_response"
    ERROR = "error"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    AGENT_UPDATE = "agent_update"
    INTERMEDIATE_UPDATE = "intermediate_update"
    AGENT_INTERMEDIATE = "agent_intermediate"
    DOWNLOAD_FILE = "download_file"
    FILE_DOWNLOAD = "file_download"


@dataclass
class Message:
    """Domain model for a chat message."""
    id: UUID = field(default_factory=uuid4)
    role: MessageRole = MessageRole.USER
    content: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": str(self.id),
            "role": self.role.value,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """Create from dictionary."""
        return cls(
            id=UUID(data["id"]) if "id" in data else uuid4(),
            role=MessageRole(data.get("role", "user")),
            content=data.get("content", ""),
            timestamp=datetime.fromisoformat(data["timestamp"]) if "timestamp" in data else datetime.now(timezone.utc),
            metadata=data.get("metadata", {})
        )


@dataclass
class ToolCall:
    """Domain model for a tool call."""
    id: str
    name: str
    arguments: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments
        }


@dataclass
class ToolResult:
    """Domain model for a tool result with v2 MCP support."""
    tool_call_id: str
    content: str
    success: bool = True
    error: Optional[str] = None
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    display_config: Optional[Dict[str, Any]] = None
    meta_data: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "tool_call_id": self.tool_call_id,
            "content": self.content,
            "success": self.success,
            "error": self.error,
            "artifacts": self.artifacts,
        }
        if self.display_config:
            result["display_config"] = self.display_config
        if self.meta_data:
            result["meta_data"] = self.meta_data
        return result


@dataclass
class ConversationHistory:
    """Domain model for conversation history."""
    messages: List[Message] = field(default_factory=list)

    def add_message(self, message: Message) -> None:
        """Add a message to the history."""
        self.messages.append(message)

    def truncate_at_user_index(self, user_index: int) -> List[Message]:
        """Truncate history at the Nth user message (0-based).

        Removes the ``user_index``-th user message and every message after it,
        leaving the conversation as it was just before that prompt was sent.
        This powers the "rewind / edit a previous prompt" flow: the caller then
        appends the new (edited) user message and re-runs the turn.

        Addressing by user-message ordinal (rather than absolute list position)
        keeps the frontend and backend in agreement even though the backend
        history also contains assistant/tool messages the UI renders differently.

        Args:
            user_index: Zero-based ordinal of the user message to rewind to.

        Returns:
            The list of removed messages (the discarded trajectory). Returns an
            empty list when ``user_index`` does not address an existing user
            message, leaving history unchanged.
        """
        # Self-defending: this is an irreversible mutation, so reject anything
        # that is not a genuine, non-negative ``int`` (``bool`` is an ``int``
        # subclass but is never a valid ordinal) regardless of caller coercion.
        if isinstance(user_index, bool) or not isinstance(user_index, int):
            return []
        if user_index < 0:
            return []

        seen = 0
        cut_at: Optional[int] = None
        for i, msg in enumerate(self.messages):
            if msg.role == MessageRole.USER:
                if seen == user_index:
                    cut_at = i
                    break
                seen += 1

        if cut_at is None:
            return []

        removed = self.messages[cut_at:]
        self.messages = self.messages[:cut_at]
        return removed

    def get_messages_for_llm(self) -> List[Dict[str, str]]:
        """Get messages formatted for LLM API.

        Display-only messages (``message_type`` in :data:`DISPLAY_ONLY_MESSAGE_TYPES`)
        are excluded. These are records kept purely so the UI can re-render a
        reloaded conversation (e.g. persisted ``tool_call`` rows from issue #684);
        they carry no role/content the model should reason over and, in the case
        of orphaned ``tool`` rows, would make some providers reject the request.
        """
        return [
            {"role": msg.role.value, "content": msg.content}
            for msg in self.messages
            if msg.metadata.get("message_type") not in DISPLAY_ONLY_MESSAGE_TYPES
        ]

    def to_dict(self) -> List[Dict[str, Any]]:
        """Convert to dictionary list."""
        return [msg.to_dict() for msg in self.messages]


@dataclass
class ElicitationRequest:
    """Domain model for an elicitation request from MCP server."""
    elicitation_id: str
    tool_call_id: str
    tool_name: str
    message: str
    response_schema: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for WebSocket transmission."""
        return {
            "type": "elicitation_request",
            "elicitation_id": self.elicitation_id,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "message": self.message,
            "response_schema": self.response_schema
        }


@dataclass
class ElicitationResponse:
    """Domain model for an elicitation response from user."""
    elicitation_id: str
    action: Literal["accept", "decline", "cancel"]
    data: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "elicitation_id": self.elicitation_id,
            "action": self.action,
            "data": self.data
        }
