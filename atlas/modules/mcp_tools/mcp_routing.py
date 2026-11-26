"""Elicitation / log routing for MCPToolManager.

Holds the per-request routing context classes and the RoutingMixin that
creates per-server log/elicitation handlers.

Elicitation reaches the handler two ways, depending on the protocol era the
connection negotiated:

- MCP 2026-07-28 (SEP-2322 multi round-trip requests): the server answers
  ``tools/call`` with an ``InputRequiredResult`` and the FastMCP client runs
  the handler from inside the ``call_tool`` await, then retries the call with
  the answers. The handler therefore runs in the calling task's context, so
  the routing entry comes from a contextvar set by ``_use_elicitation_context``.
- Handshake-era servers (2025-11-25 and older): the server sends an
  ``elicitation/create`` request over the back-channel. That request is
  served from the session's receive loop, which is a different task, so
  routing falls back to the dictionary keyed by (server_name, tool_call_id).
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

# Routing entry of the tool call running in the current task. Authoritative on
# modern (2026-07-28) connections only; see the module docstring.
_ACTIVE_ELICITATION_ROUTING: contextvars.ContextVar[
    Optional[_ElicitationRoutingContext]
] = contextvars.ContextVar("mcp_active_elicitation_routing", default=None)

# URL-mode elicitation sends the user to an external page. Only web URLs are
# ever shown; anything else (javascript:, data:, file:, custom schemes) is
# declined before it reaches the browser.
_URL_ELICITATION_SCHEMES = frozenset({"http", "https"})


def _is_modern_session(context: Any) -> bool:
    """True when the handler was invoked on a 2026-07-28 (back-channel-free) session."""
    from mcp_types.version import MODERN_PROTOCOL_VERSIONS

    session = getattr(context, "session", None)
    version = getattr(session, "protocol_version", None)
    return isinstance(version, str) and version in MODERN_PROTOCOL_VERSIONS


def _safe_elicitation_url(url: Any) -> Optional[str]:
    """Return ``url`` when it is an absolute http(s) URL, otherwise None."""
    from urllib.parse import urlsplit

    if not isinstance(url, str) or not url.strip():
        return None
    candidate = url.strip()
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme.lower() not in _URL_ELICITATION_SCHEMES or not parts.netloc:
        return None
    return candidate


def _form_content(data: Any, response_schema: Any) -> Dict[str, Any]:
    """Shape a form answer into the JSON object the elicitation result carries.

    A bare value answers a single-field (scalar) request and is wrapped under
    ``value``. Optional fields the user left blank are omitted: elicitation
    schemas have no null type, and the FastMCP client validates the accepted
    content against the requested schema, so a blank optional enum or number
    would otherwise fail the whole tool call.
    """
    required: set = set()
    if isinstance(response_schema, dict):
        required = set(response_schema.get("required") or [])
    if not isinstance(data, dict):
        if data == "" and "value" not in required:
            return {}
        return {"value": data}
    return {
        key: value
        for key, value in data.items()
        if key in required or (value is not None and value != "")
    }


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

        Registers the entry twice: in the dictionary keyed by
        (server_name, tool_call.id), which handshake-era back-channel requests
        resolve against, and in a contextvar, which multi round-trip
        (2026-07-28) elicitations inherit from the calling task.
        """
        routing = _ElicitationRoutingContext(server_name, tool_call, update_cb, user_email)
        routing_key = (server_name, tool_call.id)
        # Tool call ids come from the model provider and are not guaranteed
        # unique across users. Two live calls sharing a key cannot be told
        # apart, so the key is poisoned until every holder has left and
        # neither call can be prompted through it.
        holders = self.__dict__.setdefault("_elicitation_routing_holders", {})
        holders[routing_key] = holders.get(routing_key, 0) + 1
        if holders[routing_key] == 1:
            self._elicitation_routing[routing_key] = routing
        else:
            logger.warning(
                "Two live tool calls share id '%s' on server '%s'; elicitation is disabled for both",
                sanitize_for_logging(tool_call.id),
                sanitize_for_logging(server_name),
            )
            self._elicitation_routing[routing_key] = AMBIGUOUS_ROUTING
        token = _ACTIVE_ELICITATION_ROUTING.set(routing)
        try:
            yield
        finally:
            _ACTIVE_ELICITATION_ROUTING.reset(token)
            holders[routing_key] -= 1
            if holders[routing_key] <= 0:
                holders.pop(routing_key, None)
                self._elicitation_routing.pop(routing_key, None)

    def _client_input_kwargs(self, server_name: str) -> Dict[str, Any]:
        """Client() arguments that let a server ask the user for input.

        The elicitation handler answers both foreground requests and prompts
        raised by a background task, so the tasks extension is built around
        the same handler.
        """
        from atlas.modules.mcp_tools.mcp_task_extension import AtlasTasksClientExtension

        handler = self._create_elicitation_handler(server_name)
        return {
            "elicitation_handler": handler,
            "extensions": [AtlasTasksClientExtension(handler)],
        }

    def _create_elicitation_handler(self, server_name: str):
        """
        Create an elicitation handler for a specific MCP server.

        Returns a handler function that captures the server_name,
        allowing dictionary-based routing that works across async tasks.
        """
        async def handler(message, response_type, params, _context):
            """Per-server elicitation handler with captured server_name."""
            from fastmcp.client.elicitation import ElicitResult
            from mcp_types import ElicitRequestFormParams, ElicitRequestURLParams

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
            request_url: Optional[str] = None
            is_url_mode = isinstance(params, ElicitRequestURLParams)
            if is_url_mode:
                request_url = _safe_elicitation_url(params.url)
                if request_url is None:
                    logger.warning(
                        "URL elicitation from server '%s' declined: not an absolute http(s) URL",
                        sanitize_for_logging(server_name),
                    )
                    return ElicitResult(action="decline", content=None)
            elif isinstance(params, ElicitRequestFormParams):
                response_schema = params.requested_schema or {}

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
                event: Dict[str, Any] = {
                    "type": "elicitation_request",
                    "elicitation_id": elicitation_id,
                    "tool_call_id": routing.tool_call.id,
                    "tool_name": routing.tool_call.name,
                    "message": message,
                    "response_schema": response_schema,
                    "mode": "url" if is_url_mode else "form",
                }
                if is_url_mode:
                    event["url"] = request_url
                await routing.update_cb(event)

                try:
                    response = await request.wait_for_response(timeout=resolve_approval_timeout())
                finally:
                    elicitation_manager.cleanup_request(elicitation_id)

                action = response.get("action", "cancel")
                data = response.get("data")

                if action != "accept":
                    # Anything that is not a recognised refusal is a cancel.
                    return ElicitResult(
                        action="decline" if action == "decline" else "cancel",
                        content=None,
                    )

                # URL mode carries no form data: accepting only records consent
                # to open the page. Never forward a payload for it.
                if is_url_mode:
                    return ElicitResult(action="accept", content=None)

                # An empty schema asks for confirmation only and must be answered
                # with an empty object. Some UIs send placeholder payloads like
                # {'none': ''}; don't forward them.
                if response_type is None:
                    return ElicitResult(action="accept", content={})

                if data is None:
                    return ElicitResult(action="accept", content=None)

                return ElicitResult(
                    action="accept",
                    content=_form_content(data, response_schema),
                )

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
        """Resolve the routing entry for an elicitation request.

        On a 2026-07-28 session the handler runs inside the tool call that
        asked, so the entry is the one bound to the current task. It is used
        only while it is still the registered entry for that call, and nothing
        the server sent can redirect the prompt to another call; a request
        that has no live entry is refused.

        On a handshake-era session the request arrives over the back-channel,
        in the receive loop's task, where the contextvar says nothing about
        which call asked and the protocol carries no reference to the
        originating ``tools/call``. Routing is therefore only attempted when
        the answer is unambiguous:

        - exactly one call is live on this server: that call raised it;
        - several are live and the request meta names one of them by
          ``tool_call_id``: that one (the id is the one Atlas sent with the
          call, echoed back by the server).

        Everything else is refused: a meta id that matches no live call is
        never redirected to whichever call happens to be alone, several live
        calls with no usable id are ambiguous, and so is any key two live
        calls share. Returns None or AMBIGUOUS_ROUTING when refused.
        """
        if _is_modern_session(context):
            active = _ACTIVE_ELICITATION_ROUTING.get()
            if (
                active is not None
                and active.server_name == server_name
                and routing_dict.get((server_name, active.tool_call.id)) is active
            ):
                return active
            return None

        tcid = None
        meta = getattr(context, "meta", None) if context else None
        if isinstance(meta, dict):
            tcid = meta.get("tool_call_id")
        elif meta is not None:
            tcid = (getattr(meta, "model_extra", None) or {}).get("tool_call_id")

        matches = [v for (srv, _), v in routing_dict.items() if srv == server_name]
        if any(v is AMBIGUOUS_ROUTING for v in matches):
            logger.warning(
                "Elicitation for server '%s' refused: live tool calls share an id",
                sanitize_for_logging(server_name),
            )
            return AMBIGUOUS_ROUTING

        if tcid is not None:
            routing = routing_dict.get((server_name, tcid))
            if routing is None:
                logger.warning(
                    "Elicitation for server '%s' names a tool call that is not live; refused",
                    sanitize_for_logging(server_name),
                )
            return routing

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            logger.warning(
                "Ambiguous routing for server '%s' with %d entries",
                sanitize_for_logging(server_name), len(matches),
            )
            return AMBIGUOUS_ROUTING
        return None
