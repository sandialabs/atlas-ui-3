"""WebSocket-based event publisher implementation."""

import logging
from typing import Any, Dict, Optional

from atlas.application.chat.utilities import event_notifier
from atlas.interfaces.transport import ChatConnectionProtocol

logger = logging.getLogger(__name__)


class WebSocketEventPublisher:
    """
    WebSocket implementation of EventPublisher.

    Wraps event_notifier and ChatConnectionProtocol to publish
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
            await event_notifier.notify_chat_response(
                message=message,
                has_pending_tools=has_pending_tools,
                update_callback=self.connection.send_json,
            )

    async def publish_response_complete(self) -> None:
        """Signal that the response is complete."""
        if self.connection:
            await event_notifier.notify_response_complete(
                self.connection.send_json
            )

    async def publish_agent_update(
        self,
        update_type: str,
        **kwargs: Any
    ) -> None:
        """Publish an agent-specific update."""
        if self.connection:
            await event_notifier.notify_agent_update(
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
            await event_notifier.notify_agent_update(
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
            await event_notifier.notify_agent_update(
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

    async def publish_token_stream(
        self,
        token: str,
        is_first: bool = False,
        is_last: bool = False,
    ) -> None:
        """Publish a streaming token chunk."""
        if self.connection:
            await event_notifier.notify_token_stream(
                token=token,
                is_first=is_first,
                is_last=is_last,
                update_callback=self.connection.send_json,
            )

    async def send_json(self, data: Dict[str, Any]) -> None:
        """Send raw JSON message."""
        if self.connection:
            await self.connection.send_json(data)

    async def publish_elicitation_request(
        self,
        elicitation_id: str,
        tool_call_id: str,
        tool_name: str,
        message: str,
        response_schema: Dict[str, Any]
    ) -> None:
        """Publish an elicitation request to the user."""
        if self.connection:
            await self.connection.send_json({
                "type": "elicitation_request",
                "elicitation_id": elicitation_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "message": message,
                "response_schema": response_schema
            })
