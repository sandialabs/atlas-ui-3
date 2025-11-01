"""WebSocket-based event publisher implementation."""

import logging
from typing import Any, Dict, Optional

from interfaces.transport import ChatConnectionProtocol
from application.chat.utilities import notification_utils

logger = logging.getLogger(__name__)


class WebSocketEventPublisher:
    """
    WebSocket implementation of EventPublisher.
    
    Wraps notification_utils and ChatConnectionProtocol to publish
    events to connected WebSocket clients.
    """

    def __init__(self, connection: Optional[ChatConnectionProtocol] = None):
        """
        Initialize WebSocket event publisher.
        
        Args:
            connection: WebSocket connection for sending messages
        """
        self.connection = connection

    async def publish_chat_response(
        self,
        message: str,
        has_pending_tools: bool = False,
    ) -> None:
        """Publish a chat response message."""
        if self.connection:
            await notification_utils.notify_chat_response(
                message=message,
                has_pending_tools=has_pending_tools,
                update_callback=self.connection.send_json,
            )

    async def publish_response_complete(self) -> None:
        """Signal that the response is complete."""
        if self.connection:
            await notification_utils.notify_response_complete(
                self.connection.send_json
            )

    async def publish_agent_update(
        self,
        update_type: str,
        **kwargs: Any
    ) -> None:
        """Publish an agent-specific update."""
        if self.connection:
            await notification_utils.notify_agent_update(
                update_type=update_type,
                connection=self.connection,
                **kwargs
            )

    async def publish_tool_start(
        self,
        tool_name: str,
        **kwargs: Any
    ) -> None:
        """Publish notification that a tool is starting."""
        if self.connection:
            await notification_utils.notify_agent_update(
                update_type="tool_start",
                connection=self.connection,
                tool=tool_name,
                **kwargs
            )

    async def publish_tool_complete(
        self,
        tool_name: str,
        result: Any,
        **kwargs: Any
    ) -> None:
        """Publish notification that a tool has completed."""
        if self.connection:
            await notification_utils.notify_agent_update(
                update_type="tool_complete",
                connection=self.connection,
                tool=tool_name,
                result=result,
                **kwargs
            )

    async def publish_files_update(
        self,
        files: Dict[str, Any]
    ) -> None:
        """Publish update about session files."""
        if self.connection:
            await self.connection.send_json({
                "type": "files_update",
                "files": files
            })

    async def publish_canvas_content(
        self,
        content: str,
        content_type: str = "text/html",
        **kwargs: Any
    ) -> None:
        """Publish content for canvas display."""
        if self.connection:
            await self.connection.send_json({
                "type": "canvas_content",
                "content": content,
                "content_type": content_type,
                **kwargs
            })

    async def send_json(self, data: Dict[str, Any]) -> None:
        """Send raw JSON message."""
        if self.connection:
            await self.connection.send_json(data)
