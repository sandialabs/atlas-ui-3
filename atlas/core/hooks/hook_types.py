"""Hook points, events, and the ``HookResult`` return-value contract.

This module defines the *vocabulary* of the plugin system: where hooks fire
(:class:`HookPoint`), what a plugin receives (the ``*Event`` dataclasses), and
what it may return (:class:`HookResult`).

Design rules that the rest of the system depends on:

- Every event carries an immutable **context** (session, user, compliance level)
  plus a small set of **mutable fields** declared in ``MUTABLE_FIELDS``. A
  ``MODIFY`` result is a structured patch restricted to those fields, so a
  plugin can never rewrite the trusted context.
- Events may impose additional *tighten-only* invariants by overriding
  :meth:`HookEvent.validate_patch`. A patch that would widen an authorization or
  compliance boundary is rejected and treated as a plugin error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional, Tuple


class HookPoint(str, Enum):
    """Lifecycle points a plugin can subscribe to."""

    SESSION_START = "session_start"
    USER_PROMPT_SUBMIT = "user_prompt_submit"
    PRE_LLM_CALL = "pre_llm_call"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    PERMISSION_REQUEST = "permission_request"
    RAG_CALL = "rag_call"
    RAG_RESPONSE = "rag_response"


# Per-hook-point default for what happens when a plugin raises or times out.
#
# Hooks that can weaken a security boundary fail CLOSED: the turn/tool/retrieval
# is denied rather than allowed to proceed unchecked. Annotation-oriented hooks
# fail OPEN so an observability plugin bug cannot brick a deployment. Individual
# registrations may override this with ``fail_open=``.
DEFAULT_FAIL_OPEN: Dict[HookPoint, bool] = {
    HookPoint.SESSION_START: True,
    HookPoint.USER_PROMPT_SUBMIT: False,
    HookPoint.PRE_LLM_CALL: False,
    HookPoint.PRE_TOOL_USE: False,
    HookPoint.POST_TOOL_USE: True,
    HookPoint.PERMISSION_REQUEST: False,
    HookPoint.RAG_CALL: False,
    HookPoint.RAG_RESPONSE: False,
}


#: Shown to the end user when a plugin denies without supplying its own message.
#: Deliberately generic -- ``reason`` is operator-facing and may name handlers,
#: internal rule identifiers, or fragments of the offending input.
DEFAULT_DENY_USER_MESSAGE = "This request was blocked by policy."


class HookDecision(str, Enum):
    """The decision a plugin (or a whole hook chain) returned."""

    CONTINUE = "continue"
    MODIFY = "modify"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class HookResult:
    """What a plugin returns from a hook.

    Returning ``None`` from a plugin is equivalent to :meth:`continue_`.
    """

    decision: HookDecision = HookDecision.CONTINUE
    #: For MODIFY: a ``{field: value}`` patch over the event's ``MUTABLE_FIELDS``.
    payload: Optional[Dict[str, Any]] = None
    #: Operator-facing explanation, recorded in logs/spans.
    reason: Optional[str] = None
    #: End-user-facing text surfaced in the chat when a turn/tool is denied.
    user_message: Optional[str] = None
    #: Free-form annotations accumulated across the chain for auditing.
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def continue_(cls, **metadata: Any) -> "HookResult":
        """No change; optionally attach audit metadata."""
        return cls(decision=HookDecision.CONTINUE, metadata=dict(metadata))

    @classmethod
    def modify(cls, payload: Dict[str, Any], reason: Optional[str] = None, **metadata: Any) -> "HookResult":
        """Replace one or more mutable fields of the event."""
        return cls(
            decision=HookDecision.MODIFY,
            payload=dict(payload or {}),
            reason=reason,
            metadata=dict(metadata),
        )

    @classmethod
    def deny(
        cls,
        reason: str,
        user_message: Optional[str] = None,
        **metadata: Any,
    ) -> "HookResult":
        """Block the prompt, tool call, or retrieval.

        ``reason`` is operator-facing and is only written to logs and spans. It
        commonly names the handler, the rule that fired, or the offending value,
        so it is *not* used as the user-facing text: a plugin that wants the end
        user to see a specific explanation must pass ``user_message`` explicitly.
        """
        return cls(
            decision=HookDecision.DENY,
            reason=reason,
            user_message=user_message or DEFAULT_DENY_USER_MESSAGE,
            metadata=dict(metadata),
        )

    @classmethod
    def require_approval(cls, reason: Optional[str] = None, **metadata: Any) -> "HookResult":
        """Force the runtime approval gate even when it would normally be skipped."""
        return cls(
            decision=HookDecision.REQUIRE_APPROVAL,
            reason=reason,
            metadata=dict(metadata),
        )

    @property
    def denied(self) -> bool:
        return self.decision == HookDecision.DENY


@dataclass
class HookEvent:
    """Base event: trusted, server-side context common to every hook point.

    None of these fields are ever patchable by a plugin -- ``MUTABLE_FIELDS``
    on each subclass is the complete allow-list.
    """

    HOOK_POINT: ClassVar[HookPoint]
    MUTABLE_FIELDS: ClassVar[Tuple[str, ...]] = ()

    session_id: Optional[str] = None
    user_email: Optional[str] = None
    conversation_id: Optional[str] = None
    compliance_level: Optional[Any] = None

    def validate_patch(self, patch: Dict[str, Any]) -> Optional[str]:
        """Return an error string if *patch* is not acceptable, else ``None``.

        The registry has already checked that every key is in ``MUTABLE_FIELDS``
        before calling this. Subclasses override to enforce tighten-only rules.
        """
        return None

    def apply_patch(self, patch: Dict[str, Any]) -> None:
        """Apply a validated patch in place."""
        for key, value in patch.items():
            setattr(self, key, value)


#: ``session.context`` keys owned by the runtime. Every other writer validates
#: these (conversation-id ownership, compliance validation, file ingestion), so a
#: plugin patch must not be able to seed them behind those checks.
RESERVED_SESSION_CONTEXT_KEYS = frozenset({
    "session_id",
    "user_email",
    "conversation_id",
    "compliance_level",
    "files",
    "selected_data_sources",
    "available_tools",
    "agent_mode",
    "model",
    "temperature",
    "system_prompt",
    "messages_prefix",
})


@dataclass
class SessionStartEvent(HookEvent):
    """Fires when a chat session is created."""

    HOOK_POINT: ClassVar[HookPoint] = HookPoint.SESSION_START
    MUTABLE_FIELDS: ClassVar[Tuple[str, ...]] = ("context",)

    #: Session-scoped context a plugin may seed with policy state or metadata.
    context: Dict[str, Any] = field(default_factory=dict)

    def validate_patch(self, patch: Dict[str, Any]) -> Optional[str]:
        if "context" not in patch:
            return None
        new_context = patch["context"]
        if not isinstance(new_context, dict):
            return "context must be a dict"
        # A plugin seeds its *own* policy state here. Runtime-owned keys reach
        # session.context only through the writers that validate them (the
        # conversation-id ownership check, compliance validation, file
        # ingestion), and keys starting with "_" are internal bookkeeping. The
        # patch replaces the whole dict, so a plugin may carry those keys
        # through unchanged -- it just may not add, alter, or drop them.
        def _is_reserved(key: Any) -> bool:
            return key in RESERVED_SESSION_CONTEXT_KEYS or str(key).startswith("_")

        touched = sorted(
            str(key)
            for key in set(new_context) | set(self.context or {})
            if _is_reserved(key)
            and (
                key not in new_context
                or key not in (self.context or {})
                or new_context[key] != (self.context or {})[key]
            )
        )
        if touched:
            return f"context keys {touched} are runtime-owned and cannot be set by a plugin"
        return None


@dataclass
class UserPromptSubmitEvent(HookEvent):
    """Fires after the prompt is received, before routing or any LLM call."""

    HOOK_POINT: ClassVar[HookPoint] = HookPoint.USER_PROMPT_SUBMIT
    MUTABLE_FIELDS: ClassVar[Tuple[str, ...]] = (
        "prompt",
        "selected_tools",
        "selected_data_sources",
        "agent_mode",
    )

    prompt: str = ""
    model: str = ""
    selected_tools: Optional[List[str]] = None
    selected_data_sources: Optional[List[str]] = None
    agent_mode: bool = False

    def validate_patch(self, patch: Dict[str, Any]) -> Optional[str]:
        if "prompt" in patch and not isinstance(patch["prompt"], str):
            return "prompt must be a string"
        for key in ("selected_tools", "selected_data_sources"):
            if key in patch and patch[key] is not None and not isinstance(patch[key], list):
                return f"{key} must be a list or None"
        # Tighten-only: a plugin may drop tools/sources but never add ones the
        # user did not select and the authorization layer has not yet filtered.
        for key, original in (
            ("selected_tools", self.selected_tools),
            ("selected_data_sources", self.selected_data_sources),
        ):
            if key in patch and patch[key]:
                added = set(patch[key]) - set(original or [])
                if added:
                    return f"{key} may only be narrowed; cannot add {sorted(added)}"
        if "agent_mode" in patch:
            if not isinstance(patch["agent_mode"], bool):
                return "agent_mode must be a bool"
            if patch["agent_mode"] and not self.agent_mode:
                return "agent_mode may only be disabled, not enabled"
        return None


#: Message roles the chat pipeline builds. A patched message list may only use
#: these; an unknown role would either be rejected by the provider or, worse,
#: silently reinterpreted.
ALLOWED_MESSAGE_ROLES = frozenset({"system", "user", "assistant", "tool", "function"})


@dataclass
class PreLlmCallEvent(HookEvent):
    """Fires immediately before the request is handed to the LLM provider.

    Unlike ``USER_PROMPT_SUBMIT``, which fires once per turn on the raw user
    text, this fires on every provider round-trip -- each agentic-loop
    iteration, each tool-synthesis call, streaming and non-streaming alike --
    and sees the fully assembled request: system prompt, history, RAG context,
    and tool results. It is where a plugin inspects or rewrites what actually
    leaves the process, pins or swaps the model, or refuses the call.

    Model patches are *not* authorized here: the per-user group check is async
    and needs the model catalog, so the call site re-runs
    ``check_model_access`` against the patched name and denies the call when the
    turn's user may not use it. ``validate_patch`` only enforces shape.
    """

    HOOK_POINT: ClassVar[HookPoint] = HookPoint.PRE_LLM_CALL
    MUTABLE_FIELDS: ClassVar[Tuple[str, ...]] = ("messages", "model", "temperature")

    model: str = ""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    #: True when the response is streamed token-by-token.
    streaming: bool = False
    #: True when a tool schema is attached to this request.
    has_tools: bool = False

    def validate_patch(self, patch: Dict[str, Any]) -> Optional[str]:
        if "model" in patch:
            model = patch["model"]
            if not isinstance(model, str) or not model.strip():
                return "model must be a non-empty string"
        if "temperature" in patch:
            temperature = patch["temperature"]
            if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
                return "temperature must be a number"
            if not 0.0 <= float(temperature) <= 2.0:
                return "temperature must be between 0.0 and 2.0"
        if "messages" in patch:
            messages = patch["messages"]
            if not isinstance(messages, list):
                return "messages must be a list"
            # Emptying the list would send a contentless request rather than
            # block anything; a plugin that wants to stop the call says DENY.
            if not messages:
                return "messages cannot be emptied; return DENY to block the call"
            for index, message in enumerate(messages):
                if not isinstance(message, dict):
                    return f"messages[{index}] must be a dict"
                role = message.get("role")
                if not isinstance(role, str):
                    return f"messages[{index}].role must be a string"
                if role not in ALLOWED_MESSAGE_ROLES:
                    return (
                        f"messages[{index}].role {role!r} is not one of "
                        f"{sorted(ALLOWED_MESSAGE_ROLES)}"
                    )
        return None


@dataclass
class PreToolUseEvent(HookEvent):
    """Fires immediately before a tool executes.

    Every tool call in the system -- tools mode and the agentic loop -- reaches
    ``execute_single_tool``, so this is the single uniform control point for
    argument mutation, denial, and approval escalation.
    """

    HOOK_POINT: ClassVar[HookPoint] = HookPoint.PRE_TOOL_USE
    MUTABLE_FIELDS: ClassVar[Tuple[str, ...]] = ("arguments",)

    tool_name: str = ""
    tool_call_id: Optional[str] = None
    tool_source: Optional[str] = None
    arguments: Dict[str, Any] = field(default_factory=dict)

    def validate_patch(self, patch: Dict[str, Any]) -> Optional[str]:
        if "arguments" in patch and not isinstance(patch["arguments"], dict):
            return "arguments must be a dict"
        return None


@dataclass
class PostToolUseEvent(HookEvent):
    """Fires after a tool returns, before the result reaches the model or UI."""

    HOOK_POINT: ClassVar[HookPoint] = HookPoint.POST_TOOL_USE
    MUTABLE_FIELDS: ClassVar[Tuple[str, ...]] = ("content",)

    tool_name: str = ""
    tool_call_id: Optional[str] = None
    arguments: Dict[str, Any] = field(default_factory=dict)
    content: str = ""
    success: bool = True
    error: Optional[str] = None

    def validate_patch(self, patch: Dict[str, Any]) -> Optional[str]:
        if "content" in patch and not isinstance(patch["content"], str):
            return "content must be a string"
        return None


@dataclass
class PermissionRequestEvent(HookEvent):
    """Fires once the approval requirement for a tool call has been computed."""

    HOOK_POINT: ClassVar[HookPoint] = HookPoint.PERMISSION_REQUEST
    MUTABLE_FIELDS: ClassVar[Tuple[str, ...]] = ("needs_approval",)

    tool_name: str = ""
    tool_call_id: Optional[str] = None
    arguments: Dict[str, Any] = field(default_factory=dict)
    needs_approval: bool = True
    #: True when approval is admin-mandated (global force flag or mcp.json).
    admin_required: bool = False
    #: Set once any handler in the chain has raised ``needs_approval``. Keeps the
    #: composed outcome most-restrictive-wins and independent of handler order.
    escalated_by_hook: bool = field(default=False, init=False, repr=False, compare=False)

    def validate_patch(self, patch: Dict[str, Any]) -> Optional[str]:
        if "needs_approval" not in patch:
            return None
        if not isinstance(patch["needs_approval"], bool):
            return "needs_approval must be a bool"
        # A plugin may escalate to approval, but must never auto-approve past an
        # admin-mandated gate.
        if self.admin_required and patch["needs_approval"] is False:
            return "cannot auto-approve a tool whose approval is admin-mandated"
        # ...nor undo an escalation an earlier handler in this chain applied.
        # Without this, "does this tool need approval?" would depend on plugin
        # ordering rather than on the most restrictive answer given.
        if self.escalated_by_hook and patch["needs_approval"] is False:
            return "cannot lower approval after an earlier hook escalated it"
        return None

    def apply_patch(self, patch: Dict[str, Any]) -> None:
        # Flag on *assertion*, not on change. The tools-mode call site already
        # enters with needs_approval=True whenever the tool is configured to
        # require it, so "only flag when the value flips" would leave the
        # escalation invisible there and let a later handler auto-approve it.
        if patch.get("needs_approval") is True:
            self.escalated_by_hook = True
        super().apply_patch(patch)


@dataclass
class RagCallEvent(HookEvent):
    """Fires before RAG retrieval runs."""

    HOOK_POINT: ClassVar[HookPoint] = HookPoint.RAG_CALL
    MUTABLE_FIELDS: ClassVar[Tuple[str, ...]] = ("query", "data_sources")

    query: str = ""
    data_sources: List[str] = field(default_factory=list)
    batch: bool = False

    def validate_patch(self, patch: Dict[str, Any]) -> Optional[str]:
        if "query" in patch and not isinstance(patch["query"], str):
            return "query must be a string"
        if "data_sources" in patch:
            new_sources = patch["data_sources"]
            if not isinstance(new_sources, list):
                return "data_sources must be a list"
            # Tighten-only: the caller's list is already compliance-filtered and
            # authorization-filtered. A plugin may drop sources, never add them.
            added = set(new_sources) - set(self.data_sources or [])
            if added:
                return f"data_sources may only be narrowed; cannot add {sorted(added)}"
            if not new_sources:
                return "data_sources cannot be emptied; return DENY to block retrieval"
        return None


@dataclass
class RagResponseEvent(HookEvent):
    """Fires after chunks return, before they are injected into the prompt."""

    HOOK_POINT: ClassVar[HookPoint] = HookPoint.RAG_RESPONSE
    MUTABLE_FIELDS: ClassVar[Tuple[str, ...]] = ("content", "metadata")

    query: str = ""
    data_sources: List[str] = field(default_factory=list)
    content: str = ""
    metadata: Optional[Any] = None
    batch: bool = False

    def validate_patch(self, patch: Dict[str, Any]) -> Optional[str]:
        if "content" in patch and not isinstance(patch["content"], str):
            return "content must be a string"
        if "metadata" in patch:
            # Metadata is the structured document/chunk data that
            # ``_format_rag_references`` renders into the References section.
            # A plugin may drop it (``None``) or replace it, but must not set it
            # to a string -- that is what ``content`` is for.
            if isinstance(patch["metadata"], str):
                return "metadata must be None or a non-string object"
        return None
