"""Redis-backed idempotency reservations for the Agent Runs API (#1220).

One hash per ``(user, idempotency_key)``:

``<p>:idem:<user>:<key>`` (hash)
    ``token`` (the reserving request's id), ``fingerprint``, ``created_at``
    (epoch seconds), ``handle`` (JSON; absent while the first request is still
    admitting the run), ``addressed_run_id`` and ``conversation_id`` (follow-ups).
    A pending reservation (no handle) lives ``IDEMPOTENCY_PENDING_TTL_SECONDS``;
    once the handle is recorded the record lives ``IDEMPOTENCY_TTL_SECONDS``.
``<p>:idem-addr:<run_id>:<key>`` and ``<p>:idem-conv:<conversation_id>:<key>`` (strings)
    The user whose follow-up addressed ``run_id`` (in ``conversation_id``) with
    ``key``, so a retry that lands after the run was reaped, on another replica,
    or naming a sibling run of the same conversation still finds its record.
    Same lifetime as the hash.

There is no entry cap: the namespace is bounded by TTL only (see
``docs/admin/agent-runs-api.md``).

Reservation, completion and release are single Lua scripts, so two replicas
racing on one key cannot both win. Variable key parts are escaped with
``key_part`` (a client-chosen key may contain ``:``). Redis is unreachable ->
``RunCoordinationUnavailableError``, the same failure admission raises.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

from atlas.application.chat.runs.coordination import RunCoordinationUnavailableError
from atlas.application.chat.runs.idempotency import (
    IDEMPOTENCY_PENDING_TTL_SECONDS,
    IDEMPOTENCY_TTL_SECONDS,
    IdempotencyEntry,
    IdempotencySlot,
)
from atlas.core.user_identity import normalize_user_email
from atlas.infrastructure.redis.run_coordinator import key_part

logger = logging.getLogger(__name__)

# An index slot is written only if absent (or already this owner's): another
# caller reusing a run id and key cannot take over the slot. Shared by the
# scripts that write indexes.
_CLAIM_INDEX = """
local function claim_index(k, owner, ttl)
  local cur = redis.call('GET', k)
  if not cur then
    redis.call('SET', k, owner, 'PX', ttl)
  elseif cur == owner then
    redis.call('PEXPIRE', k, ttl)
  end
end
local function touch_index(k, owner, ttl)
  if redis.call('GET', k) == owner then redis.call('PEXPIRE', k, ttl) end
end
local function drop_index(k, owner)
  if redis.call('GET', k) == owner then redis.call('DEL', k) end
end
"""

# KEYS: record, addr_index, conv_index (unused when the request addresses no run)
# ARGV: token, fingerprint, created_at, addressed_run_id, conversation_id,
#       pending_ms, user_email
# Returns {} when the key was taken by this call, else the existing hash as a
# flat field/value list.
RESERVE_SCRIPT = _CLAIM_INDEX + """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return redis.call('HGETALL', KEYS[1])
end
redis.call('HSET', KEYS[1], 'token', ARGV[1], 'fingerprint', ARGV[2],
  'created_at', ARGV[3], 'addressed_run_id', ARGV[4], 'conversation_id', ARGV[5])
redis.call('PEXPIRE', KEYS[1], ARGV[6])
if ARGV[4] ~= '' then
  claim_index(KEYS[2], ARGV[7], ARGV[6])
  if ARGV[5] ~= '' then claim_index(KEYS[3], ARGV[7], ARGV[6]) end
end
return {}
"""

# KEYS: record, addr_index, conv_index
# ARGV: token, handle_json, ttl_ms, user_email, addressed_run_id, fingerprint,
#       created_at, conversation_id
# Records the handle unless another request now holds the key. A reservation
# that already expired is re-taken (with its full fields), so a slow launch
# still leaves its handle.
COMPLETE_SCRIPT = _CLAIM_INDEX + """
local tok = redis.call('HGET', KEYS[1], 'token')
if tok and tok ~= ARGV[1] then return 0 end
redis.call('HSET', KEYS[1], 'token', ARGV[1], 'handle', ARGV[2],
  'fingerprint', ARGV[6], 'created_at', ARGV[7], 'addressed_run_id', ARGV[5],
  'conversation_id', ARGV[8])
