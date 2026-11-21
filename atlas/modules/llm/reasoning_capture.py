"""Capture a model's reasoning so it can be sent back within a turn (issue #1230).

Some reasoning models reject a follow-up request when an earlier assistant
tool-call message in the same request is missing the reasoning that model
produced with it, for example::

    The assistant turn at messages[2] is missing its reasoning ...
    Send it back in 'reasoning_content'

The agent loop and tools mode append the assistant tool-call message
themselves, so the reasoning has to be collected from the LLM response
(streaming and non-streaming) and attached to that message.

Three provider shapes are collected, each under the key the provider (or
LiteLLM, which translates it for the provider on the way out) reads back:

* ``reasoning_content``: plain reasoning text (OpenAI-compatible reasoning
  models, OpenRouter, DeepSeek, vLLM and others; LiteLLM maps ``reasoning``
  to this name).
* ``reasoning_details``: OpenRouter's structured reasoning blocks, which may
  carry encrypted or signed items that only round-trip verbatim.
* ``thinking_blocks``: LiteLLM's signed thinking blocks (Anthropic extended
  thinking, Gemini thought signatures).

Reasoning is model-internal working state. It is kept only on the in-memory
message list for the rest of the turn: it is never persisted to conversation
history, published to the UI, written to logs or diagnostics, or included in
fine-tune capture. Later turns do not need it, because earlier turns' tool-call
messages are display-only rows that are never replayed to the model.
"""

import json
from typing import Any, Dict, List, Optional

# The message keys that carry reasoning. Code that copies messages somewhere
# other than the next LLM request (capture, persistence) strips these.
REASONING_MESSAGE_KEYS = ("reasoning_content", "reasoning_details", "thinking_blocks")

# String fields of a streamed ``reasoning_details`` item that arrive in pieces
# and are concatenated; every other field is taken from the latest fragment.
_DETAIL_TEXT_FIELDS = ("text", "summary", "data")


def _plain(value: Any) -> Any:
    """Return a JSON-safe plain dict/list copy of a provider object."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return _plain(dump())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return {k: _plain(v) for k, v in vars(value).items() if not k.startswith("_")}
    return value


def _field(obj: Any, name: str) -> Any:
    """Read ``name`` from a provider message/delta (attribute or dict)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def extract_reasoning(message: Any) -> Dict[str, Any]:
    """Collect the reasoning fields of a complete (non-streamed) message.

    Returns only the fields that are present and non-empty, as plain values,
    so the result can be merged straight into an outgoing assistant message.
    """
    fields: Dict[str, Any] = {}
    text = _field(message, "reasoning_content")
    if isinstance(text, str) and text:
        fields["reasoning_content"] = text
    for key in ("reasoning_details", "thinking_blocks"):
        value = _field(message, key)
        if isinstance(value, (list, tuple)) and value:
            fields[key] = _plain(list(value))
    return fields


class ReasoningAccumulator:
    """Accumulate reasoning across streamed deltas into one message's fields."""

    def __init__(self) -> None:
        self._text_parts: List[str] = []
        self._details: List[Dict[str, Any]] = []
        self._detail_slots: Dict[Any, Dict[str, Any]] = {}
        self._blocks: List[Dict[str, Any]] = []
        self._thinking_parts: List[str] = []
        self._signature: Optional[str] = None

    def reset(self) -> None:
        """Discard everything, e.g. before a retried stream attempt."""
        self.__init__()

    def observe(self, delta: Any) -> None:
        """Fold one streamed delta's reasoning into the accumulator."""
        text = _field(delta, "reasoning_content")
        if isinstance(text, str) and text:
            self._text_parts.append(text)

        details = _field(delta, "reasoning_details")
        if isinstance(details, (list, tuple)):
            for item in details:
                self._add_detail(_plain(item))

        blocks = _field(delta, "thinking_blocks")
        if isinstance(blocks, (list, tuple)):
            for block in blocks:
                self._add_thinking_block(_plain(block))

    def _add_detail(self, item: Any) -> None:
        # OpenRouter streams reasoning_details as fragments of indexed items:
        # fragments sharing an index (and type) are one item whose text
        # arrives in pieces. Items without an index are complete on their own.
        if not isinstance(item, dict):
            return
        index = item.get("index")
        if index is None:
            self._details.append(dict(item))
            return
        slot_key = (index, item.get("type"))
        slot = self._detail_slots.get(slot_key)
        if slot is None:
            slot = dict(item)
            self._detail_slots[slot_key] = slot
            self._details.append(slot)
            return
        for key, value in item.items():
            if key in _DETAIL_TEXT_FIELDS and isinstance(value, str) and isinstance(slot.get(key), str):
                slot[key] += value
            elif value is not None:
                slot[key] = value

    def _flush_thinking(self) -> None:
        # Mirrors LiteLLM's stream_chunk_builder: a thinking block is only
        # usable with its signature, so unsigned text is not sent back.
        if self._thinking_parts and self._signature:
            self._blocks.append({
                "type": "thinking",
                "thinking": "".join(self._thinking_parts),
                "signature": self._signature,
            })
        self._thinking_parts = []
        self._signature = None

    def _add_thinking_block(self, block: Any) -> None:
        if not isinstance(block, dict):
            return
        if block.get("type") == "redacted_thinking":
            self._flush_thinking()
            if block.get("data"):
                self._blocks.append({"type": "redacted_thinking", "data": block["data"]})
            return
        thinking = block.get("thinking")
        if isinstance(thinking, str) and thinking:
            self._thinking_parts.append(thinking)
        signature = block.get("signature")
        if isinstance(signature, str) and signature:
            self._signature = signature
            self._flush_thinking()

    def fields(self) -> Dict[str, Any]:
        """The accumulated reasoning, in the shape :func:`extract_reasoning` returns."""
        self._flush_thinking()
        fields: Dict[str, Any] = {}
        text = "".join(self._text_parts)
        if text:
            fields["reasoning_content"] = text
        if self._details:
            fields["reasoning_details"] = [dict(d) for d in self._details]
        if self._blocks:
            fields["thinking_blocks"] = [dict(b) for b in self._blocks]
        return fields


def reasoning_text_for_estimate(message: Dict[str, Any]) -> str:
    """Serialized reasoning on ``message``, for token estimates only.

    Reasoning sent back within a turn is part of the request, so context
    window and compaction estimates must count it. Never log the result.
    """
    parts = []
    for key in REASONING_MESSAGE_KEYS:
        value = message.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif value:
            parts.append(json.dumps(value, default=str))
    return "\n".join(parts)


def strip_reasoning(message: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``message`` without reasoning keys (a copy only when needed)."""
    if not any(key in message for key in REASONING_MESSAGE_KEYS):
        return message
    return {k: v for k, v in message.items() if k not in REASONING_MESSAGE_KEYS}
