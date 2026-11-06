"""Find which replica owns a run, from the shared run record (issue #1128).

Run admission (``RedisRunCoordinator``) writes the run record
``<p>:run:<run_id>`` (a hash: ``owner``, ``generation``, ``status``, ...) and
``<p>:run-conv:<conversation_id>`` when it claims a run, renews both while the
run is live and marks the record terminal on release; see
``docs/developer/run-coordination.md``. Run control only reads them: the
owner's replica id names the inbox a command goes to, and the generation the
sender read travels with the command so the owner can refuse it if the claim
moved on (fencing).

Records are read through the coordinator's own ``read()`` and keys come from
its ``RunCoordinationKeys``, so this module never formats a contract key or
parses the hash itself.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return value.decode()
    return str(value)


def owner_entry(record: Optional[Dict[str, str]], *, include_terminal: bool = False) -> Optional[Dict[str, Any]]:
    """The routing view of a run record, or None unless the run is live.

    A released record is kept for a while with a terminal status and
    ``lease_expires_at`` 0; it names its last owner but nothing should be sent
    there any more, except a stop (``include_terminal``): stopping a finished
    parent still stops the sub-conversations it launched, which run on the
    same replica (#925). A record an orphan sweep took over names no owner and
    is never routed.
    """
    if not record or not record.get("owner"):
        return None
    terminal = record.get("status") in TERMINAL_STATUSES or record.get("lease_expires_at") == "0"
    if terminal and not include_terminal:
        return None
    try:
        generation = int(record.get("generation") or "")
    except ValueError:
        return None
    return {
        "replica": record["owner"],
        "run_id": record.get("run_id"),
        "conversation_id": record.get("conversation_id"),
        "user_email": record.get("user_email"),
        "generation": generation,
        "status": record.get("status"),
        "source": record.get("source") or "",
        "terminal": terminal,
    }


class RunDirectory:
    """Reads the coordinator's run records from the shared Redis."""

    def __init__(self, coordinator: Any, redis_manager: Any):
        self._coordinator = coordinator
        self._redis = redis_manager
        self.keys = coordinator.keys

    async def record(self, run_id: str) -> Optional[Dict[str, str]]:
        """The run record (all fields as text, via ``RunCoordinator.read``), or None."""
        if not run_id:
            return None
        return await self._coordinator.read(run_id)

    async def owner_of(self, run_id: str, *, include_terminal: bool = False) -> Optional[Dict[str, Any]]:
        return owner_entry(await self.record(run_id), include_terminal=include_terminal)

    async def run_for_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """The live run holding ``conversation_id``, whoever owns it."""
        if not conversation_id:
            return None
        run_id = _text(await self._redis.client.get(self.keys.conversation(conversation_id)))
        if not run_id:
            return None
        entry = await self.owner_of(run_id)
        if entry is None or entry.get("conversation_id") != conversation_id:
            return None
        return entry
