"""Event publisher interface for transport-agnostic UI updates."""

from typing import Any, Dict, Protocol


class EventPublisher(Protocol):
    """
    Protocol for publishing events to UI/clients.

    Provides transport-agnostic interface for sending various update types
    to connected clients (e.g., via WebSocket, SSE, HTTP streaming, etc.).

    This interface lives in the interfaces layer to avoid circular dependencies
    and keep the application layer decoupled from atlas.infrastructure concerns.
    """

    async def publish_chat_response(
        self,
        message: str,
        has_pending_tools: bool = False,
    ) -> None:
        """
        Publish a chat response message.

        Args:
            message: Response text from assistant
            has_pending_tools: Whether tools are still executing
        """
        pass

    async def publish_response_complete(self) -> None:
        """Signal that the response is complete."""
        pass

    async def publish_agent_update(
        self,
        update_type: str,
        **kwargs: Any
    ) -> None:
        """
        Publish an agent-specific update.

        Args:
            update_type: Type of agent update (agent_start, agent_turn_start, etc.)
            **kwargs: Additional data specific to the update type
        """
        pass

    async def publish_tool_start(
        self,
        tool_name: str,
        **kwargs: Any
    ) -> None:
        """
        Publish notification that a tool is starting.

        Args:
            tool_name: Name of the tool being executed
            **kwargs: Additional tool execution metadata
        """
        pass

    async def publish_tool_complete(
        self,
        tool_name: str,
        result: Any,
        **kwargs: Any
    ) -> None:
        """
        Publish notification that a tool has completed.

        Args:
            tool_name: Name of the tool that completed
            result: Tool execution result
            **kwargs: Additional tool execution metadata
        """
        pass

    async def publish_files_update(
        self,
        files: Dict[str, Any]
    ) -> None:
        """
        Publish update about session files.

        Args:
            files: Dictionary of file information
        """
        pass

    async def publish_canvas_content(
        self,
        content: str,
        content_type: str = "text/html",
        **kwargs: Any
    ) -> None:
        """
        Publish content for canvas display.

        Args:
            content: Content to display in canvas
            content_type: MIME type of content
            **kwargs: Additional canvas metadata
        """
        pass

    async def publish_token_stream(
        self,
        token: str,
        is_first: bool = False,
        is_last: bool = False,
    ) -> None:
        """
        Publish a streaming token chunk.

        Args:
            token: Text chunk from LLM
            is_first: Whether this is the first token in the stream
            is_last: Whether this is the last token (stream complete)
        """
        pass

    async def send_json(self, data: Dict[str, Any]) -> None:
        """
        Send raw JSON message.

        Args:
            data: Dictionary to send as JSON
        """
        pass

    async def publish_elicitation_request(
        self,
        elicitation_id: str,
        tool_call_id: str,
        tool_name: str,
        message: str,
        response_schema: Dict[str, Any]
    ) -> None:
        """
        Publish an elicitation request to the user.

        Args:
            elicitation_id: Unique identifier for this elicitation
            tool_call_id: ID of the tool call that requested elicitation
            tool_name: Name of the tool requesting input
            message: Prompt message to display to the user
            response_schema: JSON schema defining expected response structure
        """
        pass
