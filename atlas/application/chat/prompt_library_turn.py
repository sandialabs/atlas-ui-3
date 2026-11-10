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

Whenever a library prompt applies, authorization is checked on that turn. A
refusal fails the turn with one generic message -- it never falls back to a
different version or to the default prompt, so a revoked or stale selection
cannot quietly run something the user did not pick. Preserving history and
permitting new use are separate: a pinned version's text stays in the store
forever, but running it again requires current access.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from atlas.domain.errors import AuthorizationError, ValidationError

logger = logging.getLogger(__name__)

PIN_CONTEXT_KEY = "prompt_library_pin"
PIN_HISTORY_CONTEXT_KEY = "prompt_library_pin_history"
PIN_METADATA_KEY = "prompt_library"
PIN_HISTORY_METADATA_KEY = "prompt_library_history"
MAX_PIN_HISTORY = 50
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
    """Restore a stored conversation's pin into the session (hydrate/restore)."""
    meta = metadata if isinstance(metadata, dict) else {}
    context[PIN_CONTEXT_KEY] = _valid_pin(meta.get(PIN_METADATA_KEY))
    history = meta.get(PIN_HISTORY_METADATA_KEY)
    context[PIN_HISTORY_CONTEXT_KEY] = [h for h in history if isinstance(h, dict)][-MAX_PIN_HISTORY:] if isinstance(history, list) else []


def clear_pin(context: Dict[str, Any]) -> None:
    """The session is leaving its conversation: the pin does not travel."""
    context.pop(PIN_CONTEXT_KEY, None)
    context.pop(PIN_HISTORY_CONTEXT_KEY, None)


def pin_metadata(context: Dict[str, Any]) -> Dict[str, Any]:
    """Metadata entries for ``_save_conversation`` (empty when unpinned)."""
    result: Dict[str, Any] = {}
    pin = _valid_pin(context.get(PIN_CONTEXT_KEY))
    if pin:
        result[PIN_METADATA_KEY] = pin
    history = context.get(PIN_HISTORY_CONTEXT_KEY)
    if isinstance(history, list) and history:
        result[PIN_HISTORY_METADATA_KEY] = history[-MAX_PIN_HISTORY:]
    return result


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
) -> Optional[str]:
    """Return the system-prompt text a library selection contributes, or None.

    ``request`` is the frame value or ``omitted``. ``library`` is the
    ``PromptLibraryService`` (None when the feature is unavailable).
    Raises ``AuthorizationError`` when a library prompt applies but this user
    may not run it now; the caller fails the turn before anything is sent.
    """
    parsed = request if request is omitted else parse_library_request(request)
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
