"""Redis-backed run admission for multi-replica deployments (issue #1128).

Every replica claims a run here before it starts one. The claim is a single
Lua script, so the three checks it makes -- is the conversation free, is the
user under the cap, and the writes that take the slot -- happen as one step in
Redis no matter how many replicas race for the same user.

Key layout (``<p>`` is ``REDIS_KEY_PREFIX``; variable parts are escaped with
:func:`key_part`). This is the shared contract the rest of #1128 builds on and
is documented in ``docs/developer/run-coordination.md``:

``<p>:run:<run_id>`` (hash)
    The live run record: ``run_id``, ``conversation_id``, ``user_email``,
    ``owner`` (replica id), ``generation`` (fencing token), ``status``,
    ``lease_expires_at`` (epoch ms, 0 once released), ``created_at``,
    ``updated_at``, and the routing fields known at admission: ``source``,
    ``parent_run_id``, ``follow_up_of`` (empty when absent). Kept for the
    retention window after release, and for as long as a descendant is live.
``<p>:run-kids:<run_id>`` (set)
    The run ids launched by ``run_id``; how the scripts find live descendants.
``<p>:run-conv:<conversation_id>`` (string, PX lease)
    The run id holding the conversation. One per conversation, across all
    users: conversation ids are global in storage, so two users must not
    both run (and then both try to save) the same unsaved id.
``<p>:run-user:<user>`` (sorted set)
    The user's claimed run ids, scored by lease expiry (epoch ms). Members
    whose lease has passed are pruned before every count, so a replica that
    died holding slots frees them within one lease.
``<p>:run-fence`` (string counter)
    Source of fencing generations. One counter, only ever incremented (by
    claims and by takeovers of a lost run, ``run_leases.py``), and floored at
    the Redis clock in microseconds so it keeps increasing across a flush.
``<p>:runs-live`` (sorted set)
    Every claimed, unreleased run id, scored by lease expiry (epoch ms), so
    the orphan sweep reads only runs whose lease may have lapsed.
``<p>:ctl:<replica_id>`` (stream), ``<p>:ctl-reply:<scope>`` (stream),
``<p>:ctl-done:<scope>`` (string)
    Run control (stop, steer, approval routed to the owner); see
    ``atlas/application/chat/runs/control_bus.py``.
``<p>:replica:<replica_id>``
    Reserved for replica liveness (later #1128 work); nothing writes it yet.

All timestamps come from the Redis server's clock (``TIME``), so replicas
with skewed clocks still agree on whether a lease has expired.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Iterable, Optional

from atlas.application.chat.runs.admission_errors import (
    ConcurrencyLimitError,
    ConversationBusyError,
)
from atlas.application.chat.runs.coordination import (
    RUN_LEASE_SECONDS,
    RUN_RETENTION_SECONDS,
    RunClaim,
    RunCoordinationUnavailableError,
    default_replica_id,
)
from atlas.core.user_identity import normalize_user_email

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = ("completed", "failed", "cancelled")
# Lease renewals in flight at once, per replica.
RENEW_CONCURRENCY = 16

# Shared by the scripts: Redis server time in epoch milliseconds.
_NOW = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
"""

# Subtree retention, shared by the scripts that end a run. ``p`` is the key
# prefix (``<p>:``), because the keys of a run's ancestors and descendants are
# not known to the caller. A run with a live descendant keeps its record, so a
# stop or cancel through another replica still finds it; the ancestor walk is
# bounded like the registry's (``MAX_SUBTREE_DEPTH``).
_SUBTREE = """
local function terminal(status)
  return status == 'completed' or status == 'failed' or status == 'cancelled'
end
local function live_below(p, id, depth)
  if depth > 64 then return false end
  local kids_key = p .. 'run-kids:' .. id
  for _, kid in ipairs(redis.call('SMEMBERS', kids_key)) do
    local rec = p .. 'run:' .. kid
    local status = redis.call('HGET', rec, 'status')
    if not status then
      redis.call('SREM', kids_key, kid)
    else
      if (not terminal(status))
          and tonumber(redis.call('HGET', rec, 'lease_expires_at') or '0') > now then
        return true
      end
      if live_below(p, kid, depth + 1) then return true end
    end
  end
  return false
end
local function expire_both(p, id, ms)
  redis.call('PEXPIRE', p .. 'run:' .. id, ms)
  redis.call('PEXPIRE', p .. 'run-kids:' .. id, ms)
end
-- A run just ended: its record keeps `retention` after the last descendant
-- ends (`lease + retention` while one is live, renewed by that descendant's
-- own heartbeat), and finished ancestors whose last live descendant it was
-- start their retention now.
local function end_run(p, id, retention, lease)
  if live_below(p, id, 0) then
    expire_both(p, id, lease + retention)
    return
  end
  expire_both(p, id, retention)
  local cur = redis.call('HGET', p .. 'run:' .. id, 'parent_run_id')
  local hops = 0
  while cur and cur ~= '' and hops < 64 do
    local rec = p .. 'run:' .. cur
    local status = redis.call('HGET', rec, 'status')
    if (not status) or (not terminal(status)) or live_below(p, cur, 0) then break end
    expire_both(p, cur, retention)
    cur = redis.call('HGET', rec, 'parent_run_id')
    hops = hops + 1
  end
end
"""

