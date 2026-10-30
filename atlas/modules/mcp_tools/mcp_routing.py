"""Elicitation / log routing for MCPToolManager.

Holds the per-request routing context classes and the RoutingMixin that
creates per-server log/elicitation handlers. Routing uses dictionaries keyed
by (server_name, tool_call_id) rather than contextvars because the MCP receive
loop runs in a different task than the tool call.

MCP sampling was removed deliberately: a server-initiated LLM call ran under
an arbitrary configured model without the requesting user's identity or model
ACL, which is not a boundary this application can delegate to a tool server.
"""
import asyncio
import contextvars
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.domain.messages.models import ToolCall

logger = logging.getLogger(__name__)

# Type alias for log callback function
LogCallback = Callable[[str, str, str, Dict[str, Any]], Awaitable[None]]

class _ElicitationRoutingContext:
    def __init__(
        self,
        server_name: str,
        tool_call: ToolCall,
        update_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
        user_email: str = "",
    ):
        self.server_name = server_name
        self.tool_call = tool_call
        self.update_cb = update_cb
        # Carried so the elicitation request can be bound to its owner, which
        # is what lets the response handler reject another user's answer.
        self.user_email = user_email

# Context-local override used to route MCP logs to the *current* request/session.
# This prevents cross-user log leakage when MCPToolManager is shared across connections.
_ACTIVE_LOG_CALLBACK: contextvars.ContextVar[Optional[LogCallback]] = contextvars.ContextVar(
    "mcp_active_log_callback",
    default=None,
)

# Sentinel for ambiguous routing (replaces magic string)
class _AmbiguousRouting:
    """Sentinel indicating multiple routing entries matched without meta disambiguation."""
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    def __repr__(self):
        return "AMBIGUOUS_ROUTING"

AMBIGUOUS_ROUTING = _AmbiguousRouting()

# Mapping from MCP log levels to Python logging levels
MCP_TO_PYTHON_LOG_LEVEL = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "notice": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "alert": logging.CRITICAL,
    "critical": logging.CRITICAL,
    "emergency": logging.CRITICAL,
}


