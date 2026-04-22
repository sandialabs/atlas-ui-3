"""HTTP routes for the Agent Portal.

The router is built by `build_agent_portal_router(service)`; `main.py`
should only call that factory (and mount the returned router) when
`FEATURE_AGENT_PORTAL_ENABLED=true`. The service itself additionally
enforces the flag, so a mis-wired mount still fails safe.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from atlas.modules.agent_portal.models import LaunchSpec, SandboxTier, SessionState
from atlas.modules.agent_portal.service import (
    AgentPortalDisabledError,
    AgentPortalService,
    PermissiveTierForbiddenError,
)


class SessionSummary(BaseModel):
    id: str
    state: SessionState
    sandbox_tier: SandboxTier
    created_at: str
    updated_at: str
    termination_reason: Optional[str] = None


class CreateSessionResponse(BaseModel):
    id: str
    state: SessionState


def _extract_user(request: Request) -> str:
    """Mirror the project's auth convention: reverse proxy injects
    `X-User-Email`; dev falls back to query param."""
    header = request.headers.get("X-User-Email") or request.query_params.get("user_email")
    if not header:
        raise HTTPException(status_code=401, detail="missing user email")
    return header


def build_agent_portal_router(service: AgentPortalService) -> APIRouter:
    """Build the /api/agent-portal/* router around a given service instance."""
    router = APIRouter(prefix="/api/agent-portal", tags=["agent-portal"])

    def get_service() -> AgentPortalService:
        return service

    @router.get("/config")
    async def portal_config(svc: AgentPortalService = Depends(get_service)):
        """Non-admin effective config; admin details live under /admin."""
        return {
            "enabled": svc.enabled,
            "default_tier": svc.default_tier.value,
        }

    @router.get("/admin/config")
    async def admin_config(svc: AgentPortalService = Depends(get_service)):
        # Production should wrap this with the existing admin-group check
        # from AuthMiddleware; the route itself stays minimal here.
        return svc.effective_config()

    @router.post("/sessions", status_code=status.HTTP_201_CREATED, response_model=CreateSessionResponse)
    async def create_session(
        spec: LaunchSpec,
        request: Request,
        svc: AgentPortalService = Depends(get_service),
    ):
        user = _extract_user(request)
        try:
            session, _profile, _audit = svc.create_session(user, spec)
        except AgentPortalDisabledError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except PermissiveTierForbiddenError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
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
            # Admin-group bypass would be added here in production.
            raise HTTPException(status_code=403, detail="not the session owner")
        return SessionSummary(
            id=s.id,
            state=s.state,
            sandbox_tier=s.spec.sandbox_tier,
            created_at=s.created_at.isoformat(),
            updated_at=s.updated_at.isoformat(),
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
            # Drive the session to a terminal state via the valid edges for
            # its current state. `pending`/`authenticating`/`launching` go
            # straight to `failed` with reason=user_cancel since they never
            # actually ran; `running` gets a graceful `ending`->`ended`.
            if s.state is SessionState.running:
                svc.transition(session_id, SessionState.ending, reason="user_cancel")
                svc.transition(session_id, SessionState.ended, reason="user_cancel")
            elif s.state is SessionState.ending:
                svc.transition(session_id, SessionState.ended, reason="user_cancel")
            elif s.state in (SessionState.pending, SessionState.authenticating, SessionState.launching):
                svc.transition(session_id, SessionState.failed, reason="user_cancel")
            # Already terminal: no-op, return current state.
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
            termination_reason=s.termination_reason,
        )

    return router
