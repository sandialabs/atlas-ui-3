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
import logging
from typing import Any, Callable, Optional
from uuid import uuid4

from atlas.application.chat.runs.coordination import (
    RUN_LEASE_RENEW_INTERVAL_SECONDS,
    RunClaim,
    get_run_coordinator,
    release_claim_soon,
)
from atlas.application.chat.runs.registry import RunRecord, get_run_registry

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
    **start_kwargs: Any,
) -> RunRecord:
    """Admit one run cluster-wide and register it locally, or raise.

    Raises ``ConversationBusyError``, ``ConcurrencyLimitError``,
    ``RunCoordinationUnavailableError`` (all ``RunRegistryError``), or whatever
    ``precheck`` raises. ``start_kwargs`` are passed to ``RunRegistry.start``.
    """
    registry = get_run_registry()
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
    _heartbeat = loop.create_task(run_lease_heartbeat())


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


async def renew_live_claims() -> None:
    """One heartbeat: extend the lease of every live run this replica owns."""
    registry = get_run_registry()
    live = [r for r in registry.active_for_user_all() if r.claim is not None]
    if not live:
        return
    held = await get_run_coordinator().renew(
        [r.claim for r in live], {r.run_id: r.status.value for r in live}
    )
    for record in live:
        # A run that ended (and released) while the beat was in flight is
        # refused by renewal too; that is not a lost claim.
        if held.get(record.run_id) is False and record.claim is not None and not record.is_terminal:
            # Fencing lands with the lease work; for now say so loudly.
            logger.warning(
                "Run %s lost its cluster-wide claim while still running on this replica",
                record.run_id,
            )


async def run_lease_heartbeat(interval: float = RUN_LEASE_RENEW_INTERVAL_SECONDS) -> None:
    """Renew claims forever (started from the app lifespan)."""
    while True:
        try:
            await asyncio.sleep(interval)
            await renew_live_claims()
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - the heartbeat must never die
            logger.warning("Run lease heartbeat iteration failed", exc_info=True)

