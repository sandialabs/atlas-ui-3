"""Bounded, short-lived group-membership answers for the prompt library (issue #1169).

A listing of the library asks the authorizer about every granted group the
caller might be in. Without reuse that is one ``check_user_in_group`` call --
an HTTP request when an external authorizer is configured -- per group per
listing, per user, per page view. ``MembershipResolver`` bounds that load:

* **Reuse with a deadline.** A definite answer (member / not a member) is
  reused for ``ttl`` seconds (``PROMPT_LIBRARY_MEMBERSHIP_CACHE_SECONDS``,
  default 30, at most 300, 0 disables). That is also the documented
  revocation latency for a change made *in the authorization service*: a
  user removed from a group keeps group-derived ``use`` access for at most
  ``ttl`` seconds. Changes made in Atlas -- unsharing, removing a grant,
  archiving -- are read from the database on every request and are never
  delayed by this cache. An answer is never reused past its deadline.
* **Failures are never cached.** An authorizer error or timeout is "unknown"
  (``None``) for this call only; the next call asks again. Unknown is never
  turned into "member" (fail closed), and callers report it as unknown
  (incomplete listing, 503 on sharing) rather than as "not a member".
* **No cross-user reuse.** The key is a SHA-256 of the exact identity and
  group name; an answer is only ever reused for the same (user, group).
* **Shared across replicas when Redis is configured.** With
  ``SESSION_REPOSITORY_TYPE=redis`` answers live in Redis (``SET ... EX ttl``)
  through the process's bounded pool, so replicas share them and expire them
  together. Without Redis each replica keeps a bounded in-process table
  (``MAX_LOCAL_ENTRIES``). Correctness never depends on sharing: every
  replica's answer is bounded by the same ``ttl``; sharing only lowers load.
  A Redis error degrades to asking the authorizer, never to a guess.
* **Bounded concurrency.** Identical concurrent checks in one process share
  one authorizer call (single flight), and at most
  ``MAX_CONCURRENT_CHECKS`` checks run at once per process, so a burst of
  listings cannot open an unbounded number of HTTP clients.
"""

import asyncio
import hashlib
import logging
import time
import weakref
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_LOCAL_ENTRIES = 10_000
MAX_CONCURRENT_CHECKS = 32

Check = Callable[[str, str], Awaitable[Optional[bool]]]


def membership_key(user_email: str, group: str) -> str:
    """Opaque cache key for one (identity, group) pair; no identity in clear."""
    return hashlib.sha256(f"{user_email}\x00{group}".encode("utf-8")).hexdigest()


class LocalMembershipCache:
    """Per-process TTL table, bounded in size (oldest evicted first)."""

    def __init__(self, max_entries: int = MAX_LOCAL_ENTRIES, clock: Callable[[], float] = time.monotonic):
        self._entries: "OrderedDict[str, Tuple[bool, float]]" = OrderedDict()
        self._max = max_entries
        self._clock = clock

    async def get(self, key: str) -> Optional[bool]:
        hit = self._entries.get(key)
        if hit is None:
            return None
        value, expires = hit
        if expires <= self._clock():
            self._entries.pop(key, None)
            return None
        return value

    async def put(self, key: str, value: bool, ttl: int) -> None:
        self._entries.pop(key, None)
        self._entries[key] = (value, self._clock() + ttl)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)


class RedisMembershipCache:
    """Answers shared by every replica through the process's Redis pool."""

    def __init__(self, redis_manager: Any):
        self._redis = redis_manager

    def _key(self, key: str) -> str:
        return self._redis.key("prompt-library", "membership", key)

    async def get(self, key: str) -> Optional[bool]:
        raw = await self._redis.client.get(self._key(key))
        if raw is None:
            return None
        return raw in (b"1", "1")

    async def put(self, key: str, value: bool, ttl: int) -> None:
        await self._redis.client.set(self._key(key), b"1" if value else b"0", ex=ttl)


