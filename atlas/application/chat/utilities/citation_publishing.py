"""Emit and persist a turn's citations as it closes (issue #874).

Both turn owners -- ``ToolsModeRunner`` and ``AgentModeRunner`` -- close a turn
the same way: append the closing assistant message, then tell the transport the
response is done. Citations ride along at exactly that moment, so the two steps
live here rather than being written twice.

The split matters:

* **Persisting** them in the closing message's metadata is what makes a
  reloaded conversation render its sources without re-running retrieval, and
  what lets the next turn continue the numbering instead of reusing ``[1]`` for
  a different document.
* **Publishing** them is what puts them on screen for the turn happening now.

A turn that searched nothing does neither, so an ordinary chat message carries
no empty citation list and fires no event.
"""

import logging
from typing import Any, Dict, Optional

from atlas.domain.chat.citation_register import (
    CITATIONS_METADATA_KEY,
    CitationRegister,
)

logger = logging.getLogger(__name__)


def attach_citations(
    metadata: Dict[str, Any], register: Optional[CitationRegister]
) -> Dict[str, Any]:
    """Return ``metadata`` with this turn's citations added, if there are any.

    Copies rather than mutating: the caller's dict is often a literal shared
    with the digest merge a line later, and two in-place merges on one literal
    is the kind of thing that only breaks once someone reorders them.
    """
    if not register:
        return metadata
    entries = register.entries()
    if not entries:
        return metadata
    return {**metadata, CITATIONS_METADATA_KEY: entries}


async def publish_citations(
    event_publisher: Any, register: Optional[CitationRegister]
) -> None:
    """Publish the turn's citations, tolerating a transport that has gone away.

    Called on the closing path -- including the interrupted one, where the
    connection may already be gone. Losing the event must never turn a stopped
    turn into a failed one, and the citations are persisted either way.
    """
    if not register:
        return
    entries = register.entries()
    if not entries:
        return
    publish = getattr(event_publisher, "publish_citations", None)
    if publish is None:
        # An older or partial publisher (a test double, the fine-tune capture
        # recorder). The metadata still carries the citations.
        logger.debug("Event publisher has no publish_citations; skipping %d", len(entries))
        return
    try:
        await publish(entries)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to publish citations: %s", exc)
