"""Server-side resolution of a team-library prompt for one chat turn (issue #1169).

Private custom prompts (issue #153) travel inline as ``custom_system_prompt``:
the text is the user's own. A library entry is someone else's content shared
under an access policy, so its text never comes from the client. The chat
frame carries only a reference::

    "library_prompt": {"prompt_id": "...", "version_id": "..." | null}

and this module turns it into text after re-checking, against the identity the
server authenticated, that the user may run that version *now*.

Pinning. The version a conversation ran with is recorded in its metadata
(``prompt_library``) together with a short history of changes
(``prompt_library_history``). Both are written only from server state, never
from a client field. Rules, per turn:

* ``library_prompt`` names an entry with an explicit ``version_id``: that exact
  version runs (if authorized) and is pinned.
* ``library_prompt`` names an entry with no ``version_id`` ("latest"): a
  conversation already pinned to that entry keeps its pinned version -- a later
  publication never silently changes a conversation in progress. A conversation
  without a pin for that entry pins the current latest.
* ``library_prompt`` is ``null``: the user chose a non-library prompt; the pin
  is cleared and the other prompt (inline/persona/default) applies.
* ``library_prompt`` omitted (older clients, the CLI, follow-up and API runs):
  a pinned conversation keeps running its pinned version, unless the turn
  explicitly carries another prompt (inline custom prompt or persona), which
  unpins. If the pinned version is no longer available, the turn is refused
  with a message naming the explicit way out -- ``DELETE
  /api/prompts/pins/{conversation_id}`` (owner only, audited) -- because these
  clients cannot pick another prompt per turn. After the pin is cleared, the
  next run or connection continues with the default prompt.

A turn never combines prompts: a frame naming a library entry *and* an inline
custom prompt or persona is rejected before anything is looked up (no use or
denial is audited -- nothing was attempted).

Pin state is the stored record's, not the session's. Every pin change -- a
set, a re-pin, an explicit clear (``library_prompt: null`` or another prompt
chosen while pinned) -- is a compare-and-set on the stored pin revision under
the conversation's row lock, made before the turn runs; saves never write the
pin. A change decided on a revision that is no longer current (the frame's
``library_prompt_revision``, else the session's) is refused with a conflict
and the session adopts the stored state. Turns that keep the pin re-read the
stored record first and follow it. The full rules are in
``atlas.domain.conversation_prompt_pin``.

When a conversation fails to load, or its stored metadata is unreadable, the
pin state is *unknown*, not "unpinned": the turn is refused until the record
can be read, so a failed load never silently swaps a pinned prompt for the
default (with or without compliance enforcement).

Whenever a library prompt applies, authorization is checked on that turn. A
refusal fails the turn with one generic message -- it never falls back to a
different version or to the default prompt, so a revoked or stale selection
cannot quietly run something the user did not pick. Preserving history and
permitting new use are separate: a pinned version's text stays in the store
forever, but running it again requires current access.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from atlas.domain.conversation_prompt_pin import (
    MAX_PIN_HISTORY,
    PIN_HISTORY_METADATA_KEY,
    PIN_METADATA_KEY,
    PIN_REVISION_METADATA_KEY,
    PromptPinConflictError,
    pin_state,
    valid_pin,
)
from atlas.domain.errors import AuthorizationError, ValidationError

logger = logging.getLogger(__name__)

PIN_CONTEXT_KEY = "prompt_library_pin"
PIN_HISTORY_CONTEXT_KEY = "prompt_library_pin_history"
PIN_REVISION_CONTEXT_KEY = "prompt_library_pin_revision"
# Set when the conversation's stored pin could not be read (failed load).
PIN_UNKNOWN_CONTEXT_KEY = "prompt_library_pin_unknown"
PIN_STATE_CONTEXT_KEYS = (
    PIN_CONTEXT_KEY,
    PIN_HISTORY_CONTEXT_KEY,
    PIN_REVISION_CONTEXT_KEY,
    PIN_UNKNOWN_CONTEXT_KEY,
)

CONFLICTING_PROMPTS_MESSAGE = (
    "Choose either a team library prompt or a custom prompt, not both."
)
PIN_STATE_UNAVAILABLE_MESSAGE = (
    "This conversation's prompt settings could not be loaded right now, so the "
    "turn was not run. Try again shortly."
)

# Reads the conversation's stored metadata: a dict, or None when the store has
# no such conversation for this user. Raises when the store cannot answer or
# the stored metadata is unreadable.
MetadataLoader = Callable[[], Awaitable[Optional[Dict[str, Any]]]]
# Compare-and-sets the stored pin: (expected_revision, pin_or_None,
# history_entry) -> {"status", "state"}, or None when the conversation is not
# stored yet. Raises when the store cannot answer.
PinWriter = Callable[[int, Optional[Dict[str, Any]], Dict[str, Any]], Awaitable[Optional[Dict[str, Any]]]]


def parse_observed_revision(raw: Any) -> Optional[int]:
    """The frame's ``library_prompt_revision``: the pin revision the client saw."""
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ValidationError("Malformed library_prompt_revision")
    return raw


