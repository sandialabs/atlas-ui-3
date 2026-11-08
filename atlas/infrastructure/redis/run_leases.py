"""Redis scripts for run leases and fencing (issue #1128).

They act on the run record of ``run_coordinator.py`` (``<p>:run:<run_id>``,
built by :class:`RunCoordinationKeys`) and never create a record of their
own. The contract is in ``docs/developer/run-coordination.md``.

* ``INTERRUPT`` takes over a run whose lease expired by the Redis clock (its
  owner can no longer renew it): a new generation from ``<p>:run-fence``, an
  empty owner, ``status=failed`` with ``interrupted=1``, and its slots freed.
  The run stays in ``runs-live`` until the sweeper closed its execution
  projection (``forget``), so that close survives a failure or a dead sweeper;
  each retry is scheduled behind the runs due now and keeps the record alive.
  Every write the old owner still attempts then fails the owner/generation
  check of the claim, renew and release scripts and of ``MIRROR``.
* ``MIRROR`` writes the owner's public snapshot of the run (and its launch
  ``source``) into the record, fenced by owner, generation and, while the run
  is live, an unexpired lease.
"""

from __future__ import annotations

import time
from typing import Any, List, Optional

from atlas.application.chat.runs.coordination import RunClaim
from atlas.infrastructure.redis.run_coordinator import _NOW, _SUBTREE, RunCoordinationKeys

# KEYS: run, conv, user, fence, live
# ARGV: run_id, error, interrupted_by, retention_ms, retry_ms, lease_ms, key_prefix
INTERRUPT_SCRIPT = _NOW + _SUBTREE + """
local status = redis.call('HGET', KEYS[1], 'status')
if (not status) or status == 'completed' or status == 'failed' or status == 'cancelled' then
  -- Taken over earlier and still in runs-live, so its projection is not
  -- closed yet: the caller closes it (idempotent) and then forgets the run.
  -- Once forgotten, a late takeover attempt (the resumed old owner's) is 0.
  if status and redis.call('HGET', KEYS[1], 'interrupted') == '1'
      and redis.call('ZSCORE', KEYS[5], ARGV[1]) then
    -- Each attempt moves the run behind the orphans due now (a close that
    -- keeps failing cannot starve them) and keeps the record, so the
    -- obligation to close cannot expire before the close is confirmed.
    redis.call('ZADD', KEYS[5], now + tonumber(ARGV[5]), ARGV[1])
    redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[4]))
    return -1
  end
  redis.call('ZREM', KEYS[5], ARGV[1])
  return 0
end
local expires = tonumber(redis.call('HGET', KEYS[1], 'lease_expires_at') or '0')
if expires > now then return 0 end
local generation = redis.call('INCR', KEYS[4])
-- The same floor as the claim script: Redis time in microseconds, so a
-- takeover after a flush still outranks every earlier generation.
local now_us = tonumber(t[1]) * 1000000 + tonumber(t[2])
if generation < now_us then
  redis.call('SET', KEYS[4], string.format('%d', now_us))
  generation = now_us
end
redis.call('HSET', KEYS[1], 'status', 'failed', 'interrupted', '1', 'error', ARGV[2],
  'owner', '', 'interrupted_by', ARGV[3], 'generation', generation,
  'lease_expires_at', 0, 'updated_at', now)
end_run(ARGV[7], ARGV[1], tonumber(ARGV[4]), tonumber(ARGV[6]))
if redis.call('GET', KEYS[2]) == ARGV[1] then redis.call('DEL', KEYS[2]) end
redis.call('ZREM', KEYS[3], ARGV[1])
-- Kept in runs-live until the caller closed the projection, so a failed
-- close is retried by a later sweep on any replica, after the runs due now.
redis.call('ZADD', KEYS[5], now + tonumber(ARGV[5]), ARGV[1])
return generation
"""

# KEYS: run
# ARGV: owner, generation, status, snapshot_json, error, source
MIRROR_SCRIPT = _NOW + """
if redis.call('HGET', KEYS[1], 'generation') ~= ARGV[2] then return 0 end
if redis.call('HGET', KEYS[1], 'owner') ~= ARGV[1] then return 0 end
local current = redis.call('HGET', KEYS[1], 'status')
local ended = ARGV[3] == 'completed' or ARGV[3] == 'failed' or ARGV[3] == 'cancelled'
if current == 'completed' or current == 'failed' or current == 'cancelled' then
  -- Once the record is terminal, a snapshot of a still-live state is older
  -- than the one that ended it: dropped, not an error.
  if not ended then return 1 end
else
  -- Like renew: a live run whose lease has passed is no longer ours, even
  -- before a sweep has recorded the takeover.
  local expires = tonumber(redis.call('HGET', KEYS[1], 'lease_expires_at') or '0')
  if expires <= now then return 0 end
  redis.call('HSET', KEYS[1], 'status', ARGV[3])
end
redis.call('HSET', KEYS[1], 'snapshot', ARGV[4], 'error', ARGV[5], 'source', ARGV[6],
  'updated_at', now)
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
        self._retention_ms = int(getattr(coordinator, "_retention_ms"))
        self._lease_ms = int(getattr(coordinator, "_lease_ms"))
        self._prefix = coordinator._prefix
        # A failed projection close is retried after this long.
        self._retry_ms = max(1, int(self.lease_seconds * 1000 / 3))
        client = self._redis.client
        self._interrupt = client.register_script(INTERRUPT_SCRIPT)
        self._mirror = client.register_script(MIRROR_SCRIPT)

    async def interrupt(
        self, run_id: str, conversation_id: str, user_email: str, error: str
    ) -> Optional[int]:
        """Take over a run whose lease expired. Returns the new generation;
        ``-1`` for a run taken over earlier whose projection is still to be
        closed; ``None`` when the run was still leased, ended, or unknown."""
        generation = await self._interrupt(
            keys=[
                self.keys.run(run_id),
                self.keys.conversation(conversation_id),
                self.keys.user(user_email),
                self.keys.fence(),
                self.keys.live(),
            ],
            args=[run_id, error, self.replica_id, self._retention_ms, self._retry_ms,
                  self._lease_ms, self._prefix],
        )
        return int(generation) or None

    async def mirror(
        self, claim: RunClaim, status: str, snapshot: str, error: str = "", source: str = ""
    ) -> bool:
        """Write the run's public snapshot, and its launch ``source`` as a
        field of its own, if ``claim`` still owns the record and its lease."""
        written = await self._mirror(
            keys=[self.keys.run(claim.run_id)],
            args=[claim.owner, str(claim.generation), status, snapshot, error or "", source or ""],
        )
        return bool(int(written))

    async def forget(self, run_id: str) -> None:
        """Drop an interrupted run from ``runs-live`` once its projection is closed."""
        await self._redis.client.zrem(self.keys.live(), run_id)

    async def lapsed_candidates(self, limit: int = 200) -> List[str]:
        """Run ids whose lease deadline in ``runs-live`` has passed.

        Scored with the Redis clock, compared here with the local one: only a
        hint for the sweep, which re-checks the lease inside the takeover.
        """
        raw = await self._redis.client.zrangebyscore(
            self.keys.live(), "-inf", int(time.time() * 1000), start=0, num=limit
        )
        return [_text(member) for member in raw or []]
