"""Persist canvas-tool text as a display-only conversation row (issue #1157).

The canvas panel is not a transcript row, so the tool call itself is still
omitted from the tool-call history. The text it wrote is the user's document,
and it has to survive a reload and be readable on the next turn. These rows
carry that text. They are excluded from the model transcript as turns and
folded back in by :mod:`atlas.domain.messages.canvas_context`.
"""

import json
from typing import Any, Dict, List, Optional

from atlas.domain.messages.canvas_context import CANVAS_ARTIFACT_MESSAGE_TYPE
from atlas.domain.messages.models import Message, MessageRole


def canvas_text(value: Any) -> str:
    """The canvas body as the exact string the panel would show.

    A string is kept character for character. A structured value is stored as
    JSON so a later reload can show the same document. Empty input is empty.
    """
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def canvas_text_from_arguments(arguments: Any) -> str:
    """Canvas body from a canvas tool's ``content`` argument."""
    if not isinstance(arguments, dict):
        return ""
    return canvas_text(arguments.get("content"))


def canvas_artifact_message(entry: Dict[str, Any]) -> Optional[Message]:
    """A display-only row for one canvas write, or None when it has no text."""
    content = entry.get("canvas_content")
    if not isinstance(content, str) or content == "":
        return None
    artifact_id = entry.get("artifact_id") or entry.get("invocation_id") or entry.get("tool_call_id")
    metadata: Dict[str, Any] = {
        "message_type": CANVAS_ARTIFACT_MESSAGE_TYPE,
        "artifact_id": str(artifact_id) if artifact_id else "",
        "tool_call_id": entry.get("tool_call_id"),
        "tool_name": entry.get("tool_name") or "",
        "canvas_content": content,
        "status": entry.get("status") or "completed",
    }
    for field in ("invocation_id", "run_id", "step"):
        if entry.get(field) is not None:
            metadata[field] = entry[field]
    return Message(
        role=MessageRole.SYSTEM,
        content="Canvas artifact",
        metadata=metadata,
    )


def canvas_artifact_messages(entries: List[Dict[str, Any]]) -> List[Message]:
    """Build one row per entry that actually holds canvas text."""
    out: List[Message] = []
    for entry in entries:
        message = canvas_artifact_message(entry)
        if message is not None:
            out.append(message)
    return out
