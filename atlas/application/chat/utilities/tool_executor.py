"""
Tool execution utilities - pure functions for tool operations.

This module provides stateless utility functions for handling tool execution,
argument processing, and synthesis decisions without maintaining any state.
"""

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from atlas.core.capabilities import create_download_url
from atlas.domain.messages.models import ToolCall, ToolResult
from atlas.interfaces.llm import LLMResponse
from atlas.modules.mcp_tools.token_storage import AuthenticationRequiredException

from ..approval_manager import get_approval_manager
from .event_notifier import _sanitize_filename_value  # reuse same filename sanitizer for UI args

logger = logging.getLogger(__name__)


def _try_repair_json(raw: str) -> Optional[Dict[str, Any]]:
    """Attempt to repair truncated JSON from LLM tool arguments.

    Common cases: missing opening/closing braces, trailing quote.
    Returns parsed dict on success, None on failure.
    """
    s = raw.strip()
    # Add missing braces
    if not s.startswith("{"):
        s = "{" + s
    if not s.endswith("}"):
        s = s + "}"
    try:
        result = json.loads(s)
        if isinstance(result, dict):
            return result
    except Exception:
        pass
    # Try closing an open string value: e.g. {"expression": "355/113
    if s.count('"') % 2 != 0:
        s = s.rstrip("}") + '"}'
        try:
            result = json.loads(s)
            if isinstance(result, dict):
                return result
        except Exception:
            pass
    return None


# Type hint for update callback
UpdateCallback = Callable[[Dict[str, Any]], Awaitable[None]]


async def execute_multiple_tools(
    tool_calls: list,
    session_context: Dict[str, Any],
    tool_manager,
    update_callback: Optional[UpdateCallback] = None,
    config_manager=None,
    skip_approval: bool = False,
) -> List[ToolResult]:
    """Execute multiple tool calls concurrently using asyncio.gather.

    Each tool call runs as an independent coroutine so that IO-bound MCP
    tool executions (HTTP, subprocess, etc.) overlap rather than serialize.
    Results are returned in the same order as the input *tool_calls* list.
    """
    if not tool_calls:
        return []

    if len(tool_calls) == 1:
        result = await execute_single_tool(
            tool_call=tool_calls[0],
            session_context=session_context,
            tool_manager=tool_manager,
            update_callback=update_callback,
            config_manager=config_manager,
            skip_approval=skip_approval,
        )
        return [result]

    logger.info("Executing %d tool calls in parallel", len(tool_calls))

    coros = [
        execute_single_tool(
            tool_call=tc,
            session_context=session_context,
            tool_manager=tool_manager,
            update_callback=update_callback,
            config_manager=config_manager,
            skip_approval=skip_approval,
        )
        for tc in tool_calls
    ]

    results = await asyncio.gather(*coros, return_exceptions=True)

    # Convert exceptions to error ToolResults so callers always get a list
    final: List[ToolResult] = []
    for idx, res in enumerate(results):
        if isinstance(res, Exception):
            tc = tool_calls[idx]
            tc_id = getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else f"unknown-{idx}")
            tc_name = ""
            try:
                tc_name = tc.function.name
            except Exception:
                tc_name = str(tc.get("function", {}).get("name", "unknown") if isinstance(tc, dict) else "unknown")
            logger.error("Parallel tool execution failed for %s: %s", tc_name, res)
            final.append(ToolResult(
                tool_call_id=tc_id,
                content=f"Tool execution failed: {res}",
                success=False,
                error=str(res),
            ))
        else:
            final.append(res)
    return final


