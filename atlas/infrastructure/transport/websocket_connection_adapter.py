"""WebSocket connection adapter implementing ChatConnectionProtocol."""

import logging
from typing import Any, Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)


class WebSocketConnectionAdapter:
    """
    Adapter that wraps FastAPI WebSocket to implement ChatConnectionProtocol.
    This isolates the application layer from FastAPI-specific types.
    """

    def __init__(self, websocket: WebSocket, user_email: Optional[str] = None):
        """Initialize with FastAPI WebSocket and associated user."""
        self.websocket = websocket
        self.user_email = user_email

    async def send_json(self, data: Dict[str, Any]) -> None:
        """Send JSON data to the client, dropping it if the socket is gone.

        This is the transport chokepoint for every event the chat pipeline
        publishes.  A client that disconnects mid-turn would otherwise turn each
        one into a raised exception that callers either log as a spurious error
        or let abort an in-progress operation.  Neither the client nor the
        application state flags every case -- the endpoint can have already
        returned, leaving the ASGI response complete while this object still
        looks connected -- so the send is guarded as well as checked.
        """
        if self.websocket.client_state != WebSocketState.CONNECTED:
            logger.debug("Dropping %s; websocket not connected", data.get("type"))
            return
        try:
            await self.websocket.send_json(data)
        except (WebSocketDisconnect, RuntimeError) as e:
            logger.debug("Websocket closed before message could be sent: %s", e)

    async def receive_json(self) -> Dict[str, Any]:
        """Receive JSON data from the client."""
        return await self.websocket.receive_json()

    async def accept(self) -> None:
        """Accept the connection."""
        await self.websocket.accept()

    async def close(self) -> None:
        """Close the connection."""
        await self.websocket.close()
