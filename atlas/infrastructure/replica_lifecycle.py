"""Replica start, periodic check and shutdown hooks for coordination (issue #1129).

Kept out of ``atlas/main.py`` so the lifespan only calls three functions:

* ``on_replica_started``  -- one ``replica_started`` line naming the replica,
  its shared-state backends and its run cap.
* ``coordination_tick``   -- run by the run sweeper every 30 s: probes Redis and
  the database (so an outage, and its recovery, is logged even when nobody
  calls ``/api/health``) and writes a ``summary`` line when counts change.
* ``interrupt_runs_for_shutdown`` -- on SIGTERM (rollout, scale-in, node
  drain) marks this replica's live runs interrupted before the shared
  connections close, so the terminal state reaches the shared projection
  and other replicas instead of vanishing with the process.

A SIGKILLed or OOM-killed replica runs none of this; detecting that loss from
another replica is the lease/orphan work of #1128.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from atlas.core.coordination_metrics import CoordinationEvent, EndReason, coordination_metrics
from atlas.infrastructure.dependency_probes import probe_all

logger = logging.getLogger(__name__)

# How long shutdown waits for interrupted runs to unwind and for their
# terminal state to reach the shared projection. Well inside the default
# Kubernetes terminationGracePeriodSeconds (30 s).
SHUTDOWN_INTERRUPT_GRACE_SECONDS = 5.0


def on_replica_started(app_settings: Any) -> None:
    from atlas.application.chat.runs.registry import get_run_registry

    coordination_metrics().record_event(
        CoordinationEvent.REPLICA_STARTED,
        session_store=getattr(app_settings, "session_repository_type", "memory"),
        chat_history=bool(getattr(app_settings, "feature_chat_history_enabled", False)),
        max_runs_per_user=get_run_registry().max_concurrent_runs_per_user,
    )


async def coordination_tick(app_factory: Any) -> None:
    """Probe dependencies and log a summary when anything changed. Never raises."""
    from atlas.application.chat.runs.registry import get_run_registry

    try:
        await probe_all(app_factory)
        coordination_metrics().maybe_log_summary(get_run_registry().status_counts())
    except Exception:  # pragma: no cover - a periodic check must never die
        logger.warning("coordination tick failed", exc_info=True)


async def interrupt_runs_for_shutdown(grace_seconds: float = SHUTDOWN_INTERRUPT_GRACE_SECONDS) -> int:
    """Mark live runs interrupted, then wait briefly for them to unwind.

    Returns how many runs were interrupted.
    """
    from atlas.application.chat.runs import registry as registry_module

    registry = registry_module.get_run_registry()
    tasks = [r.task for r in registry.active_for_user_all() if r.task is not None and not r.task.done()]
    interrupted = registry.interrupt_active(EndReason.REPLICA_SHUTDOWN.value)
    coordination_metrics().record_event(
        CoordinationEvent.REPLICA_SHUTDOWN,
        level=logging.WARNING if interrupted else logging.INFO,
        interrupted_runs=len(interrupted),
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + grace_seconds
    pending = {t for t in tasks if not t.done()}
    # Two passes: an unwinding run task can schedule further projection
    # writes from its own cleanup, after the first snapshot.
    for _ in range(2):
        pending |= {t for t in registry_module._PROJECTION_TASKS if not t.done()}
        remaining = deadline - loop.time()
        if not pending or remaining <= 0:
            break
        _, pending = await asyncio.wait(pending, timeout=remaining)
    if pending:
        logger.warning(
            "Shutdown: %d run task(s) or projection write(s) still pending after %.1fs",
            len(pending),
            grace_seconds,
        )
    return len(interrupted)
