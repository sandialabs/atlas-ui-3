"""Server-managed team-prompt pin in conversation metadata (issue #1169).

Stored keys (written only from server state):

``prompt_library``
    The exact library version the conversation runs with (absent: none).
``prompt_library_history``
    What it ran before: one entry per change, newest last, capped.
``prompt_library_pin_revision``
    Counts every change to the pin -- a set, a re-pin to another version, an
    explicit clear (``library_prompt: null`` or another prompt chosen) and an
    owner's ``DELETE /api/prompts/pins/{conversation_id}``.

Conflict-resolution semantics
-----------------------------
The pin is changed only by a compare-and-set against the revision the change
was decided on, under the conversation's row lock (``change_prompt_pin``).
A conversation *save* never writes the pin of an existing conversation: it
carries the stored pin, history and revision through, whatever the saving
session holds. Together:

* Two sessions changing the pin at once: both read revision N; the first to
  take the row lock applies its change (N+1); the other's compare-and-set
  fails, the turn is refused with a conflict, and that session adopts the
  stored state. The user sees the newer selection and can send again.
* A session that never saw the current pin (an older tab, a reconnected
  client, a run started earlier) cannot overwrite or remove it by saving:
  saves do not carry pin changes.
* A turn that would *change* the pin (select another entry or version, or
  clear) is checked against the revision the client last saw (the frame's
  ``library_prompt_revision`` when sent, else the session's). If the stored
  revision moved since, the change is refused rather than applied blindly.
  A turn that keeps the stored pin (omitted field, or the same selection)
  simply follows the stored state.
* The owner's DELETE is unconditional: it is the explicit way out when a
  pinned prompt became unavailable.

A conversation the store does not have yet (its first turn) keeps the pin in
the session; the save that creates the record writes it. Running a pinned
version always requires current access; see ``prompt_library_turn``.
"""

from typing import Any, Dict, List, Optional, Tuple

from atlas.domain.errors import ValidationError

PIN_METADATA_KEY = "prompt_library"
PIN_HISTORY_METADATA_KEY = "prompt_library_history"
PIN_REVISION_METADATA_KEY = "prompt_library_pin_revision"
# Written by an earlier draft of this feature; read as the revision.
_LEGACY_EPOCH_KEY = "prompt_library_pin_epoch"
PIN_KEYS = (PIN_METADATA_KEY, PIN_HISTORY_METADATA_KEY, PIN_REVISION_METADATA_KEY, _LEGACY_EPOCH_KEY)
MAX_PIN_HISTORY = 50

PIN_CONFLICT_CODE = "prompt_pin_conflict"
PIN_CONFLICT_MESSAGE = (
    "This conversation's team prompt was changed in another window or session. "
    "Check the selected prompt and send your message again."
)


class PromptPinConflictError(ValidationError):
    """A pin change was decided on a revision that is no longer current.

    ``pin_state`` is the stored state the caller should adopt (pin, revision).
    """

    def __init__(self, pin_state: Dict[str, Any]):
        super().__init__(PIN_CONFLICT_MESSAGE, code=PIN_CONFLICT_CODE)
        self.pin_state = pin_state


