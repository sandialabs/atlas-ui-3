"""Close out a turn that was stopped mid-flight (issue #755).

A cancelled turn is now persisted rather than discarded, which means the saved
history can end on the user's message with no assistant reply -- agent mode
appends its own terminal message, but plain / RAG / tools mode runners only
append on success. Reloading such a conversation and sending another prompt
produces ``user -> user``, which strict-alternation providers reject.

So whatever cancelled the turn, the turn is closed here with one short
assistant message.
"""

import logging
from typing import Any, Dict, Optional

from atlas.domain.messages.models import (
    DISPLAY_ONLY_MESSAGE_TYPES,
    ConversationHistory,
    Message,
    MessageRole,
)

logger = logging.getLogger(__name__)

# Shown in the transcript and replayed to the model on the next turn, where it
# explains why the trajectory ends without a conclusion. Deliberately does not
# name the user as the cause: the same path runs on a client disconnect and on
# reset_session, neither of which is a Stop press.
INTERRUPTED_TURN_CONTENT = "[This turn was stopped before it finished.]"

# Used when the turn has a tool digest attached: the digest is appended to this
# message's content by get_messages_for_llm(), so it really is below. Without a
# digest there is nothing for the model to read -- the tool_call rows and the
# narration are display-only -- so the base text makes no claim about a record.
INTERRUPTED_TURN_CONTENT_WITH_DIGEST = (
    "[This turn was stopped before it finished. A record of the tool calls it "
    "completed follows below.]"
)


def make_interrupted_message(metadata: Optional[Dict[str, Any]] = None) -> Message:
    """Build the terminal assistant message for an interrupted turn."""
    meta = {"interrupted": True}
    if metadata:
        meta.update(metadata)
    return Message(
        role=MessageRole.ASSISTANT,
        content=INTERRUPTED_TURN_CONTENT,
        metadata=meta,
    )


def close_open_turn(history: ConversationHistory) -> bool:
    """Append a terminal assistant message if the turn has no reply yet.

    "Has no reply" is judged against what the model will actually see, so the
    scan walks back past display-only rows (``tool_call`` narration and the
    like). Tools mode flushes its recorded calls before unwinding, leaving
    history ending on a ``tool_call`` row while the last model-visible message
    is still the user's -- exactly the case this exists to catch.

    Returns True when a message was appended. Safe to call on any cancel path:
    a turn already closed by its mode runner (agent mode) is left alone.
    """
    messages = getattr(history, "messages", None)
    if not messages:
        return False
    for message in reversed(messages):
        if message.metadata.get("message_type") in DISPLAY_ONLY_MESSAGE_TYPES:
            continue
        if message.role != MessageRole.USER:
            return False
        break
    else:
        # Nothing but display-only rows: no user turn to close.
        return False
    history.add_message(make_interrupted_message())
    return True
