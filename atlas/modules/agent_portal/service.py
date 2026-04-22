"""AgentPortalService: public facade wiring flags, sandbox, and sessions.

This is the single entry point HTTP routes use. It performs every
policy check (feature flag, permissive-tier opt-in, backend selection)
before delegating to the session manager, adapter, and audit log.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from atlas.modules.agent_portal.adapters.local_process import LocalProcessAdapter
from atlas.modules.agent_portal.audit import AuditStream
from atlas.modules.agent_portal.models import (
    LaunchSpec,
    SandboxProfile,
    SandboxTier,
    Session,
    SessionState,
)
from atlas.modules.agent_portal.sandbox.profiles import get_default_profile
from atlas.modules.agent_portal.session_manager import SessionManager


class AgentPortalDisabledError(RuntimeError):
    """Raised when a portal operation is attempted with the feature flag off."""


class PermissiveTierForbiddenError(RuntimeError):
    """Raised when a launch requests the permissive tier but the
    admin has not set AGENT_PORTAL_ALLOW_PERMISSIVE_TIER=true."""


class AgentPortalService:
    """Policy-aware facade; safe to instantiate even when disabled."""

    def __init__(
        self,
        *,
        enabled: bool,
        default_tier: SandboxTier = SandboxTier.standard,
        allow_permissive_tier: bool = False,
        sandbox_backend: str = "bubblewrap",
        audit_dir: Optional[Path] = None,
        session_manager: Optional[SessionManager] = None,
    ) -> None:
        self._enabled = enabled
        self._default_tier = default_tier
        self._allow_permissive = allow_permissive_tier
        self._sandbox_backend = sandbox_backend
        self._audit_dir = Path(audit_dir) if audit_dir else None
        self._sessions = session_manager or SessionManager()

    # --- public ---------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def default_tier(self) -> SandboxTier:
        return self._default_tier

    def effective_config(self) -> dict:
        """Return a snapshot for admin endpoints / shell config."""
        return {
            "enabled": self._enabled,
            "default_tier": self._default_tier.value,
            "allow_permissive_tier": self._allow_permissive,
            "sandbox_backend": self._sandbox_backend,
            "audit_dir": str(self._audit_dir) if self._audit_dir else None,
        }

    def validate_spec(self, spec: LaunchSpec) -> LaunchSpec:
        """Policy checks. Returns the spec (possibly tier-normalized)."""
        self._require_enabled()
        if spec.sandbox_tier is SandboxTier.permissive and not self._allow_permissive:
            raise PermissiveTierForbiddenError(
                "permissive tier requires AGENT_PORTAL_ALLOW_PERMISSIVE_TIER=true"
            )
        return spec

    def prepare_profile(self, spec: LaunchSpec) -> SandboxProfile:
        """Build the sandbox profile for the spec. Callers may mutate
        the returned copy before handing it to the adapter."""
        self._require_enabled()
        return get_default_profile(spec.sandbox_tier)

    def create_session(self, user_email: str, spec: LaunchSpec) -> Tuple[Session, SandboxProfile, AuditStream]:
        """End-to-end create: validate, allocate session, open audit stream."""
        self._require_enabled()
        spec = self.validate_spec(spec)
        session = self._sessions.create(user_email=user_email, spec=spec)
        audit = self._open_audit(session)
        audit.append(
            "policy",
            payload={
                "event": "session_created",
                "user": user_email,
                "template_id": spec.template_id,
                "sandbox_tier": spec.sandbox_tier.value,
                "tool_allowlist_len": len(spec.tool_allowlist),
                "backend": self._sandbox_backend,
            },
        )
        profile = self.prepare_profile(spec)
        return session, profile, audit

    def get_session(self, session_id: str) -> Session:
        self._require_enabled()
        return self._sessions.get(session_id)

    def list_sessions(self, user_email: str) -> List[Session]:
        self._require_enabled()
        return self._sessions.list_for_user(user_email)

    def transition(self, session_id: str, new_state: SessionState, reason: Optional[str] = None) -> Session:
        self._require_enabled()
        return self._sessions.transition(session_id, new_state, reason=reason)

    def build_local_adapter(self, audit: AuditStream, execute: bool = False) -> LocalProcessAdapter:
        """Factory for the default adapter. Kept here so routes never
        import the adapter class directly."""
        return LocalProcessAdapter(
            sandbox_backend=self._sandbox_backend,
            execute=execute,
            audit_stream=audit,
        )

    # --- internals ------------------------------------------------------------

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise AgentPortalDisabledError(
                "agent portal is disabled (FEATURE_AGENT_PORTAL_ENABLED=false)"
            )

    def _open_audit(self, session: Session) -> AuditStream:
        if self._audit_dir is None:
            raise RuntimeError(
                "audit_dir is required for create_session; configure it at service construction"
            )
        path = self._audit_dir / f"{session.id}.jsonl"
        self._sessions.set_audit_path(session.id, str(path))
        return AuditStream(path=path, session_id=session.id)
