"""Registry of conversation-scoped chat/agent runs (issue #884).

Before this module the transport tracked exactly one in-flight turn per
WebSocket connection (a single ``active_chat_task`` slot). That made
"navigate away" and "stop executing" the same action: starting a new chat, or
restoring another conversation, cancelled whatever was running.

A *run* is one history-mutating execution of a conversation. It has a stable
``run_id``, the ``conversation_id`` it mutates, the authenticated owner, an
isolated ``session_id`` (so two conversations never share a Session/history
object), a status, and a cancellation scope. The registry is process-wide and
deliberately **not** owned by a connection: a run outlives the socket that
started it, which is what lets a run keep going after the browser closes.

Invariants enforced here, all from the issue's acceptance criteria:

* At most one *non-terminal* run per conversation. A second message for a busy
  conversation is not a second run -- the transport steers it into the running
  one (issue #824). ``start`` raising :class:`ConversationBusyError` is the
  backstop for a client that races past that check.
* At most ``max_concurrent_runs_per_user`` non-terminal runs per user. A run
  paused on tool approval is *not* terminal, so it counts against the cap --
  that is the explicit product decision on issue #884.
* Terminal runs are retained briefly so a client that reconnects can still see
  how a run it was watching ended, then reaped.

The registry stores no ``asyncio`` primitives that assume a particular loop
beyond the task itself, so it is safe to construct in tests without a running
loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from uuid import UUID, uuid4

from atlas.core.log_sanitizer import sanitize_for_logging

logger = logging.getLogger(__name__)

# How long a run that reached a terminal state stays queryable. Long enough
# that a browser reopened a few minutes later still learns "that finished" /
# "that failed" instead of silently showing nothing; short enough that an
# always-on server does not accumulate run records forever.
TERMINAL_RETENTION_SECONDS = 30 * 60

# Cap on retained terminal runs per user, so a scripted client that starts and
# finishes thousands of runs inside the retention window cannot grow the
# registry without bound.
MAX_RETAINED_TERMINAL_RUNS_PER_USER = 50


class RunStatus(str, Enum):
    """Lifecycle states from the issue's suggested state machine.

    ``queued -> running -> waiting_for_input -> running -> completed``
    with ``failed`` and ``cancelled`` as the other terminal states.
    """

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATUSES


_TERMINAL_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
)


class RunRegistryError(Exception):
    """Base class for run admission failures."""


class ConversationBusyError(RunRegistryError):
    """A conversation already has a non-terminal run.

    Raised only as a backstop: the normal path for a second message to a busy
    conversation is steering (issue #824), decided before ``start`` is called.
    """


class ConcurrencyLimitError(RunRegistryError):
    """The user already has the maximum number of non-terminal runs."""

    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(
            f"You already have {limit} conversation runs in progress. "
            "Stop or wait for one to finish before starting another. "
            "Runs paused waiting for tool approval count toward this limit."
        )


@dataclass
class RunRecord:
    """One tracked run.

    ``task`` is attached after creation because the task body usually needs the
    ``run_id`` (to tag its events), so the record has to exist first.
    """

    run_id: str
    conversation_id: str
    user_email: str
    session_id: UUID
    status: RunStatus = RunStatus.QUEUED
    task: Optional["asyncio.Task"] = None
    steering: Optional[Any] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    error: Optional[str] = None
    # Set once the socket that started the run goes away. Purely informational
    # -- a detached run keeps executing; this is what distinguishes "still
    # running for a user who is watching" from "still running unattended" in
    # logs and in the wall-clock reaper.
    detached: bool = False
    # Free-form label for what the run is waiting on (e.g. "tool_approval"),
    # surfaced to the client alongside WAITING_FOR_INPUT.
    waiting_on: Optional[str] = None

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal

    def to_public_dict(self) -> Dict[str, Any]:
        """The shape sent to the client for conversation-history indicators."""
        return {
            "run_id": self.run_id,
            "conversation_id": self.conversation_id,
            "status": self.status.value,
            "waiting_on": self.waiting_on,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "ended_at": self.ended_at,
            "error": self.error,
            "detached": self.detached,
        }


class RunRegistry:
    """Process-wide registry of runs, keyed by ``run_id``.

    Not thread-safe by design: every caller lives on the single asyncio event
    loop that serves the app. All mutation is synchronous and non-awaiting, so
    admission checks (cap, per-conversation lock) and the insert that follows
    them cannot be interleaved by another coroutine -- which is what makes the
    check-then-insert sequence atomic without a lock.
    """

    def __init__(self, max_concurrent_runs_per_user: int = 5):
        self._max_concurrent = max_concurrent_runs_per_user
        self._runs: Dict[str, RunRecord] = {}
        self._listeners: Dict[str, List[Callable[[RunRecord], None]]] = {}

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    @property
    def max_concurrent_runs_per_user(self) -> int:
        return self._max_concurrent

    def set_max_concurrent_runs_per_user(self, limit: int) -> None:
        """Update the cap.

        Called on each admission from settings so an admin change takes effect
        without a restart. Lowering the cap never cancels runs that are already
        admitted; it only refuses new ones until the count drops.
        """
        self._max_concurrent = max(1, int(limit))

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def get(self, run_id: Optional[str]) -> Optional[RunRecord]:
        if not run_id:
            return None
        return self._runs.get(run_id)

    def get_for_user(self, run_id: Optional[str], user_email: str) -> Optional[RunRecord]:
        """Ownership-checked lookup.

        Every transport action that addresses a run by id goes through this, so
        a client cannot stop, steer, or inspect another user's run by guessing
        an id.
        """
        record = self.get(run_id)
        if record is None or record.user_email != user_email:
            return None
        return record

    def active_for_conversation(
        self, conversation_id: Optional[str], user_email: str
    ) -> Optional[RunRecord]:
        """The non-terminal run for a conversation, if any."""
        if not conversation_id:
            return None
        for record in self._runs.values():
            if (
                record.conversation_id == conversation_id
                and record.user_email == user_email
                and not record.is_terminal
            ):
                return record
        return None

    def active_for_user(self, user_email: str) -> List[RunRecord]:
        return [
            r
            for r in self._runs.values()
            if r.user_email == user_email and not r.is_terminal
        ]

    def snapshot_for_user(self, user_email: str) -> List[Dict[str, Any]]:
        """Every run this user can still meaningfully see, newest first.

        Includes retained terminal runs: a browser reopened after a background
        run finished should be able to show "completed" rather than nothing.
        """
        self.reap_terminal()
        records = [r for r in self._runs.values() if r.user_email == user_email]
        records.sort(key=lambda r: r.created_at, reverse=True)
        return [r.to_public_dict() for r in records]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(
        self,
        *,
        conversation_id: str,
        user_email: str,
        session_id: Optional[UUID] = None,
        steering: Optional[Any] = None,
    ) -> RunRecord:
        """Admit a new run, or raise if it would violate an invariant.

        Reaping first matters: without it a user whose earlier runs all
        finished but are still inside the retention window would be refused
        once the *retained* records outnumbered the cap.
        """
        self.reap_terminal()

        if self.active_for_conversation(conversation_id, user_email) is not None:
            raise ConversationBusyError(
                "This conversation already has a run in progress."
            )

        active = self.active_for_user(user_email)
        if len(active) >= self._max_concurrent:
            raise ConcurrencyLimitError(self._max_concurrent)

        record = RunRecord(
            run_id=str(uuid4()),
            conversation_id=conversation_id,
            user_email=user_email,
            # Each run gets its own Session so two concurrent conversations
            # never write through the same history object.
            session_id=session_id or uuid4(),
            steering=steering,
        )
        self._runs[record.run_id] = record
        logger.info(
            "Run %s started for conversation %s (active runs for user: %d)",
            record.run_id,
            sanitize_for_logging(str(conversation_id)),
            len(active) + 1,
        )
        self._notify(record)
        return record

    def attach_task(self, run_id: str, task: "asyncio.Task") -> None:
        record = self.get(run_id)
        if record is None:
            return
        record.task = task
        self.set_status(run_id, RunStatus.RUNNING)

    def set_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        error: Optional[str] = None,
        waiting_on: Optional[str] = None,
    ) -> Optional[RunRecord]:
        """Move a run to a new state and notify listeners.

        Terminal is sticky: once a run is cancelled/failed/completed, a late
        status update from the unwinding task cannot resurrect it. Without this
        a cancelled run's own cleanup path -- which legitimately runs *after*
        the cancel -- would report ``completed`` and the client would show the
        wrong outcome.
        """
        record = self.get(run_id)
        if record is None:
            return None
        if record.is_terminal:
            return record

        record.status = status
        record.updated_at = time.time()
        record.error = error
        record.waiting_on = waiting_on if status == RunStatus.WAITING_FOR_INPUT else None
        if status.is_terminal:
            record.ended_at = record.updated_at
            record.steering = None
            record.task = None
        self._notify(record)
        return record

    def mark_detached(self, run_id: str) -> None:
        """Note that the run's originating socket is gone."""
        record = self.get(run_id)
        if record is None or record.is_terminal:
            return
        record.detached = True
        record.updated_at = time.time()
        self._notify(record)

    def cancel(self, run_id: str, user_email: str) -> bool:
        """Cancel one run, addressed by id and checked for ownership.

        Returns whether a live run was actually cancelled, so the transport can
        distinguish "stopped it" from "there was nothing to stop" (an id the
        client believed was live but had already finished).
        """
        record = self.get_for_user(run_id, user_email)
        if record is None or record.is_terminal:
            return False
        task = record.task
        # Mark first: the cancellation propagates asynchronously, and the run
        # must never be observable as still running once the user has stopped
        # it.
        self.set_status(run_id, RunStatus.CANCELLED)
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    def cancel_all_for_user(self, user_email: str) -> int:
        count = 0
        for record in list(self.active_for_user(user_email)):
            if self.cancel(record.run_id, user_email):
                count += 1
        return count

    def remove(self, run_id: str) -> None:
        self._runs.pop(run_id, None)

    def reap_terminal(self, now: Optional[float] = None) -> int:
        """Drop terminal runs past the retention window, and trim the excess.

        Returns the number of records removed (used by tests).
        """
        now = time.time() if now is None else now
        removed = 0
        per_user_terminal: Dict[str, List[RunRecord]] = {}

        for run_id, record in list(self._runs.items()):
            if not record.is_terminal:
                continue
            ended = record.ended_at or record.updated_at
            if now - ended > TERMINAL_RETENTION_SECONDS:
                del self._runs[run_id]
                removed += 1
            else:
                per_user_terminal.setdefault(record.user_email, []).append(record)

        for records in per_user_terminal.values():
            if len(records) <= MAX_RETAINED_TERMINAL_RUNS_PER_USER:
                continue
            records.sort(key=lambda r: r.ended_at or r.updated_at)
            for record in records[: len(records) - MAX_RETAINED_TERMINAL_RUNS_PER_USER]:
                self._runs.pop(record.run_id, None)
                removed += 1

        return removed

    def enforce_wall_clock(self, max_seconds: float, now: Optional[float] = None) -> List[str]:
        """Cancel non-terminal runs that have exceeded the wall-clock budget.

        This is the backstop against unbounded unattended execution: a detached
        run has nobody watching it, so nothing else would ever stop it.
        Returns the ids that were cancelled.
        """
        if max_seconds <= 0:
            return []
        now = time.time() if now is None else now
        expired: List[str] = []
        for record in list(self.active_for_user_all()):
            if now - record.created_at <= max_seconds:
                continue
            logger.warning(
                "Run %s exceeded the %.0fs wall-clock limit; cancelling",
                record.run_id,
                max_seconds,
            )
            task = record.task
            self.set_status(
                record.run_id,
                RunStatus.FAILED,
                error="This run exceeded the maximum allowed run time and was stopped.",
            )
            if task is not None and not task.done():
                task.cancel()
            expired.append(record.run_id)
        return expired

    def active_for_user_all(self) -> List[RunRecord]:
        """Every non-terminal run, across all users."""
        return [r for r in self._runs.values() if not r.is_terminal]

    # ------------------------------------------------------------------
    # Status notifications
    # ------------------------------------------------------------------
    def add_listener(
        self, user_email: str, listener: Callable[[RunRecord], None]
    ) -> Callable[[], None]:
        """Subscribe a connection to this user's run status changes.

        This is how a conversation the user is *not* looking at still gets an
        indicator in the history list: status transitions are published to
        every live socket of the owning user, not just the one that started
        the run. Returns an unsubscribe callable.
        """
        self._listeners.setdefault(user_email, []).append(listener)

        def _unsubscribe() -> None:
            listeners = self._listeners.get(user_email)
            if not listeners:
                return
            try:
                listeners.remove(listener)
            except ValueError:
                # Already removed -- unsubscribing twice (a reconnect racing a
                # teardown) is a no-op, not an error.
                pass
            if not listeners:
                self._listeners.pop(user_email, None)

        return _unsubscribe

    def _notify(self, record: RunRecord) -> None:
        for listener in list(self._listeners.get(record.user_email, [])):
            try:
                listener(record)
            except Exception:  # pragma: no cover - a bad listener must not
                # break run bookkeeping for every other connection.
                logger.debug("Run status listener raised", exc_info=True)


_registry: Optional[RunRegistry] = None


def get_run_registry() -> RunRegistry:
    """The process-wide registry."""
    global _registry
    if _registry is None:
        _registry = RunRegistry()
    return _registry


def reset_run_registry() -> None:
    """Drop the singleton (tests only)."""
    global _registry
    _registry = None
