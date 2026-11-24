"""
Data models for LLM responses and related structures.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class LLMResponse:
    """Response from LLM call with metadata."""
    content: str
    tool_calls: Optional[List[Dict]] = None
    model_used: str = ""
    tokens_used: int = 0
    # Names of tool calls dropped for unparseable arguments while other calls in
    # the same response survived. The turn continues, so nothing else would tell
    # the user or the model that a call was discarded.
    dropped_tool_calls: Optional[List[str]] = None
    # Whether that drop was an output-limit truncation rather than unparseable
    # JSON. The user-facing warning says why, so it must not guess.
    dropped_tool_calls_truncated: bool = False
    # The model's reasoning for this response (issue #1230), keyed by the
    # message field it is sent back under: ``reasoning_content``,
    # ``reasoning_details`` and/or ``thinking_blocks``. Carried only so the
    # assistant tool-call message can return it on the next request of the
    # same turn. Excluded from repr so it cannot reach a log line by accident.
    reasoning: Dict[str, Any] = field(default_factory=dict, repr=False)

    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return self.tool_calls is not None and len(self.tool_calls) > 0

    def assistant_message(self, tool_calls: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """The assistant message that records this response in the request list.

        Carries the response's reasoning so models that require it back on
        the next request of the turn accept the follow-up call (issue #1230).
        ``tool_calls`` are the already-normalized dicts; omitted for a
        text-only message.
        """
        message: Dict[str, Any] = {"role": "assistant", "content": self.content}
        if tool_calls is not None:
            message["tool_calls"] = tool_calls
        message.update(self.reasoning or {})
        return message


@dataclass
class PlainCompletion:
    """The text of one plain LLM call plus non-sensitive metadata (#1138).

    ``diagnostics`` never carries content, reasoning text or the request body:
    counts, the allow-listed finish reason and a few structure flags are enough
    to tell an empty reply, a drained output budget and a provider failure
    apart. Callers that only want the text use :attr:`text`.
    """

    text: str = ""
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.text


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
