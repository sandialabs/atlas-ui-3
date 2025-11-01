"""Event publisher interface for transport-agnostic UI updates."""

from typing import Protocol, Any, Dict


class EventPublisher(Protocol):
    """
    Protocol for publishing events to UI/clients.
    
    Provides transport-agnostic interface for sending various update types
    to connected clients (e.g., via WebSocket, SSE, HTTP streaming, etc.).
    
    This interface lives in the interfaces layer to avoid circular dependencies
    and keep the application layer decoupled from infrastructure concerns.
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
        ...

    async def publish_response_complete(self) -> None:
        """Signal that the response is complete."""
        ...

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
        ...

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
        ...

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
        ...

    async def publish_files_update(
        self,
        files: Dict[str, Any]
    ) -> None:
        """
        Publish update about session files.
        
        Args:
            files: Dictionary of file information
        """
        ...

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
        ...

    async def send_json(self, data: Dict[str, Any]) -> None:
        """
        Send raw JSON message.
        
        Args:
            data: Dictionary to send as JSON
        """
        ...
