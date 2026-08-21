"""Load persisted conversation messages back into a live session's history.

Two callers need this and must agree exactly:

* ``handle_restore_conversation`` -- the user picked a conversation out of the
  sidebar.
* ``handle_chat_message`` -- a turn arrived naming a conversation this session
  is not already carrying (a reconnect, most often), so the server rehydrates
  it rather than running the turn against an empty history.

Keeping one implementation matters because the second path is what makes
persistence independent of the client: if the two drifted, a rehydrated turn
could be saved back in a different shape than a restored one and quietly
rewrite the stored record.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.domain.messages.models import ConversationHistory, Message, MessageRole

logger = logging.getLogger(__name__)


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """Best-effort ISO-8601 parse; ``None`` when absent or unparseable.

    A message whose timestamp cannot be read is still worth loading -- the
    content is the point -- so the caller falls back to ``Message``'s default
    (now) rather than dropping the row.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def load_messages_into_history(
    history: ConversationHistory,
    messages: List[Dict[str, Any]],
    conversation_id: str,
) -> int:
    """Append persisted ``messages`` to ``history``. Returns the count loaded.

    Rows with an unrecognised role are skipped rather than failing the load: a
    single bad row must not cost the user the rest of the conversation.
    """
    loaded = 0
    for msg_data in messages:
        role_value = msg_data.get("role", "user") or "user"
        try:
            message_role = MessageRole(role_value)
        except ValueError:
            logger.warning(
                "Skipping message with invalid role %s in conversation %s",
                sanitize_for_logging(str(role_value)),
                sanitize_for_logging(conversation_id),
            )
            continue

        # Preserve metadata so display-only rows (e.g. persisted tool_call
        # messages, issue #684) keep their ``message_type`` and are excluded
        # from get_messages_for_llm rather than replayed as orphan tool
        # messages the provider would reject. It also carries the agent tool
        # digest (issue #755) forward, so a rehydrated conversation still shows
        # the model what its earlier turns did.
        metadata = msg_data.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        # Defense-in-depth: some persisted shapes carry ``message_type`` at the
        # top level only (e.g. the local IndexedDB autosave, which keeps it as a
        # sibling of ``metadata`` rather than inside it). Fold it in so
        # display-only rows are reliably excluded from get_messages_for_llm
        # instead of being replayed as orphan ``tool`` messages.
        if "message_type" not in metadata and msg_data.get("message_type"):
            metadata = {**metadata, "message_type": msg_data["message_type"]}

        # Carry the original timestamp through. Rebuilding without it restamps
        # every message with the load time, and because a later save rewrites
        # the whole row set, that restamped value is what gets persisted -- so
        # each load/save cycle destroyed the real message times.
        timestamp = _parse_timestamp(msg_data.get("timestamp"))
        extra = {"timestamp": timestamp} if timestamp is not None else {}

        history.add_message(
            Message(
                role=message_role,
                content=msg_data.get("content", ""),
                metadata=metadata,
                **extra,
            )
        )
        loaded += 1

    return loaded