async def execute_tools_workflow(
    llm_response: LLMResponse,
    messages: List[Dict],
    model: str,
    session_context: Dict[str, Any],
    tool_manager,
    llm_caller,
    prompt_provider,
    update_callback: Optional[UpdateCallback] = None,
    config_manager=None,
    skip_approval: bool = False,
    user_email: Optional[str] = None,
) -> tuple[str, List[ToolResult]]:
    """
    Execute the complete tools workflow: calls -> results -> synthesis.

    Pure function that coordinates tool execution without maintaining state.
    """
    logger.debug("Entering execute_tools_workflow")
    # Add assistant message with tool calls
    messages.append({
        "role": "assistant",
        "content": llm_response.content,
        "tool_calls": llm_response.tool_calls
    })

    # Execute all tool calls in parallel
    tool_results = await execute_multiple_tools(
        tool_calls=llm_response.tool_calls,
        session_context=session_context,
        tool_manager=tool_manager,
        update_callback=update_callback,
        config_manager=config_manager,
        skip_approval=skip_approval,
    )

    # Add tool results to messages
    for result in tool_results:
        messages.append({
            "role": "tool",
            "content": result.content,
            "tool_call_id": result.tool_call_id
        })

    # Determine if synthesis is needed
    final_response = await handle_synthesis_decision(
        llm_response=llm_response,
        messages=messages,
        model=model,
        session_context=session_context,
        llm_caller=llm_caller,
        prompt_provider=prompt_provider,
        update_callback=update_callback,
        user_email=user_email,
    )

    return final_response, tool_results


def requires_approval(tool_name: str, config_manager) -> tuple[bool, bool, bool]:
    """
    Check if a tool requires approval before execution.

    Args:
        tool_name: Name of the tool to check
        config_manager: ConfigManager instance (can be None)

    Returns:
        Tuple of (requires_approval, allow_edit, admin_required)
        - requires_approval: Whether approval is needed (always True)
        - allow_edit: Whether arguments can be edited (always True)
        - admin_required: Whether this is admin-mandated (True) or user-level (False)

    Admin-required (True) means user CANNOT toggle auto-approve:
        - FORCE_TOOL_APPROVAL_GLOBALLY=true
        - Per-tool require_approval=true in mcp.json

    User-level (False) means user CAN toggle auto-approve via inline UI:
        - All other cases (including REQUIRE_TOOL_APPROVAL_BY_DEFAULT)
    """
    if config_manager is None:
        return (True, True, False)  # Default to requiring user-level approval

    try:
        # Global override: force approval for all tools (admin-enforced)
        app_settings = getattr(config_manager, "app_settings", None)
        force_flag = False
        if app_settings is not None:
            raw_force = getattr(app_settings, "force_tool_approval_globally", False)
            force_flag = (isinstance(raw_force, bool) and raw_force is True)
        if force_flag:
            return (True, True, True)

        approvals_config = config_manager.tool_approvals_config

        # Per-tool explicit requirement (admin-enforced)
        if tool_name in approvals_config.tools:
            tool_config = approvals_config.tools[tool_name]
            # Only treat as admin-required if explicitly required
            if getattr(tool_config, "require_approval", False):
                return (True, True, True)
            # Explicit false falls through to default behavior

        # Default requirement: user-level regardless of default setting
        # Users can always toggle auto-approve via inline UI unless admin explicitly requires it
        return (True, True, False)

    except Exception as e:
        logger.warning(f"Error checking approval requirements for {tool_name}: {e}")
    return (True, True, False)  # Default to user-level approval on error


def tool_accepts_mcp_data(tool_name: str, tool_manager) -> bool:
    """
    Check if a tool accepts an _mcp_data parameter by examining its schema.

    Returns True if the tool schema defines an '_mcp_data' parameter, False otherwise.
    """
    if not tool_name or not tool_manager:
        return False

    try:
        tools_schema = tool_manager.get_tools_schema([tool_name])
        if not tools_schema:
            return False

        for tool_schema in tools_schema:
            if tool_schema.get("function", {}).get("name") == tool_name:
                parameters = tool_schema.get("function", {}).get("parameters", {})
                properties = parameters.get("properties", {})
                return "_mcp_data" in properties

        return False
    except Exception as e:
        logger.warning(f"Could not determine if tool {tool_name} accepts _mcp_data: {e}")
        return False