def _revision_value(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def pin_revision(metadata: Any) -> int:
    """The stored pin revision (0 for a conversation whose pin never changed)."""
    if not isinstance(metadata, dict):
        return 0
    for key in (PIN_REVISION_METADATA_KEY, _LEGACY_EPOCH_KEY):
        value = _revision_value(metadata.get(key))
        if value is not None:
            return value
    return 0


def _nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def valid_pin(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    if not _nonblank(value.get("prompt_id")) or not _nonblank(value.get("version_id")):
        return None
    return {
        "prompt_id": value["prompt_id"],
        "version_id": value["version_id"],
        "version_number": value.get("version_number"),
        "title": value.get("title"),
    }


def pin_fields_readable(metadata: Dict[str, Any]) -> bool:
    """Whether the stored pin and pin revision are well formed.

    The rest of this module reads them leniently (a malformed pin as "no pin",
    a malformed revision as 0). That is right for values this module wrote,
    but a stored record carrying a malformed one has been damaged: reading it
    as "no pin" would silently run the default prompt in a pinned
    conversation, and reading the revision as 0 would let a stale change pass
    the compare-and-set. So the repository treats such a record as unreadable
    (``ConversationMetadataUnreadableError``), exactly like invalid JSON.

    Absent keys and ``prompt_library: null`` are legitimate (no pin).
    """
    pin = metadata.get(PIN_METADATA_KEY)
    if pin is not None and valid_pin(pin) is None:
        return False
    for key in (PIN_REVISION_METADATA_KEY, _LEGACY_EPOCH_KEY):
        if key in metadata and _revision_value(metadata[key]) is None:
            return False
    return True


def pin_history(metadata: Any) -> List[Dict[str, Any]]:
    history = metadata.get(PIN_HISTORY_METADATA_KEY) if isinstance(metadata, dict) else None
    if not isinstance(history, list):
        return []
    return [h for h in history if isinstance(h, dict)][-MAX_PIN_HISTORY:]


def pin_state(metadata: Any) -> Dict[str, Any]:
    """The pin part of stored metadata, normalized."""
    return {
        "pin": valid_pin(metadata.get(PIN_METADATA_KEY)) if isinstance(metadata, dict) else None,
        "history": pin_history(metadata),
        "revision": pin_revision(metadata),
    }


def public_pin_fields(state: Dict[str, Any]) -> Dict[str, Any]:
    """What a client sees of a pin state (frames, error payloads)."""
    return {
        PIN_METADATA_KEY: state.get("pin"),
        PIN_REVISION_METADATA_KEY: state.get("revision", 0),
    }


def _write_state(metadata: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    result = {k: v for k, v in metadata.items() if k not in PIN_KEYS}
    if state.get("pin"):
        result[PIN_METADATA_KEY] = state["pin"]
    if state.get("history"):
        result[PIN_HISTORY_METADATA_KEY] = state["history"][-MAX_PIN_HISTORY:]
    if state.get("revision"):
        result[PIN_REVISION_METADATA_KEY] = state["revision"]
    return result


def carry_stored_pin(stored_metadata: Any, incoming: Dict[str, Any]) -> Dict[str, Any]:
    """Metadata for saving an *existing* conversation: the stored pin wins.

    Whatever pin keys the saving session sent are dropped; pin changes go
    through ``apply_pin_change`` only. Call under the conversation's row lock.
    """
    return _write_state(incoming, pin_state(stored_metadata))


def new_record_pin(incoming: Dict[str, Any]) -> Dict[str, Any]:
    """Metadata for *creating* a conversation: the creating session's pin."""
    return _write_state(incoming, pin_state(incoming))


def apply_pin_change(
    stored_metadata: Dict[str, Any],
    expected_revision: Optional[int],
    pin: Optional[Dict[str, Any]],
    history_entry: Optional[Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    """Compare-and-set a pin change. Returns ``(status, metadata)``.

    ``status`` is ``"ok"`` (applied; revision + 1), ``"unchanged"`` (the
    stored pin already is ``pin``; nothing written) or ``"conflict"``
    (``expected_revision`` is stale; the stored metadata is returned as is).
    ``expected_revision=None`` applies unconditionally (owner DELETE).
    """
    state = pin_state(stored_metadata)
    if expected_revision is not None and state["revision"] != expected_revision:
        return "conflict", stored_metadata
    current = state["pin"]
    target = valid_pin(pin)
    if (current or {}).get("version_id") == (target or {}).get("version_id") and bool(current) == bool(target):
        return "unchanged", stored_metadata
    history = state["history"]
    if history_entry is not None:
        history = (history + [history_entry])[-MAX_PIN_HISTORY:]
    new_state = {"pin": target, "history": history, "revision": state["revision"] + 1}
    return "ok", _write_state(stored_metadata, new_state)
