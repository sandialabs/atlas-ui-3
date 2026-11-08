"""The one admission path for tracked runs (issues #884, #1128).

Every way a run starts -- a WebSocket turn, ``atlas_launch`` from inside a run,
the agent-runs REST API -- goes through :func:`admit_run`. That is what makes
``MAX_CONCURRENT_RUNS_PER_USER`` apply the same way to all of them: before
#1128 only the WebSocket handler copied the setting onto the registry, so a
replica that had served only REST traffic enforced the built-in default.

Admission is two checks, in order:

1. The cluster-wide claim (``coordination.get_run_coordinator()``): with Redis,
   one atomic script that claims the conversation and a user slot across every
   replica. Without Redis it admits everything and never suspends.
2. The process-local registry check and insert (``RunRegistry.start``), run
   synchronously together with any caller ``precheck`` so nothing on this
   event loop can interleave with it.

If step 2 refuses, or anything fails between the two, the claim is released
before the error propagates: a refused admission never holds a slot.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Callable, List, Optional, Tuple
from uuid import uuid4

from atlas.application.chat.runs.coordination import (
    RUN_LEASE_RENEW_INTERVAL_SECONDS,
    RunClaim,
    RunCoordinationUnavailableError,
    get_run_coordinator,
    release_claim_soon,
)
from atlas.application.chat.runs.registry import RunRecord, RunStatus, get_run_registry

logger = logging.getLogger(__name__)

def apply_run_limit(app_settings: Any) -> int:
    """Copy ``MAX_CONCURRENT_RUNS_PER_USER`` onto the registry; return the cap.

    Called on every admission (so an admin change applies without a restart)
    and once at startup. ``app_settings`` may be ``None`` in tests that build
    no config; the registry keeps its current cap then.
    """
    registry = get_run_registry()
    limit = getattr(app_settings, "max_concurrent_runs_per_user", None)
    # ``bool`` is an ``int``; a mocked settings object yields neither.
    if isinstance(limit, int) and not isinstance(limit, bool):
        registry.set_max_concurrent_runs_per_user(limit)
    return registry.max_concurrent_runs_per_user


async def _claim_or_release_on_cancel(coordinator, **kwargs) -> Optional[RunClaim]:
    """Await the claim; if the caller is cancelled mid-claim, release what it got.

    A cancellation can arrive after Redis granted the claim but before the
    reply was read. Shielding the claim and releasing its result when it lands
    keeps that slot from leaking until the lease expires.
    """
    if not getattr(coordinator, "tracks_claims", True):
        # Nothing to leak, and awaiting directly keeps an admission that never
        # suspends from suspending (the in-process coordinator).
        return await coordinator.claim(**kwargs)
    task = asyncio.ensure_future(coordinator.claim(**kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        def _release_late(done: "asyncio.Task") -> None:
            if done.cancelled() or done.exception() is not None:
                return
            claim = done.result()
            if claim is not None:
                release_claim_soon(claim, "cancelled")

        task.add_done_callback(_release_late)
        raise


async def admit_run(
    *,
    conversation_id: str,
    user_email: str,
    app_settings: Any = None,
    precheck: Optional[Callable[[], None]] = None,
    source: str = "",
    follow_up_of: Optional[str] = None,
    **start_kwargs: Any,
) -> RunRecord:
    """Admit one run cluster-wide and register it locally, or raise.

    Raises ``ConversationBusyError``, ``ConcurrencyLimitError``,
    ``RunCoordinationUnavailableError`` (all ``RunRegistryError``), or whatever
    ``precheck`` raises. ``start_kwargs`` are passed to ``RunRegistry.start``.
    ``source`` (the launch source) and ``follow_up_of`` (the run an API
    follow-up continues) go to the shared run record with the claim, beside
    ``parent_run_id``, so any replica can route by them from the first moment.
    """
    registry = get_run_registry()
    if _admission_closed:
        # This replica is shutting down; the client retries and lands on
        # another one.
        raise RunCoordinationUnavailableError("shutting down")
    limit = apply_run_limit(app_settings)
    # Cheap local refusal first: a busy conversation or a user at the cap on
    # this replica needs no Redis round trip (and gets no claim to undo).
    registry.check_admission(conversation_id, user_email)
    if precheck is not None:
        precheck()

    coordinator = get_run_coordinator()
    run_id = str(uuid4())
    claim = await _claim_or_release_on_cancel(
        coordinator,
        run_id=run_id,
        conversation_id=conversation_id,
        user_email=user_email,
        max_runs_per_user=limit,
        source=source,
        parent_run_id=start_kwargs.get("parent_run_id"),
        follow_up_of=follow_up_of,
    )
    try:
        # Synchronous from here: the precheck and the insert cannot be
        # interleaved by another admission on this loop.
        if precheck is not None:
            precheck()
        record = registry.start(
            conversation_id=conversation_id,
            user_email=user_email,
            run_id=run_id,
            **start_kwargs,
        )
    except BaseException:
        if claim is not None:
            release_claim_soon(claim, "failed")
        raise
    record.claim = claim
    if claim is not None:
        ensure_lease_heartbeat()
    return record


_heartbeat: Optional["asyncio.Task"] = None


def ensure_lease_heartbeat() -> None:
    """Start the renewal loop on this event loop if it is not running.

    Started by the first claim rather than by the server lifespan, so a run
    admitted anywhere a coordinator tracks claims -- the API server, or an
    ``AtlasClient`` running headless -- keeps its lease alive.
    """
    global _heartbeat
    loop = asyncio.get_running_loop()
    if _heartbeat is not None and not _heartbeat.done() and _heartbeat.get_loop() is loop:
        return
    # A third of the coordinator's lease, so a configured RUN_LEASE_SECONDS
    # keeps one missed beat inside the lease.
    lease = getattr(get_run_coordinator(), "lease_seconds", None)
    interval = lease / 3 if lease else RUN_LEASE_RENEW_INTERVAL_SECONDS
    _heartbeat = loop.create_task(run_lease_heartbeat(interval))


async def stop_lease_heartbeat() -> None:
    """Stop the renewal loop (shutdown, tests)."""
    global _heartbeat
    task, _heartbeat = _heartbeat, None
    if task is None or task.done():
        return
    try:
        if task.get_loop() is not asyncio.get_running_loop():
            return
    except RuntimeError:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        # The cancellation we just requested.
        logger.debug("Run lease heartbeat stopped")


# Called with the ``RunRecord`` of a live local run whose cluster-wide claim
# was lost (renewal refused it). The extension point for fencing: a listener
# may stop the run or refuse its writes. Listeners must not raise or block.
ClaimLostListener = Callable[[RunRecord], None]
_claim_lost_listeners: List[ClaimLostListener] = []


def add_claim_lost_listener(listener: ClaimLostListener) -> Callable[[], None]:
    """Subscribe to lost claims; returns an unsubscribe callable."""
    _claim_lost_listeners.append(listener)

    def _unsubscribe() -> None:
        try:
            _claim_lost_listeners.remove(listener)
        except ValueError:
            # Already unsubscribed; a second call is a no-op.
            logger.debug("Claim-lost listener was already removed")

    return _unsubscribe


# Called after a heartbeat with the run ids whose claims renewal confirmed,
# and the listener's own ``clock`` read just before the renewal was sent (the
# lease runs from no earlier than that). Lets fencing count a heartbeat as
# proof that the claim was still held. Listeners must not raise or block.
ClaimsRenewedListener = Callable[[List[str], float], None]
_claims_renewed_listeners: List[Tuple[ClaimsRenewedListener, Callable[[], float]]] = []


def add_claims_renewed_listener(
    listener: ClaimsRenewedListener, clock: Callable[[], float] = time.monotonic
) -> Callable[[], None]:
    """Subscribe to confirmed renewals; returns an unsubscribe callable."""
    entry = (listener, clock)
    _claims_renewed_listeners.append(entry)

    def _unsubscribe() -> None:
        with contextlib.suppress(ValueError):
            _claims_renewed_listeners.remove(entry)

    return _unsubscribe


async def renew_live_claims() -> None:
    """One heartbeat: extend the lease of every live run this replica owns."""
    registry = get_run_registry()
    live = [r for r in registry.active_for_user_all() if r.claim is not None]
    if not live:
        return
    started = [(listener, clock()) for listener, clock in list(_claims_renewed_listeners)]
    held = await get_run_coordinator().renew(
        [r.claim for r in live], {r.run_id: r.status.value for r in live}
    )
    renewed = [r.run_id for r in live if held.get(r.run_id) is True]
    if renewed:
        for renewed_listener, at in started:
            try:
                renewed_listener(renewed, at)
            except Exception:
                logger.warning("Claims-renewed listener raised", exc_info=True)
    for record in live:
        # A run that ended (and released) while the beat was in flight is
        # refused by renewal too; that is not a lost claim.
        if held.get(record.run_id) is False and record.claim is not None and not record.is_terminal:
            # Lost once, reported once: Redis already revoked it, so renewing
            # it again is a wasted round trip and a repeated warning, and the
            # terminal release has nothing of ours left to free.
            record.lost_claim, record.claim = record.claim, None
            logger.warning(
                "Run %s lost its cluster-wide claim while still running on this replica",
                record.run_id,
            )
            for listener in list(_claim_lost_listeners):
                try:
                    listener(record)
                except Exception:
                    # One bad listener must not stop the others or the beat.
                    logger.warning("Claim-lost listener raised", exc_info=True)


# Set by graceful shutdown: no run is admitted once the replica has started
# stopping the ones it owns.
_admission_closed = False


async def shut_down_live_runs(timeout: float = 10.0) -> int:
    """Graceful shutdown: refuse new runs, stop live ones, then free their claims.

    The order matters. A claim released while its run's task still executes
    would let another replica admit a second run on the same conversation, so
    admission closes first, the tasks are cancelled and awaited (up to
    ``timeout``, well inside one lease), and only then are the conversation
    locks and user slots given back. A task that does not stop in time keeps
    its claim; the lease frees it once the process is gone. Returns how many
    claims were released (scheduled; the caller drains them).
    """
    global _admission_closed
    _admission_closed = True
    registry = get_run_registry()
    live = registry.active_for_user_all()
    tasks = {r.run_id: r.task for r in live if r.task is not None and not r.task.done()}
    held = []
    for record in live:
        # Taken out of the record so the run's own unwinding (its terminal
        # set_status) cannot release before its task is gone.
        claim, record.claim = record.claim, None
        if claim is not None:
            held.append((record, claim))
    # Recorded failed (interrupted, ``replica_shutdown``) before the tasks
    # unwind, so a run's own handler cannot record a user-style cancel over it
    # and the claim released below carries the same status as the projection.
    # The claims were taken out of the records above, so this releases nothing.
    registry.interrupt_active()
    for task in tasks.values():
        task.cancel()
    if tasks:
        await asyncio.wait(list(tasks.values()), timeout=timeout)
    released = 0
    for record, claim in held:
        task = tasks.get(record.run_id)
        if task is not None and not task.done():
            logger.warning("Run %s did not stop before shutdown; its lease will expire", record.run_id)
            continue
        if not record.is_terminal:
            registry.set_status(record.run_id, RunStatus.FAILED, error="The server shut down")
        release_claim_soon(claim, record.status.value)
        released += 1
    return released


async def run_lease_heartbeat(interval: float = RUN_LEASE_RENEW_INTERVAL_SECONDS) -> None:
    """Renew claims forever (started with the first claim on a loop).

    Beats are scheduled on a fixed monotonic grid, not ``interval`` after the
    previous pass finished, so a slow pass shortens the next wait instead of
    pushing every later renewal toward the lease's end.
    """
    loop = asyncio.get_running_loop()
    next_beat = loop.time() + interval
    while True:
        try:
            await asyncio.sleep(max(0.0, next_beat - loop.time()))
            await renew_live_claims()
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - the heartbeat must never die
            logger.warning("Run lease heartbeat iteration failed", exc_info=True)
        # A pass that overran a whole interval is followed by one right away.
        next_beat = max(next_beat + interval, loop.time())

