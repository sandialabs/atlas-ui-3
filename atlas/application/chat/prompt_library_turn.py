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

Pin state is the stored record's, not the session's. Clearing a pin bumps a
pin epoch in the record (see ``atlas.domain.conversation_prompt_pin``); every
turn on a pinned conversation, every turn that selects a library entry, and
every turn whose pin state is unknown re-reads the record's metadata first and
adopts the stored pin when the epoch moved. A session that was open when the
pin was cleared therefore runs unpinned from its next turn, and its saves
cannot write the old pin back. When a conversation fails to load the pin state
is *unknown*, not "unpinned": the turn is refused until the record can be read,
so a failed load never silently swaps a pinned prompt for the default (with or
without compliance enforcement).

Whenever a library prompt applies, authorization is checked on that turn. A
refusal fails the turn with one generic message -- it never falls back to a
different version or to the default prompt, so a revoked or stale selection
cannot quietly run something the user did not pick. Preserving history and
permitting new use are separate: a pinned version's text stays in the store
forever, but running it again requires current access.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from atlas.domain.conversation_prompt_pin import (
    KEEP_STORED_PIN,
    PIN_EPOCH_METADATA_KEY,
    PIN_HISTORY_METADATA_KEY,
    PIN_METADATA_KEY,
    pin_epoch,
)
from atlas.domain.errors import AuthorizationError, ValidationError

logger = logging.getLogger(__name__)

PIN_CONTEXT_KEY = "prompt_library_pin"
PIN_HISTORY_CONTEXT_KEY = "prompt_library_pin_history"
PIN_EPOCH_CONTEXT_KEY = "prompt_library_pin_epoch"
# Set when the conversation's stored pin could not be read (failed load).
PIN_UNKNOWN_CONTEXT_KEY = "prompt_library_pin_unknown"
PIN_STATE_CONTEXT_KEYS = (
    PIN_CONTEXT_KEY,
    PIN_HISTORY_CONTEXT_KEY,
    PIN_EPOCH_CONTEXT_KEY,
    PIN_UNKNOWN_CONTEXT_KEY,
)
MAX_PIN_HISTORY = 50

CONFLICTING_PROMPTS_MESSAGE = (
    "Choose either a team library prompt or a custom prompt, not both."
)
PIN_STATE_UNAVAILABLE_MESSAGE = (
    "This conversation's prompt settings could not be loaded right now, so the "
    "turn was not run. Try again shortly."
)

# Reads the conversation's stored metadata: a dict, or None when the store has
# no such conversation for this user. Raises when the store cannot answer.
MetadataLoader = Callable[[], Awaitable[Optional[Dict[str, Any]]]]


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