redis.call('PEXPIRE', KEYS[1], ARGV[3])
if ARGV[5] ~= '' then
  claim_index(KEYS[2], ARGV[4], ARGV[3])
  if ARGV[8] ~= '' then claim_index(KEYS[3], ARGV[4], ARGV[3]) end
end
return 1
"""

# KEYS: record, addr_index, conv_index
# ARGV: token, pending_ms, addressed_run_id, conversation_id, user_email
# Extends a reservation that is still ours and still pending. Returns 2 when
# it is ours but already completed (nothing to extend, nothing lost), 0 when
# it is gone or another request's.
RENEW_SCRIPT = _CLAIM_INDEX + """
if redis.call('HGET', KEYS[1], 'token') ~= ARGV[1] then return 0 end
if redis.call('HEXISTS', KEYS[1], 'handle') == 1 then return 2 end
redis.call('PEXPIRE', KEYS[1], ARGV[2])
if ARGV[3] ~= '' then
  touch_index(KEYS[2], ARGV[5], ARGV[2])
  if ARGV[4] ~= '' then touch_index(KEYS[3], ARGV[5], ARGV[2]) end
end
return 1
"""

# KEYS: record, addr_index, conv_index
# ARGV: token, addressed_run_id, conversation_id, user_email
# Deletes the reservation only while it is still ours and still pending, and
# only the index slots that belong to this owner.
RELEASE_SCRIPT = _CLAIM_INDEX + """
if redis.call('HGET', KEYS[1], 'token') ~= ARGV[1] then return 0 end
if redis.call('HEXISTS', KEYS[1], 'handle') == 1 then return 0 end
redis.call('DEL', KEYS[1])
if ARGV[2] ~= '' then
  drop_index(KEYS[2], ARGV[4])
  if ARGV[3] ~= '' then drop_index(KEYS[3], ARGV[4]) end
