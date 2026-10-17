"""The ``atlas_follow_up`` built-in tool: talking back to a sub-conversation.

``atlas_launch`` starts a sub-conversation and hands back a handle; the
observation tools (``atlas_get_runs``, ``atlas_result``) let the launching
conversation read what it said. This closes the loop: the launching
conversation can send the sub-conversation its next message -- a correction,
a clarifying question, the next step -- without starting over in a fresh child
that has forgotten everything.

A follow-up is a new run on the child's *existing* conversation id. The chat
service hydrates that conversation's stored history before the turn, so the
child sees its whole transcript, and the reply lands in the same conversation
in the user's history. Everything else is the launch path, shared through
``_start_child_run``: the same re-authorization, approval rule, limits and
atomic admission.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from atlas.application.chat.runs.launcher import (
    MAX_PROMPT_CHARS,
    LaunchRefused,
    _require_text,
    _scoped_children,
    _start_child_run,
    resolve_model,
)
from atlas.application.chat.runs.registry import RunStatus, get_run_registry
from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.domain.messages.models import ToolResult
from atlas.modules.mcp_tools.atlas_server import launch_tool_enabled

__all__ = ["execute_follow_up_tool", "follow_up_sub_conversation"]

logger = logging.getLogger(__name__)


async def follow_up_sub_conversation(
    arguments: Dict[str, Any],
    context: Optional[Dict[str, Any]],
    *,
    factory: Any = None,
) -> Dict[str, Any]:
    """Send the next user turn to a sub-conversation; return the new run's handle.

    The follow-up is a new run on the child's *existing* conversation id, so
    the chat service hydrates the child's stored history before the turn and
    the reply lands in the same transcript. It runs on the launch spec the
    child was started with -- the model never re-chooses the workspace or
    model -- but every part of that spec is re-checked against the caller's
    access now, exactly as a launch would be: a follow-up can only narrow
    what the sub-conversation may touch, never widen it.

    Like ``atlas_launch`` it returns as soon as the run is admitted.
    """
    if factory is None:
        from atlas.infrastructure.app_factory import app_factory as factory

    config_manager = factory.get_config_manager()
    app_settings = getattr(config_manager, "app_settings", None)
    if not launch_tool_enabled(app_settings):
        raise LaunchRefused(
            "Sub-conversations are disabled in this deployment "
            "(FEATURE_ATLAS_LAUNCH_ENABLED)."
        )

    user_email = (context or {}).get("user_email")
    if not user_email:
        raise LaunchRefused("Sub-conversations require an authenticated user.")

    if (context or {}).get("incognito"):
        # Same reason a launch is refused: the follow-up and its reply are
        # written to the sub-conversation's saved transcript.
        raise LaunchRefused(
            "Sub-conversations cannot be continued from an incognito or "
            "local-save conversation, because the sub-conversation's "
            "transcript has to be saved on the server."
        )

    run_id = _require_text(arguments, "run_id")
    message = _require_text(arguments, "message")
    if len(message) > MAX_PROMPT_CHARS:
        raise LaunchRefused(
            f"'message' is too long ({len(message)} characters); the limit is "
            f"{MAX_PROMPT_CHARS}. Summarize it for the sub-conversation."
        )

    # Scoped exactly like ``atlas_result``: only direct children of this
    # conversation, owned by this user. A guessed or foreign run id gets the
    # same answer as an unknown one.
    children = _scoped_children(context)
    addressed = next((record for record in children if record.run_id == run_id), None)
    if addressed is None:
        raise LaunchRefused(
            "That run is not a sub-conversation of this conversation, or it "
            "has expired (sub-conversations are kept for about 30 minutes "
            "after they finish). Use a current run_id from atlas_get_runs, or "
            "launch a new sub-conversation."
        )
    # Continue from the sub-conversation's *latest* run, whichever of its run
    # ids was passed: the latest carries the narrowest spec, and an older run
    # id must not resurrect a tool an earlier follow-up already dropped.
    target = max(
        (r for r in children if r.conversation_id == addressed.conversation_id),
        key=lambda r: r.created_at,
    )

    registry = get_run_registry()
    active = registry.active_for_conversation(target.conversation_id, user_email)
    if active is not None and active.status is RunStatus.WAITING_FOR_INPUT:
        # Polling would never end: the child is paused on the *user*, not on
        # its own work.
        raise LaunchRefused(
            f"Sub-conversation {active.conversation_id} is waiting for the user "
            f"({active.waiting_on or 'input'}) and cannot take a follow-up until "
            "they answer it in that conversation. Tell the user, rather than "
            "waiting for it."
        )
    if active is not None:
        raise LaunchRefused(
            f"Sub-conversation {target.conversation_id} is still working (run "
            f"{active.run_id}, status {active.status.value}). Wait for it with "
            "atlas_sleep and check atlas_result before sending a follow-up."
        )

    spec = target.launch_spec
    if not spec:
        raise LaunchRefused(
            "That sub-conversation cannot be continued: what it was launched "
            "with is no longer known. Launch a new one instead."
        )

    compliance_level = (context or {}).get("compliance_level")
    if compliance_level != spec.get("compliance_level"):
        # The child's conversation was recorded under the level it was
        # launched at, and the chat service refuses a turn at any other level.
        # Saying so here beats a run that fails on its first step.
        raise LaunchRefused(
            "This conversation's compliance level has changed since that "
            "sub-conversation was launched, so it cannot be continued from "
            "here. Launch a new sub-conversation instead."
        )

    model_name = spec["model"]
    await resolve_model(config_manager, model_name, user_email)

    record, new_spec, admin_gated = await _start_child_run(
        factory=factory,
        config_manager=config_manager,
        context=context,
        user_email=user_email,
        conversation_id=target.conversation_id,
        model=model_name,
        prompt=message,
        selected_tools=list(spec.get("tools") or []),
        data_sources=list(
            spec.get("requested_data_sources", spec.get("data_sources")) or []
        ),
        workspace_id=spec.get("workspace_id"),
        workspace_name=spec.get("workspace_name"),
        # The conversation keeps the name its launch prompt gave it.
        title=target.title,
    )

    logger.info(
        "atlas_follow_up started run %s on conversation %s (after run %s) for %s",
        record.run_id,
        sanitize_for_logging(record.conversation_id),
        sanitize_for_logging(target.run_id),
        sanitize_for_logging(user_email),
    )

    return {
        "run_id": record.run_id,
        "conversation_id": record.conversation_id,
        "previous_run_id": target.run_id,
        "status": record.status.value,
        "workspace": new_spec["workspace_name"],
        "model": model_name,
        "tools": new_spec["tools"],
        "data_sources": new_spec["data_sources"],
        # Sources the sub-conversation was launched with that this turn could
        # not authorize -- access lost, or discovery unavailable right now.
        # Each later follow-up retries them.
        "data_sources_unavailable": [
            source
            for source in new_spec["requested_data_sources"]
            if source not in new_spec["data_sources"]
        ],
        "depth": record.depth,
        "parent_run_id": record.parent_run_id,
        "tools_needing_approval": admin_gated,
    }


async def execute_follow_up_tool(
    tool_call: Any, context: Optional[Dict[str, Any]]
) -> ToolResult:
    """``atlas_follow_up`` as the tool manager calls it.

    Refusals come back as unsuccessful results, as for ``atlas_launch``: "still
    working" or "not one of yours" is something the model can act on.
    """
    arguments = getattr(tool_call, "arguments", None) or {}
    if not isinstance(arguments, dict):
        arguments = {}
    try:
        handle = await follow_up_sub_conversation(
            arguments, context, factory=(context or {}).get("factory")
        )
    except LaunchRefused as e:
        return ToolResult(
            tool_call_id=getattr(tool_call, "id", None),
            content=str(e),
            success=False,
            error=str(e),
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.error("atlas_follow_up failed unexpectedly: %s", e, exc_info=True)
        message = "The follow-up could not be sent due to an internal error."
        return ToolResult(
            tool_call_id=getattr(tool_call, "id", None),
            content=message,
            success=False,
            error=message,
        )

    summary = (
        f"Sent the follow-up to sub-conversation {handle['conversation_id']}; "
        f"it is answering as run {handle['run_id']}. This call did not wait "
        "for the reply -- read it with atlas_result using that run_id.\n"
        + (
            "It will pause for approval on these tools until the user opens "
            f"that conversation: {', '.join(handle['tools_needing_approval'])}.\n"
            if handle["tools_needing_approval"]
            else ""
        )
        + (
            "These data sources could not be authorized for this turn: "
            f"{', '.join(handle['data_sources_unavailable'])}.\n"
            if handle["data_sources_unavailable"]
            else ""
        )
        + json.dumps(handle)
    )
    return ToolResult(
        tool_call_id=getattr(tool_call, "id", None),
        content=summary,
        success=True,
    )
