"""
Tool execution utilities - pure functions for tool operations.

This module provides stateless utility functions for handling tool execution,
argument processing, and synthesis decisions without maintaining any state.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Callable, Awaitable

from domain.messages.models import ToolCall, ToolResult, Message, MessageRole
from interfaces.llm import LLMResponse
from core.capabilities import create_download_url
from .notification_utils import _sanitize_filename_value  # reuse same filename sanitizer for UI args

logger = logging.getLogger(__name__)

# Type hint for update callback
UpdateCallback = Callable[[Dict[str, Any]], Awaitable[None]]


async def execute_tools_workflow(
    llm_response: LLMResponse,
    messages: List[Dict],
    model: str,
    session_context: Dict[str, Any],
    tool_manager,
    llm_caller,
    prompt_provider,
    update_callback: Optional[UpdateCallback] = None
) -> tuple[str, List[ToolResult]]:
    """
    Execute the complete tools workflow: calls -> results -> synthesis.
    
    Pure function that coordinates tool execution without maintaining state.
    """
    # Add assistant message with tool calls
    messages.append({
        "role": "assistant",
        "content": llm_response.content,
        "tool_calls": llm_response.tool_calls
    })

    # Execute all tool calls
    tool_results: List[ToolResult] = []
    for tool_call in llm_response.tool_calls:
        result = await execute_single_tool(
            tool_call=tool_call,
            session_context=session_context,
            tool_manager=tool_manager,
            update_callback=update_callback
        )
        tool_results.append(result)

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
        update_callback=update_callback
    )

    return final_response, tool_results


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
    update_callback: Optional[UpdateCallback] = None
) -> ToolResult:
    """
    Execute a single tool with argument preparation and error handling.
    
    Pure function that doesn't maintain state - all context passed as parameters.
    """
    from . import notification_utils
    
    try:
        # Prepare arguments with injections (username, filename URL mapping)
        parsed_args = prepare_tool_arguments(tool_call, session_context, tool_manager)

        # Filter to only schema-declared parameters so MCP tools don't receive extras
        filtered_args = _filter_args_to_schema(parsed_args, tool_call.function.name, tool_manager)

        # Sanitize arguments for UI (hide tokens in URLs, etc.)
        display_args = _sanitize_args_for_ui(dict(filtered_args))

        # Send tool start notification with sanitized args
        await notification_utils.notify_tool_start(tool_call, display_args, update_callback)

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

        # Send tool complete notification
        await notification_utils.notify_tool_complete(tool_call, result, parsed_args, update_callback)

        return result

    except Exception as e:
        logger.error(f"Error executing tool {tool_call.function.name}: {e}")
        
        # Send tool error notification
        await notification_utils.notify_tool_error(tool_call, str(e), update_callback)
        
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
        allowed: set[str] = set()
        for tool_schema in tools_schema or []:
            if tool_schema.get("function", {}).get("name") == tool_name:
                params = tool_schema.get("function", {}).get("parameters", {})
                props = params.get("properties", {}) or {}
                allowed = set(props.keys())
                break

        if allowed:
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
    """
    if not isinstance(parsed_args, dict):
        return parsed_args

    try:
        # Inject username. Prefer schema-aware injection; if schema unavailable,
        # include username by default to support tools that expect it.
        user_email = session_context.get("user_email")
        if user_email and (not tool_manager or tool_accepts_username(tool_name, tool_manager)):
            parsed_args["username"] = user_email

        # Provide URL hints for filename/file_names fields
        files_ctx = session_context.get("files", {})
        
        def to_url(key: str) -> str:
            # Use tokenized URL so tools can fetch without cookies
            return create_download_url(key, user_email)

        # Handle single filename
        if "filename" in parsed_args and isinstance(parsed_args["filename"], str):
            fname = parsed_args["filename"]
            ref = files_ctx.get(fname)
            if ref and ref.get("key"):
                url = to_url(ref["key"])
                parsed_args.setdefault("original_filename", fname)
                parsed_args["filename"] = url
                parsed_args.setdefault("file_url", url)

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
    update_callback: Optional[UpdateCallback] = None
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

    # Get final synthesis
    return await synthesize_tool_results(
        model=model,
        messages=messages,
        llm_caller=llm_caller,
        prompt_provider=prompt_provider,
        update_callback=update_callback
    )


async def synthesize_tool_results(
    model: str,
    messages: List[Dict[str, Any]],
    llm_caller,
    prompt_provider,
    update_callback: Optional[UpdateCallback] = None
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

    final_response = await llm_caller.call_plain(model, synthesis_messages)

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