def _valid_pin(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    if not isinstance(value.get("prompt_id"), str) or not isinstance(value.get("version_id"), str):
        return None
    return {
        "prompt_id": value["prompt_id"],
        "version_id": value["version_id"],
        "version_number": value.get("version_number"),
        "title": value.get("title"),
    }


def load_pin_from_metadata(context: Dict[str, Any], metadata: Any) -> None:
    """Restore a stored conversation's pin into the session (hydrate/restore/refresh)."""
    meta = metadata if isinstance(metadata, dict) else {}
    context[PIN_CONTEXT_KEY] = _valid_pin(meta.get(PIN_METADATA_KEY))
    history = meta.get(PIN_HISTORY_METADATA_KEY)
    context[PIN_HISTORY_CONTEXT_KEY] = [h for h in history if isinstance(h, dict)][-MAX_PIN_HISTORY:] if isinstance(history, list) else []
    context[PIN_EPOCH_CONTEXT_KEY] = pin_epoch(meta)
    context.pop(PIN_UNKNOWN_CONTEXT_KEY, None)


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
    """Metadata entries for ``_save_conversation``.

    With the pin state unknown, asks the repository to keep whatever is
    stored rather than writing an absent pin over it.
    """
    if context.get(PIN_UNKNOWN_CONTEXT_KEY):
        return {KEEP_STORED_PIN: True}
    result: Dict[str, Any] = {}
    epoch = pin_epoch({PIN_EPOCH_METADATA_KEY: context.get(PIN_EPOCH_CONTEXT_KEY)})
    if epoch:
        result[PIN_EPOCH_METADATA_KEY] = epoch
    pin = _valid_pin(context.get(PIN_CONTEXT_KEY))
    if pin:
        result[PIN_METADATA_KEY] = pin
    history = context.get(PIN_HISTORY_CONTEXT_KEY)
    if isinstance(history, list) and history:
        result[PIN_HISTORY_METADATA_KEY] = history[-MAX_PIN_HISTORY:]
    return result


async def _refresh_pin_state(context: Dict[str, Any], load_metadata: MetadataLoader) -> None:
    """Adopt the stored pin when it changed under this session.

    Raises ``AuthorizationError`` when the store cannot answer: the turn
    must not run on a pin state that may be stale or unknown.
    """
    try:
        stored = await load_metadata()
    except Exception as e:
        logger.warning("Could not read the conversation's prompt pin (%s)", type(e).__name__)
        raise AuthorizationError(PIN_STATE_UNAVAILABLE_MESSAGE)
    if stored is None:
        # The store has no record yet (a new conversation): nothing stored
        # can be lost, and the session's own state stands.
        if context.get(PIN_UNKNOWN_CONTEXT_KEY):
            load_pin_from_metadata(context, {})
        return
    stored_epoch = pin_epoch(stored)
    session_epoch = pin_epoch({PIN_EPOCH_METADATA_KEY: context.get(PIN_EPOCH_CONTEXT_KEY)})
    if context.get(PIN_UNKNOWN_CONTEXT_KEY) or stored_epoch != session_epoch:
        load_pin_from_metadata(context, stored)


def _record_pin(context: Dict[str, Any], version: Dict[str, Any], message_index: int) -> None:
    pin = {
        "prompt_id": version["prompt_id"],
        "version_id": version["id"],
        "version_number": version["version_number"],
        "title": version["title"],
    }
    context[PIN_CONTEXT_KEY] = pin
    history: List[Dict[str, Any]] = list(context.get(PIN_HISTORY_CONTEXT_KEY) or [])
    history.append({
        **pin,
        "pinned_at": datetime.now(timezone.utc).isoformat(),
        # Messages already in the conversation when this version took effect.
        "from_message_index": message_index,
    })
    context[PIN_HISTORY_CONTEXT_KEY] = history[-MAX_PIN_HISTORY:]


def _unpin(context: Dict[str, Any], message_index: int) -> None:
    if context.get(PIN_CONTEXT_KEY):
        history: List[Dict[str, Any]] = list(context.get(PIN_HISTORY_CONTEXT_KEY) or [])
        history.append({
            "prompt_id": None,
            "version_id": None,
            "pinned_at": datetime.now(timezone.utc).isoformat(),
            "from_message_index": message_index,
        })
        context[PIN_HISTORY_CONTEXT_KEY] = history[-MAX_PIN_HISTORY:]
    context[PIN_CONTEXT_KEY] = None


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
) -> Optional[str]:
    """Return the system-prompt text a library selection contributes, or None.

    ``request`` is the frame value or ``omitted``. ``library`` is the
    ``PromptLibraryService`` (None when the feature is unavailable).
    ``load_metadata`` reads the conversation's stored metadata (None when the
    conversation is not persisted).
    Raises ``ValidationError`` when the turn names a library entry and another
    prompt, and ``AuthorizationError`` when a library prompt applies but this
    user may not run it now or the pin state cannot be read; the caller fails
    the turn before anything is sent.
    """
    parsed = request if request is omitted else parse_library_request(request)
    if isinstance(parsed, tuple) and other_prompt_selected:
        # Rejected before any lookup: nothing was attempted, so there is no
        # use or denial to audit. Nothing client-supplied is logged.
        logger.warning("Rejected a chat turn naming both a team prompt and another prompt")
        raise ValidationError(CONFLICTING_PROMPTS_MESSAGE)

    unknown = bool(context.get(PIN_UNKNOWN_CONTEXT_KEY))
    if unknown and load_metadata is None:
        raise AuthorizationError(PIN_STATE_UNAVAILABLE_MESSAGE)
    if load_metadata is not None and (
        unknown or _valid_pin(context.get(PIN_CONTEXT_KEY)) or isinstance(parsed, tuple)
    ):
        await _refresh_pin_state(context, load_metadata)
    current = _valid_pin(context.get(PIN_CONTEXT_KEY))

    if parsed is None:
        _unpin(context, message_index)
        return None

    from_pin_only = parsed is omitted
    if parsed is omitted:
        if current is None:
            return None
        if other_prompt_selected:
            _unpin(context, message_index)
            return None
        prompt_id, version_id, explicit = current["prompt_id"], current["version_id"], True
    else:
        prompt_id, version_id = parsed
        explicit = version_id is not None
        if not explicit and current and current["prompt_id"] == prompt_id:
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
        _record_pin(context, version, message_index)
        try:
            await library.record_use(user_email, version, context.get("conversation_id"))
        except Exception:  # pragma: no cover - audit failure is logged inside
            logger.warning("Could not record prompt-library use")
    return version["content"]
