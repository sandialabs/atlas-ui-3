"""Conversation-level data classification (issue #1042).

A saved conversation carries the classification it was created under, and it
may only be continued under that same classification. Without this, history
written in one compliance level could be reloaded days later under another one
and sent to a model or tool server that is approved only for the second level
-- the per-turn component check (classification_policy, issue #1032) looks at
the components, not at where the history came from.

The record
    ``metadata["data_classification"]`` on the stored conversation. It is
    written by the server from the turn's validated active level when the
    conversation is created, never from a client field, and never changes
    afterwards: the repository refuses a save that would alter or drop it.
    A JSON ``null`` records an explicitly unclassified conversation (created
    with no level active); a missing key marks a legacy conversation saved
    before the record existed.

The rule
    The active classification of a turn must equal the conversation's
    recorded classification (canonical names, after alias resolution).
    Levels have no ordering in compliance-levels.json, so there is no safe
    "higher" or "lower"; a conversation is never moved to another level,
    silently or otherwise. To work at another level, start a new conversation.

Fail closed
    - Legacy (no record): usable only while compliance levels are disabled.
      With enforcement on its provenance is unknown, so it is not assumed to
      be any level; it stays listed and exportable, but cannot be opened into
      the chat or resumed. An operator who knows what such conversations hold can stamp
      them explicitly (scripts/stamp_conversation_classification.py).
    - A recorded level the deployment no longer defines, or a malformed
      record, matches nothing.
    - A classified conversation cannot be continued while compliance levels
      are disabled (there is no active level to match).
"""

import logging
from typing import Any, Dict, Optional

from atlas.core.log_sanitizer import sanitize_for_logging

logger = logging.getLogger(__name__)

# Key in the stored conversation metadata.
CLASSIFICATION_METADATA_KEY = "data_classification"
# Key in ``session.context`` for the binding of the history the session holds.
SESSION_BINDING_KEY = "conversation_classification"

STATE_CLASSIFIED = "classified"
STATE_UNCLASSIFIED = "unclassified"
STATE_LEGACY = "legacy"
STATE_INVALID = "invalid"

# Error code carried on the ValidationError so transports can tell this
# refusal apart from other validation failures.
ERROR_CODE = "conversation_classification"


def make_binding(state: str, level: Optional[str] = None) -> Dict[str, Any]:
    return {"state": state, "level": level if state == STATE_CLASSIFIED else None}


def binding_for_new_conversation(active_level: Optional[str]) -> Dict[str, Any]:
    """The binding a conversation gets from the turn that creates it."""
    if active_level:
        return make_binding(STATE_CLASSIFIED, active_level)
    return make_binding(STATE_UNCLASSIFIED)


def binding_from_metadata(metadata: Any) -> Dict[str, Any]:
    """The binding recorded on a stored (or in-flight) conversation.

    Only a readable metadata object without the key is legacy. No metadata at
    all (``None``) is legacy too; anything else that is not an object --
    corrupt or malformed metadata -- may have held a record, so it is invalid
    and matches nothing.
    """
    if metadata is None:
        return make_binding(STATE_LEGACY)
    if not isinstance(metadata, dict):
        return make_binding(STATE_INVALID)
    if CLASSIFICATION_METADATA_KEY not in metadata:
        return make_binding(STATE_LEGACY)
    value = metadata[CLASSIFICATION_METADATA_KEY]
    if value is None:
        return make_binding(STATE_UNCLASSIFIED)
    if isinstance(value, str) and value.strip():
        return make_binding(STATE_CLASSIFIED, value.strip())
    return make_binding(STATE_INVALID)


def binding_from_record(record: Any) -> Dict[str, Any]:
    """The binding of a conversation record as the repository returns it.

    The record's top-level ``data_classification_state`` is preferred: the
    repository computes it from the raw stored metadata, so it still reports
    corrupt metadata as invalid where the decoded ``metadata`` dict cannot.
    """
    if isinstance(record, dict) and isinstance(record.get("data_classification_state"), str):
        return normalize_binding({
            "state": record["data_classification_state"],
            "level": record.get("data_classification"),
        })
    return binding_from_metadata(record.get("metadata") if isinstance(record, dict) else None)