# KEYS: conv, user, run, fence, live
# ARGV: run_id, conversation_id, user_email, owner, limit, lease_ms, retention_ms,
#       source, parent_run_id, follow_up_of, key_prefix
CLAIM_SCRIPT = _NOW + """
local lease = tonumber(ARGV[6])
local holder = redis.call('GET', KEYS[1])
if holder and holder ~= ARGV[1] then
  return {'busy', holder}
end
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
local count = redis.call('ZCARD', KEYS[2])
if (not redis.call('ZSCORE', KEYS[2], ARGV[1])) and count >= tonumber(ARGV[5]) then
  return {'limit', tostring(count)}
end
local generation = redis.call('INCR', KEYS[4])
-- Floor at Redis time in microseconds, so a flushed or restarted Redis, whose
-- counter starts over, still issues generations above every earlier one. The
-- counter only runs ahead of that clock if claims land faster than one per
-- microsecond, and each claim script takes longer than that to run.
local now_us = tonumber(t[1]) * 1000000 + tonumber(t[2])
if generation < now_us then
  redis.call('SET', KEYS[4], string.format('%d', now_us))
  generation = now_us
end
local expires = now + lease
redis.call('SET', KEYS[1], ARGV[1], 'PX', lease)
redis.call('ZADD', KEYS[2], expires, ARGV[1])
redis.call('PEXPIRE', KEYS[2], lease * 2)
redis.call('HSET', KEYS[3],
  'run_id', ARGV[1], 'conversation_id', ARGV[2], 'user_email', ARGV[3],
  'owner', ARGV[4], 'generation', generation, 'status', 'queued',
  'lease_expires_at', expires, 'created_at', now, 'updated_at', now,
  'source', ARGV[8], 'parent_run_id', ARGV[9], 'follow_up_of', ARGV[10])
redis.call('PEXPIRE', KEYS[3], lease + tonumber(ARGV[7]))
if ARGV[9] ~= '' then
  local kids = ARGV[11] .. 'run-kids:' .. ARGV[9]
  redis.call('SADD', kids, ARGV[1])
  redis.call('PEXPIRE', kids, lease + tonumber(ARGV[7]))
end
redis.call('ZADD', KEYS[5], expires, ARGV[1])
return {'ok', generation, expires}
"""

# KEYS: run, conv, user, live
# ARGV: run_id, owner, generation ('' = any generation this owner holds),
#       status, retention_ms, lease_ms, key_prefix
RELEASE_SCRIPT = _NOW + _SUBTREE + """
local generation = redis.call('HGET', KEYS[1], 'generation')
local owner = redis.call('HGET', KEYS[1], 'owner')
if generation then
  if owner ~= ARGV[2] then return 0 end
  if ARGV[3] ~= '' and generation ~= ARGV[3] then return 0 end
  -- Like renew: once a live run's lease has passed it is no longer ours to
  -- end, even before a sweep has recorded the takeover (run_leases.py).
  local current = redis.call('HGET', KEYS[1], 'status')
  if current ~= 'completed' and current ~= 'failed' and current ~= 'cancelled' then
    if tonumber(redis.call('HGET', KEYS[1], 'lease_expires_at') or '0') <= now then
      return 0
    end
  end
end
local freed = 0
if redis.call('GET', KEYS[2]) == ARGV[1] then
  redis.call('DEL', KEYS[2])
  freed = 1
end
if redis.call('ZREM', KEYS[3], ARGV[1]) == 1 then
  freed = 1
end
redis.call('ZREM', KEYS[4], ARGV[1])
if generation then
  redis.call('HSET', KEYS[1], 'status', ARGV[4], 'lease_expires_at', 0, 'updated_at', now)
  end_run(ARGV[7], ARGV[1], tonumber(ARGV[5]), tonumber(ARGV[6]))
end
return freed
"""