# How long one shared authorizer check may run before it counts as "unknown"
# (not cached, so the next request asks again).
CHECK_TIMEOUT_S = 30.0


class MembershipResolver:
    """``membership(user, group)`` -> True / False / None (unknown), bounded and reused."""

    def __init__(
        self,
        check: Callable[[], Check],
        ttl_seconds: Callable[[], int],
        store: Callable[[], Any],
        max_concurrent: int = MAX_CONCURRENT_CHECKS,
    ):
        # Resolved per call so a live config reload, the app's Redis manager
        # and test patches of the authorizer are always the current ones.
        self._check = check
        self._ttl = ttl_seconds
        self._store = store
        self._max_concurrent = max_concurrent
        # Per event loop: asyncio primitives must not cross loops.
        self._loop_state: "weakref.WeakKeyDictionary[Any, Tuple[asyncio.Semaphore, Dict[str, asyncio.Future]]]" = (
            weakref.WeakKeyDictionary()
        )

    def _state(self) -> Tuple[asyncio.Semaphore, Dict[str, asyncio.Future]]:
        loop = asyncio.get_running_loop()
        state = self._loop_state.get(loop)
        if state is None:
            state = (asyncio.Semaphore(self._max_concurrent), {})
            self._loop_state[loop] = state
        return state

    async def membership(self, user_email: str, group: str) -> Optional[bool]:
        key = membership_key(user_email, group)
        ttl = max(0, int(self._ttl() or 0))
        store = self._store() if ttl else None
        if store is not None:
            try:
                cached = await store.get(key)
            except Exception as e:  # a cache outage costs load, never correctness
                logger.warning("Prompt-library membership cache read failed (%s)", type(e).__name__)
                cached = None
            if cached is not None:
                return cached

        semaphore, inflight = self._state()
        task = inflight.get(key)
        if task is None:
            # One check per identity and group at a time, run as its own task:
            # a caller that stops waiting (a bound ran out, its request was
            # cancelled, or it only needed one confirmed group) stops waiting
            # for the answer, not the check. The check finishes, every other
            # caller sharing it gets the real answer, and it is cached for the
            # next request. Each check is bounded by the authorizer client's
            # own timeout and the per-process concurrency limit.
            task = asyncio.ensure_future(self._check_and_store(semaphore, key, user_email, group, store, ttl))
            inflight[key] = task
            task.add_done_callback(lambda done, key=key: inflight.pop(key, None) if inflight.get(key) is done else None)
        return await asyncio.shield(task)

    async def _check_and_store(
        self, semaphore: asyncio.Semaphore, key: str, user_email: str, group: str, store: Any, ttl: int
    ) -> Optional[bool]:
        try:
            answer = await self._ask(semaphore, user_email, group)
        except Exception:  # _ask fails closed already; this is belt and braces
            answer = None
        if answer is not None and store is not None:
            try:
                await store.put(key, answer, ttl)
            except Exception as e:
                logger.warning("Prompt-library membership cache write failed (%s)", type(e).__name__)
        return answer

    async def _ask(self, semaphore: asyncio.Semaphore, user_email: str, group: str) -> Optional[bool]:
        async with semaphore:
            try:
                # A hard bound of its own: callers stop waiting at their own
                # (shorter) bounds, but the shared check must also end, or one
                # that never returns would hold its concurrency slot and answer
                # "unknown" to every later caller for good. The built-in
                # authorizer's HTTP timeout is per phase, and a customised
                # ``is_user_in_group`` may have none.
                answer = await asyncio.wait_for(self._check()(user_email, group), CHECK_TIMEOUT_S)
            except Exception as e:
                # Fail closed: an authorizer outage must not grant access, and
                # it is "unknown", not "not a member".
                logger.warning(
                    "Group check failed for prompt-library access; treating as unknown: %s",
                    type(e).__name__,
                )
                return None
        if answer is None:
            return None
        return bool(answer)
