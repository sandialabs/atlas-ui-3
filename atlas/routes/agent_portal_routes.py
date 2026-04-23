"""HTTP routes for the Agent Portal.

Built by `build_agent_portal_router(service, groups_for_user)`; `main.py`
should only call this factory when `FEATURE_AGENT_PORTAL_ENABLED=true`.
The service additionally enforces the flag, so a mis-wired mount still
fails safe.

User identity comes from `request.state.user_email` (populated by
AuthMiddleware) with fallback to the `X-User-Email` header or
`?user_email=` query param for test harnesses.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from atlas.modules.agent_portal.audit_tail import tail_frames
from atlas.modules.agent_portal.models import (
    TERMINAL_STATES,
    LaunchSpec,
    SandboxTier,
    SessionState,
)
from atlas.modules.agent_portal.service import (
    AgentPortalDisabledError,
    AgentPortalService,
    PermissiveTierForbiddenError,
    PresetNotAllowedError,
    WorkspaceRootNotAllowedError,
)


# Callable signature: given a user email, return the groups they are in.
# In production this is a small wrapper around is_user_in_group + the
# configured group list; tests can stub it with a fixed list.
GroupsForUser = Callable[[str], Awaitable[List[str]]]


class SessionSummary(BaseModel):
    id: str
    state: SessionState
    sandbox_tier: SandboxTier
    created_at: str
    updated_at: str
    preset_id: Optional[str] = None
    termination_reason: Optional[str] = None


class CreateSessionResponse(BaseModel):
    id: str
    state: SessionState


class PresetPublic(BaseModel):
    """What the frontend sees for each visible preset."""

    id: str
    label: str
    description: str
    pty: bool
    default_tier: SandboxTier
    allowed_tiers: List[SandboxTier]
    requires_root: bool


class PortalConfig(BaseModel):
    enabled: bool
    mode: str
    default_tier: SandboxTier
    allow_permissive_tier: bool
    tiers: dict


def _extract_user(request: Request) -> str:
    """AuthMiddleware populates `request.state.user_email`.

    Falls back to the raw header / query param so test harnesses that
    bypass the middleware still work.
    """
    user_email = getattr(request.state, "user_email", None)
    if not user_email:
        user_email = request.headers.get("X-User-Email") or request.query_params.get("user_email")
    if not user_email:
        raise HTTPException(status_code=401, detail="missing user email")
    return user_email


def build_agent_portal_router(
    service: AgentPortalService,
    groups_for_user: GroupsForUser,
) -> APIRouter:
    """Build the /api/agent-portal/* router."""
    router = APIRouter(prefix="/api/agent-portal", tags=["agent-portal"])

    def get_service() -> AgentPortalService:
        return service

    async def _user_and_groups(request: Request):
        user = _extract_user(request)
        try:
            groups = await groups_for_user(user)
        except Exception:
            groups = []
        return user, groups

    # --- Config ---------------------------------------------------------------

    @router.get("/config", response_model=PortalConfig)
    async def portal_config(svc: AgentPortalService = Depends(get_service)):
        cfg = svc.effective_config()
        return PortalConfig(
            enabled=bool(cfg["enabled"]),
            mode=svc.mode,
            default_tier=SandboxTier(cfg["default_tier"]),
            allow_permissive_tier=bool(cfg["allow_permissive_tier"]),
            tiers=svc.tier_info(),
        )

    # --- Presets & workspace roots -------------------------------------------

    @router.get("/presets", response_model=List[PresetPublic])
    async def list_presets(request: Request, svc: AgentPortalService = Depends(get_service)):
        _user, groups = await _user_and_groups(request)
        visible = svc.visible_presets(groups)
        return [
            PresetPublic(
                id=p.id,
                label=p.label,
                description=p.description,
                pty=p.pty,
                default_tier=p.default_tier,
                allowed_tiers=p.allowed_tiers,
                requires_root=p.requires_root,
            )
            for p in visible
        ]

    @router.get("/workspace-roots")
    async def workspace_roots(request: Request, svc: AgentPortalService = Depends(get_service)):
        _user, groups = await _user_and_groups(request)
        return {"patterns": svc.allowed_roots_for(groups)}

    # --- Sessions -------------------------------------------------------------

    @router.post(
        "/sessions",
        status_code=status.HTTP_201_CREATED,
        response_model=CreateSessionResponse,
    )
    async def create_session(
        spec: LaunchSpec,
        request: Request,
        svc: AgentPortalService = Depends(get_service),
    ):
        user, groups = await _user_and_groups(request)
        try:
            session, _profile, audit = svc.create_session(user, spec, user_groups=groups)
        except AgentPortalDisabledError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except PresetNotAllowedError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except PermissiveTierForbiddenError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except WorkspaceRootNotAllowedError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # Resolve preset (for pty flag + command) and spawn via LocalExecutor.
        preset = None
        if session.spec.preset_id and svc.policy is not None:
            preset = svc.policy.preset_by_id(session.spec.preset_id)
        profile = svc.prepare_profile(session.spec)
        try:
            executor = svc.get_executor("local")
            executor.spawn(session, session.spec, profile, audit, preset)
        except Exception as exc:
            # Executor already transitions to failed + writes audit on
            # spawn errors, but re-raise as HTTP for the caller.
            raise HTTPException(status_code=500, detail=f"spawn failed: {exc}")

        return CreateSessionResponse(id=session.id, state=session.state)

    @router.get("/sessions", response_model=List[SessionSummary])
    async def list_sessions(
        request: Request,
        svc: AgentPortalService = Depends(get_service),
    ):
        user = _extract_user(request)
        try:
            sessions = svc.list_sessions(user)
        except AgentPortalDisabledError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        return [
            SessionSummary(
                id=s.id,
                state=s.state,
                sandbox_tier=s.spec.sandbox_tier,
                created_at=s.created_at.isoformat(),
                updated_at=s.updated_at.isoformat(),
                preset_id=s.spec.preset_id,
                termination_reason=s.termination_reason,
            )
            for s in sessions
        ]

    @router.get("/sessions/{session_id}", response_model=SessionSummary)
    async def get_session(
        session_id: str,
        request: Request,
        svc: AgentPortalService = Depends(get_service),
    ):
        user = _extract_user(request)
        try:
            s = svc.get_session(session_id)
        except AgentPortalDisabledError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except KeyError:
            raise HTTPException(status_code=404, detail="session not found")
        if s.user_email != user:
            raise HTTPException(status_code=403, detail="not the session owner")
        return SessionSummary(
            id=s.id,
            state=s.state,
            sandbox_tier=s.spec.sandbox_tier,
            created_at=s.created_at.isoformat(),
            updated_at=s.updated_at.isoformat(),
            preset_id=s.spec.preset_id,
            termination_reason=s.termination_reason,
        )

    @router.post("/sessions/{session_id}/cancel", response_model=SessionSummary)
    async def cancel_session(
        session_id: str,
        request: Request,
        svc: AgentPortalService = Depends(get_service),
    ):
        user = _extract_user(request)
        try:
            s = svc.get_session(session_id)
            if s.user_email != user:
                raise HTTPException(status_code=403, detail="not the session owner")
            # Prefer graceful executor cancel for running sessions; it
            # drives the state machine through the reader thread.
            if s.state is SessionState.running:
                try:
                    svc.get_executor("local").cancel(s)
                except KeyError:
                    # No executor registered (older test flow); fall
                    # through to the manual transition below.
                    svc.transition(session_id, SessionState.ending, reason="user_cancel")
                    svc.transition(session_id, SessionState.ended, reason="user_cancel")
            elif s.state is SessionState.ending:
                svc.transition(session_id, SessionState.ended, reason="user_cancel")
            elif s.state in (
                SessionState.pending,
                SessionState.authenticating,
                SessionState.launching,
            ):
                svc.transition(session_id, SessionState.failed, reason="user_cancel")
            # Terminal states: no-op.
            s = svc.get_session(session_id)
        except AgentPortalDisabledError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except KeyError:
            raise HTTPException(status_code=404, detail="session not found")
        return SessionSummary(
            id=s.id,
            state=s.state,
            sandbox_tier=s.spec.sandbox_tier,
            created_at=s.created_at.isoformat(),
            updated_at=s.updated_at.isoformat(),
            preset_id=s.spec.preset_id,
            termination_reason=s.termination_reason,
        )

    # --- SSE stream -----------------------------------------------------------

    @router.get("/sessions/{session_id}/stream")
    async def stream_session(
        session_id: str,
        request: Request,
        since_seq: int = 0,
        svc: AgentPortalService = Depends(get_service),
    ):
        user = _extract_user(request)
        try:
            s = svc.get_session(session_id)
        except AgentPortalDisabledError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except KeyError:
            raise HTTPException(status_code=404, detail="session not found")
        if s.user_email != user:
            raise HTTPException(status_code=403, detail="not the session owner")
        if not s.audit_path:
            raise HTTPException(status_code=500, detail="session has no audit path")

        audit_path = Path(s.audit_path)

        async def event_source():
            stop_event = asyncio.Event()
            last_keepalive = asyncio.get_event_loop().time()
            try:
                async for frame in tail_frames(audit_path, since_seq=since_seq, stop_event=stop_event):
                    if await request.is_disconnected():
                        stop_event.set()
                        return
                    # Yield every frame the browser cares about: stdout,
                    # stderr, lifecycle (so the UI updates state), and
                    # policy (initial record).
                    stream = frame.get("stream")
                    if stream not in ("stdout", "stderr", "lifecycle", "policy"):
                        continue
                    # Decode stdout/stderr data_b64 into plain text for the UI.
                    payload = dict(frame)
                    b64 = payload.pop("data_b64", None)
                    if b64 is not None:
                        try:
                            payload["text"] = base64.b64decode(b64).decode("utf-8", errors="replace")
                        except Exception:
                            payload["text"] = ""
                    yield {"event": "frame", "data": json.dumps(payload)}

                    # Close the stream once the session is terminal and
                    # we have delivered the final lifecycle frame.
                    try:
                        cur = svc.get_session(session_id).state
                    except KeyError:
                        stop_event.set()
                        return
                    if cur in TERMINAL_STATES and stream == "lifecycle":
                        stop_event.set()
                        return

                    # Periodic keepalive (browsers drop idle connections).
                    now = asyncio.get_event_loop().time()
                    if now - last_keepalive > 15:
                        last_keepalive = now
                        yield {"event": "keepalive", "data": ""}
            except asyncio.CancelledError:
                stop_event.set()
                raise

        return EventSourceResponse(event_source())

    return router