# KEYS: run, conv, user, live
# ARGV: run_id, owner, generation, status, lease_ms, retention_ms, key_prefix
RENEW_SCRIPT = _NOW + _SUBTREE + """
if redis.call('HGET', KEYS[1], 'generation') ~= ARGV[3] then return 0 end
if redis.call('HGET', KEYS[1], 'owner') ~= ARGV[2] then return 0 end
local current = redis.call('HGET', KEYS[1], 'status')
if current == 'completed' or current == 'failed' or current == 'cancelled' then
  return 0
end
if redis.call('GET', KEYS[2]) ~= ARGV[1] then return 0 end
-- A lapsed lease is lost, not re-taken: its slot may already be counted for
-- another run, and re-adding it here would put the user over the cap.
local slot = redis.call('ZSCORE', KEYS[3], ARGV[1])
if (not slot) or tonumber(slot) <= now then return 0 end
local lease = tonumber(ARGV[5])
local expires = now + lease
redis.call('SET', KEYS[2], ARGV[1], 'PX', lease)
redis.call('ZADD', KEYS[3], expires, ARGV[1])
redis.call('PEXPIRE', KEYS[3], lease * 2)
redis.call('HSET', KEYS[1], 'status', ARGV[4], 'lease_expires_at', expires, 'updated_at', now)
redis.call('PEXPIRE', KEYS[1], lease + tonumber(ARGV[6]))
redis.call('ZADD', KEYS[4], expires, ARGV[1])
-- A finished ancestor stays readable while this run is live.
local cur = redis.call('HGET', KEYS[1], 'parent_run_id')
local hops = 0
while cur and cur ~= '' and hops < 64 do
  local rec = ARGV[7] .. 'run:' .. cur
  local status = redis.call('HGET', rec, 'status')
  if not status then break end
  if terminal(status) then expire_both(ARGV[7], cur, lease + tonumber(ARGV[6])) end
  cur = redis.call('HGET', rec, 'parent_run_id')
  hops = hops + 1
end
return expires
"""


def key_part(value: str) -> str:
    """Escape one variable key component so ``:`` can only separate parts.

    A user email or a client-supplied conversation id may contain ``:``;
    unescaped, ``("a:b@x", "c")`` and ``("a", "b@x:c")`` would share a key.
    """
    return str(value).replace("%", "%25").replace(":", "%3A")


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, (bytes, bytearray)) else str(value)


class RunCoordinationKeys:
    """Builds the contract's key names from a ``RedisClientManager``."""

    def __init__(self, redis_manager: Any):
        self._redis = redis_manager

    def run(self, run_id: str) -> str:
        return self._redis.key("run", key_part(run_id))

    def conversation(self, conversation_id: str) -> str:
        return self._redis.key("run-conv", key_part(conversation_id))

    def user(self, user_email: str) -> str:
        return self._redis.key("run-user", key_part(normalize_user_email(user_email)))

    def fence(self) -> str:
        return self._redis.key("run-fence")

    def live(self) -> str:
        """Unreleased runs scored by lease expiry, for the orphan sweep."""
        return self._redis.key("runs-live")


    def control_inbox(self, replica_id: str) -> str:
        """Run-control inbox stream of one replica (stop, steer, approval)."""
        return self._redis.key("ctl", key_part(replica_id))

    def control_reply(self, scope: str) -> str:
        """Reply stream for one run-control request (``scope`` hashes actor + request id)."""
        return self._redis.key("ctl-reply", key_part(scope))

    def control_done(self, scope: str) -> str:
        """Deduplication record for one run-control request."""
        return self._redis.key("ctl-done", key_part(scope))

    def replica(self, replica_id: str) -> str:
        """Reserved: replica liveness record."""
        return self._redis.key("replica", key_part(replica_id))