def frame_has_conflicting_prompts(library_prompt: Any, custom_system_prompt: Any, persona_id: Any, omitted: Any) -> bool:
    """Whether a chat frame names a library entry *and* another prompt.

    Checked on the raw frame, before the custom prompt or persona is resolved
    (a disabled custom-prompt feature would otherwise drop one side quietly).
    An explicit ``library_prompt: null`` is a clear, not a selection.
    """
    if library_prompt is omitted or library_prompt is None:
        return False
    has_custom = isinstance(custom_system_prompt, str) and bool(custom_system_prompt.strip())
    has_persona = persona_id is not None and persona_id != ""
    return has_custom or has_persona


_MAX_ID_LEN = 64


def parse_library_request(raw: Any) -> Any:
    """Validate the frame's ``library_prompt`` field.

    Returns the caller's "omitted" sentinel unchanged when it was passed
    through untouched, ``None`` for an explicit clear, or a
    ``(prompt_id, version_id_or_None)`` tuple. Raises ``ValidationError`` for
    anything else, so a malformed reference fails the turn rather than being
    ignored.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValidationError("Malformed library_prompt reference")
    prompt_id = raw.get("prompt_id")
    version_id = raw.get("version_id")
    if not isinstance(prompt_id, str) or not prompt_id.strip() or len(prompt_id) > _MAX_ID_LEN:
        raise ValidationError("Malformed library_prompt reference")
    if version_id is not None and (
        not isinstance(version_id, str) or not version_id.strip() or len(version_id) > _MAX_ID_LEN
    ):
        raise ValidationError("Malformed library_prompt reference")
    return prompt_id.strip(), (version_id.strip() if version_id else None)


_valid_pin = valid_pin


def _apply_state(context: Dict[str, Any], state: Dict[str, Any]) -> None:
    context[PIN_CONTEXT_KEY] = state.get("pin")
    context[PIN_HISTORY_CONTEXT_KEY] = list(state.get("history") or [])[-MAX_PIN_HISTORY:]
    context[PIN_REVISION_CONTEXT_KEY] = state.get("revision", 0)
    context.pop(PIN_UNKNOWN_CONTEXT_KEY, None)


def session_revision(context: Dict[str, Any]) -> int:
    value = context.get(PIN_REVISION_CONTEXT_KEY)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def load_pin_from_metadata(context: Dict[str, Any], metadata: Any) -> None:
    """Restore a stored conversation's pin into the session (hydrate/restore/refresh)."""
    _apply_state(context, pin_state(metadata))


def clear_pin(context: Dict[str, Any]) -> None:
    """The session is leaving its conversation: the pin does not travel."""
    for key in PIN_STATE_CONTEXT_KEYS:
        context.pop(key, None)


def mark_pin_unknown(context: Dict[str, Any]) -> None:
    """The conversation failed to load: its pin is unknown, not absent."""
    clear_pin(context)
    context[PIN_UNKNOWN_CONTEXT_KEY] = True


def save_pin_state(context: Dict[str, Any]) -> Dict[str, Any]:
    """Snapshot of the session's pin state (restored after a failed reload)."""
    return {key: context[key] for key in PIN_STATE_CONTEXT_KEYS if key in context}


def pin_metadata(context: Dict[str, Any]) -> Dict[str, Any]:
    """Pin entries for ``_save_conversation`` and live (in-flight) views.

    A save only uses them when it *creates* the conversation; an existing
    record keeps its stored pin (see ``conversation_prompt_pin``). Empty when
    the pin state is unknown.
    """
    if context.get(PIN_UNKNOWN_CONTEXT_KEY):
        return {}
    result: Dict[str, Any] = {}
    pin = _valid_pin(context.get(PIN_CONTEXT_KEY))
    if pin:
        result[PIN_METADATA_KEY] = pin
    history = context.get(PIN_HISTORY_CONTEXT_KEY)
    if isinstance(history, list) and history:
        result[PIN_HISTORY_METADATA_KEY] = history[-MAX_PIN_HISTORY:]
    revision = session_revision(context)
    if revision:
        result[PIN_REVISION_METADATA_KEY] = revision
    return result


