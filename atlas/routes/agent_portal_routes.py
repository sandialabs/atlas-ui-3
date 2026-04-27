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
    get_portal_store,
    get_preset_store,
    record_audit_event,
)
from atlas.modules.process_manager import (
    GroupBudgetExceededError,
    LandlockUnavailableError,
    ProcessNotFoundError,
    get_process_manager,
    landlock_is_supported,
    make_group_slice_name,
    set_group_slice_limits,
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
    group_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional Agent Portal group to launch into. If set, the "
            "server enforces the group's max_panes budget and nests the "
            "child cgroup under the group's parent slice."
        ),
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

    # Resolve the optional group up front so we can pass enforcement
    # hints (max_panes, parent slice) into the manager. The lookup is
    # owner-scoped so a user cannot launch into another user's group.
    group_max_panes: Optional[int] = None
    group_slice: Optional[str] = None
    if body.group_id:
        store = get_portal_store()
        group = store.get_group(current_user, body.group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Group not found")
        group_max_panes = group.get("max_panes") or None
        # Only opt into the systemd-run --slice wrapping when the group
        # actually carries a parent budget worth enforcing. Wrapping for
        # nesting alone (no budgets) costs us a systemd-run dependency
        # without buying any defense-in-depth, and it breaks on hosts
        # without a per-user systemd bus (CI containers, sandboxes).
        if group.get("mem_budget_bytes") or group.get("cpu_budget_pct"):
            group_slice = make_group_slice_name(body.group_id)
            # Best-effort: pin parent slice limits on every launch so they
            # stay current if the group's budget changes between launches.
            # Failure is non-fatal — the per-process limits still apply.
            set_group_slice_limits(
                group_slice,
                mem_budget_bytes=group.get("mem_budget_bytes") or None,
                cpu_budget_pct=group.get("cpu_budget_pct") or None,
            )

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
            group_id=body.group_id,
            group_max_panes=group_max_panes,
            group_slice=group_slice,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=f"Command not found: {e}")
    except LandlockUnavailableError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except GroupBudgetExceededError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=429, detail=str(e))
    record_audit_event(
        current_user,
        "launch",
        process_id=managed.id,
        group_id=body.group_id,
        detail={
            "command": body.command,
            "args": list(body.args or []),
            "sandbox_mode": sandbox_mode,
            "use_pty": body.use_pty,
        },
    )
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
    record_audit_event(
        current_user,
        "cancel",
        process_id=process_id,
        group_id=managed.group_id,
    )
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
    record_audit_event(
        current_user,
        "rename",
        process_id=process_id,
        group_id=managed.group_id,
        detail={"display_name": body.display_name},
    )
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


# ---------------------------------------------------------------------------
# Server-side PortalStore — UI/config state that used to live in localStorage
# ---------------------------------------------------------------------------
#
# These endpoints are deliberately boring: GET returns the user's blob
# (filtered server-side by user_email), PUT replaces it, no diff/no
# optimistic concurrency. Single-user-on-own-machine target — a future
# multi-tab/multi-device deploy can layer ETags on top without changing
# the surface.


class LayoutPutRequest(BaseModel):
    """Whole-blob layout payload. Schema is opaque on the server — the
    frontend owns the shape (mode, slots, slot->process_id mapping)."""

    layout: dict = Field(default_factory=dict)


class LaunchHistoryUpsertRequest(BaseModel):
    """Single launch-history entry in the UI shape the frontend already
    uses (command, argsString, cwd, sandboxMode, ...)."""

    entry: dict


class LaunchHistoryReplaceRequest(BaseModel):
    """Bulk replace — used by the localStorage migration path."""

    entries: List[dict] = Field(default_factory=list)


class LaunchHistoryDeleteRequest(BaseModel):
    """Delete a single history entry by its server-side dedup key.

    Clients can recompute the dedup key locally (it's a sha256 of
    command|args|cwd|sandboxMode joined by U+001F) but easier to just
    take it from the GET response.
    """

    dedup_key: str = Field(..., min_length=1)


class LaunchConfigsReplaceRequest(BaseModel):
    """Bulk replace — used by the localStorage migration path. Per-config
    CRUD lives on the existing /presets endpoints; this collection
    stays as a backwards-compatible bag for legacy launchConfigs."""

    configs: List[dict] = Field(default_factory=list)


class GroupCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    max_panes: Optional[int] = None
    mem_budget_bytes: Optional[int] = None
    cpu_budget_pct: Optional[int] = None
    idle_kill_seconds: Optional[int] = None
    audit_tag: Optional[str] = None


class GroupUpdateRequest(BaseModel):
    name: Optional[str] = None
    max_panes: Optional[int] = None
    mem_budget_bytes: Optional[int] = None
    cpu_budget_pct: Optional[int] = None
    idle_kill_seconds: Optional[int] = None
    audit_tag: Optional[str] = None


class BundleCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    group_template: dict = Field(default_factory=dict)
    members: List[dict] = Field(default_factory=list)


# Layout --------------------------------------------------------------------


@router.get("/state/layout")
async def get_layout(current_user: str = Depends(get_current_user)):
    _require_enabled()
    store = get_portal_store()
    layout = store.get_layout(current_user)
    return {"layout": layout or {}}


@router.put("/state/layout")
async def put_layout(
    body: LayoutPutRequest,
    current_user: str = Depends(get_current_user),
):
    _require_enabled()
    store = get_portal_store()
    saved = store.put_layout(current_user, body.layout or {})
    return {"layout": saved}


# Launch history ------------------------------------------------------------


@router.get("/state/launch-history")
async def get_launch_history(current_user: str = Depends(get_current_user)):
    _require_enabled()
    store = get_portal_store()
    return {"entries": store.list_launch_history(current_user)}


@router.post("/state/launch-history")
async def upsert_launch_history(
    body: LaunchHistoryUpsertRequest,
    current_user: str = Depends(get_current_user),
):
    """Insert or bump a launch-history entry. Idempotent on the dedup key
    (command + args + cwd + sandboxMode), so the client can fire-and-forget
    on every launch without growing the table on duplicates."""
    _require_enabled()
    store = get_portal_store()
    try:
        entry = store.upsert_launch_history(current_user, body.entry or {})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"entry": entry, "entries": store.list_launch_history(current_user)}


@router.put("/state/launch-history")
async def replace_launch_history(
    body: LaunchHistoryReplaceRequest,
    current_user: str = Depends(get_current_user),
):
    """Bulk replace — used by the one-shot migration from localStorage."""
    _require_enabled()
    store = get_portal_store()
    entries = store.replace_launch_history(current_user, body.entries or [])
    return {"entries": entries}


@router.post("/state/launch-history/delete")
async def delete_launch_history_entry(
    body: LaunchHistoryDeleteRequest,
    current_user: str = Depends(get_current_user),
):
    """Delete a single history row by its server-side dedup_key.

    POST not DELETE because the dedup_key is a sha256 string and we'd
    rather not URL-encode it; this is a private internal endpoint.
    """
    _require_enabled()
    store = get_portal_store()
    deleted = store.delete_launch_history_entry(current_user, body.dedup_key)
    return {"deleted": deleted, "entries": store.list_launch_history(current_user)}


# Launch configs (legacy localStorage bag — distinct from server presets) --


@router.get("/state/launch-configs")
async def get_launch_configs(current_user: str = Depends(get_current_user)):
    _require_enabled()
    store = get_portal_store()
    return {"configs": store.list_launch_configs(current_user)}


@router.put("/state/launch-configs")
async def replace_launch_configs(
    body: LaunchConfigsReplaceRequest,
    current_user: str = Depends(get_current_user),
):
    """Bulk replace — used by the one-shot migration from localStorage.

    Per-config CRUD continues to live on /presets (the saved-presets
    library); this endpoint exists so the migration path is a single
    PUT instead of N POSTs.
    """
    _require_enabled()
    store = get_portal_store()
    configs = store.replace_launch_configs(current_user, body.configs or [])
    return {"configs": configs}


# Groups (CRUD only here; Phase 3 wires launch-time enforcement) -----------


@router.get("/groups")
async def list_groups(current_user: str = Depends(get_current_user)):
    _require_enabled()
    store = get_portal_store()
    return {"groups": store.list_groups(current_user)}


