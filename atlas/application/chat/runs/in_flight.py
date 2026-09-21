"""Read a tracked run's conversation before its first save (issue #884 follow-up).

A tracked run persists its conversation only when the turn ends. Until then
the run's own ``Session`` is the only place its transcript exists: the user
prompt, the tool rows it has produced so far, and any completed steps. The
sidebar can list the conversation (it knows the run), but opening it went to
the repository, found nothing, and refused -- so a run started in a new chat
was unreachable from the moment the user navigated away until it finished.

This module answers the "open" from the run's session instead, in the same
shape ``ConversationRepository.get_conversation`` returns, so the existing
route and restore paths can fall back to it without the client learning a
second format. Ownership is enforced by the registry lookup: only the run's
owner resolves the conversation id to a run.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from atlas.application.chat.runs.registry import RunRegistry

TITLE_MAX_CHARS = 200


def _run_message_dicts(session) -> list:
    messages = []
    history = getattr(session, "history", None)
    for index, msg in enumerate(getattr(history, "messages", []) or []):
        data = msg.to_dict()
        metadata = data.get("metadata") or {}
        data["message_type"] = metadata.get("message_type", "chat")
        data["sequence_number"] = index
        messages.append(data)
    return messages


async def in_flight_conversation(
    session_repository,
    run_registry: RunRegistry,
    conversation_id: Optional[str],
    user_email: Optional[str],
) -> Optional[Dict[str, Any]]:
    """The live transcript of an unsaved conversation with an active run.

    Returns ``None`` when there is no active run for this user and
    conversation, or when the run's session cannot be read -- callers treat
    that exactly like a repository miss.
    """
    if not conversation_id or not user_email or session_repository is None:
        return None
    record = run_registry.active_for_conversation(conversation_id, user_email)
    if record is None:
        return None
    try:
        session = await session_repository.get(record.session_id)
    except Exception:  # pragma: no cover - defensive; a miss is a miss
        return None
    if session is None:
        return None

    messages = _run_message_dicts(session)
    title = None
    for msg in messages:
        if msg.get("role") == "user" and msg.get("content"):
            title = str(msg["content"])[:TITLE_MAX_CHARS]
            break

    # What the run is streaming right now (issue #957). Finished segments are
    # already in the messages above; this is the open one, which exists
    # nowhere else until the turn ends. A client that opens the conversation
    # from a tab that will receive no further frames (another tab, a reload)
    # renders it with an "in progress" marker; the socket that owns the run
    # also gets it again as a replay frame on restore, so its continuation
    # appends onto the same text.
    stream_text = record.stream.text()

    return {
        "id": conversation_id,
        "user_email": user_email,
        "title": title,
        "model": None,
        "created_at": None,
        "updated_at": None,
        "message_count": len(messages),
        "metadata": {
            "agent_mode": bool(session.context.get("agent_mode")),
            "workspace_id": session.context.get("workspace_id"),
        },
        "messages": messages,
        "tags": [],
        # Tells a client this is the run's live view, not a stored record.
        "in_flight": True,
        "run_id": record.run_id,
        # The open token segment, for seeding the partial answer on reopen.
        # Absent (not None) when there is nothing open, so a record with
        # nothing streaming looks exactly like the stored shape. A segment
        # longer than the buffer's cap replays its beginning only; the
        # run-end reload replaces the view with the stored transcript either
        # way, so there is nothing for a client to do with a truncation flag
        # that the "in progress" marker does not already say.
        "streaming_text": stream_text or None,
    }
