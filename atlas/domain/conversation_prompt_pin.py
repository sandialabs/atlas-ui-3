"""Server-managed team-prompt pin in conversation metadata (issue #1169).

``prompt_library`` records the exact library version a conversation runs
with, ``prompt_library_history`` what it ran before. Both are written only
from server state.

``prompt_library_pin_epoch`` makes an explicit clear stick. Clearing a pin
(``DELETE /api/prompts/pins/{conversation_id}``) bumps the epoch under the
conversation's row lock. A session remembers the epoch it last saw, sends it
with every save, and the save -- under the same row lock -- drops any pin
that comes from an older epoch. So a session that was already open when the
pin was cleared cannot write it back, however its turns interleave with the
clear.

``KEEP_STORED_PIN`` is what a session whose pin state is unknown (its
conversation failed to load) saves instead of pin keys: the stored pin,
history and epoch are carried over untouched rather than erased.
"""

from typing import Any, Dict

PIN_METADATA_KEY = "prompt_library"
PIN_HISTORY_METADATA_KEY = "prompt_library_history"
PIN_EPOCH_METADATA_KEY = "prompt_library_pin_epoch"
KEEP_STORED_PIN = "_keep_stored_prompt_pin"

_PIN_KEYS = (PIN_METADATA_KEY, PIN_HISTORY_METADATA_KEY, PIN_EPOCH_METADATA_KEY)


def pin_epoch(metadata: Any) -> int:
    value = metadata.get(PIN_EPOCH_METADATA_KEY) if isinstance(metadata, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def reconcile_prompt_pin(stored_metadata: Any, incoming: Dict[str, Any]) -> Dict[str, Any]:
    """``incoming`` with the pin rules applied against the stored record. Returns a copy.

    Call under the conversation's row lock (see ``ConversationRepository``).
    """
    stored = stored_metadata if isinstance(stored_metadata, dict) else {}
    result = dict(incoming)
    if result.pop(KEEP_STORED_PIN, False):
        for key in _PIN_KEYS:
            result.pop(key, None)
            if key in stored:
                result[key] = stored[key]
        return result
    stored_epoch = pin_epoch(stored)
    if pin_epoch(result) < stored_epoch:
        # The pin was cleared after this session last saw the record.
        result.pop(PIN_METADATA_KEY, None)
    if PIN_HISTORY_METADATA_KEY not in result and PIN_HISTORY_METADATA_KEY in stored:
        result[PIN_HISTORY_METADATA_KEY] = stored[PIN_HISTORY_METADATA_KEY]
    epoch = max(stored_epoch, pin_epoch(result))
    if epoch:
        result[PIN_EPOCH_METADATA_KEY] = epoch
    else:
        # Never cleared: no key, so records without a pin stay as they were.
        result.pop(PIN_EPOCH_METADATA_KEY, None)
    return result
