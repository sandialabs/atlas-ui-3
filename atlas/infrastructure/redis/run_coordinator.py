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
``<p>:run-api-latest:<conversation_id>`` (string)
    Latest admitted API run id. Checked atomically with a follow-up claim.
    Its ``:retained`` sorted set tracks the conversation's retention horizon.
``<p>:runs-api``, ``<p>:runs-api-user:<user>`` (sorted sets)
    Retained API run ids globally and per normalized user, scored by expiry.
    Expired members are pruned on writes and listing; the sets expire with
    their last retained record.
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
import json
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

# Index score is the expiry of each retained record. Expired entries are
# pruned on writes, and the index itself expires when its last member does.
_API_INDEX = """
local function refresh_api_index(key, id, expiry)
  redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
  redis.call('ZADD', key, expiry, id)
  local tail = redis.call('ZREVRANGE', key, 0, 0, 'WITHSCORES')
  redis.call('PEXPIREAT', key, math.ceil(tonumber(tail[2])))
end
local function refresh_api_retention(p, latest, user_index, id, expiry)
  local conv_index = latest .. ':retained'
  refresh_api_index(p .. 'runs-api', id, expiry)
  refresh_api_index(user_index, id, expiry)
  refresh_api_index(conv_index, id, expiry)
  local newest = redis.call('GET', latest)
  if not newest then return end
  local tail = redis.call('ZREVRANGE', conv_index, 0, 0, 'WITHSCORES')
  local until_at = math.ceil(tonumber(tail[2]))
  redis.call('PEXPIREAT', latest, until_at)
  local newest_rec = p .. 'run:' .. newest
  if redis.call('EXISTS', newest_rec) == 1 then
    redis.call('PEXPIREAT', newest_rec, until_at)
    refresh_api_index(p .. 'runs-api', newest, until_at)
    refresh_api_index(user_index, newest, until_at)
    refresh_api_index(conv_index, newest, until_at)
    -- While publication is ambiguous, the predecessor must survive as
    -- long as the provisional pointer, including ancestor retention updates.
    if redis.call('HGET', newest_rec, 'api_admission_unconfirmed') then
      local previous = redis.call('HGET', newest_rec, 'follow_up_of') or ''
      local prev_rec = p .. 'run:' .. previous
      local ttl = redis.call('PTTL', prev_rec)
      if previous ~= '' and ttl > 0 and now + ttl < until_at then
        redis.call('PEXPIREAT', prev_rec, until_at)
        refresh_api_index(p .. 'runs-api', previous, until_at)
        refresh_api_index(user_index, previous, until_at)
        refresh_api_index(conv_index, previous, until_at)
      end
    end
  end
end
local function rollback_api_publication(p, rec, id)
  local latest = redis.call('HGET', rec, 'api_latest_key')
  if latest then
    local user_index = redis.call('HGET', rec, 'api_user_index')
    local conv_index = latest .. ':retained'
    redis.call('ZREM', p .. 'runs-api', id)
    redis.call('ZREM', user_index, id)
    redis.call('ZREM', conv_index, id)
    if redis.call('GET', latest) == id then
      local previous = redis.call('HGET', rec, 'follow_up_of') or ''
      local prev_rec = p .. 'run:' .. previous
      local ttl = redis.call('PTTL', prev_rec)
      if previous ~= '' and ttl > 0 then
        redis.call('SET', latest, previous, 'PX', ttl)
        refresh_api_retention(p, latest, user_index, previous, now + ttl)
      else
        redis.call('DEL', latest)
      end
    end
  end
  redis.call('HDEL', rec, 'api_spec', 'api_pending_spec', 'api_latest_key',
    'api_user_index', 'api_admission_unconfirmed')
end

"""

