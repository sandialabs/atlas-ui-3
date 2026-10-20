"""Redis-backed session repository (issue #1091).

Each session is one Redis hash, ``<prefix>:session:<uuid>``, holding the
encoded session (``data``) and a monotonically increasing ``version``. Every
write refreshes the key's TTL, so a session nobody touches expires on its own.

Identity and versioning
-----------------------
The chat pipeline mutates the ``Session`` object it is handed and expects the
next ``get`` *in the same process* to return that same object. A repository
that deserialized a fresh copy on every ``get`` would hide those mutations.
This repository therefore keeps the sessions that are live on this replica
(created or written here and still active) in a local map and returns them by
reference; everything else is read from Redis.

Writes are optimistic: the copy carries the ``store_version`` it was read at,
and ``update`` refuses the write (``SessionConflictError``) when Redis already
holds a newer version -- for example because the user reconnected to another
replica that resumed the session and wrote it there. A stale copy can never
silently replace newer state. Writes from one process to one session are
serialized with a lock, so this process does not conflict with itself.

Failure behavior
----------------
A Redis error raises ``SessionStoreUnavailableError``. There is no fallback to
process-local state: that would let two replicas diverge without anyone
noticing. A session already live on this replica keeps answering ``get`` from
its local copy during an outage; its next write fails loudly instead.
"""

import asyncio
import logging
from typing import Any, Dict, Optional
from uuid import UUID

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.domain.errors import (
    SessionConflictError,
    SessionNotFoundError,
    SessionStoreUnavailableError,
)
from atlas.domain.sessions.models import Session
from atlas.infrastructure.redis.client import RedisClientManager
from atlas.infrastructure.sessions.codec import decode_session, encode_session

logger = logging.getLogger(__name__)


