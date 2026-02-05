"""WebSocket connection adapter implementing ChatConnectionProtocol."""

from typing import Any, Dict, Optional

from fastapi import WebSocket



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
        """Send JSON data to the client."""
        await self.websocket.send_json(data)
    
    async def receive_json(self) -> Dict[str, Any]:
        """Receive JSON data from the client."""
        return await self.websocket.receive_json()
    
    async def accept(self) -> None:
        """Accept the connection."""
        await self.websocket.accept()
    
    async def close(self) -> None:
        """Close the connection."""
        await self.websocket.close()