class RedisRunCoordinator:
    """Atomic, lease-based run admission in the shared Redis."""

    tracks_claims = True

    def __init__(
        self,
        redis_manager: Any,
        *,
        replica_id: Optional[str] = None,
        lease_seconds: float = RUN_LEASE_SECONDS,
        retention_seconds: float = RUN_RETENTION_SECONDS,
    ):
        self._redis = redis_manager
        self.keys = RunCoordinationKeys(redis_manager)
        self.replica_id = replica_id or default_replica_id()
        self.lease_seconds = float(lease_seconds)
        self._lease_ms = int(lease_seconds * 1000)
        self._retention_ms = int(retention_seconds * 1000)
        # ``<p>:``: the scripts build the keys of a run's relatives from it.
        self._prefix = redis_manager.key("")
        client = redis_manager.client
        self._claim = client.register_script(CLAIM_SCRIPT)
        self._release = client.register_script(RELEASE_SCRIPT)
        self._renew = client.register_script(RENEW_SCRIPT)

    def _unavailable(self, exc: BaseException) -> RunCoordinationUnavailableError:
        stats = getattr(self._redis, "stats", None)
        if stats is not None:
            stats.record_error(exc)
        return RunCoordinationUnavailableError(type(exc).__name__)

    async def claim(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_email: str,
        max_runs_per_user: int,
        source: str = "",
        parent_run_id: Optional[str] = None,
        follow_up_of: Optional[str] = None,
    ) -> Optional[RunClaim]:
        owner_email = normalize_user_email(user_email)
        try:
            result = await self._claim(
                keys=[
                    self.keys.conversation(conversation_id),
                    self.keys.user(owner_email),
                    self.keys.run(run_id),
                    self.keys.fence(),
                    self.keys.live(),
                ],
                args=[
                    run_id,
                    conversation_id,
                    owner_email,
                    self.replica_id,
                    max(1, int(max_runs_per_user)),
                    self._lease_ms,
                    self._retention_ms,
                    source or "",
                    parent_run_id or "",
                    follow_up_of or "",
                    self._prefix,
                ],
            )
        except Exception as exc:
            logger.warning("Run admission could not reach Redis: %s", type(exc).__name__)
            # The script may have run and only its reply been lost (a timeout,
            # a reset connection). Undo whatever this owner holds under this
            # run id, so the refusal holds no slot; if Redis is really down
            # this fails too and the lease frees the slot instead.
            try:
                await self._run_release(run_id, conversation_id, owner_email, "", "failed")
            except Exception as cleanup_exc:
                # Expected when Redis is really down; the lease frees the slot.
                logger.debug("Claim cleanup after a lost reply failed: %s", type(cleanup_exc).__name__)
            raise self._unavailable(exc) from exc

        outcome = _text(result[0])
        if outcome == "busy":
            raise ConversationBusyError("This conversation already has a run in progress.")
        if outcome == "limit":
            raise ConcurrencyLimitError(max(1, int(max_runs_per_user)))
        return RunClaim(
            run_id=run_id,
            conversation_id=conversation_id,
            user_email=owner_email,
            owner=self.replica_id,
            generation=int(result[1]),
            lease_expires_at=int(result[2]) / 1000.0,
        )

    async def _run_release(
        self, run_id: str, conversation_id: str, user_email: str, generation: str, status: str
    ) -> bool:
        freed = await self._release(
            keys=[
                self.keys.run(run_id),
                self.keys.conversation(conversation_id),
                self.keys.user(user_email),
                self.keys.live(),
            ],
            args=[run_id, self.replica_id, generation, status, self._retention_ms,
                  self._lease_ms, self._prefix],
        )
        return bool(int(freed))

    async def release(self, claim: RunClaim, status: str) -> bool:
        try:
            return await self._run_release(
                claim.run_id, claim.conversation_id, claim.user_email,
                str(claim.generation), status,
            )
        except Exception as exc:
            # The lease expires on its own; the slot is held at most that long.
            self._unavailable(exc)
            logger.warning(
                "Could not release run claim %s (%s); it frees itself when its lease expires",
                claim.run_id, type(exc).__name__,
            )
            return False

    async def renew(self, claims: Iterable[RunClaim], statuses: Dict[str, str]) -> Dict[str, bool]:
        """Renew concurrently (bounded), so one beat stays well inside the lease
        even with many live runs on a slow link."""
        semaphore = asyncio.Semaphore(RENEW_CONCURRENCY)

        async def _one(claim: RunClaim):
            async with semaphore:
                try:
                    expires = await self._renew(
                        keys=[
                            self.keys.run(claim.run_id),
                            self.keys.conversation(claim.conversation_id),
                            self.keys.user(claim.user_email),
                            self.keys.live(),
                        ],
                        args=[
                            claim.run_id,
                            self.replica_id,
                            str(claim.generation),
                            statuses.get(claim.run_id, "running"),
                            self._lease_ms,
                            self._retention_ms,
                            self._prefix,
                        ],
                    )
                except Exception as exc:
                    # Unknown, not lost: the next beat tries again well inside the lease.
                    self._unavailable(exc)
                    logger.warning(
                        "Could not renew run lease %s: %s", claim.run_id, type(exc).__name__
                    )
                    return claim.run_id, None
                return claim.run_id, bool(int(expires))

        results = await asyncio.gather(*(_one(c) for c in claims))
        return {run_id: held for run_id, held in results if held is not None}

    async def read(self, run_id: str) -> Optional[Dict[str, str]]:
        """The shared run record at ``RunCoordinationKeys.run(run_id)``, or ``None``.

        Fields as strings, per the contract: ``run_id``, ``conversation_id``,
        ``user_email``, ``owner``, ``generation``, ``status``,
        ``lease_expires_at``, ``created_at``, ``updated_at``, ``source``,
        ``parent_run_id``, ``follow_up_of``. Readable by any
        replica (inspection, fencing checks).
        """
        raw = await self._redis.client.hgetall(self.keys.run(run_id))
        if not raw:
            return None
        return {_text(k): _text(v) for k, v in raw.items()}
