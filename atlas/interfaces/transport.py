"""Transport interface protocols."""

from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class ChatConnectionProtocol(Protocol):
    """Protocol for chat connections (WebSocket abstraction)."""
    
    async def send_json(self, data: Dict[str, Any]) -> None:
        """Send JSON data to the client."""
        ...
    
    async def receive_json(self) -> Dict[str, Any]:
        """Receive JSON data from the client."""
        ...
    
    async def accept(self) -> None:
        """Accept the connection."""
        ...
    
    async def close(self) -> None:
        """Close the connection."""
        ...