class RoutingMixin:
    """Log/elicitation handler creation and routing resolution."""

    def _create_log_handler(self, server_name: str):
        """Create a log handler for an MCP server.

        This handler forwards MCP server logs to the backend logger and optionally to the UI.
        Logs are filtered based on the configured LOG_LEVEL.

        Args:
            server_name: Name of the MCP server

        Returns:
            An async function that handles LogMessage objects from fastmcp
        """
        async def log_handler(message) -> None:
            """Handle log messages from MCP server."""
            try:
                # Import here to avoid circular dependency

                # Handle both LogMessage objects and dict-like structures
                if hasattr(message, 'level'):
                    log_level_str = message.level.lower()
                    log_data = message.data if hasattr(message, 'data') else {}
                else:
                    # Fallback for dict-like messages
                    log_level_str = message.get('level', 'info').lower()
                    log_data = message.get('data', {})

                msg = log_data.get('msg', '') if isinstance(log_data, dict) else str(log_data)
                extra = log_data.get('extra', {}) if isinstance(log_data, dict) else {}

                # Convert MCP log level to Python logging level
                python_log_level = MCP_TO_PYTHON_LOG_LEVEL.get(log_level_str, logging.INFO)

                # Filter based on configured minimum log level
                if python_log_level < self._min_log_level:
                    return

                # Backend log noise reduction: tool servers can be very chatty at INFO.
                # Keep their INFO messages available at LOG_LEVEL=DEBUG, but avoid flooding
                # app logs at LOG_LEVEL=INFO. Warnings/errors still surface at INFO.
                backend_log_level = python_log_level if python_log_level >= logging.WARNING else logging.DEBUG

                # Log to backend logger with server context
                logger.log(
                    backend_log_level,
                    f"[MCP:{sanitize_for_logging(server_name)}] {sanitize_for_logging(msg)}",
                    extra={"mcp_server": server_name, "mcp_extra": extra}
                )

                # Forward to the active (request-scoped) callback when present,
                # otherwise fall back to the default callback.
                callback = _ACTIVE_LOG_CALLBACK.get() or self._default_log_callback
                if callback is not None:
                    await callback(server_name, log_level_str, msg, extra)

            except Exception as e:
                logger.warning(f"Error handling log from MCP server {server_name}: {e}")

        return log_handler

    def set_log_callback(self, callback: Optional[LogCallback]) -> None:
        """Set or update the log callback for forwarding MCP server logs to UI.

        Args:
            callback: Async function that receives (server_name, level, message, extra_data)
        """
        self._default_log_callback = callback

    @asynccontextmanager
    async def _use_log_callback(self, callback: Optional[LogCallback]) -> AsyncIterator[None]:
        """Temporarily set a request-scoped log callback.

        This is used to bind MCP server logs to the current tool execution so they
        are forwarded only to the correct user's WebSocket connection.
        """
        token = _ACTIVE_LOG_CALLBACK.set(callback)
        try:
            yield
        finally:
            _ACTIVE_LOG_CALLBACK.reset(token)

    @asynccontextmanager
    async def _use_elicitation_context(
        self,
        server_name: str,
        tool_call: ToolCall,
        update_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
        user_email: str = "",
    ) -> AsyncIterator[None]:
        """
        Set up elicitation routing for a tool call.
        Uses dictionary-based routing (not contextvars) because MCP receive loop runs in a different task.
        Key is (server_name, tool_call.id) to avoid collisions with concurrent tool calls.
        """
        routing = _ElicitationRoutingContext(server_name, tool_call, update_cb, user_email)
        routing_key = (server_name, tool_call.id)
        self._elicitation_routing[routing_key] = routing
        try:
            yield
        finally:
            self._elicitation_routing.pop(routing_key, None)

    def _create_elicitation_handler(self, server_name: str):
        """
        Create an elicitation handler for a specific MCP server.

        Returns a handler function that captures the server_name,
        allowing dictionary-based routing that works across async tasks.
        """
        async def handler(message, response_type, params, _context):
            """Per-server elicitation handler with captured server_name."""
            from fastmcp.client.elicitation import ElicitResult
            from mcp.types import ElicitRequestFormParams

            # Find routing context for this server (keyed by (server_name, tool_call_id))
            routing = self._resolve_routing(self._elicitation_routing, server_name, _context)

            if routing is AMBIGUOUS_ROUTING:
                return ElicitResult(action="cancel", content=None)

            if routing is None:
                logger.warning(
                    f"Elicitation request for server '{server_name}' but no routing context - "
                    f"elicitation cancelled. Message: {message[:50]}..."
                )
                return ElicitResult(action="cancel", content=None)
            if routing.update_cb is None:
                logger.warning(
                    f"Elicitation request for server '{server_name}', tool '{routing.tool_call.name}' "
                    f"but update_cb is None - elicitation cancelled. Message: {message[:50]}..."
                )
                return ElicitResult(action="cancel", content=None)

            response_schema: Dict[str, Any] = {}
            if isinstance(params, ElicitRequestFormParams):
                response_schema = params.requestedSchema or {}

            elicitation_id = None
            try:
                import uuid

                from atlas.application.chat.approval_manager import resolve_approval_timeout
                from atlas.application.chat.elicitation_manager import get_elicitation_manager

                elicitation_id = str(uuid.uuid4())
                elicitation_manager = get_elicitation_manager()

                request = elicitation_manager.create_elicitation_request(
                    elicitation_id=elicitation_id,
                    tool_call_id=routing.tool_call.id,
                    tool_name=routing.tool_call.name,
                    message=message,
                    response_schema=response_schema,
                    user_email=getattr(routing, "user_email", "") or "",
                )

                logger.debug(f"Sending elicitation_request to frontend for server '{server_name}'")
                await routing.update_cb(
                    {
                        "type": "elicitation_request",
                        "elicitation_id": elicitation_id,
                        "tool_call_id": routing.tool_call.id,
                        "tool_name": routing.tool_call.name,
                        "message": message,
                        "response_schema": response_schema,
                    }
                )

                try:
                    response = await request.wait_for_response(timeout=resolve_approval_timeout())
                finally:
                    elicitation_manager.cleanup_request(elicitation_id)

                action = response.get("action", "cancel")
                data = response.get("data")

                if action != "accept":
                    return ElicitResult(action=action, content=None)

                # Approval-only elicitation (response_type=None) must return an empty object.
                # Some UIs send placeholder payloads like {'none': ''}; don't forward them.
                if response_type is None:
                    return ElicitResult(action="accept", content={})

                if data is None:
                    return ElicitResult(action="accept", content=None)

                # FastMCP requires elicitation response content to be a JSON object.
                if not isinstance(data, dict):
                    props: Dict[str, Any] = {}
                    if isinstance(response_schema, dict):
                        props = response_schema.get("properties") or {}
                    if list(props.keys()) == ["value"]:
                        data = {"value": data}
                    else:
                        data = {"value": data}

                return ElicitResult(action="accept", content=data)

            except asyncio.TimeoutError:
                logger.warning(f"Elicitation timeout for server '{server_name}'")
                # A settle frame is what clears the pause. Without it the run
                # stays waiting_for_input and the card is replayed on reopen.
                if elicitation_id is not None and routing.update_cb is not None:
                    try:
                        await routing.update_cb({
                            "type": "tool_error",
                            "elicitation_id": elicitation_id,
                            "tool_call_id": routing.tool_call.id,
                            "tool_name": routing.tool_call.name,
                            "error": "Elicitation timed out",
                            "status": "expired",
                        })
                    except Exception:
                        logger.warning(
                            "Could not publish elicitation timeout for server '%s'",
                            server_name, exc_info=True,
                        )
                return ElicitResult(action="cancel", content=None)
            except Exception as e:
                logger.error(f"Error handling elicitation for server '{server_name}': {e}", exc_info=True)
                return ElicitResult(action="cancel", content=None)

        return handler

    def _resolve_routing(
        self,
        routing_dict: Dict,
        server_name: str,
        context: Any,
    ) -> Any:
        """Resolve routing context from a routing dict using meta-based O(1) lookup.

        Tries composite (server_name, tool_call_id) key first via context.meta,
        then falls back to single-match scan. Returns None if unresolvable.
        """
        tcid = None
        if context and hasattr(context, 'meta') and context.meta is not None:
            tcid = getattr(context.meta, 'model_extra', {}).get("tool_call_id")

        routing = routing_dict.get((server_name, tcid))

        if routing is None:
            matches = [v for (srv, _), v in routing_dict.items() if srv == server_name]
            if len(matches) == 1:
                routing = matches[0]
            elif len(matches) > 1:
                logger.warning(
                    "Ambiguous routing for server '%s' with %d entries",
                    server_name, len(matches),
                )
                return AMBIGUOUS_ROUTING

        return routing