@router.post("/groups", status_code=201)
async def create_group(
    body: GroupCreateRequest,
    current_user: str = Depends(get_current_user),
):
    _require_enabled()
    store = get_portal_store()
    try:
        group = store.create_group(current_user, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    record_audit_event(
        current_user,
        "group_create",
        group_id=group["id"],
        detail={"name": group["name"], "max_panes": group.get("max_panes")},
    )
    return group


@router.get("/groups/{group_id}")
async def get_group(
    group_id: str,
    current_user: str = Depends(get_current_user),
):
    _require_enabled()
    store = get_portal_store()
    group = store.get_group(current_user, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


@router.patch("/groups/{group_id}")
async def update_group(
    group_id: str,
    body: GroupUpdateRequest,
    current_user: str = Depends(get_current_user),
):
    _require_enabled()
    store = get_portal_store()
    patch = body.model_dump(exclude_unset=True)
    group = store.update_group(current_user, group_id, patch)
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    # group_budget_change is a distinct event so a compliance reader can
    # filter on it cheaply (mem/cpu changes are the operationally
    # interesting ones, not display-only renames).
    budget_keys = {"max_panes", "mem_budget_bytes", "cpu_budget_pct", "idle_kill_seconds"}
    is_budget_change = any(k in patch for k in budget_keys)
    record_audit_event(
        current_user,
        "group_budget_change" if is_budget_change else "group_update",
        group_id=group_id,
        detail=patch,
    )
    return group


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(
    group_id: str,
    current_user: str = Depends(get_current_user),
):
    """Delete a group definition. Idempotent: also reaps every running
    member of the group (SIGTERM, then SIGKILL after a grace window),
    so the group's panes don't keep streaming after the group is gone.
    """
    _require_enabled()
    store = get_portal_store()
    # Confirm ownership before reaping anything; cancel members of a
    # group the user does not own would let one user kill another's
    # processes via a known group_id.
    group = store.get_group(current_user, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    manager = get_process_manager()
    members = await manager.cancel_group(group_id)
    deleted = store.delete_group(current_user, group_id)
    if not deleted:
        # Race with another delete — treat as already gone.
        pass
    record_audit_event(
        current_user,
        "group_delete",
        group_id=group_id,
        detail={"members_cancelled": [m.id for m in members]},
    )
    return None


class GroupBroadcastRequest(BaseModel):
    """Bytes (base64) to fan out to every PTY-backed member of a group."""

    data_base64: str = Field(..., min_length=1)


@router.post("/groups/{group_id}/broadcast")
async def broadcast_to_group(
    group_id: str,
    body: GroupBroadcastRequest,
    current_user: str = Depends(get_current_user),
):
    """Fan a single chunk of input out to every PTY-backed member of
    the group. Mostly useful for the CLI / scripted automation; the
    interactive sync-input flow uses the per-process WebSocket with
    ``broadcast: true`` so keystrokes don't have to traverse HTTP per
    character."""
    _require_enabled()
    store = get_portal_store()
    group = store.get_group(current_user, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    try:
        data = base64.b64decode(body.data_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid base64: {e}")
    manager = get_process_manager()
    recipients = manager.broadcast_input(group_id, data)
    record_audit_event(
        current_user,
        "sync_input",
        group_id=group_id,
        detail={"recipients": recipients, "byte_count": len(data)},
    )
    return {"recipients": recipients, "bytes": len(data)}


@router.post("/groups/{group_id}/cancel", status_code=200)
async def cancel_group(
    group_id: str,
    current_user: str = Depends(get_current_user),
):
    """SIGTERM all running members of the group without deleting the
    group definition. Useful for "stop everything but keep the slot"
    workflows."""
    _require_enabled()
    store = get_portal_store()
    group = store.get_group(current_user, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    manager = get_process_manager()
    members = await manager.cancel_group(group_id)
    record_audit_event(
        current_user,
        "group_cancel",
        group_id=group_id,
        detail={"members_cancelled": [m.id for m in members]},
    )
    return {"cancelled": [m.id for m in members]}


# Bundles (CRUD only here; Phase 4 adds the launch endpoint) ---------------


@router.get("/bundles")
async def list_bundles(current_user: str = Depends(get_current_user)):
    _require_enabled()
    store = get_portal_store()
    return {"bundles": store.list_bundles(current_user)}


@router.post("/bundles", status_code=201)
async def create_bundle(
    body: BundleCreateRequest,
    current_user: str = Depends(get_current_user),
):
    _require_enabled()
    store = get_portal_store()
    try:
        bundle = store.create_bundle(current_user, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return bundle


@router.get("/bundles/{bundle_id}")
async def get_bundle(
    bundle_id: str,
    current_user: str = Depends(get_current_user),
):
    _require_enabled()
    store = get_portal_store()
    bundle = store.get_bundle(current_user, bundle_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Bundle not found")
    return bundle


@router.post("/bundles/{bundle_id}/launch")
async def launch_bundle(
    bundle_id: str,
    current_user: str = Depends(get_current_user),
):
    """Atomic-ish bundle launch.

    1. Look up the bundle (owner-scoped).
    2. Create the group from group_template.
    3. Launch each member preset with group_id set.
    4. If any member launch fails, cancel everything launched so far
       and delete the group so the user is not left with a half-built
       bundle.
    """
    _require_enabled()
    store = get_portal_store()
    bundle = store.get_bundle(current_user, bundle_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Bundle not found")

    members = bundle.get("members") or []
    if not members:
        raise HTTPException(status_code=400, detail="Bundle has no members to launch")

    preset_store = get_preset_store()
    # Resolve every preset up front so a typo in member.preset_id fails
    # before we mutate any server state.
    resolved: List[tuple] = []  # list[(member_dict, preset)]
    for member in members:
        pid = member.get("preset_id")
        if not pid:
            raise HTTPException(status_code=400, detail="member missing preset_id")
        try:
            preset = preset_store.get(pid, current_user)
        except PresetNotFoundError:
            raise HTTPException(
                status_code=400,
                detail=f"preset {pid!r} not found in user library",
            )
        resolved.append((member, preset))

    # Step 2 — create the group.
    group_template = bundle.get("group_template") or {}
    group_payload = dict(group_template)
    if not group_payload.get("name"):
        group_payload["name"] = f"{bundle['name']} ({bundle_id[:8]})"
    try:
        group = store.create_group(current_user, group_payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    group_id = group["id"]
    record_audit_event(
        current_user,
        "group_create",
        group_id=group_id,
        detail={"name": group["name"], "from_bundle": bundle_id},
    )
    # Same budget-gated systemd-run wrapping as the single-launch path.
    group_slice: Optional[str] = None
    if group.get("mem_budget_bytes") or group.get("cpu_budget_pct"):
        group_slice = make_group_slice_name(group_id)
        set_group_slice_limits(
            group_slice,
            mem_budget_bytes=group.get("mem_budget_bytes") or None,
            cpu_budget_pct=group.get("cpu_budget_pct") or None,
        )

    manager = get_process_manager()
    launched: List[dict] = []
    for member, preset in resolved:
        try:
            display_name = (
                (member.get("display_name_override") or "").strip()
                or preset.display_name
                or preset.name
            )
            managed = await manager.launch(
                command=preset.command,
                args=preset.args,
                cwd=preset.cwd,
                user_email=current_user,
                sandbox_mode=preset.sandbox_mode,
                extra_writable_paths=preset.extra_writable_paths,
                use_pty=preset.use_pty,
                namespaces=preset.namespaces,
                isolate_network=preset.isolate_network,
                memory_limit=preset.memory_limit,
                cpu_limit=preset.cpu_limit,
                pids_limit=preset.pids_limit,
                group_id=group_id,
                group_max_panes=group.get("max_panes"),
                group_slice=group_slice,
            )
            # Apply the display name override (or preset display name)
            # right away so the UI shows the right label without an
            # extra round trip.
            if display_name:
                manager.rename(managed.id, display_name)
            launched.append(managed.to_summary())
            record_audit_event(
                current_user,
                "launch",
                process_id=managed.id,
                group_id=group_id,
                detail={"from_bundle": bundle_id, "preset_id": preset.id},
            )
        except Exception as e:
            # Roll back: cancel everything launched and drop the group.
            await manager.cancel_group(group_id)
            store.delete_group(current_user, group_id)
            record_audit_event(
                current_user,
                "bundle_launch_rollback",
                group_id=group_id,
                detail={"reason": str(e), "bundle_id": bundle_id},
            )
            raise HTTPException(
                status_code=400,
                detail=f"bundle member launch failed: {e}",
            )

    record_audit_event(
        current_user,
        "bundle_launch",
        group_id=group_id,
        detail={"bundle_id": bundle_id, "process_ids": [p["id"] for p in launched]},
    )
    return {"group": group, "processes": launched}


@router.delete("/bundles/{bundle_id}", status_code=204)
async def delete_bundle(
    bundle_id: str,
    current_user: str = Depends(get_current_user),
):
    _require_enabled()
    store = get_portal_store()
    deleted = store.delete_bundle(current_user, bundle_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Bundle not found")
    return None


# Audit log (read-only here; Phase 4 wires the writers) ---------------------


@router.get("/audit")
async def list_audit(
    limit: int = 200,
    current_user: str = Depends(get_current_user),
):
    _require_enabled()
    store = get_portal_store()
    return {"events": store.list_audit(current_user, limit=limit)}


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
        """Receive input/resize frames from the client.

        ``input`` frames may carry ``broadcast: true``, in which case
        the bytes are fanned out to every running PTY-backed member of
        the source process's group instead of being routed to the
        focused process alone. Server-side fan-out (vs the client
        mirroring N writes) means audit captures one broadcast event
        with N recipients.
        """
        while True:
            msg = await websocket.receive_json()
            mtype = msg.get("type")
            if mtype == "input":
                encoded = msg.get("data") or ""
                try:
                    data = base64.b64decode(encoded)
                except Exception:
                    continue
                if msg.get("broadcast") and managed.group_id:
                    recipients = manager.broadcast_input(managed.group_id, data)
                    record_audit_event(
                        user_email,
                        "sync_input",
                        process_id=process_id,
                        group_id=managed.group_id,
                        detail={
                            "recipients": recipients,
                            # Don't log raw bytes by default — admin
                            # opt-in (Phase 6) lifts this to record the
                            # raw input. Default keeps the size summary.
                            "byte_count": len(data),
                        },
                    )
                else:
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