def build_mcp_data(tool_manager) -> Dict[str, Any]:
    """
    Build structured metadata about all available MCP tools for injection.

    Returns a dict with server and tool information that planning tools
    can use to reason about available capabilities.
    """
    available_servers = []

    if not tool_manager or not hasattr(tool_manager, "available_tools"):
        return {"available_servers": available_servers}

    for server_name, server_data in tool_manager.available_tools.items():
        if server_name == "canvas":
            continue

        tools_list = server_data.get("tools", []) or []
        config = server_data.get("config", {}) or {}

        tools_info = []
        for tool in tools_list:
            tool_entry = {
                "name": f"{server_name}_{tool.name}",
                "description": getattr(tool, "description", "") or "",
                "parameters": getattr(tool, "inputSchema", {}) or {},
            }
            tools_info.append(tool_entry)

        server_entry = {
            "server_name": server_name,
            "description": config.get("description", "") or config.get("short_description", "") or "",
            "tools": tools_info,
        }
        available_servers.append(server_entry)

    return {"available_servers": available_servers}


def tool_accepts_username(tool_name: str, tool_manager) -> bool:
    """
    Check if a tool accepts a username parameter by examining its schema.

    Returns True if the tool schema defines a 'username' parameter, False otherwise.
    """
    if not tool_name or not tool_manager:
        return False

    try:
        # Get the tool schema for this specific tool
        tools_schema = tool_manager.get_tools_schema([tool_name])
        if not tools_schema:
            return False

        # Find the schema for our specific tool
        for tool_schema in tools_schema:
            if tool_schema.get("function", {}).get("name") == tool_name:
                # Check if username is in the parameters
                parameters = tool_schema.get("function", {}).get("parameters", {})
                properties = parameters.get("properties", {})
                return "username" in properties

        return False
    except Exception as e:
        logger.warning(f"Could not determine if tool {tool_name} accepts username: {e}")
        return False  # Default to not injecting if we can't determine


