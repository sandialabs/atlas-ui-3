"""Redis scripts for run leases and fencing (issue #1128).

They act on the run record of ``run_coordinator.py`` (``<p>:run:<run_id>``,
built by :class:`RunCoordinationKeys`) and never create a record of their
own. The contract is in ``docs/developer/run-coordination.md``.

* ``INTERRUPT`` takes over a run whose lease expired by the Redis clock (its
  owner can no longer renew it): a new generation from ``<p>:run-fence``, an
  empty owner, ``status=failed`` with ``interrupted=1``, and its slots freed.
  Every write the old owner still attempts then fails the owner/generation
  check of the claim, renew and release scripts and of ``MIRROR``.
* ``MIRROR`` writes the owner's public snapshot of the run into the record,
  fenced by owner and generation.
"""

from __future__ import annotations

import time
from typing import Any, List, Optional

from atlas.application.chat.runs.coordination import RunClaim
from atlas.infrastructure.redis.run_coordinator import _NOW, RunCoordinationKeys

# KEYS: run, conv, user, fence, live
# ARGV: run_id, error, interrupted_by, retention_ms
INTERRUPT_SCRIPT = _NOW + """
local status = redis.call('HGET', KEYS[1], 'status')
if (not status) or status == 'completed' or status == 'failed' or status == 'cancelled' then
  redis.call('ZREM', KEYS[5], ARGV[1])
  return 0
end
local expires = tonumber(redis.call('HGET', KEYS[1], 'lease_expires_at') or '0')
if expires > now then return 0 end
local generation = redis.call('INCR', KEYS[4])
redis.call('HSET', KEYS[1], 'status', 'failed', 'interrupted', '1', 'error', ARGV[2],
  'owner', '', 'interrupted_by', ARGV[3], 'generation', generation,
  'lease_expires_at', 0, 'updated_at', now)
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[4]))
if redis.call('GET', KEYS[2]) == ARGV[1] then redis.call('DEL', KEYS[2]) end
redis.call('ZREM', KEYS[3], ARGV[1])
redis.call('ZREM', KEYS[5], ARGV[1])
return generation
"""

# KEYS: run
# ARGV: owner, generation, status, snapshot_json, error
MIRROR_SCRIPT = _NOW + """
if redis.call('HGET', KEYS[1], 'generation') ~= ARGV[2] then return 0 end
if redis.call('HGET', KEYS[1], 'owner') ~= ARGV[1] then return 0 end
local current = redis.call('HGET', KEYS[1], 'status')
if current ~= 'completed' and current ~= 'failed' and current ~= 'cancelled' then
  redis.call('HSET', KEYS[1], 'status', ARGV[3])
end
redis.call('HSET', KEYS[1], 'snapshot', ARGV[4], 'error', ARGV[5], 'updated_at', now)
return 1
"""


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, (bytes, bytearray)) else str(value)


class RedisRunLeases:
    """Takeover, snapshot mirror and the live index, for one ``RedisRunCoordinator``."""

    def __init__(self, coordinator: Any):
        self._coordinator = coordinator
        self._redis = coordinator._redis
        self.keys: RunCoordinationKeys = coordinator.keys
        self.replica_id: str = coordinator.replica_id
        self.lease_seconds = float(getattr(coordinator, "lease_seconds", 30.0))
        self._retention_ms = int(getattr(coordinator, "_retention_ms", 300_000))
        client = self._redis.client
        self._interrupt = client.register_script(INTERRUPT_SCRIPT)
        self._mirror = client.register_script(MIRROR_SCRIPT)

    async def interrupt(
        self, run_id: str, conversation_id: str, user_email: str, error: str
    ) -> Optional[int]:
        """Take over a run whose lease expired. Returns the new generation, or
        ``None`` when the run was still leased, already ended, or unknown."""
        generation = await self._interrupt(
            keys=[
                self.keys.run(run_id),
                self.keys.conversation(conversation_id),
                self.keys.user(user_email),
                self.keys.fence(),
                self.keys.live(),
            ],
            args=[run_id, error, self.replica_id, self._retention_ms],
        )
        return int(generation) or None

    async def mirror(self, claim: RunClaim, status: str, snapshot: str, error: str = "") -> bool:
        """Write the run's public snapshot if ``claim`` still owns the record."""
        written = await self._mirror(
            keys=[self.keys.run(claim.run_id)],
            args=[claim.owner, str(claim.generation), status, snapshot, error or ""],
        )
        return bool(int(written))

    async def lapsed_candidates(self, limit: int = 200) -> List[str]:
        """Run ids whose lease deadline in ``runs-live`` has passed.

        Scored with the Redis clock, compared here with the local one: only a
        hint for the sweep, which re-checks the lease inside the takeover.
        """
        raw = await self._redis.client.zrangebyscore(
            self.keys.live(), "-inf", int(time.time() * 1000), start=0, num=limit
        )
        return [_text(member) for member in raw or []]
