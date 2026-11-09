"""Registry of conversation-scoped chat/agent runs (issue #884).

Before this module the transport tracked exactly one in-flight turn per
WebSocket connection (a single per-socket task slot, since removed). That made
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

# The admission errors live in their own module so the coordinator can raise
# them without importing the registry.
from atlas.application.chat.runs.admission_errors import (
    ConcurrencyLimitError,
    ConversationBusyError,
)
from atlas.application.chat.runs.coordination import RUN_RETENTION_SECONDS, release_claim_soon
from atlas.application.chat.runs.stream_replay import StreamReplay
from atlas.core.coordination_metrics import (
    EndReason,
    coordination_metrics,
    default_end_reason,
    outcome_for,
)
from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.user_identity import normalize_user_email
from atlas.domain.conversation_origin import origin_for_run_source

logger = logging.getLogger(__name__)


def _current_task() -> Optional[asyncio.Task]:
    try:
        return asyncio.current_task()
    except RuntimeError:  # no running loop
        return None

# Projection writes the registry has scheduled and not yet finished. Holding
# them here keeps each task alive until its write lands.
_PROJECTION_TASKS: "set[asyncio.Task]" = set()

# A run that reached a terminal state stays queryable for RUN_RETENTION_SECONDS
# (coordination.py): long enough that a browser reopened a few minutes later
# still learns "that finished" / "that failed", short enough that an always-on
# server does not accumulate run records forever.
# Cap on retained terminal runs per user, so a scripted client that starts and
# finishes thousands of runs inside the retention window cannot grow the
# registry without bound.
MAX_RETAINED_TERMINAL_RUNS_PER_USER = 50

# How far down (and, in the shared record's scripts, up) the launch tree
# liveness and retention look. ATLAS_LAUNCH_MAX_DEPTH has no upper bound, so
# this is a safety cap, not the depth limit itself.
MAX_SUBTREE_DEPTH = 64

# Matches the title length ConversationRepository stores for a conversation.
RUN_TITLE_MAX_CHARS = 200

# Frames that pause a run until the user answers, and frames that mean the
# tool it was paused on has settled one way or another.
INPUT_REQUEST_EVENTS = frozenset({"tool_approval_request", "elicitation_request"})
TOOL_SETTLED_EVENTS = frozenset(
    {"tool_complete", "tool_error", "tool_interrupted", "tool_result"}
)


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


STEER_QUEUED = "steering_queued"
STEER_QUEUE_FULL = "steering_queue_full"
STEER_RUN_FINISHED = "run_finished"
STEER_NOT_DRAINING = "not_draining"


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
    # Request ids a steer queued on this run (id -> outcome), kept after the
    # channel is released at the end so a late retry still gets its remembered
    # answer. Ids only: the queued text is not retained.
    steer_seen: Optional[Any] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    error: Optional[str] = None
    # Why the run reached its terminal state (issue #1129): an
    # ``EndReason`` value. It is what lets operators tell a run that failed
    # on its own from one that was interrupted because its replica went away;
    # both are ``FAILED`` on the public run API.
    end_reason: Optional[str] = None
    # Set once the socket that started the run goes away. Purely informational
    # -- a detached run keeps executing; this is what distinguishes "still
    # running for a user who is watching" from "still running unattended" in
    # logs and in the wall-clock reaper.
    detached: bool = False
    # The user's auto-approve switch, captured when the turn was admitted and
    # updated when they change it. The tool gate reads this on the owning
    # replica, so a closed browser does not leave a user-level tool waiting.
    # Server-side only: not part of the public dict. Never set on a launched
    # child; those tools are covered by the launch approval, not this switch.
    auto_approve_user_tools: bool = False
    # Free-form label for what the run is waiting on (e.g. "tool_approval"),
    # surfaced to the client alongside WAITING_FOR_INPUT.
    waiting_on: Optional[str] = None
    # Set when this run was started by ``atlas_launch`` from another run
    # (issue #925). ``parent_run_id`` is what makes stopping a parent stop its
    # children, and ``depth`` is what bounds recursion: a launched run is one
    # deeper than the run that launched it.
    parent_run_id: Optional[str] = None
    # The conversation that launched this run. Observation tools are scoped to
    # the conversation, not just the specific parent run, so later turns can
    # still see children launched earlier from the same conversation.
    parent_conversation_id: Optional[str] = None
    depth: int = 0
    # The prompt that started the run, truncated. A run's conversation is not
    # stored until the turn ends, so this is the only name the history list
    # can give it in the meantime -- in this tab and in every other one.
    title: Optional[str] = None
    # The launch source given at admission (``atlas_launch``,
    # ``agent_runs_api``, or empty for a turn the user sent). Known before
    # ``launch_spec`` is attached, so the first public dict already says how
    # the run's conversation was launched (issue #1187).
    source: str = ""
    # What a launched run was started with -- model, tools, data sources,
    # workspace, compliance level -- so ``atlas_follow_up`` can continue the
    # sub-conversation on the same capability surface without the model
    # re-choosing it. Server-side only: never part of the public dict.
    launch_spec: Optional[Dict[str, Any]] = field(default=None, repr=False, compare=False)
    # The final assistant reply of *this* run, captured when it completes. A
    # sub-conversation that has received follow-ups holds several runs on one
    # conversation, so "the conversation's last assistant message" answers for
    # the newest run only; ``atlas_result`` on an older run id reads this.
    result: Optional[str] = field(default=None, repr=False, compare=False)
    # The exact frames that asked for input, kept so they can be re-sent.
    # A request emitted while the user was looking at another conversation (or
    # had the browser closed) is otherwise gone: the client dropped it, and the
    # id and arguments needed to answer it exist nowhere else. Replaying them
    # when the conversation is opened is what makes "a run paused on approval
    # can be approved after reconnect" true rather than aspirational. A list,
    # not a single slot: one agent step can fire several approval-gated tools
    # in parallel, and every one of them needs its answer.
    pending_requests: List[Dict[str, Any]] = field(default_factory=list)
    # The token segment the run is streaming right now (issue #957). A client
    # that left the conversation mid-stream dropped every frame since; this is
    # what a reopen replays so the reply starts at its beginning instead of
    # the token that happened to be current on arrival.
    stream: StreamReplay = field(default_factory=StreamReplay, repr=False, compare=False)
    # The cluster-wide admission this run holds (issue #1128), a
    # ``coordination.RunClaim`` when a shared coordinator granted one. Released
    # when the run reaches a terminal state; renewed by the lease heartbeat
    # while it is live. Server-side only.
    claim: Optional[Any] = field(default=None, repr=False, compare=False)
    # The claim lease renewal refused, moved out of ``claim`` the first time
    # (so it is neither renewed nor reported again). Kept for fencing: its
    # ``generation`` is the one this replica held.
    lost_claim: Optional[Any] = field(default=None, repr=False, compare=False)
    # Set when this replica lost the run's ownership lease (issue #1128):
    # another replica declared it interrupted, so nothing this process does
    # for the run may reach shared state any more.
    fenced: bool = False
    # An incognito or not-saved turn (issue #1128). The run still claims its
    # conversation cluster-wide, but nothing about the turn is shared: the
    # claim carries owner, generation and status, and no snapshot, title,
    # error text, launch spec, projection row or approval outcome is written.
    off_record: bool = False
    # The turn executes on the socket's own session, so it ends with the
    # socket (a plain chat turn, or an agent turn without history). A run with
    # its own session is detached when the socket closes and keeps going.
    connection_bound: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal

    def follows_user_auto_approve(self) -> bool:
        """Whether the user's switch applies to this run.

        A launched child does not follow it. A child started from a plain
        turn has no ``parent_run_id``, but its depth is greater than zero and
        it carries a launch spec. An agent-runs API run also carries a launch
        spec: the browser switch does not approve its tools.
        """
        if self.parent_run_id:
            return False
        if (self.depth or 0) > 0:
            return False
        if self.launch_spec:
            return False
        return True

    def note_auto_approve(self, frame: Any) -> None:
        """Record an explicit ``auto_approve_tools`` bit from an admission frame.

        Absent means the client did not say, so the run keeps what it had.
        Only a real boolean ``True`` opts in. A launched child is left alone:
        its tools run on model-chosen arguments, and the launch approval is
        what covers them. Call this when the run is admitted, not when a later
        chat or steer frame arrives: that frame can be a stale tab.
        """
        if not self.follows_user_auto_approve() or self.is_terminal:
            return
        if not isinstance(frame, dict) or "auto_approve_tools" not in frame:
            return
        self.auto_approve_user_tools = frame.get("auto_approve_tools") is True

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
            # completed | user_cancelled | error | wall_clock_limit |
            # replica_shutdown (issue #1129); None while the run is live.
            "end_reason": self.end_reason,
            "detached": self.detached,
            "parent_run_id": self.parent_run_id,
            # The conversation that launched this run (issue #1081). The sidebar
            # draws the tree from conversation ids, so it needs this while the
            # child is still only an in-memory run and has no stored record yet.
            "parent_conversation_id": self.parent_conversation_id,
            # Set for a run started by the Agent Runs REST API (issue #1187),
            # so the history list can mark its conversation before the first
            # save records the same value in the conversation's metadata.
            "launch_origin": origin_for_run_source(self.source),
            "depth": self.depth,
            "title": self.title,
            # Ends with the socket that started it: nothing to rediscover.
            "connection_bound": self.connection_bound,
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
        self._children: Dict[str, List[str]] = {}
        self._conversation_children: Dict[str, List[str]] = {}
        self._listeners: Dict[str, List[Callable[[RunRecord], None]]] = {}
        # Every-run observers, any owner (the lease coordinator, #1128).
        self._observers: List[Callable[[RunRecord], None]] = []

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
        an id. Ownership compares normalized emails, the same way the
        conversation repository does: a proxy that hands over ``User`` and
        ``user`` must read as one owner, not two.
        """
        record = self.get(run_id)
        if record is None or record.user_email != normalize_user_email(user_email):
            return None
        return record

    @staticmethod
    def _release_steering(record: "RunRecord") -> None:
        """Drop the channel at the end of a run but keep its remembered ids."""
        if record.steering is not None:
            record.steer_seen = record.steering.seen_ids()
        record.steering = None

    def prior_steer(self, run_id: Optional[str], request_id: Optional[str]) -> Optional[str]:
        """The outcome an earlier steer with this ``request_id`` had, or None."""
        record = self.get(run_id)
        if record is None:
            return None
        if record.steering is not None:
            return record.steering.recall_outcome(request_id)
        if isinstance(request_id, str) and record.steer_seen:
            return record.steer_seen.get(request_id)
        return None

    def steer(self, run_id: Optional[str], text: str, request_id: Optional[str] = None) -> str:
        """Queue a steering message on a run this replica owns: the only way in.

        Returns ``STEER_QUEUED``, ``STEER_QUEUE_FULL``, ``STEER_RUN_FINISHED``
        or ``STEER_NOT_DRAINING``. A ``request_id`` already queued on this run
        answers ``STEER_QUEUED`` again, even once the run has ended, and queues
        nothing; refusals changed nothing, so they are not remembered, so a
        client retry, a replayed bus command and a local resend all land once. Callers check
        ownership and refusals first; this checks the run's state at the moment
        of queueing, after any of their awaits.
        """
        record = self.get(run_id)
        if record is None:
            return STEER_RUN_FINISHED
        steering = record.steering
        # The remembered outcome wins over the terminal guard: a retry of an
        # id that was queued answers the same even after the run ends.
        seen = self.prior_steer(run_id, request_id)
        if seen is not None:
            return seen
        if record.is_terminal:
            return STEER_RUN_FINISHED
        if steering is None:
            return STEER_NOT_DRAINING
        if not steering.active:
            return STEER_NOT_DRAINING
        try:
            # Imported here: the agent package imports this module's run state.
            from atlas.application.chat.agent.steering import QueuedSteer

            # A non-text payload (a null frame) goes in as it came, so the
            # loop skips it as before.
            steering.queue.put_nowait(
                QueuedSteer(text, request_id) if isinstance(text, str) else text
            )
        except asyncio.QueueFull:
            # Nothing was queued, so nothing is remembered: a retry of this
            # request may succeed once the loop has drained.
            return STEER_QUEUE_FULL
        steering.remember_outcome(request_id, STEER_QUEUED)
        return STEER_QUEUED

    def active_for_conversation(
        self, conversation_id: Optional[str], user_email: str
    ) -> Optional[RunRecord]:
        """The non-terminal run for a conversation, if any."""
        if not conversation_id:
            return None
        owner = normalize_user_email(user_email)
        for record in self._runs.values():
            if (
                record.conversation_id == conversation_id
                and record.user_email == owner
                and not record.is_terminal
            ):
                return record
        return None

    def stoppable_for_conversation(
        self, conversation_id: Optional[str], user_email: str
    ) -> List[RunRecord]:
        """Every run of the user's conversation that a stop must reach.

        The conversation's live run, and any finished run of it that still has
        a live descendant (stopping it cascades to the sub-conversations it
        launched, which usually outlive the turn that started them).
        """
        if not conversation_id:
            return []
        owner = normalize_user_email(user_email)
        found = []
        for record in list(self._runs.values()):
            if record.conversation_id != conversation_id or record.user_email != owner:
                continue
            if not record.is_terminal or self._has_live_descendant(record.run_id, 0):
                found.append(record)
        return found

    def _has_live_descendant(self, run_id: str, depth: int) -> bool:
        if depth > MAX_SUBTREE_DEPTH:
            return False
        return any(
            not child.is_terminal or self._has_live_descendant(child.run_id, depth + 1)
            for child in self.children_of(run_id, include_terminal=True)
        )

    def active_for_conversation_any_owner(
        self, conversation_id: Optional[str]
    ) -> Optional[str]:
        """The owner of the non-terminal run on a conversation, if any.

        Used by the transport to refuse a turn that names a conversation id
        currently executing for *someone else*: an unsaved run's id is not in
        the repository yet, so the ownership check on stored records cannot
        catch it. Only the owner's email is returned, never the record.
        """
        if not conversation_id:
            return None
        for record in self._runs.values():
            if record.conversation_id == conversation_id and not record.is_terminal:
                return normalize_user_email(record.user_email)
        return None

    def children_of(self, run_id: Optional[str], *, include_terminal: bool = False) -> List[RunRecord]:
        """Runs launched by ``run_id`` (issue #925).

        Direct children only; the cascade in :meth:`cancel` walks the tree one
        level at a time, so a grandchild is reached through its own parent.
        """
        if not run_id:
            return []
        return [
            self._runs[child_id]
            for child_id in self._children.get(run_id, [])
            if child_id in self._runs
            and (include_terminal or not self._runs[child_id].is_terminal)
        ]

    def children_of_conversation(
        self, conversation_id: Optional[str], *, include_terminal: bool = False
    ) -> List[RunRecord]:
        """Runs launched from ``conversation_id`` across all of its turns."""
        if not conversation_id:
            return []
        return [
            self._runs[child_id]
            for child_id in self._conversation_children.get(conversation_id, [])
            if child_id in self._runs
            and (include_terminal or not self._runs[child_id].is_terminal)
        ]

    def set_auto_approve_user_tools(self, user_email: str, enabled: bool) -> int:
        """Apply the user's switch to the user-started runs this process owns.

        Returns how many records changed. Launched children are skipped, and
        another user's runs are never touched. The executor reads the flag on
        the replica that owns the run; a socket attached to a different
        replica can only update runs that replica itself is executing.
        """
        owner = normalize_user_email(user_email)
        opted_in = enabled is True
        changed = 0
        for record in self._runs.values():
            if (
                record.user_email != owner
                or record.is_terminal
                or not record.follows_user_auto_approve()
            ):
                continue
            if record.auto_approve_user_tools is opted_in:
                continue
            record.auto_approve_user_tools = opted_in
            changed += 1
        return changed

    def active_for_user(self, user_email: str) -> List[RunRecord]:
        owner = normalize_user_email(user_email)
        return [
            r
            for r in self._runs.values()
            if r.user_email == owner and not r.is_terminal
        ]

    def active_bound_to(self, session_id: Any) -> List[RunRecord]:
        """Live runs executing on this connection's own session."""
        return [
            r for r in self._runs.values()
            if r.connection_bound and r.session_id == session_id and not r.is_terminal
        ]

    def records_for_user(self, user_email: str) -> List[RunRecord]:
        """Every retained run for this user, newest first, terminal included.

        The download path needs the terminal ones: a file is usually fetched
        just *after* the run that produced it finished, and that run's session
        is where the file is registered.
        """
        owner = normalize_user_email(user_email)
        records = [r for r in self._runs.values() if r.user_email == owner]
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    def all_records(self) -> List[RunRecord]:
        """Every retained record, any owner. For callers already authorized
        across users (the agent-runs service API); never a per-user view."""
        return list(self._runs.values())

    def snapshot_for_user(self, user_email: str) -> List[Dict[str, Any]]:
        """Every run this user can still meaningfully see, newest first.

        Includes retained terminal runs: a browser reopened after a background
        run finished should be able to show "completed" rather than nothing.
        """
        self.reap_terminal()
        owner = normalize_user_email(user_email)
        records = [r for r in self._runs.values() if r.user_email == owner]
        records.sort(key=lambda r: r.created_at, reverse=True)
        return [r.to_public_dict() for r in records]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def check_admission(self, conversation_id: str, user_email: str) -> List[RunRecord]:
        """Raise if this process would refuse the run; return the user's active runs.

        Reaping first matters: without it a user whose earlier runs all
        finished but are still inside the retention window would be refused
        once the *retained* records outnumbered the cap.
        """
        self.reap_terminal()

        if self.active_for_conversation(conversation_id, user_email) is not None:
            coordination_metrics().run_rejected("conversation_busy")
            raise ConversationBusyError(
                "This conversation already has a run in progress."
            )

        active = self.active_for_user(user_email)
        if len(active) >= self._max_concurrent:
            coordination_metrics().run_rejected("user_concurrency_limit")
            raise ConcurrencyLimitError(self._max_concurrent)
        return active

    def start(
        self,
        *,
        conversation_id: str,
        user_email: str,
        session_id: Optional[UUID] = None,
        steering: Optional[Any] = None,
        parent_run_id: Optional[str] = None,
        parent_conversation_id: Optional[str] = None,
        depth: int = 0,
        title: Optional[str] = None,
        run_id: Optional[str] = None,
        off_record: bool = False,
        connection_bound: bool = False,
        source: str = "",
    ) -> RunRecord:
        """Admit a new run, or raise if it would violate an invariant.

        ``run_id`` is supplied when the id was chosen before admission -- the
        cluster-wide claim (``admission.admit_run``) is keyed by it.
        """
        active = self.check_admission(conversation_id, user_email)

        if parent_conversation_id is None and parent_run_id:
            parent = self.get(parent_run_id)
            parent_conversation_id = None if parent is None else parent.conversation_id

        record = RunRecord(
            run_id=run_id or str(uuid4()),
            conversation_id=conversation_id,
            # Stored normalized so ownership comparisons (this registry's, the
            # transport guard's) read one owner for ``User@x`` and ``user@x``,
            # the way the conversation repository already does.
            user_email=normalize_user_email(user_email),
            # Each run gets its own Session so two concurrent conversations
            # never write through the same history object.
            session_id=session_id or uuid4(),
            steering=steering,
            parent_run_id=parent_run_id,
            parent_conversation_id=parent_conversation_id,
            depth=max(0, int(depth or 0)),
            # An off-record run has no title: it is the prompt.
            title=(str(title)[:RUN_TITLE_MAX_CHARS] or None) if title and not off_record else None,
            # Set here, not after the insert: listeners are notified at the
            # end of this method and their first frame must carry the origin.
            source=str(source or ""),
            off_record=bool(off_record),
            connection_bound=bool(connection_bound),
        )
        self._runs[record.run_id] = record
        if record.parent_run_id:
            self._children.setdefault(record.parent_run_id, []).append(record.run_id)
        if record.parent_conversation_id:
            self._conversation_children.setdefault(record.parent_conversation_id, []).append(
                record.run_id
            )
        logger.info(
            "Run %s started for conversation %s (active runs for user: %d)",
            record.run_id,
            sanitize_for_logging(str(conversation_id)),
            len(active) + 1,
        )
        coordination_metrics().run_admitted(
            record.run_id, kind="launched" if record.parent_run_id else "top_level", depth=record.depth
        )
        # A previous run's projection may never have received its terminal
        # write (a lost publish, a dead replica). This run replaces it, so a
        # text-only run that publishes no tool frame still clears a stale
        # pending card. The store ignores it when there is no document.
        if not record.off_record:
            self._publish_projection({
                "type": "run_started",
                "run_id": record.run_id,
                "conversation_id": record.conversation_id,
                "user_email": record.user_email,
            })
        self._notify(record)
        return record

    def attach_task(self, run_id: str, task: "asyncio.Task") -> None:
        record = self.get(run_id)
        if record is None:
            return
        record.task = task
        if record.is_terminal:
            # The run was stopped (or failed) in the window between admission
            # and the task starting -- ``run_started`` goes out before the
            # task exists, so a stop can land first. Terminal is sticky, so
            # set_status below would keep the recorded outcome while the
            # fresh task ran the turn to completion and persisted it anyway.
            # Cancel it: the user already stopped this run.
            if not task.done():
                task.cancel()
            return
        self.set_status(run_id, RunStatus.RUNNING)

    def set_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        error: Optional[str] = None,
        waiting_on: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Optional[RunRecord]:
        """Move a run to a new state and notify listeners.

        ``reason`` is the ``EndReason`` recorded with a terminal state; it
        defaults from the status (completed / user_cancelled / error).

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

        old_status = record.status
        record.status = status
        record.updated_at = time.time()
        record.error = error
        record.waiting_on = waiting_on if status == RunStatus.WAITING_FOR_INPUT else None
        if status != RunStatus.WAITING_FOR_INPUT:
            # Whatever it was waiting for is no longer outstanding.
            record.pending_requests.clear()
        if status.is_terminal:
            record.ended_at = record.updated_at
            record.end_reason = reason or default_end_reason(status.value)
            self._release_steering(record)
            record.task = None
            # Whatever the run was streaming closed with it (issue #957): the
            # turn's text is in the stored transcript now, and a stale buffer
            # would replay a finished answer over a reopen's fresh one.
            record.stream.clear()
            self._publish_terminal(record)
            self._release_claim(record)
        if old_status != status:
            coordination_metrics().run_transition(
                record.run_id,
                old_status.value,
                status.value,
                end_reason=record.end_reason if status.is_terminal else None,
                duration_s=(record.ended_at - record.created_at) if status.is_terminal else None,
                detached=record.detached,
            )
        self._notify(record)
        return record

    @staticmethod
    def _release_claim(record: "RunRecord") -> None:
        """Give the run's cluster-wide slot back (issue #1128).

        Scheduled, not awaited: status changes are synchronous. If the release
        never lands (no loop, Redis down) the claim's lease expires instead.
        """
        claim, record.claim = record.claim, None
        if claim is None:
            return
        release_claim_soon(claim, record.status.value)

    def _publish_terminal(self, record: "RunRecord") -> None:
        """Tell every replica this run finished.

        The registry stays process-local. The shared projection is what
        another replica's run list would otherwise keep showing as active
        until the document expired. A fenced run's outcome was already
        published by the replica that took it over.
        """
        if not record.status.is_terminal or record.fenced or record.off_record:
            return
        event = {
            "type": "run_status",
            "status": record.status.value,
            "run_id": record.run_id,
            "conversation_id": record.conversation_id,
            "user_email": record.user_email,
        }
        if record.error:
            event["error"] = record.error
        if record.end_reason:
            event["end_reason"] = record.end_reason
        self._publish_projection(event)

    @staticmethod
    def _publish_projection(event: Dict[str, Any]) -> None:
        """Write one run-lifecycle event to the shared projection, best effort.

        The registry is synchronous, so the write runs as a task. The task is
        kept in ``_PROJECTION_TASKS`` until it finishes: the event loop only
        holds a weak reference, and an unreferenced task can be collected
        before the write lands.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _send(payload=event):
            try:
                from atlas.application.chat.runs.execution_state import publish_execution_event

                await publish_execution_event(payload)
            except Exception:
                logger.warning("execution projection lifecycle update failed", exc_info=True)

        task = loop.create_task(_send())
        _PROJECTION_TASKS.add(task)
        task.add_done_callback(_PROJECTION_TASKS.discard)

    def note_stream_token(
        self, run_id: Optional[str], token: str, is_first: bool, is_last: bool
    ) -> None:
        """Record a ``token_stream`` frame into the run's replay buffer.

        Called from the notifier, which every token producer reaches exactly
        once, from inside the run's own task -- so the buffer is one run's
        text, never a blend of two concurrent ones. Unknown or terminal runs
        are ignored: there is nothing to replay for a run that is over.
        """
        if not run_id:
            return
        record = self._runs.get(run_id)
        if record is None or record.is_terminal:
            return
        record.stream.observe(token, is_first, is_last)

    def note_event(self, run_id: Optional[str], frame: Any) -> None:
        """Keep a run's status in step with the frames it emits.

        An approval or elicitation request pauses the run: it is marked
        ``waiting_for_input`` (which drives the "Needs approval" marker in the
        history list) and the frame is kept for replay, because a client that
        is looking at another conversation drops it. Several requests can be
        outstanding at once -- one agent step may fire several approval-gated
        tools in parallel -- so each is stored and replayed individually. A
        tool settling clears its own request -- timed out, or answered on a
        path that did not go through the transport -- but only when the settle
        frame answers a request that is outstanding; a sibling tool finishing
        while another approval is still pending leaves the pause intact.

        Every transport chokepoint calls this -- the connection adapter the
        agent loop publishes through, the turn callback, and a launched run's
        child connection -- so the bookkeeping does not depend on which path
        a producer happened to use. Only the first observer of a frame can
        change anything; the rest are no-ops.
        """
        if not run_id or not isinstance(frame, dict):
            return
        event_type = frame.get("type")
        if event_type in INPUT_REQUEST_EVENTS:
            record = self.get(run_id)
            if record is None or record.is_terminal:
                return
            if self._is_stored_request(record.pending_requests, frame):
                return
            self.set_status(run_id, RunStatus.WAITING_FOR_INPUT, waiting_on=event_type)
            self.set_pending_request(run_id, frame)
        elif event_type == "tool_start":
            self._drop_started_approval(run_id, frame)
        elif event_type in TOOL_SETTLED_EVENTS:
            record = self.get(run_id)
            if record is None or record.status != RunStatus.WAITING_FOR_INPUT:
                return
            outstanding = record.pending_requests
            frame_ids = {
                key: frame[key]
                for key in ("tool_call_id", "elicitation_id")
                if frame.get(key) is not None
            }
            if frame_ids:
                # Clear only the requests this frame actually answers; a
                # sibling tool settling while others are still outstanding
                # must not drop requests that still need an answer -- the
                # exact state ``note_event`` exists to fix.
                record.pending_requests = [
                    request for request in outstanding
                    if not any(
                        request.get(key) == value for key, value in frame_ids.items()
                    )
                ]
                if not record.pending_requests:
                    self.set_status(run_id, RunStatus.RUNNING)
                return
            # A frame with no identifier at all can only mean something when
            # exactly one request is outstanding; with several it is
            # ambiguous, and dropping any of them could strand an unanswered
            # tool until its approval timed out.
            if len(outstanding) == 1:
                self.set_status(run_id, RunStatus.RUNNING)

    @staticmethod
    def _is_stored_request(pending_requests: List[Dict[str, Any]], frame: Dict[str, Any]) -> bool:
        """Whether the exact request frame is already stored (idempotent replay)."""
        return any(
            request.get("tool_call_id") == frame.get("tool_call_id")
            and request.get("elicitation_id") == frame.get("elicitation_id")
            for request in pending_requests
        )

    def _drop_started_approval(self, run_id: str, frame: Dict[str, Any]) -> None:
        """A tool that starts was approved, on this replica or another.

        An answer recorded through the shared store (issue #1122) never goes
        through ``answer_pending_request`` here, so the start frame is what
        clears the card. Only approval requests match: an elicitation is
        raised after its tool has started.
        """
        record = self.get(run_id)
        if record is None or record.status != RunStatus.WAITING_FOR_INPUT:
            return
        invocation = frame.get("invocation_id")
        tool_call_id = frame.get("tool_call_id")
        if not invocation and not tool_call_id:
            return

        def _started(request: Dict[str, Any]) -> bool:
            if request.get("type") != "tool_approval_request":
                return False
            if invocation and request.get("invocation_id"):
                return request["invocation_id"] == invocation
            return bool(tool_call_id) and request.get("tool_call_id") == tool_call_id

        remaining = [request for request in record.pending_requests if not _started(request)]
        if len(remaining) == len(record.pending_requests):
            return
        record.pending_requests = remaining
        if not remaining:
            self.set_status(run_id, RunStatus.RUNNING)
        else:
            self._notify(record)

    def set_pending_request(self, run_id: str, frame: Optional[Dict[str, Any]]) -> None:
        """Remember a request the run is blocked on, for later replay."""
        record = self.get(run_id)
        if record is None or record.is_terminal:
            return
        if not isinstance(frame, dict):
            return
        if self._is_stored_request(record.pending_requests, frame):
            return
        record.pending_requests.append(dict(frame))

    def pending_requests_for_conversation(
        self, conversation_id: Optional[str], user_email: str
    ) -> List[Dict[str, Any]]:
        """Outstanding input requests for a conversation, for replay on open."""
        record = self.active_for_conversation(conversation_id, user_email)
        if record is None or not record.pending_requests:
            return []
        return [dict(request) for request in record.pending_requests]

    def answer_pending_request(
        self,
        run_id: str,
        *,
        tool_call_id: Optional[str] = None,
        elicitation_id: Optional[str] = None,
    ) -> bool:
        """One outstanding request was answered through the response path.

        Drops the matching stored request and returns the run to ``running``
        only when nothing is outstanding anymore. With several tools paused
        in parallel, answering one must not discard the others' replayable
        requests -- they still need their answers, and the run is still
        paused on them. Returns whether the run was waiting on input.
        """
        record = self.get(run_id)
        if record is None or record.status != RunStatus.WAITING_FOR_INPUT:
            return False

        def _is_answered(request: Dict[str, Any]) -> bool:
            if tool_call_id is not None and request.get("tool_call_id") == tool_call_id:
                return True
            if elicitation_id is not None and request.get("elicitation_id") == elicitation_id:
                return True
            return False

        record.pending_requests = [
            request for request in record.pending_requests if not _is_answered(request)
        ]
        if not record.pending_requests:
            self.set_status(run_id, RunStatus.RUNNING)
        else:
            # Still paused on the others; republish so every tab's "Needs
            # approval" marker reflects what remains.
            self._notify(record)
        return True

    def mark_detached(self, run_id: str) -> None:
        """Note that the run's originating socket is gone."""
        record = self.get(run_id)
        if record is None or record.is_terminal:
            return
        record.detached = True
        record.updated_at = time.time()
        self._notify(record)

    def cancel(
        self,
        run_id: str,
        user_email: str,
        *,
        status: RunStatus = RunStatus.CANCELLED,
        error: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> bool:
        """Cancel one run, addressed by id and checked for ownership.

        Returns whether a live run was actually cancelled, so the transport can
        distinguish "stopped it" from "there was nothing to stop" (an id the
        client believed was live but had already finished).
        """
        record = self.get_for_user(run_id, user_email)
        if record is None:
            return False
        # Stopping a parent stops the sub-conversations it launched (#925).
        # Children run in their own tasks, so cancelling the parent's task
        # alone would leave them working -- and billing -- with nobody
        # watching.
        #
        # This is deliberately *above* the terminal check. ``atlas_launch``
        # returns a handle rather than an answer, so the parent turn normally
        # finishes while its children are still working: by the time a user
        # stops the conversation, the parent run is usually already terminal.
        # Cascading only for a live parent would make "stopping a conversation
        # stops the ones it launched" true in the rare case and false in the
        # common one.
        cascaded = False
        # ``include_terminal=True``: a child that has already finished may still
        # have running children of its own, and skipping it would hide that
        # whole branch from the stop. Cancelling a terminal record is a no-op
        # beyond its own cascade.
        for child in self.children_of(run_id, include_terminal=True):
            cascaded = self.cancel(
                child.run_id, user_email, status=status, error=error, reason=reason
            ) or cascaded
        if record.is_terminal:
            return cascaded
        task = record.task
        # Mark first: the cancellation propagates asynchronously, and the run
        # must never be observable as still running once the user has stopped
        # it.
        self.set_status(run_id, status, error=error, reason=reason)
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    def mark_interrupted(self, run_id: str, error: str) -> Optional[RunRecord]:
        """This replica lost the run's lease (#1128): stop it, locally only.

        Another replica has recorded the run as failed/interrupted, so the
        local record says the same even if it had already ended here, and
        the task is cancelled so no further tool runs. ``fenced`` keeps the
        outcome out of shared state: the projection already has it.
        """
        record = self.get(run_id)
        if record is None:
            return None
        task = record.task
        old_status = record.status
        record.fenced = True
        record.status = RunStatus.FAILED
        record.end_reason = EndReason.FENCED.value
        record.error = error
        record.waiting_on = None
        record.pending_requests.clear()
        record.updated_at = time.time()
        record.ended_at = record.ended_at or record.updated_at
        self._release_steering(record)
        record.task = None
        # The claim belongs to whoever took the run over; never renew or
        # release it from here (the scripts would refuse anyway).
        record.claim = None
        record.stream.clear()
        if not old_status.is_terminal:
            coordination_metrics().run_transition(
                record.run_id,
                old_status.value,
                record.status.value,
                end_reason=record.end_reason,
                duration_s=record.ended_at - record.created_at,
                detached=record.detached,
            )
        self._notify(record)
        if task is not None and not task.done() and task is not _current_task():
            # A run that fences itself is already unwinding with the
            # CancelledError its guard raised; a second, pending cancel would
            # abort the stopped-turn commit that keeps its partial transcript.
            task.cancel()
        return record

    def cancel_all_for_user(self, user_email: str) -> int:
        count = 0
        for record in list(self.active_for_user(user_email)):
            if self.cancel(record.run_id, user_email):
                count += 1
        return count

    def remove(self, run_id: str) -> None:
        record = self._runs.pop(run_id, None)
        if record is None:
            return
        if record.parent_run_id:
            siblings = self._children.get(record.parent_run_id, [])
            if run_id in siblings:
                siblings.remove(run_id)
            if not siblings:
                self._children.pop(record.parent_run_id, None)
        if record.parent_conversation_id:
            siblings = self._conversation_children.get(record.parent_conversation_id, [])
            if run_id in siblings:
                siblings.remove(run_id)
            if not siblings:
                self._conversation_children.pop(record.parent_conversation_id, None)
        self._children.pop(run_id, None)

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
            if self._has_live_descendants(run_id):
                # A finished parent whose sub-conversations are still running
                # must outlive them (#925): reaping it now would orphan the
                # subtree, and a later stop addressed at the parent would find
                # no record and cascade to nothing. Descendants, not just direct
                # children -- a finished child can itself have a live one.
                continue
            # The window starts when the last descendant ended, as the shared
            # record's does (coordinator scripts), so both expire together.
            ended = max(self._ended_at(record), self._latest_end_below(run_id))
            if now - ended > RUN_RETENTION_SECONDS:
                self.remove(run_id)
                removed += 1
            else:
                per_user_terminal.setdefault(record.user_email, []).append(record)

        for records in per_user_terminal.values():
            if len(records) <= MAX_RETAINED_TERMINAL_RUNS_PER_USER:
                continue
            records.sort(key=lambda r: r.ended_at or r.updated_at)
            for record in records[: len(records) - MAX_RETAINED_TERMINAL_RUNS_PER_USER]:
                self.remove(record.run_id)
                removed += 1

        return removed

    @staticmethod
    def _ended_at(record: RunRecord) -> float:
        return record.ended_at or record.updated_at

    def _latest_end_below(self, run_id: str, _depth: int = 0) -> float:
        """When the last run below ``run_id`` in the launch tree ended (0 if none)."""
        if _depth > MAX_SUBTREE_DEPTH:  # pragma: no cover - depth is capped far below this
            return 0.0
        latest = 0.0
        for child in self.children_of(run_id, include_terminal=True):
            latest = max(latest, self._ended_at(child) if child.is_terminal else 0.0,
                         self._latest_end_below(child.run_id, _depth + 1))
        return latest

    def _has_live_descendants(self, run_id: str, _depth: int = 0) -> bool:
        """Whether anything below ``run_id`` in the launch tree is still running."""
        if _depth > MAX_SUBTREE_DEPTH:  # pragma: no cover - depth is capped far below this
            return False
        for child in self.children_of(run_id, include_terminal=True):
            if not child.is_terminal:
                return True
            if self._has_live_descendants(child.run_id, _depth + 1):
                return True
        return False

    def enforce_wall_clock(self, max_seconds: float, now: Optional[float] = None) -> List[str]:
        """Stop non-terminal runs that have exceeded the wall-clock budget.

        This is the backstop against unbounded unattended execution: a detached
        run has nobody watching it, so nothing else would ever stop it.

        The run is recorded as ``FAILED``, not ``CANCELLED``: ``CANCELLED``
        means the user stopped it, and a user who returns to a conversation
        needs to see that it hit a limit rather than believe they stopped it
        themselves. Returns the ids that were stopped.
        """
        if max_seconds <= 0:
            return []
        now = time.time() if now is None else now
        expired: List[str] = []
        for record in list(self.active_for_user_all()):
            if record.is_terminal:
                continue
            if now - record.created_at <= max_seconds:
                continue
            logger.warning(
                "Run %s exceeded the %.0fs wall-clock limit; stopping it and marking it failed",
                record.run_id,
                max_seconds,
            )
            task = record.task
            # Same cascade as an explicit stop (#925): a parent that hit the
            # wall clock must not leave its sub-conversations running.
            timeout_error = "This run exceeded the maximum allowed run time and was stopped."
            for child in self.children_of(record.run_id, include_terminal=True):
                self.cancel(
                    child.run_id,
                    child.user_email,
                    status=RunStatus.FAILED,
                    error=timeout_error,
                    reason=EndReason.WALL_CLOCK_LIMIT.value,
                )
            self.set_status(
                record.run_id,
                RunStatus.FAILED,
                error=timeout_error,
                reason=EndReason.WALL_CLOCK_LIMIT.value,
            )
            if task is not None and not task.done():
                task.cancel()
            expired.append(record.run_id)
        return expired

    def active_for_user_all(self) -> List[RunRecord]:
        """Every non-terminal run, across all users."""
        return [r for r in self._runs.values() if not r.is_terminal]

    def interrupt_active(self, reason: str = EndReason.REPLICA_SHUTDOWN.value) -> List[str]:
        """Mark every non-terminal run on this replica interrupted, and cancel it.

        Called when the replica shuts down (issue #1129). Without it the event
        loop cancels the run tasks as it closes and their own handlers record
        ``CANCELLED`` -- indistinguishable from a user pressing stop -- or the
        process exits before any terminal state is published at all. The run
        is recorded ``FAILED`` with an interrupted ``end_reason``; completed
        steps stay in history and nothing is retried.
        """
        error = (
            "This run was interrupted because the server running it shut down "
            "(restart, rollout or scale-in). Completed steps were kept; it was not retried."
        )
        interrupted: List[str] = []
        for record in list(self.active_for_user_all()):
            if record.is_terminal:  # a cascade above may have ended it already
                continue
            task = record.task
            self.set_status(record.run_id, RunStatus.FAILED, error=error, reason=reason)
            if task is not None and not task.done():
                task.cancel()
            interrupted.append(record.run_id)
        return interrupted

    def status_counts(self) -> Dict[str, int]:
        """Runs on this replica by status, plus detached and interrupted counts."""
        counts: Dict[str, int] = {s.value: 0 for s in RunStatus.__members__.values()}
        counts["detached"] = 0
        counts["interrupted"] = 0
        for record in self._runs.values():
            counts[record.status.value] += 1
            if record.detached and not record.is_terminal:
                counts["detached"] += 1
            if record.is_terminal and outcome_for(record.status.value, record.end_reason) == "interrupted":
                counts["interrupted"] += 1
        return counts

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
        the run. Returns an unsubscribe callable. The key is normalized so a
        socket authenticated under a differently-cased email still receives
        the runs it owns.
        """
        key = normalize_user_email(user_email)
        self._listeners.setdefault(key, []).append(listener)

        def _unsubscribe() -> None:
            listeners = self._listeners.get(key)
            if not listeners:
                return
            try:
                listeners.remove(listener)
            except ValueError:
                # Already removed -- unsubscribing twice (a reconnect racing a
                # teardown) is a no-op, not an error.
                pass
            if not listeners:
                self._listeners.pop(key, None)

        return _unsubscribe

    def add_observer(self, observer: Callable[[RunRecord], None]) -> Callable[[], None]:
        """Observe every run's changes, whoever owns it. Returns an unsubscribe."""
        self._observers.append(observer)

        def _unsubscribe() -> None:
            if observer in self._observers:
                self._observers.remove(observer)

        return _unsubscribe

    def _notify(self, record: RunRecord) -> None:
        for observer in list(self._observers):
            try:
                observer(record)
            except Exception:  # pragma: no cover - same rule as listeners
                logger.debug("Run observer raised", exc_info=True)
        for listener in list(self._listeners.get(normalize_user_email(record.user_email), [])):
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