async def execute_single_tool(
    tool_call,
    session_context: Dict[str, Any],
    tool_manager,
    update_callback: Optional[UpdateCallback] = None,
    config_manager=None,
    skip_approval: bool = False,
) -> ToolResult:
    """
    Execute a single tool with argument preparation and error handling.

    Pure function that doesn't maintain state - all context passed as parameters.
    """
    logger.debug("Entering execute_single_tool")
    from . import event_notifier

    try:
        # Prepare arguments with injections (username, filename URL mapping)
        parsed_args = prepare_tool_arguments(tool_call, session_context, tool_manager)

        # Filter to only schema-declared parameters so MCP tools don't receive extras
        filtered_args = _filter_args_to_schema(parsed_args, tool_call.function.name, tool_manager)

        # Sanitize arguments for UI (hide tokens in URLs, etc.)
        display_args = _sanitize_args_for_ui(dict(filtered_args))

        # Check if this tool requires approval
        needs_approval = False
        allow_edit = True
        admin_required = False
        if skip_approval:
            needs_approval = False
        elif config_manager:
            needs_approval, allow_edit, admin_required = requires_approval(tool_call.function.name, config_manager)
        else:
            # No config manager means user-level approval by default
            needs_approval = True
            allow_edit = True
            admin_required = False

        # Track if arguments were edited (for LLM context)
        arguments_were_edited = False
        original_display_args = dict(display_args) if isinstance(display_args, dict) else display_args

        # If approval is required, request it from the user
        if needs_approval:
            logger.info(f"Tool {tool_call.function.name} requires approval (admin_required={admin_required})")

            # Send approval request to frontend
            if update_callback:
                await update_callback({
                    "type": "tool_approval_request",
                    "tool_call_id": tool_call.id,
                    "tool_name": tool_call.function.name,
                    "arguments": display_args,
                    "allow_edit": allow_edit,
                    "admin_required": admin_required
                })

            # Wait for approval response
            approval_manager = get_approval_manager()
            request = approval_manager.create_approval_request(
                tool_call.id,
                tool_call.function.name,
                filtered_args,
                allow_edit
            )

            try:
                response = await request.wait_for_response(timeout=300.0)
                approval_manager.cleanup_request(tool_call.id)

                if not response["approved"]:
                    # Tool was rejected
                    reason = response.get("reason", "User rejected the tool call")
                    logger.info(f"Tool {tool_call.function.name} rejected by user: {reason}")
                    return ToolResult(
                        tool_call_id=tool_call.id,
                        content=f"Tool execution rejected by user: {reason}",
                        success=False,
                        error=reason
                    )

                # Use potentially edited arguments
                if allow_edit and response.get("arguments"):
                    edited_args = response["arguments"]
                    # Check if arguments actually changed by comparing with what we sent (display_args)
                    # Use json comparison to avoid false positives from dict ordering
                    if json.dumps(edited_args, sort_keys=True) != json.dumps(original_display_args, sort_keys=True):
                        arguments_were_edited = True
                        logger.info(f"User edited arguments for tool {tool_call.function.name}")

                        # SECURITY: Re-apply security injections after user edits
                        # This ensures username and other security-critical parameters cannot be tampered with
                        re_injected_args = inject_context_into_args(
                            edited_args,
                            session_context,
                            tool_call.function.name,
                            tool_manager
                        )

                        # Re-filter to schema to ensure only valid parameters
                        filtered_args = _filter_args_to_schema(
                            re_injected_args,
                            tool_call.function.name,
                            tool_manager
                        )
                    else:
                        # No actual changes, but response included arguments - keep original filtered_args
                        logger.debug(f"Arguments returned unchanged for tool {tool_call.function.name}")

            except asyncio.TimeoutError:
                approval_manager.cleanup_request(tool_call.id)
                logger.warning(f"Approval timeout for tool {tool_call.function.name}")
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content="Tool execution timed out waiting for user approval",
                    success=False,
                    error="Approval timeout"
                )

        # Send tool start notification with sanitized args
        await event_notifier.notify_tool_start(tool_call, display_args, update_callback)

        # Create tool call object and execute with filtered args only
        tool_call_obj = ToolCall(
            id=tool_call.id,
            name=tool_call.function.name,
            arguments=filtered_args
        )

        result = await tool_manager.execute_tool(
            tool_call_obj,
            context={
                "session_id": session_context.get("session_id"),
                "user_email": session_context.get("user_email"),
                # pass update callback so MCP client can emit progress
                "update_callback": update_callback,
            }
        )

        # If arguments were edited, prepend a note to the result for LLM context
        if arguments_were_edited:
            edit_note = (
                f"[IMPORTANT: The user manually edited the tool arguments before execution. "
                f"Security-critical parameters (like username) were re-injected by the system and cannot be modified. "
                f"The ACTUAL arguments executed were: {json.dumps(filtered_args)}. "
                f"Your response must reflect these arguments as the user's true intent.]\\n\\n"
            )
            if isinstance(result.content, str):
                result.content = edit_note + result.content
            else:
                # If content is not a string, convert and prepend
                result.content = edit_note + str(result.content)

        # Send tool complete notification
        await event_notifier.notify_tool_complete(tool_call, result, parsed_args, update_callback)

        return result

    except AuthenticationRequiredException as auth_err:
        # Special handling for authentication required - send OAuth redirect info
        logger.info(f"Tool {tool_call.function.name} requires authentication for server {auth_err.server_name}")

        # Send authentication required notification with OAuth URL
        if update_callback:
            await update_callback({
                "type": "auth_required",
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.function.name,
                "server_name": auth_err.server_name,
                "auth_type": auth_err.auth_type,
                "oauth_start_url": auth_err.oauth_start_url,
                "message": auth_err.message,
            })

        # Return error result with auth info
        return ToolResult(
            tool_call_id=tool_call.id,
            content=f"Authentication required: {auth_err.message}",
            success=False,
            error=str(auth_err),
            meta_data={
                "auth_required": True,
                "server_name": auth_err.server_name,
                "auth_type": auth_err.auth_type,
                "oauth_start_url": auth_err.oauth_start_url,
            }
        )

    except Exception as e:
        logger.error(f"Error executing tool {tool_call.function.name}: {e}")

        # Send tool error notification
        await event_notifier.notify_tool_error(tool_call, str(e), update_callback)

        # Return error result instead of raising
        return ToolResult(
            tool_call_id=tool_call.id,
            content=f"Tool execution failed: {str(e)}",
            success=False,
            error=str(e)
        )


