"""Leases, orphan detection and fencing for run claims (issue #1128).

Admission (``admission.py``, ``RedisRunCoordinator``) gives every run a
cluster-wide claim in the shared run record ``<p>:run:<run_id>``: an owner
(replica id), a fencing generation from ``<p>:run-fence``, and a lease the
owner renews every third of ``RUN_LEASE_SECONDS``. This module adds what
happens when the owner stops renewing, on top of that same record (see
``docs/developer/run-coordination.md``):

* **Orphan sweep.** Every replica, every half lease (jittered), reads
  ``<p>:runs-live`` for runs whose lease deadline passed and runs the takeover
  script on each. A run whose lease really expired (by the Redis clock) is
  marked ``failed`` with ``interrupted=1`` under a *new* generation from
  ``run-fence`` and an empty owner, its slots are freed, and the run is
  closed in the execution projection: completed tool rows are kept, open ones
  become interrupted. **Nothing is replayed.**
* **Fencing.** Every write the old owner still attempts carries its claim's
  owner and generation and is refused by the scripts (renew, release, the
  snapshot mirror). A lost claim (``admission.add_claim_lost_listener``) or a
  refused mirror stops the run locally (``RunRegistry.mark_interrupted``).
* **Checks in the run's own code.** A frozen owner may run a pending tool
  call or projection write before its heartbeat notices anything. Before
  either, :func:`ensure_current_run_may_act` / :func:`may_publish_for_run`
  trust the claim only for 80% of ``RUN_LEASE_SECONDS`` after this guard last
  proved it with a renewal (the run's first check always renews), measured
  on a clock that keeps counting while the process is stopped; on refusal or
  an unreachable Redis they fence the run.
* **Snapshot mirror.** The owner writes the run's public status (source,
  title, model, tools, result, ...) as the record's ``snapshot`` field, so any
  replica can answer ``GET /api/agent-runs/{id}``.

Without Redis no guard is installed and every check here is a no-op.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from atlas.application.chat.runs.registry import RunRecord, RunRegistry

logger = logging.getLogger(__name__)

INTERRUPTED_ERROR = (
    "The server running this run stopped responding, so the run was marked "
    "interrupted. Work it had finished is kept; nothing was retried."
)
# The API returns at most 20,000 characters (``launcher.MAX_RESULT_CHARS``).
# One more is kept so a reader can still tell the reply was truncated.
MAX_SNAPSHOT_RESULT_CHARS = 20_001


class RunFencedError(Exception):
    """This replica no longer holds the run's claim."""


def lease_clock() -> float:
    """Seconds on a clock that keeps counting while the process is stopped.

    ``CLOCK_BOOTTIME`` also counts a suspended host; ``monotonic`` is the
    fallback where it does not exist.
    """
    boottime = getattr(time, "CLOCK_BOOTTIME", None)
    if boottime is not None:
        try:
            return time.clock_gettime(boottime)
        except OSError:  # pragma: no cover - platform without the clock
            pass
    return time.monotonic()


def run_snapshot(record: RunRecord) -> Dict[str, Any]:
    """What another replica may read about ``record``."""
    spec = record.launch_spec or {}
    snapshot = record.to_public_dict()
    snapshot.update({
        "user_email": record.user_email,
        "source": spec.get("source"),
        "workspace": spec.get("workspace_name"),
        "model": spec.get("model"),
        "tools": list(spec.get("tools") or []),
        "data_sources": list(spec.get("data_sources") or []),
        "compliance_level": spec.get("compliance_level"),
        "previous_run_id": spec.get("previous_run_id"),
        "result": None,
    })
    if record.status.value == "completed" and record.result is not None:
        snapshot["result"] = record.result[:MAX_SNAPSHOT_RESULT_CHARS]
    return snapshot


