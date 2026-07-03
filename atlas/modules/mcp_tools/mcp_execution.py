"""Tool/prompt execution for MCPToolManager.

Client selection (per-user auth vs per-conversation HTTP vs shared STDIO),
adaptive task-mode polling, and the high-level execute_tool entry point that
wires routing/log contexts and normalizes results. config_manager is
referenced via the client module to preserve test patch targets.
"""
import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.metrics_logger import log_metric
from atlas.domain.messages.models import ToolCall, ToolResult
from atlas.modules.mcp_tools.mcp_errors import (
    _is_session_terminated_error,
    _is_task_forbidden_error,
    _is_task_forbidden_result,
)
from atlas.modules.mcp_tools.mcp_discovery import (
    _ATLAS_RAG_DISCOVER_TOOL,
    _ATLAS_RAG_QUERY_TOOL,
)
from atlas.modules.mcp_tools.token_storage import AuthenticationRequiredException

logger = logging.getLogger(__name__)


def _client():
    """Lazily import the client module to avoid a module-level import cycle.

    The patched globals (``config_manager`` / ``Client`` /
    ``StreamableHttpTransport``) live on the client module; resolving them at
    call time keeps ``@patch('atlas.modules.mcp_tools.client.<name>')`` working
    regardless of which module the calling method now lives in.
    """
    from atlas.modules.mcp_tools import client
    return client


