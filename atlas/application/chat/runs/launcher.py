"""The ``atlas_launch`` built-in tool: a conversation starting conversations (#925).

ATLAS already had the two hard halves of subagents. Issue #884 gave it a run
registry that owns several concurrent runs on one connection, and #915 made
run-scoped event tagging a single authority, so a run's output routes back to
its own conversation instead of whichever one is on screen. What was missing
was a way for the *model* to start one. This module is that way.

The contract, decided on the issue:

* **Three inputs.** ``workspace``, ``model``, ``prompt``. The workspace is the
  child's whole capability surface -- its tools *and* its data sources -- so
  the thing that decides what a sub-conversation may touch is something the
  user already configures and can audit, not a list the model assembles.
* **A handle, not an answer.** ``atlas_launch`` returns the child's ``run_id``
  and ``conversation_id`` as soon as the run is admitted, and never blocks.
  Blocking would pin the parent's agent loop (and its model context) open for
  the length of the child, which is exactly the cost fanning out is meant to
  avoid. The child's transcript is its own conversation in history; that is
  where the user reads the result.
* **No privilege widening.** The child runs as the caller, under a workspace
  the caller owns, on a model the caller may use, with the workspace's tools
  re-filtered through the caller's ACLs at launch time. A workspace saved
  before an ACL change cannot resurrect a tool the user has since lost.
* **Bounded.** Depth and per-run child count are capped here; the per-user
  concurrency cap is the registry's and still applies underneath.

Cancellation lives in the registry: ``cancel`` cascades to children, so
stopping a parent stops what it launched.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from atlas.application.chat.runs.context import set_current_run, tag_event
from atlas.application.chat.runs.registry import (
    ConcurrencyLimitError,
    ConversationBusyError,
    RunRegistryError,
    RunStatus,
    get_run_registry,
)
from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.model_access import ModelAccessDecision, check_model_access
from atlas.domain.messages.models import ToolResult
from atlas.modules.mcp_tools.atlas_server import launch_tool_enabled

__all__ = [
    "LaunchRefused",
    "execute_launch_tool",
    "launch_sub_conversation",
    "launch_tool_enabled",
]

logger = logging.getLogger(__name__)

# A prompt long enough to be a document is not a task description; the child
# has its own context window and its own history to fill.
MAX_PROMPT_CHARS = 20000

# Agent steps a launched run is allowed. Deliberately the same order as the
# default interactive budget: a sub-conversation is a peer of a normal agent
# turn, not a longer-running species of one.
LAUNCH_AGENT_MAX_STEPS = 10


class LaunchRefused(Exception):
    """A launch that cannot proceed, with a message meant for the model.

    Raised for every refusal -- unknown workspace, unusable model, depth or
    concurrency cap -- because the model's next move depends on knowing which,
    and the text is written to be actionable rather than apologetic.
    """


def _require_text(arguments: Dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LaunchRefused(f"'{key}' is required and must be a non-empty string.")
    return value.strip()


def resolve_workspace(repository: Any, workspace: str, user_email: str) -> Dict[str, Any]:
    """Find the caller's workspace by id or by name.

    The lookup is scoped to ``user_email`` at the repository, so a model that
    guesses another user's workspace id gets "not found" rather than that
    user's tool and data-source selections. Names are matched case-insensitively
    because the model is repeating a name a human typed, not an identifier.
    """
    found = None
    try:
        found = repository.get_workspace(workspace, user_email)
    except Exception:  # pragma: no cover - defensive: a bad id must not 500
        logger.debug("Workspace lookup by id failed", exc_info=True)
    if found:
        return found

    try:
        available = repository.list_workspaces(user_email) or []
    except Exception:  # pragma: no cover - defensive
        logger.warning("Workspace listing failed during atlas_launch", exc_info=True)
        available = []

    wanted = workspace.casefold()
    for candidate in available:
        if str(candidate.get("name", "")).casefold() == wanted:
            return candidate

    names = ", ".join(sorted(str(c.get("name", "")) for c in available if c.get("name")))
    raise LaunchRefused(
        f"No workspace named '{workspace}'. "
        + (f"Available workspaces: {names}." if names else "You have no saved workspaces yet.")
    )


async def resolve_model(config_manager: Any, model: str, user_email: str) -> str:
    """Validate the requested model against what this user may use.

    ``UNKNOWN`` and ``DENIED`` deliberately produce the same message: telling
    the model that a name it guessed exists but is off-limits would make
    ``atlas_launch`` a probe for the deployment's restricted model list.
    """
    models = getattr(getattr(config_manager, "llm_config", None), "models", None) or {}
    decision = await check_model_access(models, model, user_email, context="atlas_launch")
    if decision is ModelAccessDecision.ALLOWED:
        return model
    raise LaunchRefused(
        f"Model '{model}' is not available to you. Choose one of the models "
        "offered in this conversation."
    )


async def resolve_child_tools(
    chat_service: Any,
    workspace_config: Dict[str, Any],
    user_email: str,
) -> List[str]:
    """The workspace's tools, re-checked against the caller's access *now*.

    A workspace is a saved bookmark, and the ACLs behind it move. Filtering
    here rather than trusting the stored list is what makes "the child cannot
    exceed the caller's own access" true for a workspace saved last month.
    """
    selected = workspace_config.get("selected_tools") or []
    authorization = getattr(chat_service, "tool_authorization", None)
    if authorization is None:
        raise LaunchRefused(
            "Sub-conversations are unavailable: tool authorization is not configured."
        )
    authorized = await authorization.filter_authorized_tools(list(selected), user_email)
    if not authorized:
        raise LaunchRefused(
            "That workspace has no tools you are currently authorized to use, so "
            "a sub-conversation started under it would have nothing to work with. "
            "Pick a workspace with tools, or do the work in this conversation."
        )
    return authorized


def _parent_run_identity(registry: Any, run_context: Any) -> Tuple[Optional[str], int]:
    """The launching run's id and depth, or ``(None, 0)`` when untracked.

    An untracked turn (history off for this turn, or a non-agent turn that
    still reached a tool) is treated as depth 0 rather than refused: it is the
    root of a launch tree exactly as a tracked run would be. It simply has no
    parent record to cascade a cancel from, which the caller is told about in
    the returned handle.
    """
    if run_context is None:
        return None, 0
    record = registry.get(run_context.run_id)
    if record is None:
        # A run id the registry has never seen (or has already reaped) is not a
        # parent: hanging children off it would make ``children_of`` answer
        # "none" forever, so the fan-out cap would never bite and a stop on the
        # parent would cascade to nothing. Treat it as untracked, which is the
        # branch that counts the user's launched runs instead.
        return None, 0
    return record.run_id, record.depth


def _check_limits(
    registry: Any,
    app_settings: Any,
    parent_run_id: Optional[str],
    parent_depth: int,
    user_email: str,
) -> None:
    max_depth = max(1, int(getattr(app_settings, "atlas_launch_max_depth", 2) or 2))
    if parent_depth + 1 > max_depth:
        raise LaunchRefused(
            f"Sub-conversations may not nest more than {max_depth} deep, and this "
            "conversation is already at the limit. Do the work here instead."
        )

    max_children = max(
        1, int(getattr(app_settings, "atlas_launch_max_children_per_run", 3) or 3)
    )
    if parent_run_id:
        in_flight = len(registry.children_of(parent_run_id))
    else:
        # An untracked turn has no parent record to hang children off, so
        # "children of this run" cannot be counted. Fall back to every launched
        # run the user has in flight: without it the per-run cap would be
        # vacuous for exactly the turns that are not otherwise bounded.
        in_flight = len(
            [
                r
                for r in registry.active_for_user(user_email)
                if r.parent_run_id is None and r.depth > 0
            ]
        )
    if in_flight >= max_children:
        raise LaunchRefused(
            f"There are already {in_flight} sub-conversations running for you "
            f"(the limit is {max_children} at a time). Wait for one to finish "
            "before launching another."
        )


def _raw_transport(update_callback: Any) -> Any:
    """Unwrap the turn's callback down to the socket send.

    In agent mode the callback handed to a tool is the parent turn's
    ``ToolCallRecorder``, which persists every ``tool_start`` / ``tool_complete``
    it sees into *that turn's* conversation history. A child's tool rows belong
    in the child's transcript, so the child sends past the recorder rather than
    through it. The recorder also drops foreign ``run_id``s as a second line of
    defence; this is the first.
    """
    from atlas.application.chat.utilities.tool_history import ToolCallRecorder

    # Unwrap the recorder specifically, not "anything with an .inner": another
    # wrapper in the chain may be doing work the child's frames still need.
    while isinstance(update_callback, ToolCallRecorder):
        update_callback = update_callback.inner
    return update_callback


class _ChildConnection:
    """Routes a child run's events back over the parent's socket, tagged as the child's.

    The child gets no socket of its own -- it is started from inside a tool
    call, not from a client message -- so its frames travel out over the
    connection that is already there. Every frame is stamped with the *child's*
    run and conversation ids first, and the tagging rule is setdefault-based
    (see ``tag_event``), so nothing downstream can re-label it as the parent's.
    That stamp is the whole reason a sub-conversation's tokens do not splice
    themselves into the transcript the user is reading.
    """

    def __init__(self, update_callback, run_id: str, conversation_id: str):
        self._update_callback = _raw_transport(update_callback)
        self._run_id = run_id
        self._conversation_id = conversation_id

    def bind_run(self, run_id: str) -> None:
        """Attach the run id, which only exists once the registry admits it."""
        self._run_id = run_id

    async def send_json(self, data: Dict[str, Any]) -> None:
        try:
            tagged = tag_event(data, self._run_id, self._conversation_id)
            if isinstance(tagged, dict):
                event_type = tagged.get("type")
                registry = get_run_registry()
                record = registry.get(self._run_id)
                if event_type in {"tool_approval_request", "elicitation_request"}:
                    registry.set_status(
                        self._run_id,
                        RunStatus.WAITING_FOR_INPUT,
                        waiting_on=event_type,
                    )
                    registry.set_pending_request(self._run_id, tagged)
                elif event_type in {"tool_complete", "tool_error", "tool_interrupted", "tool_result"}:
                    if record is not None and record.status is RunStatus.WAITING_FOR_INPUT:
                        registry.set_status(self._run_id, RunStatus.RUNNING)
            if self._update_callback is not None:
                await self._update_callback(tagged)
        except Exception:
            # A dead socket must not kill the run: the child's transcript is
            # persisted either way, and that is where the user reads it.
            logger.debug("Dropping child run frame; transport unavailable", exc_info=True)

    async def receive_json(self) -> Dict[str, Any]:  # pragma: no cover - never read from
        raise NotImplementedError("A launched run does not read from the connection.")

    async def accept(self) -> None:  # pragma: no cover - no-op
        return None

    async def close(self) -> None:  # pragma: no cover - no-op
        return None


def _preapprove_child_tools(chat_service: Any) -> bool:
    """Mark the child's agent loop as already approved. Returns whether it took."""
    factory = getattr(getattr(chat_service, "agent_mode", None), "agent_loop_factory", None)
    if factory is None:
        return False
    factory.skip_approval = True
    return True


