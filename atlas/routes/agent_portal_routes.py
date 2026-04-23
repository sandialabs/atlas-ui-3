"""Agent Portal routes: launch, list, inspect, cancel, and stream host processes.

Current state: dev/preview. Any authenticated user can launch any command
the backend itself can run. Governance (allow-lists, role checks, quotas,
audit trail) will be added in follow-up work.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from atlas.core.auth import get_user_from_header
from atlas.core.log_sanitizer import get_current_user, sanitize_for_logging
from atlas.infrastructure.app_factory import app_factory
from atlas.modules.process_manager import (
    LandlockUnavailableError,
    ProcessNotFoundError,
    get_process_manager,
    landlock_is_supported,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-portal", tags=["agent-portal"])


class LaunchRequest(BaseModel):
    command: str = Field(..., min_length=1, description="Executable to run")
    args: List[str] = Field(default_factory=list)
    cwd: Optional[str] = Field(default=None, description="Working directory")
    sandbox_mode: str = Field(
        default="off",
        description=(
            "Landlock sandbox mode. 'off' = no sandbox; 'strict' = reads "
            "restricted to standard system roots + the target binary's "
            "directory, writes only in cwd; 'workspace-write' = reads "
            "allowed everywhere, writes only in cwd."
        ),
    )
    # Back-compat alias: older clients may still send restrict_to_cwd=true.
    restrict_to_cwd: bool = Field(default=False, description="Deprecated; use sandbox_mode='strict'.")
    extra_writable_paths: List[str] = Field(
        default_factory=list,
        description="Additional directories granted write access alongside cwd in sandboxed modes.",
    )


def _require_enabled():
    app_settings = app_factory.get_config_manager().app_settings
    if not getattr(app_settings, "feature_agent_portal_enabled", False):
        raise HTTPException(status_code=404, detail="Agent portal is disabled")


@router.get("/capabilities")
async def capabilities(current_user: str = Depends(get_current_user)):
    _require_enabled()
    return {
        "landlock_supported": landlock_is_supported(),
    }


@router.get("/processes")
async def list_processes(current_user: str = Depends(get_current_user)):
    _require_enabled()
    manager = get_process_manager()
    return {"processes": manager.list_processes(user_email=current_user)}


@router.post("/processes", status_code=201)
async def launch_process(
    body: LaunchRequest,
    current_user: str = Depends(get_current_user),
):
    _require_enabled()
    manager = get_process_manager()
    sandbox_mode = body.sandbox_mode
    if sandbox_mode == "off" and body.restrict_to_cwd:
        sandbox_mode = "strict"
    if sandbox_mode not in ("off", "strict", "workspace-write"):
        raise HTTPException(status_code=400, detail=f"invalid sandbox_mode: {sandbox_mode}")

    try:
        managed = await manager.launch(
            command=body.command,
            args=body.args,
            cwd=body.cwd,
            user_email=current_user,
            sandbox_mode=sandbox_mode,
            extra_writable_paths=body.extra_writable_paths,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=f"Command not found: {e}")
    except LandlockUnavailableError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=429, detail=str(e))
    return managed.to_summary()


@router.get("/processes/{process_id}")
async def get_process(
    process_id: str,
    current_user: str = Depends(get_current_user),
):
    _require_enabled()
    manager = get_process_manager()
    try:
        managed = manager.get(process_id)
    except ProcessNotFoundError:
        raise HTTPException(status_code=404, detail="Process not found")
    return managed.to_summary()


@router.delete("/processes/{process_id}")
async def cancel_process(
    process_id: str,
    current_user: str = Depends(get_current_user),
):
    _require_enabled()
    manager = get_process_manager()
    try:
        managed = await manager.cancel(process_id)
    except ProcessNotFoundError:
        raise HTTPException(status_code=404, detail="Process not found")
    return managed.to_summary()


def _authenticate_ws(websocket: WebSocket) -> Optional[str]:
    """Mirror the authentication flow used by /ws for consistency."""
    config_manager = app_factory.get_config_manager()
    is_debug_mode = config_manager.app_settings.debug_mode

    if config_manager.app_settings.feature_proxy_secret_enabled and not is_debug_mode:
        if not config_manager.app_settings.proxy_secret:
            return None
        header = config_manager.app_settings.proxy_secret_header
        if websocket.headers.get(header) != config_manager.app_settings.proxy_secret:
            return None

    auth_header_name = config_manager.app_settings.auth_user_header
    x_header = websocket.headers.get(auth_header_name)
    if x_header:
        user_email = get_user_from_header(x_header)
        if user_email:
            return user_email

    if is_debug_mode:
        user_email = websocket.query_params.get("user")
        if user_email:
            return user_email
        return config_manager.app_settings.test_user or "test@test.com"

    return None


@router.websocket("/processes/{process_id}/stream")
async def stream_process_output(websocket: WebSocket, process_id: str):
    """Stream stdout/stderr for a managed process.

    The connection replays the recent history buffer first, then relays
    live chunks as the process produces them, then closes when the
    process ends.
    """
    app_settings = app_factory.get_config_manager().app_settings
    if not getattr(app_settings, "feature_agent_portal_enabled", False):
        await websocket.close(code=1008, reason="Agent portal disabled")
        return

    user_email = _authenticate_ws(websocket)
    if not user_email:
        await websocket.close(code=1008, reason="Authentication required")
        return

    manager = get_process_manager()
    try:
        managed = manager.get(process_id)
    except ProcessNotFoundError:
        await websocket.close(code=1008, reason="Process not found")
        return

    await websocket.accept()
    logger.info(
        "agent_portal stream opened process=%s user=%s",
        sanitize_for_logging(process_id),
        sanitize_for_logging(user_email),
    )

    await websocket.send_json({
        "type": "process_info",
        "process": managed.to_summary(),
    })

    try:
        async for chunk in manager.subscribe(process_id):
            await websocket.send_json({
                "type": "output",
                "stream": chunk.stream,
                "text": chunk.text,
                "timestamp": chunk.timestamp,
            })
        # Send final state so client can reflect exit status
        await websocket.send_json({
            "type": "process_end",
            "process": manager.get(process_id).to_summary(),
        })
    except WebSocketDisconnect:
        logger.info("agent_portal stream client disconnected process=%s", sanitize_for_logging(process_id))
        return
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("agent_portal stream error process=%s: %s", process_id, e, exc_info=True)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
