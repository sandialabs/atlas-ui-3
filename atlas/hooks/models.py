"""Pydantic models and event definitions for the config-driven hook system.

A "hook" is an arbitrary executable (bash, Python, anything) registered in
``config/hooks.json`` against a chat lifecycle event. Atlas spawns it as a
subprocess at the event's chokepoint, sends the event payload as JSON on stdin,
and reads a decision (exit code + optional JSON on stdout) that can allow,
modify, block, or escalate the operation. See ``docs/hooks.md`` and GH #713.

This module holds only data models + the event enumeration -- no I/O -- so it
can be imported without pulling in the subprocess machinery.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class HookEvent(str, Enum):
    """Lifecycle events at which operator-installed hooks may fire.

    Each value is the stable ``hook_event_name`` string used in the stdin
    envelope and in ``config/hooks.json`` keys. Keeping the enum values == the
    JSON keys means config and wire format can never drift apart.
    """

    SESSION_START = "SessionStart"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_LLM_CALL = "PreLlmCall"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    PERMISSION_REQUEST = "PermissionRequest"
    RAG_CALL = "RagCall"
    RAG_RESPONSE = "RagResponse"
    SESSION_END = "SessionEnd"


# Per-event ``on_error`` defaults. Security/interceptor events that guard a
# boundary (PreToolUse, PermissionRequest, PreLlmCall, RagCall) fail *closed*
# (``deny``): a crashing/hanging hook must not silently weaken the boundary.
# Observability/lifecycle events (SessionStart, SessionEnd, UserPromptSubmit,
# PostToolUse, RagResponse) fail *open* (``allow``): a broken audit hook should
# not take down the chat turn. Operators override per-hook in ``hooks.json``.
_EVENT_DEFAULT_ON_ERROR: Dict[HookEvent, Literal["deny", "allow"]] = {
    HookEvent.PRE_TOOL_USE: "deny",
    HookEvent.PERMISSION_REQUEST: "deny",
    HookEvent.PRE_LLM_CALL: "deny",
    HookEvent.RAG_CALL: "deny",
    HookEvent.SESSION_START: "allow",
    HookEvent.SESSION_END: "allow",
    HookEvent.USER_PROMPT_SUBMIT: "allow",
    HookEvent.POST_TOOL_USE: "allow",
    HookEvent.RAG_RESPONSE: "allow",
}


def default_on_error(event: HookEvent) -> Literal["deny", "allow"]:
    """Return the fail-closed/fail-open default for an event."""
    return _EVENT_DEFAULT_ON_ERROR.get(event, "allow")


class HookConfig(BaseModel):
    """A single hook registration (one entry under ``hooks.<EventName>``).

    Mirrors the Claude Code ``settings.json`` hook entry shape so operators
    familiar with that system can reuse muscle memory.

    Fields:
        name: Human-readable identifier used in logs/audit spans.
        matcher: Optional regex over the event's "matcher value" (the tool name
            for tool events, the qualified data source for RAG events). Omit or
            set to ``"*"`` to match everything. Ignored for events without a
            natural matcher value (SessionStart/SessionEnd/UserPromptSubmit).
        command: argv array spawned with ``asyncio.create_subprocess_exec`` --
            **never** ``shell=True``. Supports ``${ATLAS_CONFIG_DIR}`` and
            ``${ATLAS_PROJECT_DIR}`` interpolation so config-relative script
            paths are portable without leaking the server's full environment.
        timeout_ms: Wall-clock budget for the subprocess. On expiry the process
            is killed and ``on_error`` decides the outcome.
        on_error: Outcome when the hook crashes, times out, or emits malformed
            output. ``deny`` short-circuits the operation as blocked;
            ``allow`` continues as if the hook returned ``continue``. When
            omitted, the per-event default (see ``default_on_error``) applies.
    """

    name: str
    matcher: Optional[str] = None
    command: List[str]
    timeout_ms: int = Field(default=2000, ge=1)
    on_error: Optional[Literal["deny", "allow"]] = None

    @field_validator("command")
    @classmethod
    def _command_non_empty(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("hook command must be a non-empty argv array")
        return v

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("hook name must be non-empty")
        return v

    def matches(self, matcher_value: Optional[str]) -> bool:
        """Return True if ``matcher_value`` satisfies this hook's matcher.

        ``None``/``"*"`` matcher = match all. A missing ``matcher_value`` (event
        has no natural value) only matches a wildcard matcher, so a hook that
        set an explicit matcher on e.g. SessionStart simply never fires -- the
        operator gets a clear signal rather than silent over-firing.
        """
        pattern = self.matcher
        if pattern is None or pattern == "*" or pattern == "":
            return True
        if matcher_value is None:
            return False
        import re

        try:
            return re.search(pattern, matcher_value) is not None
        except re.error:
            # Treat an invalid matcher as a non-match rather than crashing the
            # turn; the misconfiguration is surfaced via the validate_config
            # path and the startup log.
            return False

    def effective_on_error(self, event: HookEvent) -> Literal["deny", "allow"]:
        return self.on_error if self.on_error is not None else default_on_error(event)


class HooksConfig(BaseModel):
    """Top-level ``config/hooks.json`` model: event name -> list of hooks.

    Hooks for an event run sequentially in config order; each sees the previous
    hook's ``modify`` output. ``deny`` short-circuits; ``require_approval`` is
    sticky (cannot be downgraded by a later hook). See ``HookManager.run_event``.
    """

    hooks: Dict[str, List[HookConfig]] = Field(default_factory=dict)

    def hooks_for(self, event_name: str) -> List[HookConfig]:
        """Return the ordered hook list for an event name (empty if none)."""
        return self.hooks.get(event_name, []) or []


class HookDecision(BaseModel):
    """Parsed stdout JSON from a hook (the structured path).

    The fast path is the exit code: 0 = continue (apply stdout if non-empty
    JSON), 2 = block (stderr is the reason), other = error. When a hook emits
    stdout JSON on exit 0, this model validates it.
    """

    decision: Literal["continue", "modify", "deny", "require_approval"] = "continue"
    reason: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
