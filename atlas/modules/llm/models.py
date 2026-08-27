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


def tool_call_function_field(tool_call: Any, field: str, default: str = "") -> str:
    """Read ``function.<field>`` from a tool call in either shape.

    Tool calls arrive as litellm pydantic models (attribute access) from the
    provider and as plain dicts from history round-trips and tests.
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


def partition_tool_calls_by_json_validity(
    tool_calls: Optional[List[Any]],
) -> Tuple[List[Any], List[Any]]:
    """Split tool calls into ``(valid, malformed)`` by argument parseability.

    Malformed calls must be dropped rather than executed or persisted: their
    arguments are unparseable, so the call cannot be run faithfully, and
    re-sending them makes the provider reject every later turn in the
    conversation (see ``LLMMalformedToolCallError``).
    """
    valid: List[Any] = []
    malformed: List[Any] = []
    for tool_call in tool_calls or []:
        if tool_call is None:
            continue
        (valid if tool_call_arguments_are_valid_json(tool_call) else malformed).append(tool_call)
    return valid, malformed
