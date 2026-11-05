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
    ``updated_at``. Kept for a retention window after release.
``<p>:run-conv:<conversation_id>`` (string, PX lease)
    The run id holding the conversation. One per conversation, across all
    users: conversation ids are global in storage, so two users must not
    both run (and then both try to save) the same unsaved id.
``<p>:run-user:<user>`` (sorted set)
    The user's claimed run ids, scored by lease expiry (epoch ms). Members
    whose lease has passed are pruned before every count, so a replica that
    died holding slots frees them within one lease.
``<p>:run-fence`` (string counter)
    Source of fencing generations. One counter, only ever incremented.
``<p>:run-cmd:<run_id>`` (stream) and ``<p>:replica:<replica_id>``
    Reserved for command routing and replica liveness (later #1128 work);
    nothing writes them yet.

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
    RUN_RECORD_RETENTION_SECONDS,
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

# KEYS: conv, user, run, fence
# ARGV: run_id, conversation_id, user_email, owner, limit, lease_ms, retention_ms
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
local expires = now + lease
redis.call('SET', KEYS[1], ARGV[1], 'PX', lease)
redis.call('ZADD', KEYS[2], expires, ARGV[1])
redis.call('PEXPIRE', KEYS[2], lease * 2)
redis.call('HSET', KEYS[3],
  'run_id', ARGV[1], 'conversation_id', ARGV[2], 'user_email', ARGV[3],
  'owner', ARGV[4], 'generation', generation, 'status', 'queued',
  'lease_expires_at', expires, 'created_at', now, 'updated_at', now)
redis.call('PEXPIRE', KEYS[3], lease + tonumber(ARGV[7]))
return {'ok', generation, expires}
"""

# KEYS: run, conv, user
# ARGV: run_id, owner, generation ('' = any generation this owner holds),
#       status, retention_ms
RELEASE_SCRIPT = _NOW + """
local generation = redis.call('HGET', KEYS[1], 'generation')
local owner = redis.call('HGET', KEYS[1], 'owner')
if generation then
  if owner ~= ARGV[2] then return 0 end
  if ARGV[3] ~= '' and generation ~= ARGV[3] then return 0 end
end
local freed = 0
if redis.call('GET', KEYS[2]) == ARGV[1] then
  redis.call('DEL', KEYS[2])
  freed = 1
end
if redis.call('ZREM', KEYS[3], ARGV[1]) == 1 then
  freed = 1
end
if generation then
  redis.call('HSET', KEYS[1], 'status', ARGV[4], 'lease_expires_at', 0, 'updated_at', now)
  redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[5]))
end
return freed
"""

# KEYS: run, conv, user
# ARGV: run_id, owner, generation, status, lease_ms, retention_ms
RENEW_SCRIPT = _NOW + """
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

    def commands(self, run_id: str) -> str:
        """Reserved: per-run command stream for cross-replica routing."""
        return self._redis.key("run-cmd", key_part(run_id))

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
        retention_seconds: float = RUN_RECORD_RETENTION_SECONDS,
    ):
        self._redis = redis_manager
        self.keys = RunCoordinationKeys(redis_manager)
        self.replica_id = replica_id or default_replica_id()
        self._lease_ms = int(lease_seconds * 1000)
        self._retention_ms = int(retention_seconds * 1000)
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
    ) -> Optional[RunClaim]:
        owner_email = normalize_user_email(user_email)
        try:
            result = await self._claim(
                keys=[
                    self.keys.conversation(conversation_id),
                    self.keys.user(owner_email),
                    self.keys.run(run_id),
                    self.keys.fence(),
                ],
                args=[
                    run_id,
                    conversation_id,
                    owner_email,
                    self.replica_id,
                    max(1, int(max_runs_per_user)),
                    self._lease_ms,
                    self._retention_ms,
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
            ],
            args=[run_id, self.replica_id, generation, status, self._retention_ms],
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
                        ],
                        args=[
                            claim.run_id,
                            self.replica_id,
                            str(claim.generation),
                            statuses.get(claim.run_id, "running"),
                            self._lease_ms,
                            self._retention_ms,
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
        ``lease_expires_at``, ``created_at``, ``updated_at``. Readable by any
        replica (inspection, fencing checks).
        """
        raw = await self._redis.client.hgetall(self.keys.run(run_id))
        if not raw:
            return None
        return {_text(k): _text(v) for k, v in raw.items()}