class ExecutionMixin:
    """Tool/prompt execution, task-mode polling, and result assembly."""

    def _supports_tasks(self, server_name: str, client: Any) -> bool:
        """Check if a server supports background tasks (cached)."""
        if server_name in self._server_task_support:
            return self._server_task_support[server_name]

        supports = False
        try:
            init_result = getattr(client, 'initialize_result', None)
            if init_result and hasattr(init_result, 'capabilities'):
                caps = init_result.capabilities
                supports = getattr(caps, 'tasks', None) is not None
        except Exception as e:
            logger.debug("Could not determine task support for server '%s': %s", server_name, e)

        self._server_task_support[server_name] = supports
        return supports

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        progress_handler: Optional[Any] = None,
        elicitation_handler: Optional[Any] = None,
        user_email: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
        update_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Any:
        """Call a specific tool on an MCP server.

        When ``conversation_id`` is provided, a persistent session is used (and
        reused across calls within that conversation).  Servers that advertise
        task support will be called in adaptive-polling mode: wait up to
        ``MCP_TASK_TIMEOUT`` synchronously, then switch to polling with UI
        progress notifications via ``update_cb``.

        Without ``conversation_id``, a single-use session is opened and closed
        per call (no task-mode support).

        Args:
            server_name: Name of the MCP server
            tool_name: Name of the tool to call
            arguments: Tool arguments
            progress_handler: Optional progress callback handler
            elicitation_handler: Optional elicitation callback handler. Prefer the built-in
                elicitation routing (registered at client creation time) for shared clients.
            user_email: User's email for per-user authentication (required for oauth/jwt servers)
            meta: Optional metadata dict forwarded to the MCP server (e.g. tool_call_id)
            conversation_id: If set, use a persistent session for this conversation
            update_cb: Async callback for emitting task lifecycle events to the UI
        """
        # Determine which client to use
        client = None

        # Check if this server requires per-user authentication
        if self._requires_user_auth(server_name):
            logger.debug(f"Server '{server_name}' requires user auth, user_email={user_email}")
            if user_email:
                client = await self._get_user_client(server_name, user_email, conversation_id)
                logger.debug(f"_get_user_client for '{server_name}' returned client: {client is not None}")
                if client is None:
                    # Get auth type and build OAuth URL if applicable
                    server_config = self.servers_config.get(server_name, {})
                    auth_type = server_config.get("auth_type", "oauth")
                    oauth_start_url = None
                    if auth_type == "oauth":
                        # Build OAuth start URL for automatic redirect
                        oauth_start_url = f"/api/mcp/auth/{server_name}/oauth/start"
                    raise AuthenticationRequiredException(
                        server_name=server_name,
                        auth_type=auth_type,
                        message=f"Server '{server_name}' requires authentication.",
                        oauth_start_url=oauth_start_url,
                    )
            else:
                server_config = self.servers_config.get(server_name, {})
                auth_type = server_config.get("auth_type", "oauth")
                raise AuthenticationRequiredException(
                    server_name=server_name,
                    auth_type=auth_type,
                    message=f"Server '{server_name}' requires authentication but no user context.",
                    oauth_start_url=f"/api/mcp/auth/{server_name}/oauth/start" if auth_type == "oauth" else None,
                )
        elif self._is_http_server(server_name) and user_email:
            # HTTP servers get per-user/per-conversation clients for session
            # state isolation and to avoid FastMCP nesting-counter leaks.
            client = await self._get_or_create_user_http_client(
                server_name, user_email, conversation_id
            )
        else:
            # STDIO servers use shared client (safe: BlockedStateStore prevents state use)
            if server_name not in self.clients:
                raise ValueError(f"No client available for server: {server_name}")
            client = self.clients[server_name]

        call_timeout = _client().config_manager.app_settings.mcp_call_timeout
        try:
            # Set elicitation callback before opening the client context.
            # FastMCP negotiates supported capabilities during session init.
            if elicitation_handler is not None:
                client.set_elicitation_callback(elicitation_handler)

            kwargs = {}
            if progress_handler is not None:
                kwargs["progress_handler"] = progress_handler
            if meta is not None:
                kwargs["meta"] = meta

            task_timeout = self._task_timeout

            if conversation_id:
                # Use persistent session
                session = await self._session_manager.acquire(
                    conversation_id, server_name, client, user_email=user_email
                )
                active_client = session.client
            else:
                # Fallback: no conversation context, use per-call session
                # Can't use task mode without a persistent session
                async with client:
                    result = await asyncio.wait_for(
                        client.call_tool(tool_name, arguments, **kwargs),
                        timeout=call_timeout,
                    )
                    logger.info(f"Successfully called {sanitize_for_logging(tool_name)} on {sanitize_for_logging(server_name)}")
                    return result

            # With persistent session, try adaptive task mode — unless this
            # specific tool was already observed to refuse task mode
            # (fastmcp tasks.mode="forbidden").
            use_tasks = (
                self._supports_tasks(server_name, active_client)
                and (server_name, tool_name) not in self._tool_task_forbidden
            )

            tool_task = None
            if use_tasks:
                try:
                    tool_task = await active_client.call_tool(tool_name, arguments, task=True, **kwargs)
                except Exception as task_exc:
                    if _is_task_forbidden_error(task_exc):
                        logger.info(
                            "Tool %s on %s does not support task-augmented execution; "
                            "falling back to synchronous call and caching the decision.",
                            sanitize_for_logging(tool_name),
                            sanitize_for_logging(server_name),
                        )
                        self._tool_task_forbidden.add((server_name, tool_name))
                        use_tasks = False
                    else:
                        raise

            if use_tasks:
                if tool_task.returned_immediately:
                    result = await tool_task.result()
                    # fastmcp's graceful-degradation path does not raise when
                    # the server refuses task mode mid-request: the McpError
                    # is wrapped as a ToolError on the server, the low-level
                    # handler converts it to CallToolResult(isError=True),
                    # and _call_tool_as_task returns it as an immediate
                    # result. Detect that here and fall back to sync.
                    if _is_task_forbidden_result(result):
                        logger.info(
                            "Tool %s on %s returned task-forbidden as an "
                            "immediate error result; falling back to "
                            "synchronous call and caching the decision.",
                            sanitize_for_logging(tool_name),
                            sanitize_for_logging(server_name),
                        )
                        self._tool_task_forbidden.add((server_name, tool_name))
                        use_tasks = False
                else:
                    try:
                        await asyncio.wait_for(
                            tool_task.wait(),
                            timeout=task_timeout,
                        )
                        result = await tool_task.result()
                    except asyncio.TimeoutError:
                        # Exceeded threshold -- notify UI and keep waiting
                        if update_cb:
                            await update_cb({
                                "type": "tool_task_started",
                                "tool_call_id": meta.get("tool_call_id") if meta else None,
                                "tool_name": tool_name,
                                "server_name": server_name,
                            })

                        if update_cb:
                            async def _task_progress(status):
                                try:
                                    await update_cb({
                                        "type": "tool_task_progress",
                                        "tool_call_id": meta.get("tool_call_id") if meta else None,
                                        "status": getattr(status, 'state', 'running'),
                                        "progress": getattr(status, 'progress', None),
                                        "total": getattr(status, 'total', None),
                                        "message": getattr(status, 'message', None),
                                    })
                                except Exception:
                                    logger.debug("Task progress callback failed", exc_info=True)
                            tool_task.on_status_change(_task_progress)

                        try:
                            remaining = max(call_timeout - task_timeout, 1)
                            await tool_task.wait(timeout=remaining)
                            result = await tool_task.result()
                        except asyncio.CancelledError:
                            await tool_task.cancel()
                            raise
                        finally:
                            if update_cb:
                                try:
                                    await update_cb({
                                        "type": "tool_task_completed",
                                        "tool_call_id": meta.get("tool_call_id") if meta else None,
                                    })
                                except Exception as e:
                                    logger.debug("tool_task_completed update callback failed: %s", e)
                    except asyncio.CancelledError:
                        await tool_task.cancel()
                        raise

            if not use_tasks:
                result = await asyncio.wait_for(
                    active_client.call_tool(tool_name, arguments, **kwargs),
                    timeout=call_timeout,
                )

            logger.info(f"Successfully called {sanitize_for_logging(tool_name)} on {sanitize_for_logging(server_name)}")
            return result
        except asyncio.TimeoutError:
            error_msg = f"Tool call '{tool_name}' on server '{server_name}' timed out after {call_timeout}s"
            logger.error(error_msg)
            self._record_server_failure(server_name, error_msg)
            raise TimeoutError(error_msg)
        except Exception as e:
            logger.error(f"Error calling {tool_name} on {server_name}: {e}")
            raise

    async def get_prompt(
        self,
        server_name: str,
        prompt_name: str,
        arguments: Dict[str, Any] = None,
        *,
        user_email: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
    ) -> Any:
        """Get a specific prompt from an MCP server.

        Mirrors ``call_tool``'s auth routing: servers that declare an
        ``auth_type`` of oauth/jwt/bearer/api_key route through
        ``_get_user_client`` so the request is sent under the user's
        stored token. Other HTTP servers fall through to the
        per-conversation plain HTTP client.
        """
        if self._requires_user_auth(server_name):
            if not user_email:
                server_config = self.servers_config.get(server_name, {})
                auth_type = server_config.get("auth_type", "oauth")
                raise AuthenticationRequiredException(
                    server_name=server_name,
                    auth_type=auth_type,
                    message=(
                        f"Server '{server_name}' requires authentication "
                        "but no user context."
                    ),
                    oauth_start_url=(
                        f"/api/mcp/auth/{server_name}/oauth/start"
                        if auth_type == "oauth" else None
                    ),
                )
            client = await self._get_user_client(server_name, user_email, conversation_id)
            if client is None:
                server_config = self.servers_config.get(server_name, {})
                auth_type = server_config.get("auth_type", "oauth")
                raise AuthenticationRequiredException(
                    server_name=server_name,
                    auth_type=auth_type,
                    message=f"Server '{server_name}' requires authentication.",
                    oauth_start_url=(
                        f"/api/mcp/auth/{server_name}/oauth/start"
                        if auth_type == "oauth" else None
                    ),
                )
        elif self._is_http_server(server_name) and user_email:
            client = await self._get_or_create_user_http_client(
                server_name, user_email, conversation_id
            )
        elif server_name not in self.clients:
            raise ValueError(f"No client available for server: {server_name}")
        else:
            client = self.clients[server_name]
        try:
            async with client:
                kwargs = {}
                if meta is not None:
                    kwargs["meta"] = meta
                if arguments:
                    result = await client.get_prompt(prompt_name, arguments, **kwargs)
                else:
                    result = await client.get_prompt(prompt_name, **kwargs)
                logger.info(f"Successfully retrieved prompt {prompt_name} from {server_name}")
                return result
        except Exception as e:
            logger.error(f"Error getting prompt {prompt_name} from {server_name}: {e}")
            raise

    async def execute_tool(
        self,
        tool_call: ToolCall,
        context: Optional[Dict[str, Any]] = None
    ) -> ToolResult:
        """Execute a tool call."""
        logger.debug("ToolManager.execute_tool: tool=%s", tool_call.name)
        # Handle canvas pseudo-tool
        if tool_call.name == "canvas_canvas":
            # Canvas tool just returns the content - it's handled by frontend
            content = tool_call.arguments.get("content", "")
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Canvas content displayed: {content[:100]}..." if len(content) > 100 else f"Canvas content displayed: {content}",
                success=True
            )
        if tool_call.name in (_ATLAS_RAG_DISCOVER_TOOL, _ATLAS_RAG_QUERY_TOOL):
            return await self._execute_atlas_rag_tool(tool_call, context)

        # Use the tool index to get server and tool name (avoids parsing issues with dashes/underscores)
        if not hasattr(self, "_tool_index") or not getattr(self, "_tool_index"):
            # Build tool index if not available (same logic as in get_tools_schema)
            index = {}
            for server_name, server_data in self.available_tools.items():
                if server_name == "canvas":
                    index["canvas_canvas"] = {
                        'server': 'canvas',
                        'tool': None  # pseudo tool
                    }
                else:
                    for tool in server_data.get('tools', []):
                        full_name = f"{server_name}_{tool.name}"
                        index[full_name] = {
                            'server': server_name,
                            'tool': tool
                        }
            self._tool_index = index

        # Look up the tool in our index
        tool_entry = self._tool_index.get(tool_call.name)
        if not tool_entry:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Tool not found: {tool_call.name}",
                success=False,
                error=f"Tool not found: {tool_call.name}"
            )

        server_name = tool_entry['server']
        actual_tool_name = tool_entry['tool'].name if tool_entry['tool'] else tool_call.name

        try:
            update_cb = None
            user_email = None
            conversation_id = None
            if isinstance(context, dict):
                update_cb = context.get("update_callback")
                user_email = context.get("user_email")
                conversation_id = context.get("conversation_id")

            if update_cb is None:
                logger.warning(
                    f"Executing tool '{tool_call.name}' without update_callback - "
                    f"elicitation will not work. Context type: {type(context)}"
                )
            else:
                logger.debug(f"Executing tool '{tool_call.name}' with update_callback present")

            async def _tool_log_callback(
                log_server_name: str,
                level: str,
                message: str,
                extra: Dict[str, Any],
            ) -> None:
                if update_cb is None:
                    return
                try:
                    # Deferred import to avoid cycles
                    from atlas.application.chat.utilities.event_notifier import notify_tool_log
                    await notify_tool_log(
                        server_name=log_server_name,
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                        level=level,
                        message=sanitize_for_logging(message),
                        extra=extra,
                        update_callback=update_cb,
                    )
                except Exception:
                    logger.debug("Tool log forwarding failed", exc_info=True)

            # Build a progress handler that forwards to UI if provided via context
            async def _progress_handler(progress: float, total: Optional[float], message: Optional[str]) -> None:
                try:
                    if update_cb is not None:
                        # Deferred import to avoid cycles
                        from atlas.application.chat.utilities.event_notifier import notify_tool_progress
                        await notify_tool_progress(
                            tool_call_id=tool_call.id,
                            tool_name=tool_call.name,
                            progress=progress,
                            total=total,
                            message=message,
                            update_callback=update_cb,
                        )
                except Exception:
                    logger.debug("Progress handler forwarding failed", exc_info=True)

            if update_cb is not None:
                async with self._use_log_callback(_tool_log_callback):
                    async with self._use_elicitation_context(server_name, tool_call, update_cb):
                        async with self._use_sampling_context(server_name, tool_call, update_cb):
                            raw_result = await self.call_tool(
                                server_name,
                                actual_tool_name,
                                tool_call.arguments,
                                progress_handler=_progress_handler,
                                user_email=user_email,
                                meta={"tool_call_id": tool_call.id},
                                conversation_id=conversation_id,
                                update_cb=update_cb,
                            )
            else:
                async with self._use_elicitation_context(server_name, tool_call, update_cb):
                    async with self._use_sampling_context(server_name, tool_call, update_cb):
                        raw_result = await self.call_tool(
                            server_name,
                            actual_tool_name,
                            tool_call.arguments,
                            progress_handler=_progress_handler,
                            user_email=user_email,
                            meta={"tool_call_id": tool_call.id},
                            conversation_id=conversation_id,
                            update_cb=update_cb,
                        )
            normalized_content = self._normalize_mcp_tool_result(raw_result)
            content_str = json.dumps(normalized_content, ensure_ascii=False)

            artifacts, display_config, meta_data = self._extract_v2_components(raw_result, tool_call.name)

            log_metric("tool_call", user_email, tool_name=actual_tool_name)

            return ToolResult(
                tool_call_id=tool_call.id,
                content=content_str,
                success=True,
                artifacts=artifacts,
                display_config=display_config,
                meta_data=meta_data
            )
        except Exception as e:
            logger.error(f"Error executing tool {tool_call.name}: {e}")

            # If the server terminated its session, evict the cached session so
            # the next call transparently opens a fresh one instead of reusing
            # the dead session and failing again.
            if _is_session_terminated_error(e) and conversation_id:
                logger.warning(
                    "Session terminated for server=%s conversation=%s — evicting dead session",
                    server_name,
                    conversation_id,
                )
                try:
                    await self._session_manager.release(
                        conversation_id, server_name, user_email=user_email
                    )
                except Exception as release_exc:
                    logger.warning(
                        "Failed to release dead session for server=%s conversation=%s: %s",
                        server_name,
                        conversation_id,
                        release_exc,
                    )

            log_metric("tool_error", user_email, tool_name=actual_tool_name)

            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error executing tool: {str(e)}",
                success=False,
                error=str(e)
            )

    async def execute_tool_calls(
        self,
        tool_calls: List[ToolCall],
        context: Optional[Dict[str, Any]] = None
    ) -> List[ToolResult]:
        """Execute multiple tool calls."""
        results = []
        for tool_call in tool_calls:
            result = await self.execute_tool(tool_call, context)
            results.append(result)
        return results

    async def _execute_atlas_rag_tool(
        self,
        tool_call: ToolCall,
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        """Execute Atlas RAG pseudo-tools exposed to agent mode."""
        from atlas.infrastructure.app_factory import app_factory

        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        # Never trust a model-supplied identity. The authenticated user must come
        # from the server-side execution context only; strip any _atlas_user the
        # model may have set so it cannot reach an authorization decision, and
        # fail closed if the trusted context has no user.
        args.pop("_atlas_user", None)
        user_email = None
        selected_data_sources: List[str] = []
        compliance_level = None
        if isinstance(context, dict):
            user_email = context.get("user_email")
            selected_data_sources = list(context.get("selected_data_sources") or [])
            compliance_level = context.get("compliance_level")

        if not user_email:
            return ToolResult(
                tool_call_id=tool_call.id,
                content="Atlas RAG tool requires an authenticated user context.",
                success=False,
                error="Missing user context",
            )

        try:
            unified_rag = app_factory.get_unified_rag_service()
            rag_mcp = app_factory.get_rag_mcp_service()

            # The authorization allow-list is the user's discoverable set, which
            # is bounded by TWO server-side boundaries, both keyed on the
            # authenticated ``user_email`` and neither settable by the model:
            #   1. group membership, and
            #   2. the compliance level, when the compliance feature is enabled.
            # ``compliance_level`` comes from the trusted execution context (the
            # user's active level, validated in the service layer), NOT from tool
            # arguments -- so the model cannot widen its reach or mix sources
            # across compliance levels. Discovery applies the compliance filter,
            # and the query gate below only permits sources in this set, so a
            # model- or client-supplied ``data_sources`` list cannot cross it.
            # HTTP sources come from ``unified_rag`` and MCP sources from
            # ``rag_mcp``; each must be queried through the matching service, so
            # track each source's origin.
            http_servers: List[Dict[str, Any]] = []
            mcp_servers: List[Dict[str, Any]] = []
            if unified_rag:
                http_servers = await unified_rag.discover_data_sources(
                    user_email, user_compliance_level=compliance_level
                )
            if rag_mcp:
                mcp_servers = await rag_mcp.discover_servers(
                    user_email, user_compliance_level=compliance_level
                )

            def _flatten(servers: List[Dict[str, Any]]) -> List[str]:
                out: List[str] = []
                for server in servers:
                    server_name = server.get("server", "")
                    for source in server.get("sources", []):
                        source_id = source.get("id", "")
                        if server_name and source_id:
                            out.append(f"{server_name}:{source_id}")
                return out

            http_sources = _flatten(http_servers)
            mcp_sources = _flatten(mcp_servers)
            source_origin: Dict[str, str] = {}
            for s in http_sources:
                source_origin.setdefault(s, "http")
            for s in mcp_sources:
                source_origin.setdefault(s, "mcp")

            rag_servers = http_servers + mcp_servers
            # Preserve discovery order while de-duping
            deduped_sources = list(dict.fromkeys(http_sources + mcp_sources))

            if tool_call.name == _ATLAS_RAG_DISCOVER_TOOL:
                payload = {
                    "results": {
                        "sources": deduped_sources,
                        "rag_servers": rag_servers,
                    }
                }
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=json.dumps(payload, ensure_ascii=False),
                    success=True,
                )

            query = args.get("query")
            if not isinstance(query, str) or not query.strip():
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content="atlas_rag_query requires a non-empty 'query' string.",
                    success=False,
                    error="Missing query",
                )

            requested_sources = args.get("data_sources")
            if isinstance(requested_sources, list):
                sources = [s for s in requested_sources if isinstance(s, str) and ":" in s]
            else:
                sources = []
            if not sources:
                sources = [s for s in selected_data_sources if isinstance(s, str) and ":" in s]
            if not sources:
                sources = deduped_sources

            # Authorization gate: only query sources in the user's discovered
            # (group/compliance-authorized) set. ``data_sources`` may be filled
            # by the model or a crafted client, and ``selected_data_sources``
            # arrives from the request context, so neither can be trusted to
            # name a source the user is actually allowed to read. ``query_rag``
            # itself only checks that a source is *configured*, not that this
            # user is authorized for it, so without this intersection a caller
            # could bypass the UI-filtered list by naming a configured source
            # directly. ``deduped_sources`` is the authoritative allow-list.
            authorized = set(deduped_sources)
            ignored_sources = sorted(s for s in sources if s not in authorized)
            if ignored_sources:
                logger.warning(
                    "atlas_rag_query: ignoring %d requested source(s) outside the "
                    "user's authorized set for user %s",
                    len(ignored_sources),
                    sanitize_for_logging(user_email),
                )
            sources = [s for s in sources if s in authorized]

            if not sources:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content="No authorized RAG data sources are available for this query.",
                    success=False,
                    error="No authorized RAG data sources",
                )

            messages = [{"role": "user", "content": query}]

            # Group by server AND by origin so each source is queried through the
            # service that can actually resolve it: HTTP sources via unified_rag,
            # MCP sources via rag_mcp.synthesize. Without this split, MCP sources
            # (which live in rag_mcp_config, not rag_sources_config) pass the auth
            # gate but fail unified_rag.query_rag with "RAG source not found".
            http_groups: Dict[str, List[str]] = {}
            mcp_groups: Dict[str, List[str]] = {}
            for source in sources:
                server_name = source.split(":", 1)[0]
                if source_origin.get(source) == "mcp":
                    mcp_groups.setdefault(server_name, []).append(source)
                else:
                    http_groups.setdefault(server_name, []).append(source)

            async def _query_http(group: List[str]) -> Dict[str, Any]:
                if not unified_rag:
                    raise RuntimeError("Unified RAG service is not configured")
                if len(group) == 1:
                    resp = await unified_rag.query_rag(user_email, group[0], messages)
                else:
                    resp = await unified_rag.query_rag_batch(user_email, group, messages)
                return {
                    "data_sources": group,
                    "content": resp.content,
                    "is_completion": bool(resp.is_completion),
                }

            async def _query_mcp(group: List[str]) -> Dict[str, Any]:
                if not rag_mcp:
                    raise RuntimeError("RAG MCP service is not configured")
                mcp_response = await rag_mcp.synthesize(
                    username=user_email, query=query, sources=group
                )
                results = mcp_response.get("results", {}) if isinstance(mcp_response, dict) else {}
                return {
                    "data_sources": group,
                    "content": results.get("answer", ""),
                    "is_completion": False,
                }

            # Run per-server queries concurrently; isolate failures so one backend
            # error does not discard every other source's answer.
            group_lists = list(http_groups.values()) + list(mcp_groups.values())
            coros = [_query_http(g) for g in http_groups.values()]
            coros += [_query_mcp(g) for g in mcp_groups.values()]
            settled = await asyncio.gather(*coros, return_exceptions=True)

            answers: List[Dict[str, Any]] = []
            errors: List[Dict[str, Any]] = []
            for group_sources, outcome in zip(group_lists, settled):
                if isinstance(outcome, Exception):
                    logger.error(
                        "atlas_rag_query: source group %s failed: %s",
                        sanitize_for_logging(", ".join(group_sources)),
                        outcome,
                        exc_info=True,
                    )
                    errors.append({"data_sources": group_sources, "error": str(outcome)})
                else:
                    answers.append(outcome)

            results_payload: Dict[str, Any] = {
                "query": query,
                "answers": answers,
                "combined_answer": "\n\n".join(
                    answer["content"] for answer in answers if answer.get("content")
                ),
            }
            # Surface partial coverage so the model does not summarize a silently
            # narrowed corpus as complete.
            if ignored_sources:
                results_payload["ignored_sources"] = ignored_sources
            if errors:
                results_payload["errors"] = errors

            payload = {"results": results_payload}
            succeeded = bool(answers)
            return ToolResult(
                tool_call_id=tool_call.id,
                content=json.dumps(payload, ensure_ascii=False),
                success=succeeded,
                error=None if succeeded else "All RAG source queries failed",
            )
        except Exception as e:
            logger.error("Error executing %s: %s", tool_call.name, e, exc_info=True)
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error executing tool: {str(e)}",
                success=False,
                error=str(e),
            )
