"""Health check routes for service monitoring and load balancing.

Provides simple health check endpoint for monitoring tools, orchestrators,
and load balancers to verify service availability.
"""

import logging
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter

from atlas.version import VERSION

logger = logging.getLogger(__name__)


def _resolve_git_commit() -> str:
    """Read the short git commit hash.

    Checks GIT_COMMIT env var first (set during Docker build), then
    falls back to running git, then to 'unknown'.
    """
    import os

    from_env = os.environ.get("GIT_COMMIT", "").strip()
    if from_env:
        return from_env
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError):
        pass
    return "unknown"


GIT_COMMIT = _resolve_git_commit()

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/heartbeat")
async def heartbeat() -> Dict[str, str]:
    """Lightweight heartbeat endpoint for uptime monitoring.

    Returns a minimal response to confirm the service is reachable.
    Bypasses authentication but is rate-limited to prevent abuse.
    Intended for frequent polling by load balancers and health checkers.
    """
    return {"status": "ok"}


@router.get("/health")
async def health_check() -> Dict[str, Any]:
    """Health check endpoint for service monitoring.

    Returns basic service status information. This endpoint does not require
    authentication and is intended for use by load balancers, monitoring
    systems, and orchestration platforms.

    Returns:
        Dictionary containing:
        - status: Service health status ("healthy" or "degraded")
        - service: Service name
        - version: Service version
        - timestamp: Current UTC timestamp in ISO-8601 format
        - checks: Startup outcomes of optional features (issue #992), e.g.
          ``{"chat_history": "ok" | "failed" | "disabled"}``
        - session_store: Only with a shared session store (issue #1091):
          its backend, reachability and latency.

    "degraded" means an enabled feature failed to start (chat history) or a
    shared dependency is unreachable (session store). It still returns
    HTTP 200, matching the session-store convention: the replica answers,
    and a readiness probe that pulled every replica during a database
    outage would turn a degraded feature into a full outage. Error detail
    stays in the server logs; this endpoint is unauthenticated.
    """
    report: Dict[str, Any] = {
        "status": "healthy",
        "service": "atlas-ui-3-backend",
        "version": VERSION,
        "git_commit": GIT_COMMIT,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    from atlas.infrastructure.app_factory import app_factory

    # Startup outcomes of optional features (issue #992). A feature that is
    # enabled but failed to initialize degrades the report without failing
    # the endpoint; the error detail stays in the startup logs and in
    # ``app_factory.startup_errors`` (server-side).
    outcomes = getattr(app_factory, "startup_outcomes", {})
    report["checks"] = {"chat_history": outcomes.get("chat_history", "disabled")}
    if report["checks"]["chat_history"] == "failed":
        report["status"] = "degraded"

    if getattr(app_factory, "redis", None) is not None:
        store = await app_factory.session_store_health()
        # This endpoint is unauthenticated: report reachability only. The
        # store's address, namespace and counters stay in the startup and
        # error logs, not in an anonymous response.
        report["session_store"] = {
            k: store[k] for k in ("backend", "status", "latency_ms") if k in store
        }
        if store.get("status") != "ok":
            report["status"] = "degraded"
    return report
