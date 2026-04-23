"""AgentPortalService: public facade wiring flags, policy, sandbox, sessions.

Single entry point used by HTTP routes. Responsibilities:
  - feature-flag gating
  - policy-aware validation (preset visibility, workspace roots, mode)
  - session creation + audit stream open
  - state-machine transitions
  - executor dispatch (v1: local only)

Policy is loaded once at startup via `load_policy` and handed in; the
service never parses yaml itself.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from atlas.modules.agent_portal.adapters.local_process import LocalProcessAdapter
from atlas.modules.agent_portal.audit import AuditStream
from atlas.modules.agent_portal.models import (
    Budget,
    LaunchSpec,
    SandboxProfile,
    SandboxTier,
    Session,
    SessionState,
    WorkspaceSpec,
)
from atlas.modules.agent_portal.policy import Policy, Preset
from atlas.modules.agent_portal.sandbox.profiles import get_default_profile
from atlas.modules.agent_portal.session_manager import SessionManager


class AgentPortalDisabledError(RuntimeError):
    """Raised when a portal operation is attempted with the feature flag off."""


class PermissiveTierForbiddenError(RuntimeError):
    """permissive tier used in prod, or without the opt-in flag in dev."""


class PresetNotAllowedError(RuntimeError):
    """User requested a preset that is not visible to any of their groups,
    or a tier that the preset does not allow."""


class WorkspaceRootNotAllowedError(RuntimeError):
    """Workspace root does not match any glob allowed for this user."""


# Signature of the project's async group-check helper (see atlas.core.auth).
GroupCheck = Callable[[str, str], "object"]  # actually Awaitable[bool]


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
        policy: Optional[Policy] = None,
        mode: Optional[str] = None,
        executor_registry: Optional[dict] = None,
    ) -> None:
        self._enabled = enabled
        self._default_tier = default_tier
        self._allow_permissive = allow_permissive_tier
        self._sandbox_backend = sandbox_backend
        self._audit_dir = Path(audit_dir) if audit_dir else None
        self._sessions = session_manager or SessionManager()
        self._policy = policy
        # Mode precedence: explicit arg > policy.mode > "prod" (safe default).
        # Env var AGENT_PORTAL_MODE is resolved in main.py before construction.
        self._mode = mode or (policy.mode if policy else "prod")
        # Executor registry resolved by caller; v1 ships with LocalExecutor.
        self._executors = executor_registry or {}

    # --- public ---------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def default_tier(self) -> SandboxTier:
        return self._default_tier

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def policy(self) -> Optional[Policy]:
        return self._policy

    def effective_config(self) -> dict:
        """Snapshot for debugging. Not exposed via HTTP in v1 (policy is yaml)."""
        return {
            "enabled": self._enabled,
            "default_tier": self._default_tier.value,
            "allow_permissive_tier": self._allow_permissive,
            "sandbox_backend": self._sandbox_backend,
            "audit_dir": str(self._audit_dir) if self._audit_dir else None,
            "mode": self._mode,
        }

    # Group-aware helpers for routes --------------------------------------------

    def visible_presets(self, user_groups: List[str]) -> List[Preset]:
        if self._policy is None:
            return []
        return self._policy.visible_presets(user_groups)

    def allowed_roots_for(self, user_groups: List[str]) -> List[str]:
        if self._policy is None:
            return []
        return self._policy.allowed_roots_for(user_groups)

    def tier_info(self) -> dict:
        """Serializable tier descriptions for /api/agent-portal/config."""
        if self._policy is None or not self._policy.tiers:
            return {}
        return {
            tier_id: ti.model_dump()
            for tier_id, ti in self._policy.tiers.items()
        }

    # Validation / resolution ---------------------------------------------------

    def resolve_spec(
        self,
        spec: LaunchSpec,
        user_groups: List[str],
    ) -> Tuple[LaunchSpec, Optional[Preset]]:
        """Apply preset defaults + policy validation.

        Returns `(resolved_spec, preset)` where `resolved_spec` has
        agent_command populated from the preset if it was unset, budget
        overrides merged, and sandbox_tier defaulted if missing.
        """
        self._require_enabled()

        preset: Optional[Preset] = None
        if spec.preset_id is not None:
            if self._policy is None:
                raise PresetNotAllowedError("no policy loaded; preset launches unavailable")
            visible_ids = {p.id for p in self._policy.visible_presets(user_groups)}
            if spec.preset_id not in visible_ids:
                raise PresetNotAllowedError(
                    f"preset {spec.preset_id!r} is not available to this user"
                )
            preset = self._policy.preset_by_id(spec.preset_id)
            if preset is None:  # pragma: no cover - visibility check already handles this
                raise PresetNotAllowedError(f"preset {spec.preset_id!r} not found")

            # Resolve command from preset. Users cannot override preset command.
            spec = spec.model_copy(update={"agent_command": list(preset.command)})

            # Tier check.
            if spec.sandbox_tier not in preset.allowed_tiers:
                raise PresetNotAllowedError(
                    f"tier {spec.sandbox_tier.value!r} is not allowed for preset "
                    f"{preset.id!r} (allowed: {[t.value for t in preset.allowed_tiers]})"
                )

            # Budget overrides merge (preset values replace defaults, within field bounds).
            overrides = preset.budget_overrides.model_dump(exclude_none=True)
            if overrides:
                merged = spec.budget.model_copy(update=overrides)
                spec = spec.model_copy(update={"budget": merged})

        # Mode + permissive gating.
        if spec.sandbox_tier is SandboxTier.permissive:
            if self._mode == "prod":
                raise PermissiveTierForbiddenError(
                    "permissive tier is forbidden in prod mode"
                )
            if not self._allow_permissive:
                raise PermissiveTierForbiddenError(
                    "permissive tier requires AGENT_PORTAL_ALLOW_PERMISSIVE_TIER=true"
                )

        # Refuse "none" sandbox backend in prod - there'd be no sandbox.
        if self._mode == "prod" and self._sandbox_backend == "none":
            raise PermissiveTierForbiddenError(
                "sandbox_backend='none' is not allowed in prod mode"
            )

        # Workspace validation.
        requires_root = preset.requires_root if preset is not None else False
        if requires_root:
            if spec.workspace is None:
                raise WorkspaceRootNotAllowedError(
                    "this preset requires a workspace.root but none was provided"
                )
            self._validate_workspace(spec.workspace, user_groups)

        return spec, preset

    def _validate_workspace(
        self,
        ws: WorkspaceSpec,
        user_groups: List[str],
    ) -> None:
        """Check that `ws.root` exists and matches an allowed glob."""
        if self._policy is None:
            raise WorkspaceRootNotAllowedError("no policy loaded; workspace rejected")
        if not self._policy.root_allowed(ws.root, user_groups):
            allowed = self._policy.allowed_roots_for(user_groups)
            raise WorkspaceRootNotAllowedError(
                f"workspace root {ws.root!r} is not in allowed globs for this user "
                f"({allowed!r})"
            )
        # Existence check happens on the executor so remote executors can
        # delegate it; locally we still do a best-effort check so users
        # get a fast failure.
        if not os.path.isdir(ws.root):
            raise WorkspaceRootNotAllowedError(
                f"workspace root {ws.root!r} does not exist or is not a directory"
            )
        for extra in (*ws.additional_read_paths, *ws.additional_read_write_paths):
            if not self._policy.root_allowed(extra, user_groups):
                raise WorkspaceRootNotAllowedError(
                    f"additional path {extra!r} is not in allowed globs"
                )

    # Backwards-compatible entry used by older callers / tests.
    def validate_spec(self, spec: LaunchSpec, user_groups: Optional[List[str]] = None) -> LaunchSpec:
        resolved, _ = self.resolve_spec(spec, user_groups or [])
        return resolved

    def prepare_profile(self, spec: LaunchSpec) -> SandboxProfile:
        """Build the sandbox profile. Caller may mutate before handing to adapter."""
        self._require_enabled()
        return get_default_profile(spec.sandbox_tier)

    # Session lifecycle --------------------------------------------------------

    def create_session(
        self,
        user_email: str,
        spec: LaunchSpec,
        user_groups: Optional[List[str]] = None,
    ) -> Tuple[Session, SandboxProfile, AuditStream]:
        """End-to-end create: validate, allocate session, open audit stream."""
        self._require_enabled()
        spec, preset = self.resolve_spec(spec, user_groups or [])
        session = self._sessions.create(user_email=user_email, spec=spec)
        audit = self._open_audit(session)
        audit.append(
            "policy",
            payload={
                "event": "session_created",
                "user": user_email,
                "preset_id": spec.preset_id,
                "template_id": spec.template_id,
                "sandbox_tier": spec.sandbox_tier.value,
                "tool_allowlist_len": len(spec.tool_allowlist),
                "backend": self._sandbox_backend,
                "mode": self._mode,
                "workspace_root": spec.workspace.root if spec.workspace else None,
                "pty": (preset.pty if preset else False),
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

    def get_session_manager(self) -> SessionManager:
        """Exposed so executors can call transition/set_adapter directly."""
        return self._sessions

    # Executor registry --------------------------------------------------------

    def get_executor(self, name: str = "local"):
        """Return the registered executor for the given name (default 'local')."""
        try:
            return self._executors[name]
        except KeyError as exc:
            raise KeyError(f"no executor registered for {name!r}") from exc

    def build_local_adapter(self, audit: AuditStream, execute: bool = False) -> LocalProcessAdapter:
        """Legacy factory kept for tests that predate the executor registry."""
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
