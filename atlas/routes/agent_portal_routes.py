"""Agent Portal routes: launch, list, inspect, cancel, and stream host processes.

Current state: dev/preview. Any authenticated user can launch any command
the backend itself can run. Governance (allow-lists, role checks, quotas,
audit trail) will be added in follow-up work.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from atlas.core.auth import get_user_from_header
from atlas.core.log_sanitizer import get_current_user, sanitize_for_logging
from atlas.infrastructure.app_factory import app_factory
from atlas.modules.agent_portal import (
    PresetNotFoundError,
    get_preset_store,
)
from atlas.modules.process_manager import (
    LandlockUnavailableError,
    ProcessNotFoundError,
    get_process_manager,
    landlock_is_supported,
)
from atlas.modules.process_manager.manager import probe_isolation_capabilities

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
    use_pty: bool = Field(
        default=False,
        description="Allocate a pseudo-terminal so the child sees stdout as a TTY (TUIs, progress bars).",
    )
    namespaces: bool = Field(
        default=False,
        description="Run the child in isolated Linux namespaces (user, pid, uts, ipc, mnt).",
    )
    isolate_network: bool = Field(
        default=False,
        description="Also isolate the network namespace (blocks all outbound connections). Requires namespaces=true.",
    )
    memory_limit: Optional[str] = Field(
        default=None,
        description="Cgroup MemoryMax (e.g. '512M', '2G'). Uses systemd-run --user --scope.",
    )
    cpu_limit: Optional[str] = Field(
        default=None,
        description="Cgroup CPUQuota percent (e.g. '50%', '200%').",
    )
    pids_limit: Optional[int] = Field(
        default=None,
        description="Cgroup TasksMax (max pids/threads).",
    )
    display_name: Optional[str] = Field(
        default="",
        description="Friendly name shown in the process list. Defaults to the command.",
    )


class RenameRequest(BaseModel):
    display_name: str = Field(default="", description="New display name for the process.")


class PresetCreateRequest(BaseModel):
    """All launch-form fields plus a human label and optional description.

    Mirrors LaunchRequest except ``command`` is not marked required here;
    a partially-specified preset is allowed so users can stub one out.
    """

    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    command: str = Field(default="")
    args: List[str] = Field(default_factory=list)
    cwd: Optional[str] = None
    sandbox_mode: str = Field(default="off")
    extra_writable_paths: List[str] = Field(default_factory=list)
    use_pty: bool = False
    namespaces: bool = False
    isolate_network: bool = False
    memory_limit: Optional[str] = None
    cpu_limit: Optional[str] = None
    pids_limit: Optional[int] = None
    display_name: Optional[str] = None


class PresetUpdateRequest(BaseModel):
    """Partial update. Any field omitted (or explicitly None) is unchanged."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    command: Optional[str] = None
    args: Optional[List[str]] = None
    cwd: Optional[str] = None
    sandbox_mode: Optional[str] = None
    extra_writable_paths: Optional[List[str]] = None
    use_pty: Optional[bool] = None
    namespaces: Optional[bool] = None
    isolate_network: Optional[bool] = None
    memory_limit: Optional[str] = None
    cpu_limit: Optional[str] = None
    pids_limit: Optional[int] = None
    display_name: Optional[str] = None


_VALID_SANDBOX_MODES = ("off", "strict", "workspace-write")


def _validate_sandbox_mode(mode: Optional[str]) -> None:
    if mode is None:
        return
    if mode not in _VALID_SANDBOX_MODES:
        raise HTTPException(status_code=400, detail=f"invalid sandbox_mode: {mode}")


def _require_enabled():
    app_settings = app_factory.get_config_manager().app_settings
    if not getattr(app_settings, "feature_agent_portal_enabled", False):
        raise HTTPException(status_code=404, detail="Agent portal is disabled")


