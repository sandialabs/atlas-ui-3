"""Team-prompt pin storage on a conversation record (issue #1169).

A mixin of ``ConversationRepository``: the pin lives in the conversation's
metadata blob, so it is read and changed through the same rows and sessions,
under the same row lock as saves (``_owned_conversation``). Kept apart from
the repository so the rest of the conversation store stays as it was before
the team prompt library; see ``atlas.domain.conversation_prompt_pin`` for the
rules and ``conversation_prompt_pin_markers`` for legacy compatibility.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from atlas.core.user_identity import normalize_user_email
from atlas.domain.conversation_prompt_pin import (
    PIN_METADATA_KEY,
    apply_pin_change,
    pin_fields_readable,
    pin_state,
    recover_unreadable_metadata,
)
from atlas.domain.errors import ConversationMetadataUnreadableError

from .models import ConversationPromptPinMarker, ConversationRecord

logger = logging.getLogger(__name__)


class ConversationPinStore:
    """Pin reads and compare-and-set writes for ``ConversationRepository``."""

    def get_conversation_metadata(
        self, conversation_id: str, user_email: str
    ) -> Optional[Dict[str, Any]]:
        """The metadata of one of the user's conversations, without its messages.

        Returns ``{}`` for a conversation with no metadata and None when the
        user owns no such conversation. Raises
        ``ConversationMetadataUnreadableError`` when the stored blob is not a
        JSON object: an unreadable record must never read as "no pin"
        (issue #1169). Active sessions use it to follow pin changes.
        """
        user_email = normalize_user_email(user_email)
        with self._get_session() as session:
            row = session.query(ConversationRecord.metadata_json).filter(
                ConversationRecord.id == conversation_id,
                ConversationRecord.user_email == user_email,
            ).first()
            if row is None:
                return None
            return _readable_metadata(row[0])

    def change_prompt_pin(
        self,
        conversation_id: str,
        user_email: str,
        expected_revision: Optional[int],
        pin: Optional[Dict[str, Any]],
        history_entry: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Compare-and-set a conversation's team-prompt pin (issue #1169).

        Under the row lock (``SELECT ... FOR UPDATE`` on PostgreSQL; DuckDB
        fails a conflicting concurrent commit instead): applies ``pin``
        (None clears it) when the stored pin revision equals
        ``expected_revision`` (None: unconditionally), appending
        ``history_entry`` and bumping the revision. Returns
        ``{"status": "ok" | "unchanged" | "conflict", "state": <stored pin
        state after the call>}``, or None when the user owns no such
        conversation. Raises ``ConversationMetadataUnreadableError`` for an
        unreadable blob, which is left untouched.
        """
        user_email = normalize_user_email(user_email)
        with self._get_session() as session:
            conv = self._owned_conversation(session, conversation_id, user_email)
            if not conv:
                return None
            current = _readable_metadata(conv.metadata_json)
            status, updated = apply_pin_change(current, expected_revision, pin, history_entry)
            if status == "ok":
                conv.metadata_json = json.dumps(updated)
                if pin_state(updated)["pin"]:
                    self._mark_pinned(session, conversation_id, user_email)
                session.commit()
            return {"status": status, "state": pin_state(updated)}

    @staticmethod
    def _mark_pinned(session: Session, conversation_id: str, user_email: str) -> None:
        """Record, in the caller's transaction, that the conversation has a pin."""
        if session.get(ConversationPromptPinMarker, conversation_id) is None:
            session.add(ConversationPromptPinMarker(conversation_id=conversation_id, user_email=user_email))

    def conversation_was_pinned(self, conversation_id: str, user_email: str) -> Optional[bool]:
        """Whether one of the user's conversations has, or had, a team-prompt pin.

        None when the user owns no such conversation. Read from the marker
        table, not the metadata blob, so it answers even when the blob cannot
        be read (issue #1169): ``False`` then proves there is no pin to lose.
        """
        user_email = normalize_user_email(user_email)
        with self._get_session() as session:
            owned = session.query(ConversationRecord.metadata_json).filter(
                ConversationRecord.id == conversation_id,
                ConversationRecord.user_email == user_email,
            ).first()
            if owned is None:
                return None
            if session.get(ConversationPromptPinMarker, conversation_id) is not None:
                return True
            # Belt and braces, the same rule as the migration's backfill: a
            # blob that mentions the library at all may hold a pin, whatever
            # the marker table says.
            return "prompt_library" in (owned[0] or "")

    def clear_prompt_pin(
        self, conversation_id: str, user_email: str
    ) -> Optional[Dict[str, Any]]:
        """Clear a conversation's pinned team-library prompt (owner DELETE).

        Unconditional (no expected revision), under the row lock, bumping the
        pin revision so every session sees the change on its next turn. The
        pin history is kept. Returns ``{"prompt_library": <removed pin>}``
        (empty when none was stored), or None when the user owns no such
        conversation; ``{"recovered": True}`` after recovering an unreadable
        record (see ``clear_prompt_pin_checked``).
        """
        result = self.clear_prompt_pin_checked(conversation_id, user_email)
        if result is None:
            return None
        if result.get("recovered"):
            return {"recovered": True}
        return {PIN_METADATA_KEY: result["removed"]} if result.get("removed") else {}

    def clear_prompt_pin_checked(
        self, conversation_id: str, user_email: str, expected_revision: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """``clear_prompt_pin`` with the details the route reports.

        Returns None when the user owns no such conversation, else a dict:
        ``removed`` (the removed pin or None), ``revision`` (the pin revision
        now stored, or None when it cannot be read), ``recovered`` and
        ``classification_unknown`` (an unreadable record was recovered; with
        nothing readable in it, its classification stays unknown), or
        ``conflict: True`` when ``expected_revision`` was given and the stored
        revision moved past it (nothing changed: the pin was changed since the
        caller looked).

        A record whose pin state is unreadable (invalid JSON, not an object,
        or a malformed pin or revision) refuses every turn and save, so this
        explicit owner action is how it is recovered: under the same lock,
        ``recover_unreadable_metadata`` resets the pin state without guessing
        a pin and keeps everything else (see there). A never-pinned record is
        left exactly as it is. Nothing from a damaged pin is audited or
        logged: it cannot be trusted.
        """
        user_email = normalize_user_email(user_email)
        with self._get_session() as session:
            conv = self._owned_conversation(session, conversation_id, user_email)
            if not conv:
                return None
            try:
                current = _readable_metadata(conv.metadata_json)
            except ConversationMetadataUnreadableError:
                if (
                    session.get(ConversationPromptPinMarker, conv.id) is None
                    and "prompt_library" not in (conv.metadata_json or "")
                ):
                    # Never pinned: there is no pin to clear, and the record is
                    # left exactly as it is (as before the library existed).
                    return {"removed": None, "revision": None}
                now = datetime.now(timezone.utc)
                recovered = recover_unreadable_metadata(conv.metadata_json, int(now.timestamp()), now.isoformat())
                conv.metadata_json = json.dumps(recovered)
                session.commit()
                # No identifiers or content: the owner's request is the audit.
                logger.warning("An owner reset a conversation's unreadable team-prompt state")
                return {
                    "removed": None,
                    "recovered": True,
                    "revision": pin_state(recovered)["revision"],
                    "classification_unknown": recovered.get("prompt_library_recovery", {}).get("reason")
                    == "unreadable_metadata",
                }
            state = pin_state(current)
            if expected_revision is not None and state["revision"] != expected_revision:
                return {"removed": None, "revision": state["revision"], "conflict": True,
                        "pin": state.get("pin")}
            status, updated = apply_pin_change(current, None, None, None)
            if status == "ok":
                conv.metadata_json = json.dumps(updated)
                session.commit()
            return {"removed": state["pin"], "revision": pin_state(updated)["revision"]}

def _readable_metadata(metadata_json: Optional[str]) -> Dict[str, Any]:
    """Stored metadata as a dict; ``{}`` only for genuinely empty metadata.

    A blob that is not valid JSON, is JSON but not an object, or carries a
    malformed team-prompt pin or pin revision (``pin_fields_readable``) raises
    ``ConversationMetadataUnreadableError`` (the message names no content).
    """
    if not metadata_json:
        return {}
    try:
        value = json.loads(metadata_json)
    except (json.JSONDecodeError, TypeError):
        raise ConversationMetadataUnreadableError() from None
    if value is None:
        return {}
    if not isinstance(value, dict) or not pin_fields_readable(value):
        raise ConversationMetadataUnreadableError()
    return value