end
return 1
"""


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, (bytes, bytearray)) else str(value)


def _entry_from_fields(fields: Dict[str, str]) -> IdempotencyEntry:
    handle = None
    raw = fields.get("handle")
    if raw:
        handle = json.loads(raw)
    return IdempotencyEntry(
        fingerprint=fields.get("fingerprint", ""),
        created_at=float(fields.get("created_at") or 0),
        handle=handle,
        addressed_run_id=fields.get("addressed_run_id") or None,
        conversation_id=fields.get("conversation_id") or None,
    )


class RedisIdempotencyStore:
    """Cluster-wide idempotency reservations in the shared Redis."""

    def __init__(
        self,
        redis_manager: Any,
        *,
        ttl_seconds: float = IDEMPOTENCY_TTL_SECONDS,
        pending_ttl_seconds: float = IDEMPOTENCY_PENDING_TTL_SECONDS,
    ):
        self._redis = redis_manager
        self._ttl_ms = int(ttl_seconds * 1000)
        self._pending_ms = int(pending_ttl_seconds * 1000)
        client = redis_manager.client
        self._reserve = client.register_script(RESERVE_SCRIPT)
        self._complete = client.register_script(COMPLETE_SCRIPT)
        self._release = client.register_script(RELEASE_SCRIPT)
        self._renew = client.register_script(RENEW_SCRIPT)

    def _record_key(self, user_email: str, key: str) -> str:
        return self._redis.key("idem", key_part(normalize_user_email(user_email)), key_part(key))

    def _addr_key(self, run_id: str, key: str) -> str:
        return self._redis.key("idem-addr", key_part(run_id), key_part(key))

    def _conv_key(self, conversation_id: str, key: str) -> str:
        return self._redis.key("idem-conv", key_part(conversation_id), key_part(key))

    def _keys(self, user_email: str, key: str, run_id, conversation_id) -> list:
        return [
            self._record_key(user_email, key),
            self._addr_key(run_id or "-", key),
            self._conv_key(conversation_id or "-", key),
        ]

    def _unavailable(self, exc: BaseException) -> RunCoordinationUnavailableError:
        stats = getattr(self._redis, "stats", None)
        if stats is not None:
            stats.record_error(exc, op="idempotency")
        return RunCoordinationUnavailableError(type(exc).__name__)

    async def reserve(
        self,
        user_email: str,
        key: str,
        fingerprint: str,
        *,
        addressed_run_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Tuple[Optional[IdempotencySlot], Optional[IdempotencyEntry]]:
        owner = normalize_user_email(user_email)
        token = uuid4().hex
        addr = addressed_run_id or ""
        try:
            raw = await self._reserve(
                keys=self._keys(owner, key, addr, conversation_id),
                args=[
                    token,
                    fingerprint,
                    f"{time.time():.3f}",
                    addr,
                    conversation_id or "",
                    self._pending_ms,
                    owner,
                ],
            )
        except Exception as exc:
            raise self._unavailable(exc) from exc
        if not raw:
            return (
                IdempotencySlot(owner, key, token, fingerprint, addressed_run_id, conversation_id),
                None,
            )
        items = [_text(item) for item in raw]
        return None, _entry_from_fields(dict(zip(items[::2], items[1::2])))

    async def complete(self, slot: IdempotencySlot, handle: Dict[str, Any]) -> Optional[bool]:
        try:
            stored = await self._complete(
                keys=self._keys(
                    slot.user_email, slot.key, slot.addressed_run_id, slot.conversation_id
                ),
                args=[
                    slot.token,
                    json.dumps(handle, default=str),
                    self._ttl_ms,
                    slot.user_email,
                    slot.addressed_run_id or "",
                    slot.fingerprint,
                    f"{time.time():.3f}",
                    slot.conversation_id or "",
                ],
            )
        except Exception as exc:
            # The run exists; failing the request now would invite a retry
            # that launches it twice. The caller retries the write.
            self._unavailable(exc)
            logger.warning(
                "Could not record the run handle for an idempotency key: %s", type(exc).__name__
            )
            return False
        # 0: another request holds the key now, so there is nothing to record.
        return True if stored else None

    async def renew(self, slot: IdempotencySlot) -> Optional[bool]:
        try:
            extended = await self._renew(
                keys=self._keys(
                    slot.user_email, slot.key, slot.addressed_run_id, slot.conversation_id
                ),
                args=[
                    slot.token,
                    self._pending_ms,
                    slot.addressed_run_id or "",
                    slot.conversation_id or "",
                    slot.user_email,
                ],
            )
        except Exception as exc:
            # Retried at the next interval; the TTL is three intervals long.
            self._unavailable(exc)
            logger.warning(
                "Could not extend an idempotency reservation: %s", type(exc).__name__
            )
            return None
        return bool(extended)  # 1 extended, 2 already completed (ours): both fine

    async def release(self, slot: IdempotencySlot) -> None:
        try:
            await self._release(
                keys=self._keys(
                    slot.user_email, slot.key, slot.addressed_run_id, slot.conversation_id
                ),
                args=[
                    slot.token,
                    slot.addressed_run_id or "",
                    slot.conversation_id or "",
                    slot.user_email,
                ],
            )
        except Exception as exc:
            self._unavailable(exc)
            logger.warning(
                "Could not release an idempotency reservation (it expires on its own): %s",
                type(exc).__name__,
            )

    async def find_follow_up(
        self,
        run_id: str,
        key: str,
        conversation_id: Optional[str] = None,
        owner: Optional[str] = None,
    ) -> Optional[Tuple[str, IdempotencyEntry]]:
        wanted = normalize_user_email(owner) if owner else None
        indexes = [self._addr_key(run_id, key)]
        if conversation_id:
            indexes.append(self._conv_key(conversation_id, key))
        try:
            # Each index is tried in turn: one that points at a record that is
            # gone (or at another run's) must not hide the next.
            for index in indexes:
                holder = await self._redis.client.get(index)
                if not holder:
                    continue
                holder = _text(holder)
                if wanted is not None and holder != wanted:
                    continue
                fields = await self._redis.client.hgetall(self._record_key(holder, key))
                if not fields:
                    continue
                entry = _entry_from_fields({_text(k): _text(v) for k, v in fields.items()})
                if entry.addressed_run_id is None:
                    continue
                if entry.addressed_run_id == run_id or (
                    conversation_id and entry.conversation_id == conversation_id
                ):
                    return holder, entry
        except Exception as exc:
            raise self._unavailable(exc) from exc
        return None