def normalize_binding(binding: Any) -> Dict[str, Any]:
    """A well-formed binding; anything unreadable is invalid (fails closed)."""
    if not isinstance(binding, dict):
        return make_binding(STATE_INVALID)
    state = binding.get("state")
    if state == STATE_CLASSIFIED:
        level = binding.get("level")
        if isinstance(level, str) and level:
            return make_binding(STATE_CLASSIFIED, level)
        return make_binding(STATE_INVALID)
    if state in (STATE_UNCLASSIFIED, STATE_LEGACY, STATE_INVALID):
        return make_binding(state)
    return make_binding(STATE_INVALID)


def metadata_value(binding: Any) -> Any:
    """What to store under ``CLASSIFICATION_METADATA_KEY``, or ``...`` for nothing.

    Legacy and invalid bindings are never written: the server does not know
    what that history holds, so it must not stamp a classification on it.
    """
    binding = normalize_binding(binding)
    if binding["state"] == STATE_CLASSIFIED:
        return binding["level"]
    if binding["state"] == STATE_UNCLASSIFIED:
        return None
    return ...


def _canonical(compliance_mgr: Any, level: Optional[str]) -> Optional[str]:
    if not level:
        return None
    canonical = compliance_mgr.get_canonical_name(level)
    if canonical:
        return canonical
    # Permissive mode (no definitions loaded) keeps names as given.
    return None if compliance_mgr.levels else level


def describe(binding: Any) -> str:
    """A short, content-free description for messages and the UI."""
    binding = normalize_binding(binding)
    if binding["state"] == STATE_CLASSIFIED:
        return binding["level"]
    if binding["state"] == STATE_UNCLASSIFIED:
        return "no compliance level"
    if binding["state"] == STATE_LEGACY:
        return "an unrecorded compliance level"
    return "an unreadable compliance level"


def resume_refusal(
    binding: Any,
    active_level: Optional[str],
    *,
    compliance_enabled: bool,
    compliance_mgr: Any,
) -> Optional[str]:
    """Why the conversation may not continue at ``active_level``, or None.

    The message names only classifications, never conversation content.
    """
    binding = normalize_binding(binding)
    state = binding["state"]
    active_label = active_level or "no compliance level"

    if state == STATE_LEGACY:
        if not compliance_enabled:
            return None
        return (
            "This conversation was saved before compliance levels were recorded "
            "for conversations, so it cannot be continued while compliance levels "
            "are enforced. It stays in your history and in conversation exports; "
            "start a new conversation to keep working."
        )
    if state == STATE_INVALID:
        return (
            "This conversation's recorded compliance level could not be read, so "
            "it cannot be continued. Start a new conversation."
        )
    if state == STATE_UNCLASSIFIED:
        if not active_level:
            return None
        return (
            f"This conversation was saved with no compliance level and cannot be "
            f"continued under {active_label}. Start a new conversation at "
            f"{active_label}."
        )

    # Classified.
    recorded = binding["level"]
    if not compliance_enabled:
        return (
            f"This conversation was saved under {recorded}, and compliance levels "
            "are not enabled, so it cannot be continued."
        )
    recorded_canonical = _canonical(compliance_mgr, recorded)
    if recorded_canonical is None:
        return (
            f"This conversation was saved under {recorded}, which is no longer a "
            "defined compliance level, so it cannot be continued."
        )
    if recorded_canonical == _canonical(compliance_mgr, active_level):
        return None
    return (
        f"This conversation was saved under {recorded_canonical} and cannot be "
        f"continued under {active_label}. Switch the compliance level to "
        f"{recorded_canonical} to continue it, or start a new conversation."
    )


def audit_refusal(
    action: str,
    conversation_id: Optional[str],
    user_email: Optional[str],
    binding: Any,
    active_level: Optional[str],
) -> None:
    """Record a refused cross-classification attempt (no conversation content)."""
    binding = normalize_binding(binding)
    logger.warning(
        "Refused %s of conversation %s for user %s: recorded classification "
        "state=%s level=%s, active level=%s",
        action,
        sanitize_for_logging(str(conversation_id)),
        sanitize_for_logging(str(user_email)),
        binding["state"],
        sanitize_for_logging(str(binding["level"])),
        sanitize_for_logging(str(active_level)),
    )


def public_fields(metadata: Any) -> Dict[str, Any]:
    """The classification fields a conversation listing exposes to the client."""
    binding = binding_from_metadata(metadata)
    return {
        "data_classification": binding["level"],
        "data_classification_state": binding["state"],
    }
