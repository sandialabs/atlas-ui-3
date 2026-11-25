"""Idempotency records for the Agent Runs API (#1220).

A launch or follow-up that carries an ``idempotency_key`` is reserved here
before anything is admitted, so a client that retries after a timeout gets the
original run back instead of a second one. The reservation is keyed by
``(user, key)`` and holds the request fingerprint and, once the run exists,
its handle.

Two implementations, picked the way run admission is (``coordination.py``):

* :class:`InProcessIdempotencyStore`, the default. A process-local dict, which
  is exactly the single-instance behaviour from before #1220.
* ``RedisIdempotencyStore`` (``atlas/infrastructure/redis/idempotency_store.py``),
  bound by the app factory whenever the shared Redis pool exists. The
  reservation is one atomic script, so two replicas receiving the same key
  cannot both launch, and a replay on any replica returns the original handle.

Reserving is the only step that can refuse the request (``reserve`` raises
:class:`RunCoordinationUnavailableError` when the shared store cannot be
reached, so a keyed request fails closed). ``complete`` and ``release`` are
best effort: the run already exists or the launch already failed, and a lost
write is bounded by the pending TTL.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, Tuple
from uuid import uuid4

from atlas.application.chat.runs.coordination import RunCoordinationUnavailableError
from atlas.core.user_identity import normalize_user_email

__all__ = [
    "IDEMPOTENCY_PENDING_TTL_SECONDS",
    "IDEMPOTENCY_COMPLETE_RETRY_SECONDS",
    "IDEMPOTENCY_RENEW_INTERVAL_SECONDS",
    "IDEMPOTENCY_TTL_SECONDS",
    "MAX_IDEMPOTENCY_ENTRIES",
    "IdempotencyEntry",
    "IdempotencySlot",
    "IdempotencyStore",
    "InProcessIdempotencyStore",
    "RunCoordinationUnavailableError",
    "get_idempotency_store",
    "set_idempotency_store",
]

# Idempotency keys outlive the run record on purpose: a retry that arrives
# after the run was reaped must still not launch the work a second time.
IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60
# How long a reservation that has no run handle yet is honoured. Admission
# takes seconds; if the replica that reserved dies before it records the
# handle, the key frees itself after this long instead of staying blocked for
# the whole retention window.
IDEMPOTENCY_PENDING_TTL_SECONDS = 120
# While a request is admitting, its owner extends the pending reservation this
# often, so a slow admission (RAG discovery, a busy coordinator) never loses
# the key while it is still working. Only a replica that stops renewing (a
# crash) lets it lapse.
IDEMPOTENCY_RENEW_INTERVAL_SECONDS = IDEMPOTENCY_PENDING_TTL_SECONDS / 3
# When recording the run handle fails (a Redis blip after the run was
# admitted), the write is retried this often, for as long as the pending
# reservation would still have been honoured.
IDEMPOTENCY_COMPLETE_RETRY_SECONDS = 5.0
# In-process cap only; the shared store is bounded by its TTLs.
MAX_IDEMPOTENCY_ENTRIES = 10000


@dataclass
class IdempotencyEntry:
    fingerprint: str
    created_at: float
    handle: Optional[Dict[str, Any]] = None  # None while the first request is admitting
    # A follow-up's addressed run and conversation, so a retry can still be
    # replayed after that run has been reaped from the registry.
    addressed_run_id: Optional[str] = None
    conversation_id: Optional[str] = None


@dataclass(frozen=True)
class IdempotencySlot:
    """A granted reservation: what the owner needs to complete or release it."""

    user_email: str
    key: str
    token: str
    fingerprint: str
    addressed_run_id: Optional[str] = None
    conversation_id: Optional[str] = None
    # time.monotonic() taken before the reserve call, so a reservation's
    # expiry is never judged later than the store's own clock started.
    reserved_at: float = 0.0


class IdempotencyStore(Protocol):
    """Reservations for ``(user, idempotency_key)``. All methods are coroutines."""

    async def reserve(
        self,
        user_email: str,
        key: str,
        fingerprint: str,
        *,
        addressed_run_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Tuple[Optional[IdempotencySlot], Optional[IdempotencyEntry]]:
        """Claim the key, atomically.

        Returns ``(slot, None)`` when this request now owns the key, or
        ``(None, entry)`` with the entry already holding it (pending or
        completed). Raises :class:`RunCoordinationUnavailableError` when a
        shared store cannot be reached.
        """

    async def complete(self, slot: IdempotencySlot, handle: Dict[str, Any]) -> Optional[bool]:
        """Record the run handle on the reservation and keep it for the full TTL.

        ``True`` recorded. ``False`` the write could not be made (a shared store
        that is unreachable) and should be retried; the run already exists.
        ``None`` the key is another request's now: the handle must not be
        returned to this request's caller as the key's run.
        """

    async def renew(self, slot: IdempotencySlot) -> Optional[bool]:
        """Extend a still-pending reservation this request owns.

        ``True`` extended, ``False`` the reservation is gone or another
        request's (lost), ``None`` the store could not be reached.
        """

    async def release(self, slot: IdempotencySlot) -> None:
        """Drop a reservation that never produced a run, so a retry can succeed."""

    async def find_follow_up(
        self,
        run_id: str,
        key: str,
        conversation_id: Optional[str] = None,
        owner: Optional[str] = None,
    ) -> Optional[Tuple[str, IdempotencyEntry]]:
        """The ``(owner, entry)`` of a follow-up that used ``key`` and addressed
        ``run_id``, or any run of ``conversation_id`` when it is given. With
        ``owner`` only that user's record qualifies."""