def _admin_gated_tools(tools: List[str], config_manager: Any) -> List[str]:
    """Which of the child's tools an admin has made non-auto-approvable."""
    from atlas.application.chat.utilities.tool_executor import requires_approval

    gated: List[str] = []
    for tool in tools:
        try:
            _needs, _edit, admin_required = requires_approval(tool, config_manager)
        except Exception:  # pragma: no cover - defensive: unknown tool name
            admin_required = True
        if admin_required:
            gated.append(tool)
    return gated


async def resolve_child_data_sources(
    factory: Any,
    selected: List[str],
    user_email: str,
    compliance_level: Any,
) -> List[str]:
    unified_rag = getattr(factory, "get_unified_rag_service", lambda: None)()
    rag_mcp = getattr(factory, "get_rag_mcp_service", lambda: None)()
    if unified_rag is None and rag_mcp is None:
        return selected
    authorized: set[str] = set()
    if unified_rag is not None:
        for server in await unified_rag.discover_data_sources(
            user_email, user_compliance_level=compliance_level
        ):
            server_name = server.get("server", "")
            authorized.update(
                f"{server_name}:{source.get('id', '')}"
                for source in server.get("sources", [])
                if server_name and source.get("id")
            )
    if rag_mcp is not None:
        for server in await rag_mcp.discover_servers(
            user_email, user_compliance_level=compliance_level
        ):
            server_name = server.get("server", "")
            authorized.update(
                f"{server_name}:{source.get('id', '')}"
                for source in server.get("sources", [])
                if server_name and source.get("id")
            )
    return [source for source in selected if source in authorized]


