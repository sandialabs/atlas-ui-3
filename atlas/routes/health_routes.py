"""Health check routes for service monitoring and load balancing.

Provides simple health check endpoint for monitoring tools, orchestrators,
and load balancers to verify service availability.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter

from atlas.version import VERSION

logger = logging.getLogger(__name__)

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
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