class InProcessIdempotencyStore:
    """Process-local reservations. Its coroutines never suspend before the
    mutation, so on one event loop a reservation stays as atomic as the
    synchronous check-then-insert it replaced."""

    def __init__(self) -> None:
        self._entries: Dict[Tuple[str, str], IdempotencyEntry] = {}
        self._tokens: Dict[Tuple[str, str], str] = {}

    def clear(self) -> None:
        self._entries.clear()
        self._tokens.clear()

    def _prune(self, now: float) -> None:
        expired = [
            slot
            for slot, entry in self._entries.items()
            if now - entry.created_at > IDEMPOTENCY_TTL_SECONDS
        ]
        for slot in expired:
            self._drop(slot)
        if len(self._entries) > MAX_IDEMPOTENCY_ENTRIES:
            oldest = sorted(self._entries.items(), key=lambda item: item[1].created_at)
            for slot, entry in oldest[: len(self._entries) - MAX_IDEMPOTENCY_ENTRIES]:
                if entry.handle is not None:
                    self._drop(slot)

    def _drop(self, slot: Tuple[str, str]) -> None:
        self._entries.pop(slot, None)
        self._tokens.pop(slot, None)

    async def reserve(
        self,
        user_email: str,
        key: str,
        fingerprint: str,
        *,
        addressed_run_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Tuple[Optional[IdempotencySlot], Optional[IdempotencyEntry]]:
        now = time.time()
        self._prune(now)
        owner = normalize_user_email(user_email)
        slot_key = (owner, key)
        existing = self._entries.get(slot_key)
        if existing is not None:
            return None, existing
        token = uuid4().hex
        self._entries[slot_key] = IdempotencyEntry(
            fingerprint=fingerprint,
            created_at=now,
            addressed_run_id=addressed_run_id,
            conversation_id=conversation_id,
        )
        self._tokens[slot_key] = token
        return (
            IdempotencySlot(
                owner, key, token, fingerprint, addressed_run_id, conversation_id
            ),
            None,
        )

    async def complete(self, slot: IdempotencySlot, handle: Dict[str, Any]) -> Optional[bool]:
        slot_key = (slot.user_email, slot.key)
        entry = self._entries.get(slot_key)
        if entry is not None and self._tokens.get(slot_key) == slot.token:
            entry.handle = dict(handle)
            return True
        return None

    async def renew(self, slot: IdempotencySlot) -> Optional[bool]:
        # In-process reservations do not expire while pending.
        return self._tokens.get((slot.user_email, slot.key)) == slot.token

    async def release(self, slot: IdempotencySlot) -> None:
        slot_key = (slot.user_email, slot.key)
        entry = self._entries.get(slot_key)
        if (
            entry is not None
            and entry.handle is None
            and self._tokens.get(slot_key) == slot.token
        ):
            self._drop(slot_key)

    async def find_follow_up(
        self,
        run_id: str,
        key: str,
        conversation_id: Optional[str] = None,
        owner: Optional[str] = None,
    ) -> Optional[Tuple[str, IdempotencyEntry]]:
        self._prune(time.time())
        wanted = normalize_user_email(owner) if owner else None
        for (entry_owner, entry_key), entry in self._entries.items():
            if entry_key != key or entry.addressed_run_id is None:
                continue
            if wanted is not None and entry_owner != wanted:
                continue
            if entry.addressed_run_id == run_id or (
                conversation_id and entry.conversation_id == conversation_id
            ):
                return entry_owner, entry
        return None


_store: Optional[IdempotencyStore] = None


def get_idempotency_store() -> IdempotencyStore:
    """The process-wide store (in-process unless the factory bound Redis)."""
    global _store
    if _store is None:
        _store = InProcessIdempotencyStore()
    return _store


def set_idempotency_store(store: Optional[IdempotencyStore]) -> None:
    """Bind the store (the app factory; tests). ``None`` resets it."""
    global _store
    _store = store