@router.get("/capabilities")
async def capabilities(current_user: str = Depends(get_current_user)):
    _require_enabled()
    iso = probe_isolation_capabilities()
    return {
        "landlock_supported": landlock_is_supported(),
        "namespaces_supported": iso.get("namespaces", False),
        "cgroups_supported": iso.get("cgroups", False),
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
            use_pty=body.use_pty,
            namespaces=body.namespaces,
            isolate_network=body.isolate_network,
            memory_limit=body.memory_limit,
            cpu_limit=body.cpu_limit,
            pids_limit=body.pids_limit,
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
    # TODO(graduation): add per-user ownership check — see docs/agentportal/threat-model.md
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
    # TODO(graduation): add per-user ownership check — see docs/agentportal/threat-model.md
    _require_enabled()
    manager = get_process_manager()
    try:
        managed = await manager.cancel(process_id)
    except ProcessNotFoundError:
        raise HTTPException(status_code=404, detail="Process not found")
    return managed.to_summary()


@router.patch("/processes/{process_id}")
async def rename_process(
    process_id: str,
    body: RenameRequest,
    current_user: str = Depends(get_current_user),
):
    # TODO(graduation): add per-user ownership check — see docs/agentportal/threat-model.md
    _require_enabled()
    manager = get_process_manager()
    try:
        managed = manager.rename(process_id, body.display_name)
    except ProcessNotFoundError:
        raise HTTPException(status_code=404, detail="Process not found")
    return managed.to_summary()


# ---------------------------------------------------------------------------
# Preset library — saved launch templates
# ---------------------------------------------------------------------------
#
# Presets are per-user (owner-scoped inside the store), so unlike the
# per-process endpoints they do not carry a "graduation" TODO: the store
# itself filters by user_email on every read/write.


@router.get("/presets")
async def list_presets(current_user: str = Depends(get_current_user)):
    _require_enabled()
    store = get_preset_store()
    return {"presets": [p.to_public() for p in store.list_for_user(current_user)]}


@router.post("/presets", status_code=201)
async def create_preset(
    body: PresetCreateRequest,
    current_user: str = Depends(get_current_user),
):
    _require_enabled()
    _validate_sandbox_mode(body.sandbox_mode)
    store = get_preset_store()
    preset = store.create(body.model_dump(), current_user)
    logger.info(
        "agent_portal preset created id=%s user=%s name=%s",
        sanitize_for_logging(preset.id),
        sanitize_for_logging(current_user),
        sanitize_for_logging(preset.name),
    )
    return preset.to_public()


@router.get("/presets/{preset_id}")
async def get_preset(
    preset_id: str,
    current_user: str = Depends(get_current_user),
):
    _require_enabled()
    store = get_preset_store()
    try:
        preset = store.get(preset_id, current_user)
    except PresetNotFoundError:
        raise HTTPException(status_code=404, detail="Preset not found")
    return preset.to_public()


@router.patch("/presets/{preset_id}")
async def update_preset(
    preset_id: str,
    body: PresetUpdateRequest,
    current_user: str = Depends(get_current_user),
):
    _require_enabled()
    _validate_sandbox_mode(body.sandbox_mode)
    store = get_preset_store()
    # exclude_unset=True so fields the client omitted are not overwritten.
    patch = body.model_dump(exclude_unset=True)
    try:
        preset = store.update(preset_id, patch, current_user)
    except PresetNotFoundError:
        raise HTTPException(status_code=404, detail="Preset not found")
    logger.info(
        "agent_portal preset updated id=%s user=%s",
        sanitize_for_logging(preset.id),
        sanitize_for_logging(current_user),
    )
    return preset.to_public()


@router.delete("/presets/{preset_id}", status_code=204)
async def delete_preset(
    preset_id: str,
    current_user: str = Depends(get_current_user),
):
    _require_enabled()
    store = get_preset_store()
    try:
        store.delete(preset_id, current_user)
    except PresetNotFoundError:
        raise HTTPException(status_code=404, detail="Preset not found")
    logger.info(
        "agent_portal preset deleted id=%s user=%s",
        sanitize_for_logging(preset_id),
        sanitize_for_logging(current_user),
    )
    return None


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _origin_is_loopback(origin: Optional[str]) -> bool:
    """Return True if the Origin header names a loopback host over http(s).

    WebSocket upgrades are not covered by CORS preflight, so a page at any
    origin can open a WS to localhost:<port>. Limiting accept() to loopback
    origins blocks drive-by CSRF from an untrusted browser tab while still
    allowing the local dev UI. Any port is accepted on the loopback hosts
    for now; tighten to the configured backend port once that is threaded
    through.

    TODO: restrict the allowed port to the backend's own port instead of
    accepting any.
    """
    if not origin:
        return False
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = (parsed.hostname or "").lower()
    return hostname in _LOOPBACK_HOSTS


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
    # TODO(graduation): add per-user ownership check — see docs/agentportal/threat-model.md
    app_settings = app_factory.get_config_manager().app_settings
    if not getattr(app_settings, "feature_agent_portal_enabled", False):
        await websocket.close(code=1008, reason="Agent portal disabled")
        return

    # Origin check: WS upgrades bypass CORS preflight, so a cross-origin
    # page can open a socket to the dev server unless we reject it here.
    # See docs/agentportal/threat-model.md.
    origin = websocket.headers.get("origin")
    if not _origin_is_loopback(origin):
        logger.warning(
            "agent_portal stream rejected non-loopback origin=%s process=%s",
            sanitize_for_logging(origin or ""),
            sanitize_for_logging(process_id),
        )
        await websocket.close(code=4403, reason="Origin not allowed")
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

    async def _pump_output():
        async for chunk in manager.subscribe(process_id):
            if chunk.stream == "raw":
                # pty mode: relay base64 bytes directly so xterm.js can
                # render ANSI/cursor/SGR sequences verbatim.
                await websocket.send_json({
                    "type": "output_raw",
                    "data": chunk.text,
                    "timestamp": chunk.timestamp,
                })
            else:
                await websocket.send_json({
                    "type": "output",
                    "stream": chunk.stream,
                    "text": chunk.text,
                    "timestamp": chunk.timestamp,
                })
        await websocket.send_json({
            "type": "process_end",
            "process": manager.get(process_id).to_summary(),
        })

    async def _pump_input():
        """Receive input/resize frames from the client."""
        while True:
            msg = await websocket.receive_json()
            mtype = msg.get("type")
            if mtype == "input":
                encoded = msg.get("data") or ""
                try:
                    data = base64.b64decode(encoded)
                except Exception:
                    continue
                manager.write_input(process_id, data)
            elif mtype == "resize":
                try:
                    cols = int(msg.get("cols", 80))
                    rows = int(msg.get("rows", 24))
                except (TypeError, ValueError):
                    continue
                manager.resize_pty(process_id, cols, rows)

    output_task = asyncio.create_task(_pump_output())
    input_task = asyncio.create_task(_pump_input())
    try:
        # End when the output stream closes (process ended); cancel
        # the input reader so it stops waiting on receive_json.
        done, pending = await asyncio.wait(
            {output_task, input_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if isinstance(exc, WebSocketDisconnect):
                logger.info(
                    "agent_portal stream client disconnected process=%s",
                    sanitize_for_logging(process_id),
                )
                return
            if exc and not isinstance(exc, asyncio.CancelledError):
                logger.error(
                    "agent_portal stream error process=%s: %s",
                    sanitize_for_logging(process_id),
                    sanitize_for_logging(exc),
                    exc_info=exc,
                )
    finally:
        for t in (output_task, input_task):
            if not t.done():
                t.cancel()
        try:
            await websocket.close()
        except Exception:
            # Socket already closed by peer or framework — nothing to do.
            pass
