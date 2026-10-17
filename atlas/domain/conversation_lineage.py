"""Parent-child conversation lineage (issue #1081).

``atlas_launch`` starts a sub-conversation as a new conversation owned by the
same user. The run registry knows that relationship while the runs are retained
in memory, but the sidebar has to keep drawing the tree after the registry
reaps a terminal run or the process restarts. The durable half of the
relationship therefore lives on the *child's* conversation record, in its
metadata:

``{"parent_conversation_id": "<parent conversation id>"}``

That is the same metadata blob that already carries ``workspace_id`` and the
server-managed classification record. It is server-written: the launch path is
the only producer, and a websocket ``chat`` frame cannot set it.

Two rules matter for privacy and robustness, and both are enforced where the
data leaves the server rather than only in the UI:

* A parent link is only published when the parent conversation is owned by the
  same user. A dangling, deleted, foreign or filtered parent is simply omitted,
  so the child renders as an ordinary top-level conversation instead of
  revealing another user's id or title.
* The link is an id only. Titles, models and classification are never copied
  from the parent into the child's listing, so the existing per-conversation
  authorization and classification checks keep deciding what the user sees.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

PARENT_CONVERSATION_METADATA_KEY = "parent_conversation_id"

__all__ = [
    "PARENT_CONVERSATION_METADATA_KEY",
    "lineage_fields",
    "parent_conversation_id_from_metadata",
]


def parent_conversation_id_from_metadata(metadata: Any) -> Optional[str]:
    """The recorded parent conversation id, when the metadata holds one.

    Values are defensively narrowed to a non-empty string. Legacy records have
    no key at all, and a malformed blob must read as "no parent", never as an
    exception on the history-list path.
    """
    if not isinstance(metadata, dict):
        return None
    value = metadata.get(PARENT_CONVERSATION_METADATA_KEY)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def lineage_fields(metadata: Any, *, visible_parent_id: Optional[str]) -> Dict[str, Any]:
    """The lineage field a listing may expose.

    ``visible_parent_id`` is the parent id the caller has already checked for
    ownership. Passing ``None`` drops the link entirely, which is what makes a
    missing or inaccessible parent look like an ordinary root conversation.
    """
    recorded = parent_conversation_id_from_metadata(metadata)
    if not recorded or recorded != visible_parent_id:
        return {"parent_conversation_id": None}
    return {"parent_conversation_id": recorded}


def iter_recorded_links(conversations: Iterable[Dict[str, Any]]) -> Iterable[tuple[str, str]]:
    """Yield ``(child_id, recorded_parent_id)`` from listing-shaped records.

    Accepts both a full record with a ``metadata`` object and a row that already
    carries ``parent_conversation_id`` at the top level, so the repository and
    the tests can use the same helper.
    """
    for conversation in conversations:
        child_id = conversation.get("id")
        metadata = conversation.get("metadata")
        if metadata is None:
            metadata = conversation
        parent_id = parent_conversation_id_from_metadata(metadata)
        if isinstance(child_id, str) and child_id and parent_id and parent_id != child_id:
            yield child_id, parent_id