# Subtree retention, shared by the scripts that end a run. ``p`` is the key
# prefix (``<p>:``), because the keys of a run's ancestors and descendants are
# not known to the caller. A run with a live descendant keeps its record, so a
# stop or cancel through another replica still finds it; the ancestor walk is
# bounded like the registry's (``MAX_SUBTREE_DEPTH``).
_SUBTREE = _API_INDEX + """
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
  local rec = p .. 'run:' .. id
  redis.call('PEXPIRE', rec, ms)
  local latest = redis.call('HGET', rec, 'api_latest_key')
  if latest then
    local user_index = redis.call('HGET', rec, 'api_user_index')
    if user_index then refresh_api_retention(p, latest, user_index, id, now + ms) end
  end
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

# KEYS: conv, user, run, fence, live, api-latest, api-index, api-user-index
# ARGV: run_id, conversation_id, user_email, owner, limit, lease_ms, retention_ms,
#       source, parent_run_id, follow_up_of, key_prefix, api_continuation_json
CLAIM_SCRIPT = _NOW + _API_INDEX + """
local lease = tonumber(ARGV[6])
local holder = redis.call('GET', KEYS[1])
if holder and holder ~= ARGV[1] then
  return {'busy', holder}
end
local completed_before = '0'
if ARGV[12] ~= '' then
  local latest = redis.call('GET', KEYS[6])
  if ARGV[10] ~= '' then
    if not latest then return {'continuation_unavailable'} end
    if latest ~= ARGV[10] then return {'stale_continuation'} end
    local previous = ARGV[11] .. 'run:' .. latest
    if redis.call('HGET', previous, 'source') ~= ARGV[8]
        or redis.call('HGET', previous, 'user_email') ~= ARGV[3]
        or redis.call('HGET', previous, 'conversation_id') ~= ARGV[2]
        or not redis.call('HGET', previous, 'api_spec') then
      return {'continuation_unavailable'}
    end
    local status = redis.call('HGET', previous, 'status')
    if redis.call('HGET', previous, 'api_admission_unconfirmed') then
      return {'busy', latest}
    end
    if status ~= 'completed' and status ~= 'failed' and status ~= 'cancelled' then
      return {'busy', latest}
    end
    if status == 'completed' or redis.call('HGET', previous, 'api_completed_before') == '1' then
      completed_before = '1'
    else
      return {'continuation_unavailable'}
    end
  elseif latest then
    return {'stale_continuation'}
  end
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
if ARGV[12] ~= '' then
  redis.call('HSET', KEYS[3], 'api_pending_spec', ARGV[12],
    'api_completed_before', completed_before)
end
if ARGV[9] ~= '' then
  local kids = ARGV[11] .. 'run-kids:' .. ARGV[9]
  redis.call('SADD', kids, ARGV[1])
  redis.call('PEXPIRE', kids, lease + tonumber(ARGV[7]))
end
redis.call('ZADD', KEYS[5], expires, ARGV[1])
return {'ok', generation, expires}
"""

# Publish only after the local registry accepted the run. Repeating the
# operation is safe if the first reply was lost.
# KEYS: run, conv, api-latest, api-user-index
# ARGV: run_id, owner, generation, retention_ms, key_prefix
PUBLISH_API_SCRIPT = _NOW + _API_INDEX + """
if redis.call('HGET', KEYS[1], 'owner') ~= ARGV[2]
    or redis.call('HGET', KEYS[1], 'generation') ~= ARGV[3]
    or redis.call('GET', KEYS[2]) ~= ARGV[1] then return 0 end
local expires = tonumber(redis.call('HGET', KEYS[1], 'lease_expires_at') or '0')
if expires <= now then return 0 end
if redis.call('HGET', KEYS[1], 'api_spec') then
  if redis.call('GET', KEYS[3]) == ARGV[1] then return 1 end
  return 0
end
local spec = redis.call('HGET', KEYS[1], 'api_pending_spec')
if not spec then return 0 end
local previous = redis.call('HGET', KEYS[1], 'follow_up_of') or ''
if (redis.call('GET', KEYS[3]) or '') ~= previous then return 0 end
redis.call('HSET', KEYS[1], 'api_spec', spec, 'api_latest_key', KEYS[3],
  'api_user_index', KEYS[4], 'api_admission_owner', ARGV[2],
  'api_admission_generation', ARGV[3], 'api_admission_unconfirmed', '1')
