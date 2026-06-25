"""Routes for opt-in fine-tune capture.

User endpoints manage consent and self-deletion; admin endpoints expose
aggregate stats and a streamed JSONL export. The actual turn capture and the
rollback-correction flow happen over the chat WebSocket -- these routes only
manage consent and the resulting data store.

Admin gating mirrors ``feedback_routes.require_admin_for_feedback``.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from atlas.core.auth import is_user_in_group
from atlas.core.log_sanitizer import get_current_user, sanitize_for_logging
from atlas.infrastructure.app_factory import app_factory

logger = logging.getLogger(__name__)

capture_router = APIRouter(prefix="/api", tags=["capture"])


def _get_capture_service():
    """Build a CaptureService from the active config manager."""
    from atlas.application.chat.capture import CaptureService

    config_manager = app_factory.get_config_manager()
    return CaptureService(config_manager)


async def require_admin_for_capture(
    current_user: str = Depends(get_current_user),
) -> str:
    """Require admin group membership for capture administration."""
    config_manager = app_factory.get_config_manager()
    admin_group = config_manager.app_settings.admin_group
    if not await is_user_in_group(current_user, admin_group):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Admin access required for capture administration. "
                f"User must be in '{admin_group}' group."
            ),
        )
    return current_user


class ConsentUpdate(BaseModel):
    """Body for setting/revoking a user's capture opt-in."""

    enabled: bool


@capture_router.get("/capture/consent")
async def get_consent(current_user: str = Depends(get_current_user)) -> Dict[str, Any]:
    """Return the current user's opt-in state plus the system flag state."""
    service = _get_capture_service()
    return service.consent_state(current_user)


@capture_router.post("/capture/consent")
async def set_consent(
    body: ConsentUpdate,
    current_user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Set or revoke the current user's capture opt-in.

    Opting in is rejected when the system flag is off so a user can never be
    recorded against a disabled system.
    """
    service = _get_capture_service()
    if body.enabled and not service.system_enabled():
        raise HTTPException(
            status_code=409,
            detail="Fine-tune capture is disabled by the system administrator.",
        )
    state = service.set_consent(current_user, body.enabled)
    logger.info(
        "Capture consent set user=%s enabled=%s",
        sanitize_for_logging(current_user),
        body.enabled,
    )
    return state


@capture_router.delete("/capture/me")
async def delete_my_capture_data(
    current_user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Delete all captured data and the consent record for the current user."""
    service = _get_capture_service()
    removed, files = service.delete_user_data(current_user)
    logger.info(
        "Capture self-delete user=%s removed=%d files=%d",
        sanitize_for_logging(current_user),
        removed,
        files,
    )
    return {"deleted_records": removed, "files_touched": files}


@capture_router.get("/admin/capture/stats")
async def capture_stats(
    admin_user: str = Depends(require_admin_for_capture),
) -> Dict[str, Any]:
    """Aggregate capture stats: counts, opt-in rate, storage size (admin only)."""
    service = _get_capture_service()
    stats = service.stats()
    stats["retrieved_by"] = admin_user
    return stats


@capture_router.get("/admin/capture/export")
async def capture_export(
    start_date: Optional[str] = Query(
        default=None, description="Inclusive YYYY-MM-DD lower bound"
    ),
    end_date: Optional[str] = Query(
        default=None, description="Inclusive YYYY-MM-DD upper bound"
    ),
    admin_user: str = Depends(require_admin_for_capture),
) -> StreamingResponse:
    """Stream the raw captured records as JSONL (admin only)."""
    service = _get_capture_service()

    def _generate():
        for record in service.iter_records(start_date=start_date, end_date=end_date):
            yield json.dumps(record, ensure_ascii=False) + "\n"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"finetune_capture_export_{timestamp}.jsonl"
    logger.info("Capture export by %s", sanitize_for_logging(admin_user))
    return StreamingResponse(
        _generate(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