def _filter_args_to_schema(parsed_args: Dict[str, Any], tool_name: str, tool_manager) -> Dict[str, Any]:
    """Return only arguments that are explicitly declared in the tool schema.

    If schema can't be retrieved, fall back to dropping known injected extras
    like original_* and file_url(s) to avoid Pydantic validation errors.
    """
    try:
        tools_schema = tool_manager.get_tools_schema([tool_name]) if tool_manager else []
        found_schema = False
        allowed: set[str] = set()
        for tool_schema in tools_schema or []:
            if tool_schema.get("function", {}).get("name") == tool_name:
                params = tool_schema.get("function", {}).get("parameters", {})
                props = params.get("properties", {}) or {}
                allowed = set(props.keys())
                found_schema = True
                break

        # If we found the tool's schema, filter to allowed keys only
        # (even if allowed is empty - meaning no parameters expected)
        if found_schema:
            return {k: v for k, v in (parsed_args or {}).items() if k in allowed}
    except Exception:
        # Fall through to conservative filtering
        pass

    # Conservative fallback: drop common injected extras if schema unavailable
    drop_prefixes = ("original_",)
    drop_keys = {"file_url", "file_urls"}
    return {k: v for k, v in (parsed_args or {}).items()
            if not any(k.startswith(p) for p in drop_prefixes) and k not in drop_keys}