redis.call('HDEL', KEYS[1], 'api_pending_spec')
redis.call('SET', KEYS[3], ARGV[1])
refresh_api_retention(ARGV[5], KEYS[3], KEYS[4], ARGV[1], expires + tonumber(ARGV[4]))
return 1
"""

# KEYS: run, conv, user, live
# ARGV: run_id, owner, generation ('' = any generation this owner holds),
#       status, retention_ms, lease_ms, key_prefix
RELEASE_SCRIPT = _NOW + _SUBTREE + """
local generation = redis.call('HGET', KEYS[1], 'generation')
local owner = redis.call('HGET', KEYS[1], 'owner')
local refused = ARGV[4] == 'admission_refused'
local same_claim = generation and owner == ARGV[2]
    and (ARGV[3] == '' or generation == ARGV[3])
-- A refused publication is metadata cleanup, not a terminal write or a
-- renewed lease. Its immutable admission identity survives orphan takeover.
local same_publication = refused and ARGV[3] ~= ''
    and redis.call('HGET', KEYS[1], 'api_admission_owner') == ARGV[2]
    and redis.call('HGET', KEYS[1], 'api_admission_generation') == ARGV[3]
if generation then
  if not same_claim and not same_publication then return 0 end
  -- A delayed refusal cannot erase an activated run or the sweep's
  -- outstanding projection-close obligation.
  if refused and (redis.call('HGET', KEYS[1], 'api_admission_discarded')
      or (redis.call('HGET', KEYS[1], 'api_spec')
          and not redis.call('HGET', KEYS[1], 'api_admission_unconfirmed'))) then
    return 0
  end
  -- Like renew: once a live run's lease has passed it is no longer ours to
  -- end, even before a sweep has recorded the takeover (run_leases.py).
  local current = redis.call('HGET', KEYS[1], 'status')
  if current ~= 'completed' and current ~= 'failed' and current ~= 'cancelled' then
    if not refused and tonumber(redis.call('HGET', KEYS[1], 'lease_expires_at') or '0') <= now then
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
if generation and (redis.call('HGET', KEYS[1], 'api_pending_spec')
    or ARGV[4] == 'admission_refused') then
  -- A refused admission must not remain in the retained API index. If
  -- publication ran but its reply was lost, restore the prior pointer only
  -- while this claim still owns the published turn.
  rollback_api_publication(ARGV[7], KEYS[1], ARGV[1])
  redis.call('DEL', KEYS[1])
  return freed
end
if generation then
  redis.call('HSET', KEYS[1], 'status', ARGV[4], 'lease_expires_at', 0, 'updated_at', now)
  redis.call('HDEL', KEYS[1], 'api_admission_unconfirmed')
  end_run(ARGV[7], ARGV[1], tonumber(ARGV[5]), tonumber(ARGV[6]))