async def _run_child(
    *,
    chat_service: Any,
    record: Any,
    model: str,
    prompt: str,
    tools: List[str],
    data_sources: List[str],
    workspace_id: Optional[str],
    user_email: str,
    compliance_level: Any,
    registry: Any,
) -> None:
    """The child run's task body: bind identity, chat, record the outcome."""
    set_current_run(record.run_id, record.conversation_id)
    outcome = RunStatus.COMPLETED
    error: Optional[str] = None
    try:
        response = await chat_service.handle_chat_message(
            session_id=record.session_id,
            content=prompt,
            model=model,
            selected_tools=tools,
            selected_data_sources=data_sources,
            user_email=user_email,
            agent_mode=True,
            agent_max_steps=LAUNCH_AGENT_MAX_STEPS,
            update_callback=chat_service.connection.send_json,
            conversation_id=record.conversation_id,
            workspace_id=workspace_id,
            # The parent turn's compliance level travels with the child, so a
            # sub-conversation is never *less* restricted than the turn that
            # asked for it. It is server-side state, not a tool argument, so
            # the model cannot raise or drop it by choosing what to pass.
            compliance_level=compliance_level,
            incognito=False,
        )
        if isinstance(response, dict) and response.get("type") == "error":
            outcome = RunStatus.FAILED
            error = str(response.get("message") or "Sub-conversation turn failed.")
    except asyncio.CancelledError:
        outcome = RunStatus.CANCELLED
        raise
    except Exception as e:
        outcome = RunStatus.FAILED
        error = str(e)
        logger.warning(
            "Launched run %s failed: %s", record.run_id, sanitize_for_logging(str(e))
        )
    finally:
        # In a ``finally`` for the same reason the transport's own run wrapper
        # uses one: a run left non-terminal holds a slot against the user's
        # concurrency cap forever, and its conversation's one-run lock with it.
        registry.set_status(record.run_id, outcome, error=error)


