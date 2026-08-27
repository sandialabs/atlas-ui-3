"""
Data models for LLM responses and related structures.
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class LLMResponse:
    """Response from LLM call with metadata."""
    content: str
    tool_calls: Optional[List[Dict]] = None
    model_used: str = ""
    tokens_used: int = 0

    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return self.tool_calls is not None and len(self.tool_calls) > 0


def split_provider(litellm_model: str) -> Tuple[str, str]:
    """Split a LiteLLM model string into (provider, model_suffix).

    Examples: ``openai/gpt-4o`` -> (``openai``, ``gpt-4o``);
    ``anthropic/claude-opus-4-7`` -> (``anthropic``, ``claude-opus-4-7``).
    When no prefix is present, provider is ``unknown``.

    Lives here (not in ``litellm_caller``) so ``litellm_streaming`` can import
    it without creating an import cycle back into the caller module.
    """
    if not litellm_model:
        return "unknown", ""
    if "/" in litellm_model:
        provider, suffix = litellm_model.split("/", 1)
        return provider, suffix
    return "unknown", litellm_model


def tool_call_function_field(tool_call: Any, field: str, default: Any = "") -> Any:
    """Read ``function.<field>`` from a tool call in either shape.

    Tool calls arrive as litellm pydantic models (attribute access) from the
    provider and as plain dicts from history round-trips and tests. The value is
    returned as the provider sent it -- usually a string, but ``arguments`` can
    arrive already decoded as a dict or list -- so the return type is not
    narrowed to ``str``; callers that need one coerce it themselves.
    """
    function = None
    if isinstance(tool_call, dict):
        function = tool_call.get("function")
    else:
        function = getattr(tool_call, "function", None)
    if function is None:
        return default
    if isinstance(function, dict):
        value = function.get(field)
    else:
        value = getattr(function, field, None)
    return default if value is None else value


def tool_call_arguments_are_valid_json(tool_call: Any) -> bool:
    """Whether a tool call's ``arguments`` can be parsed back out of history.

    Empty arguments count as valid: models legitimately emit ``""`` for
    no-argument tools, and providers accept it. Only a non-empty string that
    fails to parse is a real defect -- the signature of an output truncated
    partway through the JSON object.
    """
    arguments = tool_call_function_field(tool_call, "arguments", "")
    if not isinstance(arguments, str):
        # Some providers hand back an already-decoded object; nothing to parse.
        return arguments is None or isinstance(arguments, (dict, list))
    if not arguments.strip():
        return True
    try:
        json.loads(arguments)
    except ValueError:
        return False
    return True


def tool_call_arguments_are_empty(tool_call: Any) -> bool:
    """Whether a tool call carries no arguments at all."""
    arguments = tool_call_function_field(tool_call, "arguments", "")
    if arguments is None:
        return True
    if isinstance(arguments, str):
        return not arguments.strip()
    return not arguments


def partition_tool_calls_by_json_validity(
    tool_calls: Optional[List[Any]],
    truncated: bool = False,
) -> Tuple[List[Any], List[Any]]:
    """Split tool calls into ``(valid, malformed)`` by argument parseability.

    Malformed calls must be dropped rather than executed or persisted: their
    arguments are unparseable, so the call cannot be run faithfully, and
    re-sending them makes the provider reject every later turn in the
    conversation (see ``LLMMalformedToolCallError``).

    ``truncated`` marks a response the provider cut off at the token limit
    (``finish_reason == "length"``). It closes a hole that parseability alone
    cannot see: a call cut off *before* its first argument delta arrives has
    ``arguments == ""``, which parses fine as "no arguments" and would execute
    with ``{}``. For a tool whose parameters are all optional, that is not a
    failure the user sees -- it is the wrong action performed silently. Only the
    **last** call is judged this way, because that is the only one truncation can
    have reached; earlier calls in the same response completed before the limit
    was hit, so a genuine no-argument call among them is still honoured.
    """
    calls = [tool_call for tool_call in (tool_calls or []) if tool_call is not None]
    valid: List[Any] = []
    malformed: List[Any] = []
    for index, tool_call in enumerate(calls):
        ok = tool_call_arguments_are_valid_json(tool_call)
        if (
            ok
            and truncated
            and index == len(calls) - 1
            and tool_call_arguments_are_empty(tool_call)
        ):
            ok = False
        (valid if ok else malformed).append(tool_call)
    return valid, malformed
