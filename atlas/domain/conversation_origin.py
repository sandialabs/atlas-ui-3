"""How a conversation was launched (issue #1187).

Most conversations are started by the user in the browser and record nothing
here. A conversation started by a trusted external service through the Agent
Runs REST API (``/api/agent-runs``) lands in the target user's history as an
ordinary top-level conversation; without a durable marker it is
indistinguishable from one the user typed.

The run registry knows a run's launch source only while the run is retained
in memory. The durable half lives on the conversation record, in the same
metadata blob that carries ``workspace_id`` and the parent link:

``{"launch_origin": "agent_runs_api"}``

Three rules keep the marker trustworthy:

* It is server-written. Only the launch path supplies it; no websocket frame
  or REST body can set it.
* It is fixed when the conversation is created. ``carry_recorded_origin`` is
  applied by the repository on every later save, so the user continuing the
  conversation in the browser neither drops the marker nor can any later
  write add one to a conversation that was not launched that way.
* It names the launch surface only. The calling service's identity, its key
  and its request are never part of it.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

ORIGIN_METADATA_KEY = "launch_origin"

# Also the run registry's launch ``source`` for an Agent Runs API run.
ORIGIN_AGENT_RUNS_API = "agent_runs_api"

# Run sources that are published as a conversation origin. ``atlas_launch``
# is deliberately absent: a sub-conversation is identified by its parent link.
PUBLIC_ORIGINS = frozenset({ORIGIN_AGENT_RUNS_API})

__all__ = [
    "ORIGIN_AGENT_RUNS_API",
    "ORIGIN_METADATA_KEY",
    "PUBLIC_ORIGINS",
    "carry_recorded_origin",
    "origin_fields",
    "origin_for_run_source",
    "origin_from_metadata",
]


def origin_for_run_source(source: Any) -> Optional[str]:
    """The conversation origin a run launch source maps to, or ``None``."""
    return source if isinstance(source, str) and source in PUBLIC_ORIGINS else None


def origin_from_metadata(metadata: Any) -> Optional[str]:
    """The recorded launch origin, when the metadata holds a known one.

    Legacy records have no key, and a malformed blob or an unknown value must
    read as "no origin", never as an exception on the history-list path.
    """
    if not isinstance(metadata, dict):
        return None
    return origin_for_run_source(metadata.get(ORIGIN_METADATA_KEY))


def origin_fields(metadata: Any) -> Dict[str, Any]:
    """The origin field a conversation listing exposes to the client."""
    return {ORIGIN_METADATA_KEY: origin_from_metadata(metadata)}


def carry_recorded_origin(stored_metadata: Any, incoming: Dict[str, Any]) -> Dict[str, Any]:
    """``incoming`` with the stored record's origin (or lack of one) applied.

    The origin is a fact about how the conversation was created, so the stored
    record decides it for every later save. Returns a copy.
    """
    result = dict(incoming)
    recorded = origin_from_metadata(stored_metadata)
    if recorded:
        result[ORIGIN_METADATA_KEY] = recorded
    else:
        result.pop(ORIGIN_METADATA_KEY, None)
    return result
