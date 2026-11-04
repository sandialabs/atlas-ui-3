"""Cross-replica run admission (issue #1128).

The run registry (``registry.py``) is process-local: it holds the live task,
the steering channel and the replay buffers, none of which can leave the
process that runs the turn. What *can* be shared is the decision to admit a
run, and that decision has to be shared, or every replica enforces "one run
per conversation" and "at most N runs per user" against only the runs it
happens to host.

A :class:`RunCoordinator` makes that decision. The registry still checks its
own invariants afterwards; the coordinator is the cluster-wide check that runs
first. Two implementations:

* :class:`InProcessRunCoordinator`, the default. It admits everything, which
  leaves the registry's own checks as the whole story -- exactly the
  single-instance behaviour from before #1128.
* ``RedisRunCoordinator`` (``atlas/infrastructure/redis/run_coordinator.py``),
  bound by the app factory whenever the shared Redis pool exists. It claims
  the conversation and a user slot in one Lua script, so two replicas racing
  for the last slot cannot both win. The key layout and record fields it
  writes are the coordination contract later #1128 work builds on; see
  ``docs/developer/run-coordination.md``.

A claim is a *lease*: it expires unless the owning replica renews it, so a
replica that dies holding claims frees them within one lease period instead
of locking the user out until someone cleans Redis by hand.
"""

from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass
from typing import Iterable, Optional, Protocol
from uuid import uuid4

from atlas.application.chat.runs.registry import RunRegistryError

logger = logging.getLogger(__name__)

# How long a claim lives without renewal, and how often the owner renews it.
# The interval is a third of the lease so one missed beat (a GC pause, a slow
# Redis round trip) never loses a claim that is still being worked on.
RUN_LEASE_SECONDS = 30
RUN_LEASE_RENEW_INTERVAL_SECONDS = 10
# How long a released run record stays readable after the run ended, so a
# replica asked about it shortly afterwards still finds the outcome.
RUN_RECORD_RETENTION_SECONDS = 300


class RunCoordinationUnavailableError(RunRegistryError):
    """The shared coordinator could not be reached, so nothing was admitted.

    Admission fails closed: admitting without the shared check would let a
    user exceed the cluster-wide cap during exactly the outage when nobody is
    watching.
    """

    def __init__(self, detail: str = ""):
        self.detail = detail
        super().__init__(
            "Runs cannot be started right now because the run coordinator is "
            "unavailable. Try again in a moment."
        )


@dataclass(frozen=True)
class RunClaim:
    """A granted admission: what the owner needs to renew or release it.

    ``generation`` is the fencing token. It comes from one counter that only
    ever increases, so of two claims ever granted for a conversation the later
    one has the larger generation, and a write carrying an older generation
    can be refused.
    """

    run_id: str
    conversation_id: str
    user_email: str
    owner: str
    generation: int
    lease_expires_at: float  # epoch seconds, from the coordinator's clock


class RunCoordinator(Protocol):
    """Cluster-wide admission for runs. All methods are coroutine functions."""

    replica_id: str
    # False when ``claim`` always returns ``None`` and never suspends.
    tracks_claims: bool

    async def claim(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_email: str,
        max_runs_per_user: int,
    ) -> Optional[RunClaim]:
        """Claim the conversation and one of the user's run slots, atomically.

        Returns the claim, or ``None`` when this coordinator does not track
        claims (the in-process default). Raises ``ConversationBusyError``,
        ``ConcurrencyLimitError`` or :class:`RunCoordinationUnavailableError`.
        A refusal claims nothing.
        """

    async def release(self, claim: RunClaim, status: str) -> bool:
        """Free the claim, recording the run's final ``status``.

        Only the claim's own generation is released: a stale owner can never
        free a slot a newer claim holds. Returns whether anything was freed.
        """

    async def renew(self, claims: Iterable[RunClaim], statuses: dict) -> dict:
        """Extend the leases of ``claims``; ``statuses`` maps run id to status.

        Returns run id -> whether the lease is still held. A ``False`` means the
        lease was lost (expired and taken over, or released elsewhere).
        """


_process_replica_id: Optional[tuple] = None


def default_replica_id() -> str:
    """This process's replica id: host, pid and a random suffix, fixed per process.

    The host comes first because on Kubernetes it is the pod name, which is
    what an operator searches logs for. The suffix keeps a restarted process
    on the same host (same pid after a container restart) distinct. It is
    minted once: a coordinator rebuilt later in the same process (a second
    ``AppFactory``, say) must still own the claims the first one took, or
    their renewals and releases would fail the owner check.
    """
    global _process_replica_id
    pid = os.getpid()
    # Keyed by pid so a forked worker mints its own id instead of inheriting.
    if _process_replica_id is None or _process_replica_id[0] != pid:
        _process_replica_id = (pid, f"{socket.gethostname()}-{pid}-{uuid4().hex[:8]}")
    return _process_replica_id[1]


class InProcessRunCoordinator:
    """Admits everything; the registry's process-local checks decide.

    Its coroutines never suspend, so on a single event loop an admission that
    uses it stays as atomic as the synchronous check-then-insert it replaced.
    """

    tracks_claims = False

    def __init__(self, replica_id: Optional[str] = None):
        self.replica_id = replica_id or default_replica_id()

    async def claim(self, **_kwargs) -> Optional[RunClaim]:
        return None

    async def release(self, claim: RunClaim, status: str) -> bool:
        return False

    async def renew(self, claims: Iterable[RunClaim], statuses: dict) -> dict:
        return {claim.run_id: True for claim in claims}


_coordinator: Optional[RunCoordinator] = None


def get_run_coordinator() -> RunCoordinator:
    """The process-wide coordinator (in-process unless the factory bound Redis)."""
    global _coordinator
    if _coordinator is None:
        _coordinator = InProcessRunCoordinator()
    return _coordinator


def set_run_coordinator(coordinator: Optional[RunCoordinator]) -> None:
    """Bind the coordinator (the app factory; tests). ``None`` resets it."""
    global _coordinator
    _coordinator = coordinator