def _sanitize_args_for_ui(args: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize arguments before emitting to UI.

    - Reduce any filename(s) to clean basenames (no query/token, no internal prefixes)
    - Avoid leaking full download URLs or tokens to regular users in the chat UI
    """
    cleaned = dict(args or {})

    # Single filename
    if isinstance(cleaned.get("filename"), str):
        cleaned["filename"] = _sanitize_filename_value(cleaned["filename"])  # basename only

    # Multiple filenames
    if isinstance(cleaned.get("file_names"), list):
        cleaned["file_names"] = [
            _sanitize_filename_value(x) if isinstance(x, str) else x
            for x in cleaned["file_names"]
        ]

    # If a tool schema (unexpectedly) exposes file_url(s), sanitize for display too
    if isinstance(cleaned.get("file_url"), str):
        cleaned["file_url"] = _sanitize_filename_value(cleaned["file_url"])  # show just name
    if isinstance(cleaned.get("file_urls"), list):
        cleaned["file_urls"] = [
            _sanitize_filename_value(x) if isinstance(x, str) else x
            for x in cleaned["file_urls"]
        ]

    return cleaned


def prepare_tool_arguments(tool_call, session_context: Dict[str, Any], tool_manager=None) -> Dict[str, Any]:
    """
    Process and prepare tool arguments with all injections and transformations.

    Pure function that transforms arguments based on context and tool schema.
    """
    logger.debug("Entering prepare_tool_arguments")
    # Parse raw arguments
    raw_args = getattr(tool_call.function, "arguments", {})
    if isinstance(raw_args, dict):
        parsed_args = raw_args
    else:
        if raw_args is None or raw_args == "":
            parsed_args = {}
        else:
            try:
                parsed_args = json.loads(raw_args)
                if not isinstance(parsed_args, dict):
                    parsed_args = {"_value": parsed_args}
            except Exception:
                # Attempt to repair truncated JSON (e.g., missing braces)
                repaired = _try_repair_json(raw_args)
                if repaired is not None:
                    logger.info(
                        "Repaired truncated tool arguments for %s",
                        getattr(tool_call.function, "name", "<unknown>"),
                    )
                    parsed_args = repaired
                else:
                    logger.warning(
                        "Failed to parse tool arguments as JSON for %s, using empty dict. Raw: %r",
                        getattr(tool_call.function, "name", "<unknown>"), raw_args
                    )
                    parsed_args = {}

    # Inject username and file URL mappings with schema awareness
    return inject_context_into_args(parsed_args, session_context, tool_call.function.name, tool_manager)


def inject_context_into_args(parsed_args: Dict[str, Any], session_context: Dict[str, Any], tool_name: str = None, tool_manager=None) -> Dict[str, Any]:
    """
    Inject username and file URL mappings into tool arguments.

    Pure function that adds context without side effects.
    Only injects username if the tool schema defines a username parameter.

    If BACKEND_PUBLIC_URL is configured, uses absolute URLs for file downloads.
    If INCLUDE_FILE_CONTENT_BASE64 is enabled, also injects base64 content as fallback.
    """
    if not isinstance(parsed_args, dict):
        return parsed_args

    try:
        # Inject username. Prefer schema-aware injection; if schema unavailable,
        # include username by default to support tools that expect it.
        user_email = session_context.get("user_email")
        if user_email and (not tool_manager or tool_accepts_username(tool_name, tool_manager)):
            parsed_args["username"] = user_email

        # Inject _mcp_data if the tool schema declares it
        if tool_manager and tool_accepts_mcp_data(tool_name, tool_manager):
            parsed_args["_mcp_data"] = build_mcp_data(tool_manager)

        # Provide URL hints for filename/file_names fields
        files_ctx = session_context.get("files", {})

        # Check if base64 content injection is enabled
        include_base64 = False
        try:
            from atlas.modules.config import config_manager
            settings = config_manager.app_settings
            include_base64 = getattr(settings, "include_file_content_base64", False)
        except Exception as e:
            logger.debug(f"Could not check include_file_content_base64 setting: {e}")

        def to_url(key: str) -> str:
            # Use tokenized URL so tools can fetch without cookies
            return create_download_url(key, user_email)

        async def get_file_base64(key: str) -> Optional[str]:
            """Fetch base64 content for a file key."""
            try:
                # Get file manager from session context or use global
                file_manager = session_context.get("file_manager")
                if not file_manager:
                    from atlas.infrastructure.app_factory import get_file_storage
                    file_manager = get_file_storage()

                if file_manager and user_email:
                    file_data = await file_manager.get_file(user_email, key)
                    return file_data.get("content_base64") if file_data else None
            except Exception as e:
                logger.warning(f"Failed to fetch base64 content for file key {key}: {e}")
            return None

        # Handle single filename
        if "filename" in parsed_args and isinstance(parsed_args["filename"], str):
            fname = parsed_args["filename"]
            ref = files_ctx.get(fname)
            if ref and ref.get("key"):
                url = to_url(ref["key"])
                # SECURITY: tokenized URLs can contain secrets; do not log them.
                logger.debug(
                    "Rewriting filename argument to tokenized URL (filename=%s)",
                    _sanitize_filename_value(fname),
                )
                parsed_args.setdefault("original_filename", fname)
                parsed_args["filename"] = url
                parsed_args.setdefault("file_url", url)

                # Optionally inject base64 content as fallback
                if include_base64:
                    # Note: We can't make this function async, so we mark this for future enhancement
                    # For now, just log that this feature requires additional integration
                    logger.debug(
                        "Base64 content injection requested but requires async context (filename=%s)",
                        _sanitize_filename_value(fname),
                    )
                    # TODO: Implement async context support for base64 injection
                    # For now, tools should use the URL-based approach

        # Handle multiple filenames
        if "file_names" in parsed_args and isinstance(parsed_args["file_names"], list):
            urls = []
            originals = []
            for fname in parsed_args["file_names"]:
                if not isinstance(fname, str):
                    continue
                originals.append(fname)
                ref = files_ctx.get(fname)
                if ref and ref.get("key"):
                    urls.append(to_url(ref["key"]))
                else:
                    urls.append(fname)
            if urls:
                logger.debug("Rewriting file_names arguments to tokenized URLs (count=%d)", len(urls))
                parsed_args.setdefault("original_file_names", originals)
                parsed_args["file_names"] = urls
                parsed_args.setdefault("file_urls", urls)

    except Exception as inj_err:
        logger.warning(f"Non-fatal: failed to inject tool args: {inj_err}")

    return parsed_args


async def handle_synthesis_decision(
    llm_response: LLMResponse,
    messages: List[Dict[str, Any]],
    model: str,
    session_context: Dict[str, Any],
    llm_caller,
    prompt_provider,
    update_callback: Optional[UpdateCallback] = None,
    user_email: Optional[str] = None,
) -> str:
    """
    Decide whether synthesis is needed and execute accordingly.

    Pure function that doesn't maintain state.
    """
    # Check if we have only canvas tools
    canvas_tool_calls = [tc for tc in llm_response.tool_calls if tc.function.name == "canvas_canvas"]
    has_only_canvas_tools = len(canvas_tool_calls) == len(llm_response.tool_calls)

    if has_only_canvas_tools:
        # Canvas tools don't need follow-up
        return llm_response.content or "Content displayed in canvas."

    # Add updated files manifest before synthesis
    files_manifest = build_files_manifest(session_context)
    if files_manifest:
        updated_manifest = {
            "role": "system",
            "content": (
                "Available session files (updated after tool runs):\n"
                f"{files_manifest['content'].split('Available session files:')[1].split('(You can ask')[0].strip()}\n\n"
                "(You can ask to open or analyze any of these by name.)"
            )
        }
        messages.append(updated_manifest)

    # Notify frontend that tool synthesis is starting
    if update_callback:
        try:
            await update_callback({"type": "tool_synthesis_start"})
        except Exception:
            logger.debug("Failed to send tool_synthesis_start notification")

    # Get final synthesis
    return await synthesize_tool_results(
        model=model,
        messages=messages,
        llm_caller=llm_caller,
        prompt_provider=prompt_provider,
        update_callback=update_callback,
        user_email=user_email,
    )


async def synthesize_tool_results(
    model: str,
    messages: List[Dict[str, Any]],
    llm_caller,
    prompt_provider,
    update_callback: Optional[UpdateCallback] = None,
    user_email: Optional[str] = None,
) -> str:
    """
    Prepare augmented messages with synthesis prompt and obtain final answer.

    Pure function that coordinates LLM call for synthesis.
    """
    # Extract latest user question (walk backwards)
    user_question = ""
    for m in reversed(messages):
        if m.get("role") == "user" and m.get("content"):
            user_question = m["content"]
            break

    prompt_text = None
    if prompt_provider:
        prompt_text = prompt_provider.get_tool_synthesis_prompt(user_question or "the user's last request")

    synthesis_messages = list(messages)
    if prompt_text:
        synthesis_messages.append({
            "role": "system",
            "content": prompt_text
        })
    else:
        logger.info("Proceeding without dedicated tool synthesis prompt (fallback)")

    final_response = await llm_caller.call_plain(model, synthesis_messages, user_email=user_email)

    # Do not emit a separate 'tool_synthesis' assistant-visible event here.
    # The chat service will emit a single 'chat_response' for the final answer
    # to avoid duplicate assistant messages in the UI.

    return final_response


def build_files_manifest(session_context: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    Build ephemeral files manifest for LLM context.

    Pure function that creates manifest from session context.
    """
    files_ctx = session_context.get("files", {})
    if not files_ctx:
        return None

    file_list = "\n".join(f"- {name}" for name in sorted(files_ctx.keys()))
    return {
        "role": "system",
        "content": (
            "Available session files:\n"
            f"{file_list}\n\n"
            "(You can ask to open or analyze any of these by name. "
            "Large contents are not fully in this prompt unless user or tools provided excerpts.)"
        )
    }