class RedisSessionRepository:
    """``SessionRepository`` over a shared Redis server."""

    def __init__(self, redis: RedisClientManager, ttl_seconds: int = 86400):
        if ttl_seconds < 1:
            raise ValueError("Session TTL must be at least one second")
        self._redis = redis
        self._ttl = int(ttl_seconds)
        self._live: Dict[UUID, Session] = {}
        self._locks: Dict[UUID, asyncio.Lock] = {}

    # -- helpers ---------------------------------------------------------

    def _key(self, session_id: UUID) -> str:
        return self._redis.key("session", str(session_id))

    def _lock(self, session_id: UUID) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = self._locks[session_id] = asyncio.Lock()
        return lock

    def _keep_or_evict(self, session: Session) -> None:
        if session.active:
            self._live[session.id] = session
        else:
            self._evict(session.id)

    def _evict(self, session_id: UUID) -> None:
        self._live.pop(session_id, None)
        lock = self._locks.get(session_id)
        if lock is not None and not lock.locked():
            self._locks.pop(session_id, None)

    def _unavailable(self, op: str, session_id: UUID, exc: BaseException) -> SessionStoreUnavailableError:
        self._redis.stats.record_error(exc)
        logger.error(
            "Session store %s failed for session %s: %s",
            op,
            sanitize_for_logging(str(session_id)),
            type(exc).__name__,
        )
        return SessionStoreUnavailableError(
            "The shared session store is unavailable. Try again shortly.",
            code="SESSION_STORE_UNAVAILABLE",
        )

    def is_live_here(self, session_id: UUID) -> bool:
        """True when this replica holds the session's working copy."""
        return session_id in self._live

    # -- SessionRepository protocol -------------------------------------

    async def get(self, session_id: UUID) -> Optional[Session]:
        """Return the live local copy, or a fresh copy read from Redis."""
        live = self._live.get(session_id)
        if live is not None:
            return live
        from redis.exceptions import RedisError

        try:
            fields = await self._redis.client.hmget(self._key(session_id), "data", "version")
        except RedisError as exc:
            raise self._unavailable("read", session_id, exc) from exc
        raw, version = fields
        if raw is None or version is None:
            return None
        try:
            return decode_session(raw, int(version))
        except (ValueError, KeyError, TypeError) as exc:
            self._redis.stats.incr("session_decode_errors")
            logger.error(
                "Stored session %s could not be decoded (%s); treating it as missing",
                sanitize_for_logging(str(session_id)),
                type(exc).__name__,
            )
            return None

    async def create(self, session: Session) -> Session:
        """Store ``session`` (replacing any record with the same id)."""
        from redis.exceptions import RedisError

        key = self._key(session.id)
        async with self._lock(session.id):
            payload = encode_session(session)
            try:
                async with self._redis.client.pipeline(transaction=True) as pipe:
                    pipe.hincrby(key, "version", 1)
                    pipe.hset(key, "data", payload)
                    pipe.expire(key, self._ttl)
                    version, _, _ = await pipe.execute()
            except RedisError as exc:
                raise self._unavailable("create", session.id, exc) from exc
            session.store_version = int(version)
            self._keep_or_evict(session)
        logger.info(
            "Created session %s for user %s",
            sanitize_for_logging(str(session.id)),
            sanitize_for_logging(session.user_email),
        )
        return session

    async def update(self, session: Session) -> Session:
        """Write ``session`` if no newer version has been stored since it was read."""
        from redis.exceptions import RedisError, WatchError

        key = self._key(session.id)
        async with self._lock(session.id):
            payload = encode_session(session)
            try:
                async with self._redis.client.pipeline(transaction=True) as pipe:
                    await pipe.watch(key)
                    stored = await pipe.hget(key, "version")
                    if stored is None:
                        if session.id not in self._live:
                            await pipe.unwatch()
                            raise SessionNotFoundError(
                                f"Session {session.id} not found", code="SESSION_NOT_FOUND"
                            )
                        # Live here but expired in Redis (idle past the TTL):
                        # nobody else can hold a newer copy, so re-store it.
                        self._redis.stats.incr("session_expired_while_live")
                        logger.warning(
                            "Session %s expired from the shared store while live; re-storing it",
                            sanitize_for_logging(str(session.id)),
                        )
                    elif int(stored) != session.store_version:
                        await pipe.unwatch()
                        raise self._conflict(session, int(stored))
                    new_version = (int(stored) if stored is not None else session.store_version) + 1
                    pipe.multi()
                    pipe.hset(key, mapping={"data": payload, "version": new_version})
                    pipe.expire(key, self._ttl)
                    await pipe.execute()
            except WatchError:
                raise self._conflict(session, None)
            except RedisError as exc:
                raise self._unavailable("update", session.id, exc) from exc
            session.store_version = new_version
            self._keep_or_evict(session)
        return session

    def _conflict(self, session: Session, stored: Optional[int]) -> SessionConflictError:
        self._redis.stats.incr("session_conflicts")
        self._evict(session.id)
        logger.warning(
            "Session %s write refused: this copy is version %d, the store holds %s "
            "(another connection or replica wrote it since)",
            sanitize_for_logging(str(session.id)),
            session.store_version,
            "a newer one" if stored is None else str(stored),
        )
        return SessionConflictError(
            f"Session {session.id} was changed by another connection",
            code="SESSION_CONFLICT",
        )

    async def delete(self, session_id: UUID) -> bool:
        """Remove the session everywhere. Returns True when a record existed."""
        from redis.exceptions import RedisError

        live = self._live.get(session_id)
        if live is not None:
            live.active = False
        self._evict(session_id)
        try:
            removed = await self._redis.client.delete(self._key(session_id))
        except RedisError as exc:
            raise self._unavailable("delete", session_id, exc) from exc
        if removed:
            logger.info("Deleted session %s", sanitize_for_logging(str(session_id)))
        return bool(removed) or live is not None

    async def exists(self, session_id: UUID) -> bool:
        """Check if a session exists."""
        if session_id in self._live:
            return True
        from redis.exceptions import RedisError

        try:
            return bool(await self._redis.client.exists(self._key(session_id)))
        except RedisError as exc:
            raise self._unavailable("exists", session_id, exc) from exc

    def stats(self) -> Dict[str, Any]:
        """Local counters for the health endpoint."""
        return {"live_sessions": len(self._live), "ttl_seconds": self._ttl}