def public_pin_state(context: Dict[str, Any]) -> Dict[str, Any]:
    """Pin fields for frames to the client (empty when unknown)."""
    if context.get(PIN_UNKNOWN_CONTEXT_KEY):
        return {}
    return {
        PIN_METADATA_KEY: _valid_pin(context.get(PIN_CONTEXT_KEY)),
        PIN_REVISION_METADATA_KEY: session_revision(context),
    }


async def _refresh_pin_state(context: Dict[str, Any], load_metadata: MetadataLoader) -> bool:
    """Adopt the stored pin state. Returns whether the conversation is stored.

    Raises ``AuthorizationError`` when the store cannot answer or its
    metadata is unreadable: the turn must not run on a pin state that may be
    stale or unknown.
    """
    try:
        stored = await load_metadata()
    except Exception as e:
        logger.warning("Could not read the conversation's prompt pin (%s)", type(e).__name__)
        raise AuthorizationError(PIN_STATE_UNAVAILABLE_MESSAGE)
    if stored is None:
        if context.get(PIN_UNKNOWN_CONTEXT_KEY):
            # The full load of this conversation failed, so "no record" from
            # the light read is not proof the conversation is new. Refuse
            # rather than run the default prompt; the next turn retries the
            # full load (ChatService), which settles it either way -- a
            # genuinely new conversation then proceeds normally.
            raise AuthorizationError(PIN_STATE_UNAVAILABLE_MESSAGE)
        # The store has no record yet (a new conversation): nothing stored
        # can be lost, and the session's own (pending) state stands.
        return False
    load_pin_from_metadata(context, stored)
    return True


def _history_entry(pin: Optional[Dict[str, Any]], message_index: int) -> Dict[str, Any]:
    entry = dict(pin) if pin else {"prompt_id": None, "version_id": None}
    entry["pinned_at"] = datetime.now(timezone.utc).isoformat()
    # Messages already in the conversation when this change took effect.
    entry["from_message_index"] = message_index
    return entry


async def _change_pin(
    context: Dict[str, Any],
    target: Optional[Dict[str, Any]],
    message_index: int,
    observed: int,
    store_pin: Optional[PinWriter],
    stored: bool = False,
) -> None:
    """Compare-and-set the pin to ``target`` (None clears it); see the module docs.

    ``observed`` is the revision the change was decided on. ``stored`` says
    the conversation has a stored record (from the refresh). A stored
    conversation whose turn cannot write the pin (an off-record turn, or a
    repository without pin writes) changes nothing: the turn runs its
    selection, and neither the record nor the session's view of it moves --
    recording it only in the session would make the next turn, which re-reads
    the record, see a revision it never observed. Raises
    ``PromptPinConflictError`` (after adopting the stored state) when the
    stored revision moved, ``AuthorizationError`` when the store cannot
    answer.
    """
    if stored and store_pin is None:
        return
    current_revision = session_revision(context)
    if stored and observed != current_revision:
        # The client (or this session) decided on an older pin than the one
        # stored: refuse rather than overwrite what it never saw. Only a
        # stored conversation has a pin another window could have changed; an
        # unsaved chat (browser-only, or before its first save) is this
        # session's alone, and a record created meanwhile by another session
        # is still caught by the compare-and-set below.
        raise PromptPinConflictError({"pin": _valid_pin(context.get(PIN_CONTEXT_KEY)), "revision": current_revision})
    entry = _history_entry(target, message_index)
    result = None
    if store_pin is not None:
        try:
            result = await store_pin(current_revision, target, entry)
        except Exception as e:
            logger.warning("Could not store the conversation's prompt pin (%s)", type(e).__name__)
            raise AuthorizationError(PIN_STATE_UNAVAILABLE_MESSAGE)
    if result is None:
        # Not stored yet (first turn of a new conversation, or a turn that is
        # never persisted): the change lives in the session, and the save
        # that creates the record writes it.
        history = list(context.get(PIN_HISTORY_CONTEXT_KEY) or [])
        _apply_state(context, {
            "pin": target,
            "history": (history + [entry])[-MAX_PIN_HISTORY:],
            "revision": current_revision + 1,
        })
        return
    _apply_state(context, result["state"])
    if result["status"] == "conflict":
        logger.info("Refused a stale team-prompt pin change (revision moved)")
        raise PromptPinConflictError(result["state"])