end
return freed
"""

# KEYS: run, conv, user, live
# ARGV: run_id, owner, generation, status, lease_ms, retention_ms, key_prefix, activate_api
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
if ARGV[8] == '1' then
  redis.call('HDEL', KEYS[1], 'api_admission_unconfirmed')
end
expire_both(ARGV[7], ARGV[1], lease + tonumber(ARGV[6]))
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

    def api_latest(self, conversation_id: str) -> str:
        return self._redis.key("run-api-latest", key_part(conversation_id))

    def api_index(self, user_email: Optional[str] = None) -> str:
        if user_email is not None:
            return self._redis.key("runs-api-user", key_part(normalize_user_email(user_email)))
        return self._redis.key("runs-api")

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
        self._publish_api = client.register_script(PUBLISH_API_SCRIPT)
        self._renew = client.register_script(RENEW_SCRIPT)
        # Explicit publication refusals are retried independently of live
        # leases. A successful cleanup or an expired record ends the retry.
        self._api_refusals: Dict[str, RunClaim] = {}

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
        api_continuation: Optional[dict] = None,
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
                    self.keys.api_latest(conversation_id),
                    self.keys.api_index(),
                    self.keys.api_index(owner_email),
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
                    json.dumps(api_continuation) if api_continuation is not None else "",
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
        if outcome == "stale_continuation":
            raise ConversationBusyError("A newer API turn was admitted. Retry the follow-up.")
        if outcome == "continuation_unavailable":
            raise RunCoordinationUnavailableError("API continuation is unavailable.")
        return RunClaim(
            run_id=run_id,
            conversation_id=conversation_id,
            user_email=owner_email,
            owner=self.replica_id,
            generation=int(result[1]),
            lease_expires_at=int(result[2]) / 1000.0,
        )

    async def publish_api_admission(self, claim: RunClaim) -> None:
        """Publish the accepted API turn while its original claim is still held."""
        for attempt in range(2):
            try:
                published = await self._publish_api(
                    keys=[self.keys.run(claim.run_id), self.keys.conversation(claim.conversation_id),
                          self.keys.api_latest(claim.conversation_id), self.keys.api_index(claim.user_email)],
                    args=[claim.run_id, claim.owner, str(claim.generation), self._retention_ms, self._prefix],
                )
            except Exception as exc:
                if attempt == 0:
                    continue
                raise self._unavailable(exc) from exc
            if not int(published):
                raise RunCoordinationUnavailableError("API admission could not be published.")
            return

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
        if status == "admission_refused":
            self._api_refusals.setdefault(claim.run_id, claim)
        try:
            freed = await self._run_release(
                claim.run_id, claim.conversation_id, claim.user_email,
                str(claim.generation), status,
            )
            if status == "admission_refused":
                self._api_refusals.pop(claim.run_id, None)
            return freed
        except Exception as exc:
            # The lease expires on its own; the slot is held at most that long.
            self._unavailable(exc)
            if status == "admission_refused":
                logger.warning(
                    "Could not reconcile refused API admission %s (%s); will retry",
                    claim.run_id, type(exc).__name__,
                )
            else:
                logger.warning(
                    "Could not release run claim %s (%s); it frees itself when its lease expires",
                    claim.run_id, type(exc).__name__,
                )
            return False

    async def retry_api_refusals(self) -> None:
        """Retry known refusals even when there are no live local claims."""
        for run_id, claim in list(self._api_refusals.items()):
            await self.release(claim, "admission_refused")
            if run_id in self._api_refusals:
                # Redis is still unavailable. Try again on the next sweep.
                break

    async def activate_api(self, claim: RunClaim) -> Optional[bool]:
        """Confirm the execution boundary independently of heartbeat status."""
        held = await self.renew([claim], {claim.run_id: "running"}, activate_api=True)
        return held.get(claim.run_id)

    async def renew(
        self, claims: Iterable[RunClaim], statuses: Dict[str, str], *, activate_api: bool = False
    ) -> Dict[str, bool]:
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
                            "1" if activate_api else "0",
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

    async def read_api_continuation(self, conversation_id: str) -> Optional[Dict[str, str]]:
        latest = await self._redis.client.get(self.keys.api_latest(conversation_id))
        return await self.read(_text(latest)) if latest else None

    async def read_api_runs(self, user_email: Optional[str] = None) -> list[Dict[str, str]]:
        client = self._redis.client
        seconds, micros = await client.time()
        now = seconds * 1000 + micros // 1000
        index = self.keys.api_index(user_email)
        await client.zremrangebyscore(index, "-inf", now)
        ids = await client.zrange(index, 0, -1)
        # Read in bounded batches; a record can expire between index and read.
        records = []
        for start in range(0, len(ids), 100):
            batch = await asyncio.gather(*(self.read(_text(i)) for i in ids[start:start + 100]))
            records.extend(record for record in batch if record is not None)
        return records

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
