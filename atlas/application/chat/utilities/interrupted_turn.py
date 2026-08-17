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
    AGENT_TOOL_DIGEST_KEY,
    DISPLAY_ONLY_MESSAGE_TYPES,
    ConversationHistory,
    Message,
    MessageRole,
)

logger = logging.getLogger(__name__)

# Shown in the transcript and replayed to the model on the next turn, where it
# explains why the trajectory ends without a conclusion. Deliberately does not
# name the user as the cause: the same path runs on a client disconnect and on
# reset_session, neither of which is a Stop press. When a tool digest is
# attached it is folded into this message's content by get_messages_for_llm();
# the digest's own header already announces the record, so the base text does
# not need to claim one follows.
INTERRUPTED_TURN_CONTENT = "[This turn was stopped before it finished.]"


def make_interrupted_message(metadata: Optional[Dict[str, Any]] = None) -> Message:
    """Build the terminal assistant message for an interrupted turn.

    ``metadata`` may carry an :data:`AGENT_TOOL_DIGEST_KEY` digest, which
    ``get_messages_for_llm`` folds into this message's content on the next
    turn. The digest's own header labels the record, so the content stays the
    same whether or not a digest is attached.
    """
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

    When the turn left display-only ``tool_call`` rows behind, a digest of
    those calls rides on the terminal message -- the same carrier agent mode
    uses (issue #755). Without it a stopped tools-mode turn's tool work would
    be invisible to the next request, which is the defect #798 describes for
    the default path.
    """
    messages = getattr(history, "messages", None)
    if not messages:
        return False
    turn_start_index: Optional[int] = None
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.metadata.get("message_type") in DISPLAY_ONLY_MESSAGE_TYPES:
            continue
        if message.role != MessageRole.USER:
            return False
        # +1: the user message is the turn boundary; everything after it is
        # this turn's tool calls (if any).
        turn_start_index = index + 1
        break
    else:
        # Nothing but display-only rows: no user turn to close.
        return False

    digest = None
    if turn_start_index < len(messages):
        try:
            from atlas.application.chat.utilities.agent_digest import build_tool_digest

            digest = build_tool_digest(messages, turn_start_index)
        except Exception:  # pragma: no cover - defensive
            logger.warning(
                "Failed to build interrupted-turn tool digest", exc_info=True,
            )

    history.add_message(
        make_interrupted_message(
            metadata={AGENT_TOOL_DIGEST_KEY: digest} if digest else None,
        )
    )
    return True