def shared_view(raw: Optional[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """The run record hash as one dict: the owner's snapshot, overlaid with
    the coordination fields the scripts own (status, generation, ...)."""
    if not raw:
        return None
    view: Dict[str, Any] = {}
    try:
        view.update(json.loads(raw.get("snapshot") or "{}"))
    except ValueError:
        logger.warning("unreadable run snapshot for %s", raw.get("run_id"))
    view.update({
        "run_id": raw.get("run_id"),
        "conversation_id": raw.get("conversation_id"),
        "user_email": raw.get("user_email"),
        "status": raw.get("status"),
        "owner": raw.get("owner") or None,
        "generation": int(raw.get("generation") or 0),
        "interrupted": raw.get("interrupted") == "1",
    })
    view.setdefault("created_at", int(raw.get("created_at") or 0) / 1000.0)
    view.setdefault("updated_at", int(raw.get("updated_at") or 0) / 1000.0)
    if raw.get("error"):
        view["error"] = raw["error"]
    if raw.get("interrupted") == "1":
        view["ended_at"] = int(raw.get("updated_at") or 0) / 1000.0
        view["updated_at"] = view["ended_at"]
    return view


@dataclass
class _Lease:
    claim: Any
    verified_at: float = float("-inf")  # never proven by this guard yet
    fenced: bool = False
    done: bool = False
    dirty: bool = True
    syncing: bool = False


class RunLeaseGuard:
    """One per API process: fencing state for this replica's claims, and the
    orphan sweep for everyone's."""

    def __init__(
        self,
        coordinator: Any,
        scripts: Any,
        registry: RunRegistry,
        *,
        clock: Callable[[], float] = lease_clock,
    ):
        """``coordinator`` is the admission coordinator (``renew``, ``read``);
        ``scripts`` the takeover/mirror scripts (``RedisRunLeases``)."""
        self._coordinator = coordinator
        self._scripts = scripts
        self._registry = registry
        self._clock = clock
        self.lease_seconds = float(getattr(coordinator, "lease_seconds", 30.0))
        self._leases: Dict[str, _Lease] = {}
        self._tasks: List[asyncio.Task] = []
        self._pending: "set[asyncio.Task]" = set()
        self._unsubscribe: List[Callable[[], None]] = []

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._tasks:
            return
        from atlas.application.chat.runs.admission import add_claim_lost_listener

        self._unsubscribe = [
            self._registry.add_observer(self.observe),
            add_claim_lost_listener(self._claim_lost),
        ]
        self._tasks = [asyncio.get_running_loop().create_task(self._sweep_loop())]
        logger.info("Run lease guard started (lease %.1fs)", self.lease_seconds)

    async def stop(self) -> None:
        """Stop sweeping. Claims are left to expire, so a replica shut down
        mid-run has its runs reported interrupted by the others."""
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe = []
        tasks, self._tasks = self._tasks, []
        for task in tasks + list(self._pending):
            task.cancel()
        await asyncio.gather(*tasks, *self._pending, return_exceptions=True)

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self.lease_seconds / 2 * (0.75 + 0.5 * random.random()))
            try:
                await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("run orphan sweep failed", exc_info=True)

    # -- claim bookkeeping ---------------------------------------------------------

    def _spawn(self, coro: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return
        task = loop.create_task(coro)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def _claim_lost(self, record: RunRecord) -> None:
        """``admission.add_claim_lost_listener`` callback (sync, must not block)."""
        self._spawn(self.lost(record))

    def _renewed(self, run_id: str, at: float) -> None:
        lease = self._leases.get(run_id)
        if lease is not None and not lease.fenced:
            lease.verified_at = max(lease.verified_at, at)

    async def lost(self, record: RunRecord) -> None:
        """A renewal was refused: fence the run, and record the takeover now
        if nobody has yet (it applies only once the lease has expired)."""
        lease = self._leases.get(record.run_id)
        # Admission moves the refused claim to ``lost_claim`` before calling us.
        claim = record.claim or record.lost_claim
        if lease is None and claim is not None:
            lease = self._leases[record.run_id] = _Lease(claim=claim)
        if lease is None or lease.fenced:
            return
        self._fence_local(lease, record.run_id, "lease renewal refused")
        await self._take_over(record.run_id)

    # -- snapshot mirror ---------------------------------------------------------

    def observe(self, record: RunRecord) -> None:
        """Registry observer: queue a mirror of ``record``'s public state."""
        lease = self._leases.get(record.run_id)
        if lease is None:
            # Tracked from the first change that shows a cluster-wide claim
            # (admission sets ``record.claim`` right after inserting it).
            if record.claim is None or record.is_terminal or record.fenced:
                return
            lease = self._leases[record.run_id] = _Lease(claim=record.claim)
        if lease.fenced or lease.done or record.fenced:
            return
        lease.dirty = True
        if lease.syncing:
            return
        lease.syncing = True
        self._spawn(self._mirror(record.run_id, lease))

    async def _mirror(self, run_id: str, lease: _Lease) -> None:
        try:
            while lease.dirty and not lease.fenced and not lease.done:
                lease.dirty = False
                record = self._registry.get(run_id)
                if record is None:
                    lease.done = True
                    break
                written = await self._scripts.mirror(
                    lease.claim,
                    record.status.value,
                    json.dumps(run_snapshot(record), default=str),
                    record.error or "",
                )
                if not written:
                    self._fence_local(lease, run_id, "snapshot write refused")
                elif record.is_terminal:
                    lease.done = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            lease.dirty = True  # the next status change or check retries
            logger.warning("run snapshot write for %s failed: %s", run_id, type(exc).__name__)
        finally:
            lease.syncing = False

    # -- fencing checks ------------------------------------------------------------

    def _fence_local(self, lease: _Lease, run_id: str, why: str) -> None:
        if lease.fenced:
            return
        lease.fenced = True
        logger.warning("Run %s lost its claim (%s); stopping it on this replica", run_id, why)
        self._registry.mark_interrupted(run_id, INTERRUPTED_ERROR)

    async def _verified(self, run_id: str) -> bool:
        lease = self._leases.get(run_id)
        if lease is None:
            return True  # not a coordinated run
        if lease.fenced:  # before ``done``: a refused write can set both
            return False
        record = self._registry.get(run_id)
        if lease.done or record is None or record.is_terminal:
            return True  # our own finished run
        if self._clock() - lease.verified_at < self.lease_seconds * 0.8:
            return True
        started = self._clock()
        try:
            held = await self._coordinator.renew([lease.claim], {run_id: record.status.value})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # renew() reports errors by omission; be safe anyway
            logger.warning("lease check for %s failed: %s", run_id, type(exc).__name__)
            held = {}
        if held.get(run_id) is True:
            self._renewed(run_id, started)
            return True
        # Lapsed and unprovable (refused, or Redis unreachable): another
        # replica may already have taken the run over.
        self._fence_local(lease, run_id, "lease lapsed")
        if held.get(run_id) is False:
            with contextlib.suppress(Exception):
                await self._take_over(run_id)
        return False

    async def ensure_may_act(self, run_id: Optional[str]) -> None:
        if run_id and not await self._verified(run_id):
            raise RunFencedError(run_id)

    async def may_publish(self, run_id: Optional[str]) -> bool:
        return not run_id or await self._verified(run_id)

    def is_fenced(self, run_id: str) -> bool:
        lease = self._leases.get(run_id)
        return bool(lease and lease.fenced)

    # -- orphan sweep (any replica) -------------------------------------------------

    async def sweep_once(self) -> List[str]:
        for run_id in [r for r, lease in self._leases.items() if lease.done or lease.fenced]:
            if self._registry.get(run_id) is None:
                self._leases.pop(run_id, None)
        interrupted = []
        for run_id in await self._scripts.lapsed_candidates():
            if await self._take_over(run_id):
                interrupted.append(run_id)
        return interrupted

    async def _take_over(self, run_id: str) -> bool:
        raw = await self._coordinator.read(run_id) or {}
        generation = await self._scripts.interrupt(
            run_id, raw.get("conversation_id", ""), raw.get("user_email", ""), INTERRUPTED_ERROR
        )
        if not generation:
            return False
        logger.warning(
            "Run %s lost its owner %s; marked interrupted at generation %s (no tool is replayed)",
            run_id, raw.get("owner") or "?", generation,
        )
        lease = self._leases.get(run_id)
        if lease is not None:
            self._fence_local(lease, run_id, "interrupted by orphan sweep")
        await _close_projection(run_id, raw)
        return True

    async def read_record(self, run_id: str) -> Optional[Dict[str, Any]]:
        return shared_view(await self._coordinator.read(run_id))


async def _close_projection(run_id: str, raw: Dict[str, str]) -> None:
    """Close the run in the execution projection: completed rows stay, open
    rows become interrupted. Written directly, not through the owner-side
    guard, because the sweeping replica is not the owner."""
    from atlas.application.chat.runs.execution_state import get_execution_store

    try:
        await get_execution_store().apply_event({
            "type": "run_status",
            "status": "failed",
            "run_id": run_id,
            "conversation_id": raw.get("conversation_id"),
            "user_email": raw.get("user_email"),
            "error": INTERRUPTED_ERROR,
        })
    except Exception:
        logger.warning("could not close the projection of an interrupted run", exc_info=True)


_guard: Optional[RunLeaseGuard] = None


def get_lease_guard() -> Optional[RunLeaseGuard]:
    return _guard


def set_lease_guard(guard: Optional[RunLeaseGuard]) -> None:
    global _guard
    _guard = guard


async def ensure_current_run_may_act() -> None:
    """Before a side effect: stop the run if this replica lost its claim.

    Raises ``CancelledError`` so the run unwinds as a stopped turn (its
    finished tool rows are kept); the run's task is cancelled as well.
    """
    guard = _guard
    if guard is None:
        return
    from atlas.application.chat.runs.context import get_current_run

    run = get_current_run()
    if run is None:
        return
    try:
        await guard.ensure_may_act(run.run_id)
    except RunFencedError:
        raise asyncio.CancelledError(f"run {run.run_id} lost its claim")


async def may_publish_for_run(run_id: Optional[str]) -> bool:
    guard = _guard
    return True if guard is None else await guard.may_publish(run_id)
