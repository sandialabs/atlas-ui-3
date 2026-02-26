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
    except (FileNotFoundError, subprocess.TimeoutExpired):
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
        - status: Service health status ("healthy")
        - service: Service name
        - version: Service version
        - timestamp: Current UTC timestamp in ISO-8601 format
    """
    return {
        "status": "healthy",
        "service": "atlas-ui-3-backend",
        "version": VERSION,
        "git_commit": GIT_COMMIT,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
