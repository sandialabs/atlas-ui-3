"""Infrastructure layer - external adapters and wiring."""

from .app_factory import AppFactory, app_factory
from .transport.websocket_connection_adapter import WebSocketConnectionAdapter

__all__ = [
    "AppFactory",
    "app_factory",
    "WebSocketConnectionAdapter",
]
