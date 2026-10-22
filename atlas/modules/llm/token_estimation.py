"""Estimate the token size of an assembled LLM request (issue #877).

The estimate covers what is actually sent: system prompt, conversation,
tool-call arguments and results, tool definitions, and inline images or
documents. LiteLLM's tokenizer is used where it can run locally; anything it
cannot count (or a payload too large to count quickly) falls back to a
conservative character heuristic, and the result says which was used.

Images and documents are never handed to the tokenizer: LiteLLM sizes an
image by fetching it when given a URL, and an estimate must never make a
network call. They are charged a flat, deliberately generous amount instead.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

METHOD_TOKENIZER = "tokenizer"
METHOD_HEURISTIC = "heuristic"

# Characters per token for the fallback. English prose runs near 4; code,
# JSON and non-Latin text run lower, so 3 over-counts on purpose.
HEURISTIC_CHARS_PER_TOKEN = 3.0
# Role markers and message framing the provider adds around every message.
PER_MESSAGE_OVERHEAD = 4
IMAGE_TOKENS = 1600
DOCUMENT_TOKENS = 3000
# Above this many characters, the tokenizer's cost outweighs its precision.
TOKENIZER_MAX_CHARS = 2_000_000


@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    method: str


def _split_media(content: Any) -> Tuple[Any, int]:
    """Return ``(text-only content, media tokens)`` for one message content."""
    if not isinstance(content, list):
        return content, 0
    text_parts: List[str] = []
    media = 0
    for block in content:
        if not isinstance(block, dict):
            text_parts.append(str(block))
            continue
        kind = block.get("type")
        if kind == "text":
            text_parts.append(str(block.get("text") or ""))
        elif kind in ("image_url", "image", "input_image"):
            media += IMAGE_TOKENS
        elif kind in ("file", "document", "input_file"):
            media += DOCUMENT_TOKENS
        else:
            text_parts.append(json.dumps(block, default=str))
    return "\n".join(text_parts), media


def _text_only_messages(messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    stripped: List[Dict[str, Any]] = []
    media_tokens = 0
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        content, media = _split_media(msg.get("content"))
        media_tokens += media
        clean: Dict[str, Any] = {"role": msg.get("role") or "user", "content": content or ""}
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            # Tool-call arguments are sent to the model and count; the
            # tokenizer only reads plain dicts, so normalize them here.
            clean["content"] = f"{clean['content']}\n{json.dumps(tool_calls, default=_jsonable)}"
        stripped.append(clean)
    return stripped, media_tokens


def _jsonable(value: Any) -> Any:
    function = getattr(value, "function", None)
    if function is not None:
        return {
            "id": getattr(value, "id", None),
            "function": {
                "name": getattr(function, "name", ""),
                "arguments": getattr(function, "arguments", ""),
            },
        }
    return str(value)


def _heuristic(text_chars: int, message_count: int) -> int:
    return int(text_chars / HEURISTIC_CHARS_PER_TOKEN) + message_count * PER_MESSAGE_OVERHEAD


def estimate_request_tokens(
    messages: List[Dict[str, Any]],
    tools_schema: Optional[List[Dict[str, Any]]] = None,
    model: Optional[str] = None,
) -> TokenEstimate:
    """Estimate the input tokens of one request. Never raises."""
    text_messages, media_tokens = _text_only_messages(messages)
    tools_text = json.dumps(tools_schema, default=str) if tools_schema else ""
    text_chars = sum(len(str(m["content"])) for m in text_messages) + len(tools_text)

    if text_chars <= TOKENIZER_MAX_CHARS:
        try:
            import litellm
            counted = litellm.token_counter(model=model or "", messages=text_messages)
            if tools_text:
                counted += litellm.token_counter(model=model or "", text=tools_text)
            if isinstance(counted, int) and counted >= 0:
                return TokenEstimate(counted + media_tokens, METHOD_TOKENIZER)
        except Exception:
            logger.debug("Tokenizer estimate unavailable; using the heuristic", exc_info=True)

    return TokenEstimate(_heuristic(text_chars, len(text_messages)) + media_tokens, METHOD_HEURISTIC)
