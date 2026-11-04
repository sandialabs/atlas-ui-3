"""Per-replica coordination status for operators (issue #1129).

Both endpoints describe **the replica that answers the request**. Behind a
load balancer, query each pod directly (``kubectl exec`` / port-forward, see
``docs/admin/multi-replica-observability.md``) or let Prometheus scrape every
pod; a run listed here is owned by this replica only.

``GET /admin/coordination``          JSON: identity, run counts by status and
                                     outcome, live runs, dependency states, counters
``GET /admin/coordination/metrics``  Prometheus text exposition of the same numbers

Admin-only, like the rest of ``/admin``. No user identity or content is
returned: runs are listed by id, status, age and end reason.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse

from atlas.application.chat.runs.registry import RunStatus, get_run_registry
from atlas.core.coordination_metrics import coordination_metrics, outcome_for
from atlas.infrastructure.app_factory import app_factory
from atlas.infrastructure.dependency_probes import probe_all
from atlas.routes.admin_routes import require_admin

coordination_router = APIRouter(prefix="/admin/coordination", tags=["admin", "coordination"])

MAX_LISTED_RUNS = 200


def _run_rows(include_terminal: bool) -> List[Dict[str, Any]]:
    now = time.time()
    rows = []
    for record in get_run_registry().all_records():
        if record.is_terminal and not include_terminal:
            continue
        rows.append({
            "run_id": record.run_id,
            "status": record.status.value,
            "outcome": outcome_for(record.status.value, record.end_reason) if record.is_terminal else None,
            "end_reason": record.end_reason,
            "waiting_on": record.waiting_on,
            "detached": record.detached,
            "depth": record.depth,
            "age_s": round(now - record.created_at, 1),
            "ended_at": record.ended_at,
        })
    rows.sort(key=lambda r: -r["age_s"])
    return rows[:MAX_LISTED_RUNS]


def _gauges(counts: Dict[str, int]) -> List[Tuple[str, Dict[str, Any], float]]:
    gauges: List[Tuple[str, Dict[str, Any], float]] = []
    for status in RunStatus.__members__.values():
        gauges.append(("atlas_runs", {"status": status.value}, counts.get(status.value, 0)))
    gauges.append(("atlas_runs_detached", {}, counts.get("detached", 0)))
    gauges.append(("atlas_runs_interrupted_retained", {}, counts.get("interrupted", 0)))
    oldest = max((r["age_s"] for r in _run_rows(include_terminal=False)), default=0)
    gauges.append(("atlas_run_oldest_active_age_seconds", {}, oldest))
    return gauges


@coordination_router.get("")
async def coordination_status(
    include_terminal: bool = Query(False, description="Also list retained terminal runs."),
    refresh: bool = Query(
        False, description="Probe the database now instead of reusing a result up to 2 s old."
    ),
    admin_user: str = Depends(require_admin),  # noqa: ARG001 (enforces auth)
) -> Dict[str, Any]:
    metrics = coordination_metrics()
    probed = await probe_all(app_factory, use_cache=not refresh)
    # The status is the live probes' verdict. A dependency known only from
    # past errors (no probe for it) is listed but cannot pin "degraded".
    status = "degraded" if any(d.get("status") != "ok" for d in probed.values()) else "ok"
    dependencies = {name: dict(report) for name, report in probed.items()}
    counts = get_run_registry().status_counts()
    for name, state in metrics.dependencies().items():
        dependencies.setdefault(name, {}).update({"state": state})
    return {
        "replica": metrics.identity(),
        "status": status,
        "runs": {
            "counts": counts,
            "max_concurrent_per_user": get_run_registry().max_concurrent_runs_per_user,
            "items": _run_rows(include_terminal),
        },
        "dependencies": dependencies,
        "counters": metrics.counters(),
    }


@coordination_router.get("/metrics", response_class=PlainTextResponse)
async def coordination_metrics_text(
    admin_user: str = Depends(require_admin),  # noqa: ARG001 (enforces auth)
) -> PlainTextResponse:
    await probe_all(app_factory)
    text = coordination_metrics().render_prometheus(_gauges(get_run_registry().status_counts()))
    return PlainTextResponse(text, media_type="text/plain; version=0.0.4; charset=utf-8")