def _refusal_message(context: Dict[str, Any], from_pin_only: bool, default: Optional[str]) -> str:
    """The refusal a user sees. Never names the prompt or why it is gone."""
    if from_pin_only:
        conversation_id = context.get("conversation_id") or "<conversation id>"
        return (
            "This conversation is pinned to a team prompt that is no longer available "
            "to you. Choose another prompt, or clear the conversation's team prompt "
            f"(DELETE /api/prompts/pins/{conversation_id}) to continue with the default prompt."
        )
    return default or "The selected team prompt is not available. Choose another prompt to continue."


async def resolve_library_prompt_for_turn(
    *,
    context: Dict[str, Any],
    user_email: Optional[str],
    request: Any,
    other_prompt_selected: bool,
    library: Any,
    message_index: int,
    omitted: Any,
    load_metadata: Optional[MetadataLoader] = None,
    store_pin: Optional[PinWriter] = None,
    observed_revision: Any = None,
) -> Optional[str]:
    """Return the system-prompt text a library selection contributes, or None.

    ``request`` is the frame value or ``omitted``. ``library`` is the
    ``PromptLibraryService`` (None when the feature is unavailable).
    ``load_metadata`` reads, and ``store_pin`` compare-and-sets, the
    conversation's stored pin (both None when the conversation is not
    persisted). ``observed_revision`` is the frame's
    ``library_prompt_revision`` (None: use the session's).
    Raises ``ValidationError`` for a malformed or conflicting request
    (``PromptPinConflictError`` when the pin changed since the client saw
    it), and ``AuthorizationError`` when a library prompt applies but this
    user may not run it now or the pin state cannot be read; the caller fails
    the turn before anything is sent.
    """
    parsed = request if request is omitted else parse_library_request(request)
    observed = parse_observed_revision(observed_revision)
    if isinstance(parsed, tuple) and other_prompt_selected:
        # Rejected before any lookup: nothing was attempted, so there is no
        # use or denial to audit. Nothing client-supplied is logged.
        logger.warning("Rejected a chat turn naming both a team prompt and another prompt")
        raise ValidationError(CONFLICTING_PROMPTS_MESSAGE)

    unknown = bool(context.get(PIN_UNKNOWN_CONTEXT_KEY))
    if unknown and load_metadata is None:
        raise AuthorizationError(PIN_STATE_UNAVAILABLE_MESSAGE)
    seen_revision = observed if observed is not None else session_revision(context)
    stored = False
    if load_metadata is not None:
        # Every turn of a stored conversation follows the stored pin: a
        # session that never saw a pin set elsewhere must not run the
        # default prompt in a pinned conversation (one owner-filtered read).
        stored = await _refresh_pin_state(context, load_metadata)
    current = _valid_pin(context.get(PIN_CONTEXT_KEY))

    clearing = parsed is None or (parsed is omitted and other_prompt_selected)
    if clearing:
        if current is not None:
            await _change_pin(context, None, message_index, seen_revision, store_pin, stored)
        return None

    from_pin_only = parsed is omitted
    if parsed is omitted:
        if current is None:
            return None
        prompt_id, version_id = current["prompt_id"], current["version_id"]
    else:
        prompt_id, version_id = parsed
        if version_id is None and current and current["prompt_id"] == prompt_id:
            # "Latest" in a conversation already running this entry keeps the
            # version it pinned.
            version_id = current["version_id"]

    if library is None or not user_email:
        logger.warning("Library prompt requested but the prompt library is unavailable")
        raise AuthorizationError(_refusal_message(context, from_pin_only, None))

    from atlas.modules.prompts.prompt_library_policy import PromptLibraryError, PromptLibraryUnavailable

    try:
        version = await library.resolve_for_execution(user_email, prompt_id, version_id)
    except PromptLibraryUnavailable as e:
        # Storage outage: say so, and keep the pin -- the prompt may well
        # still be the user's to run once the database is back.
        raise AuthorizationError(e.message)
    except PromptLibraryError as e:
        # Nothing client-supplied is logged: the prompt id came from the frame.
        logger.warning("Refused a team-library prompt for this turn (%s)", type(e).__name__)
        raise AuthorizationError(_refusal_message(context, from_pin_only, e.message))

    if current is None or current["version_id"] != version["id"]:
        target = {
            "prompt_id": version["prompt_id"],
            "version_id": version["id"],
            "version_number": version["version_number"],
            "title": version["title"],
        }
        await _change_pin(context, target, message_index, seen_revision, store_pin, stored)
        try:
            await library.record_use(user_email, version, context.get("conversation_id"))
        except Exception:  # pragma: no cover - audit failure is logged inside
            logger.warning("Could not record prompt-library use")
    return version["content"]