async def launch_sub_conversation(
    arguments: Dict[str, Any],
    context: Optional[Dict[str, Any]],
    *,
    factory: Any = None,
) -> Dict[str, Any]:
    """Admit and start a sub-conversation; return its handle.

    Raises :class:`LaunchRefused` with a model-readable message for every
    refusal. Returns as soon as the run is admitted and its task is scheduled --
    the child's work happens afterwards, in its own task.
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
        # Every authorization decision below keys off the caller's identity;
        # without one there is nothing to scope the child to, so this fails
        # closed rather than launching an unattributed run.
        raise LaunchRefused("Sub-conversations require an authenticated user.")

    if (context or {}).get("incognito"):
        # A launched run is a background run: its whole point is a transcript
        # the user can reopen. Starting one from an incognito or local-save
        # turn would write the prompt and the child's entire transcript to
        # durable history, which is the one thing that turn asked not to
        # happen. Refuse rather than quietly persisting it.
        raise LaunchRefused(
            "Sub-conversations cannot be started from an incognito or "
            "local-save conversation, because the sub-conversation's "
            "transcript has to be saved on the server to be readable."
        )

    workspace_name = _require_text(arguments, "workspace")
    model_name = _require_text(arguments, "model")
    prompt = _require_text(arguments, "prompt")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise LaunchRefused(
            f"'prompt' is too long ({len(prompt)} characters); the limit is "
            f"{MAX_PROMPT_CHARS}. Summarize the task for the sub-conversation."
        )

    repository = getattr(factory, "workspace_repository", None)
    if repository is None:
        raise LaunchRefused(
            "Sub-conversations are unavailable: workspaces are not configured."
        )

    workspace = resolve_workspace(repository, workspace_name, user_email)
    await resolve_model(config_manager, model_name, user_email)

    registry = get_run_registry()
    from atlas.application.chat.runs.context import get_current_run

    parent_run_id, parent_depth = _parent_run_identity(registry, get_current_run())

    workspace_config = workspace.get("config") or {}
    conversation_id = str(uuid4())
    connection = _ChildConnection(
        (context or {}).get("update_callback"), "pending", conversation_id
    )
    chat_service = factory.create_chat_service(connection=connection)
    tools = await resolve_child_tools(chat_service, workspace_config, user_email)
    # Who answers the child's tool approvals? Nobody, unless somebody is
    # looking at the child's conversation -- and it is not in the user's
    # history until its first save, so a launched run would pause on its first
    # tool call with no way to answer. The approval that covers the child is
    # the one the user already gave (or auto-gave) for *this* ``atlas_launch``
    # call, which named the workspace, the model and the task. Tools an admin
    # has pinned to mandatory approval are exempt: those keep prompting inside
    # the child, which pauses it until the user opens that conversation.
    admin_gated = _admin_gated_tools(tools, config_manager)
    if not admin_gated and not _preapprove_child_tools(chat_service):
        logger.warning("Could not pre-approve tools for launched run")
    # RAG selections only travel when the workspace has RAG switched on; a
    # workspace that keeps a source list but has retrieval off means "not now".
    data_sources = (
        list(workspace_config.get("selected_data_sources") or [])
        if workspace_config.get("rag_enabled")
        else []
    )
    data_sources = await resolve_child_data_sources(
        factory,
        data_sources,
        user_email,
        (context or {}).get("compliance_level"),
    )

    # Admission is check-then-insert, and it must be atomic: several
    # ``atlas_launch`` calls in one agent step run concurrently, and a check
    # separated from the insert by an await would let all of them pass a cap of
    # one. Everything from here to ``start`` is synchronous, which on a single
    # event loop is exactly that atomicity -- so no await may be introduced
    # between these two lines.
    _check_limits(registry, app_settings, parent_run_id, parent_depth, user_email)
    try:
        record = registry.start(
            conversation_id=conversation_id,
            user_email=user_email,
            parent_run_id=parent_run_id,
            depth=parent_depth + 1,
        )
    except (ConcurrencyLimitError, ConversationBusyError) as e:
        raise LaunchRefused(str(e)) from e
    except RunRegistryError as e:  # pragma: no cover - defensive
        raise LaunchRefused(f"The sub-conversation could not be started: {e}") from e

    connection.bind_run(record.run_id)

    task = asyncio.create_task(
        _run_child(
            chat_service=chat_service,
            record=record,
            model=model_name,
            prompt=prompt,
            tools=tools,
            data_sources=data_sources,
            workspace_id=workspace.get("id"),
            user_email=user_email,
            compliance_level=(context or {}).get("compliance_level"),
            registry=registry,
        )
    )
    registry.attach_task(record.run_id, task)

    logger.info(
        "atlas_launch started run %s (conversation %s, depth %d, parent %s) for %s",
        record.run_id,
        sanitize_for_logging(conversation_id),
        record.depth,
        sanitize_for_logging(str(parent_run_id)),
        sanitize_for_logging(user_email),
    )

    return {
        "run_id": record.run_id,
        "conversation_id": conversation_id,
        "status": record.status.value,
        "workspace": workspace.get("name"),
        "model": model_name,
        "tools": tools,
        "data_sources": data_sources,
        "depth": record.depth,
        "parent_run_id": parent_run_id,
        # Non-empty means the sub-conversation will stop and wait for the user
        # on those tools; the model should say so rather than promise a result.
        "tools_needing_approval": admin_gated,
    }


def _scoped_children(context: Optional[Dict[str, Any]]) -> List[Any]:
    from atlas.application.chat.runs.context import get_current_run

    user_email = (context or {}).get("user_email")
    current = get_current_run()
    if not user_email or current is None:
        return []
    registry = get_run_registry()
    return [
        record
        for record in registry.children_of(current.run_id, include_terminal=True)
        if record.user_email == user_email
    ]


def get_child_runs(context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [record.to_public_dict() for record in _scoped_children(context)]


def get_child_result(run_id: Any, context: Optional[Dict[str, Any]], factory: Any = None) -> Dict[str, Any]:
    if not isinstance(run_id, str) or not run_id.strip():
        raise LaunchRefused("'run_id' is required and must be a non-empty string.")
    children = {record.run_id: record for record in _scoped_children(context)}
    record = children.get(run_id)
    if record is None:
        raise LaunchRefused("That run is not a sub-conversation of this conversation.")

    result: Dict[str, Any] = {
        "run_id": record.run_id,
        "conversation_id": record.conversation_id,
        "status": record.status.value,
        "error": record.error,
        "waiting_on": record.waiting_on,
        "result": None,
    }
    if not record.is_terminal or record.status is not RunStatus.COMPLETED:
        return result

    if factory is None:
        from atlas.infrastructure.app_factory import app_factory as factory
    repository = getattr(factory, "conversation_repository", None)
    if repository is None:
        return result
    conversation = repository.get_conversation(record.conversation_id, record.user_email)
    if conversation:
        for message in reversed(conversation.get("messages", [])):
            if message.get("role") == "assistant" and message.get("content"):
                result["result"] = message["content"]
                break
    return result


async def execute_observation_tool(tool_call: Any, context: Optional[Dict[str, Any]]) -> ToolResult:
    name = getattr(tool_call, "function", None)
    name = getattr(name, "name", None) or getattr(tool_call, "name", "")
    arguments = getattr(tool_call, "arguments", None) or {}
    if not isinstance(arguments, dict):
        arguments = {}
    try:
        if name == "atlas_get_runs":
            payload = {"runs": get_child_runs(context)}
        else:
            payload = get_child_result(arguments.get("run_id"), context)
    except LaunchRefused as error:
        return ToolResult(
            tool_call_id=getattr(tool_call, "id", None),
            content=str(error),
            success=False,
            error=str(error),
        )
    return ToolResult(
        tool_call_id=getattr(tool_call, "id", None),
        content=json.dumps(payload),
        success=True,
    )


async def execute_launch_tool(tool_call: Any, context: Optional[Dict[str, Any]]) -> ToolResult:
    """``atlas_launch`` as the tool manager calls it.

    A refusal comes back as an unsuccessful ``ToolResult`` rather than an
    exception: the model is mid-loop and can act on "no workspace by that
    name" -- by naming a real one -- where a raised error would just end the
    turn.
    """
    arguments = getattr(tool_call, "arguments", None) or {}
    if not isinstance(arguments, dict):
        arguments = {}
    try:
        handle = await launch_sub_conversation(arguments, context)
    except LaunchRefused as e:
        return ToolResult(
            tool_call_id=getattr(tool_call, "id", None),
            content=str(e),
            success=False,
            error=str(e),
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.error("atlas_launch failed unexpectedly: %s", e, exc_info=True)
        message = "The sub-conversation could not be started due to an internal error."
        return ToolResult(
            tool_call_id=getattr(tool_call, "id", None),
            content=message,
            success=False,
            error=message,
        )

    summary = (
        f"Started sub-conversation {handle['conversation_id']} "
        f"(run {handle['run_id']}) on model {handle['model']} under workspace "
        f"'{handle['workspace']}'. It is running now; this call did not wait "
        "for it. Its transcript is a separate conversation in the user's "
        "history.\n"
        + (
            "It will pause for approval on these tools until the user opens "
            f"that conversation: {', '.join(handle['tools_needing_approval'])}.\n"
            if handle["tools_needing_approval"]
            else ""
        )
        + json.dumps(handle)
    )
    return ToolResult(
        tool_call_id=getattr(tool_call, "id", None),
        content=summary,
        success=True,
    )
