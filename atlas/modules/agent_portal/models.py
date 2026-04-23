"""Data models for the Agent Portal.

All models are framework-free (Pydantic only, no FastAPI dependency) so
they can be imported from protocols, services, and tests without forcing
a route import chain.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class SandboxTier(str, Enum):
    """User-visible sandbox posture choices.

    `restrictive`  - untrusted prompt / read-only analysis; no network.
    `standard`     - normal dev work; egress via allowlist proxy.
    `permissive`   - developer escape hatch; requires explicit opt-in
                     via AGENT_PORTAL_ALLOW_PERMISSIVE_TIER=true.
    """

    restrictive = "restrictive"
    standard = "standard"
    permissive = "permissive"


class NetworkPolicy(str, Enum):
    """How the agent's network stack is restricted."""

    denied = "denied"                   # --unshare-net, no interfaces
    loopback_only = "loopback_only"     # loopback + nothing else routable
    allowlist_proxy = "allowlist_proxy"  # egress forced through filtering proxy
    unrestricted = "unrestricted"       # developer escape hatch only


class SessionState(str, Enum):
    """Portal-owned session state machine.

    Transitions are linear forward-only with fan-out to terminal states
    (`failed`, `reaped`). See `SessionManager` for the allowed edges.
    """

    pending = "pending"
    authenticating = "authenticating"
    launching = "launching"
    running = "running"
    ending = "ending"
    ended = "ended"
    failed = "failed"
    reaped = "reaped"


TERMINAL_STATES = frozenset({SessionState.ended, SessionState.failed, SessionState.reaped})


class AdapterStatus(str, Enum):
    """What the adapter reports about a handle's backing process."""

    unknown = "unknown"
    starting = "starting"
    running = "running"
    exited = "exited"
    failed = "failed"


class Budget(BaseModel):
    """Per-session resource ceilings enforced by the manager and adapter."""

    wall_clock_seconds: int = Field(default=3600, ge=1)
    tool_calls: int = Field(default=200, ge=0)
    tokens: int = Field(default=200_000, ge=0)
    idle_timeout_seconds: int = Field(default=3600, ge=60)
    hard_ttl_seconds: int = Field(default=86_400, ge=60)


class SandboxProfile(BaseModel):
    """Concrete sandbox posture derived from a tier plus launch policy.

    A profile is adapter-agnostic: the `local_process` adapter translates
    it into bwrap flags, a future `kubernetes` adapter translates it into
    NetworkPolicy + SecurityContext, etc. Keeping this abstract is what
    lets the rest of the portal stay generalized.
    """

    tier: SandboxTier
    fs_read_paths: List[str] = Field(default_factory=list)
    fs_read_write_paths: List[str] = Field(default_factory=list)
    fs_exec_paths: List[str] = Field(default_factory=list)
    network: NetworkPolicy = NetworkPolicy.denied
    egress_allowlist: List[str] = Field(default_factory=list)
    seccomp_profile_path: Optional[str] = None
    env_allowlist: List[str] = Field(default_factory=list)
    clear_env: bool = True


class WorkspaceSpec(BaseModel):
    """The filesystem scope the agent is allowed to work within.

    `root` is mandatory for presets that declare `requires_root=true`.
    Additional paths let the user widen access without changing the root,
    e.g. a source tree under `root` plus a scratch dir for outputs.

    Allowed-root enforcement happens in `AgentPortalService.validate_spec`
    against the policy file, so the API layer cannot be tricked into
    mounting `/etc` no matter what the user submits.
    """

    root: str = Field(..., min_length=1)
    additional_read_paths: List[str] = Field(default_factory=list)
    additional_read_write_paths: List[str] = Field(default_factory=list)


class LaunchSpec(BaseModel):
    """User-facing launch request.

    The UI always sends `preset_id`; the backend resolves it to a fixed
    command from policy. Direct `agent_command` remains accepted for
    tests and headless callers but is not exposed through the UI.

    `executor` and `target` are reserved for v2 remote launches. v1
    refuses any value other than executor=local / target=None, so a
    stale client cannot smuggle in a remote launch.
    """

    preset_id: Optional[str] = None
    template_id: Optional[str] = None  # retained for backwards-compatible audit logging
    scope: str = Field(..., min_length=1, max_length=4000)
    tool_allowlist: List[str] = Field(default_factory=list)
    sandbox_tier: SandboxTier = SandboxTier.standard
    budget: Budget = Field(default_factory=Budget)
    workspace: Optional[WorkspaceSpec] = None
    workspace_hint: Optional[str] = None  # legacy free-form hint
    agent_command: Optional[List[str]] = Field(default=None, min_length=1)
    executor: Literal["local"] = "local"
    target: Optional[str] = None

    @model_validator(mode="after")
    def _require_preset_or_command(self) -> "LaunchSpec":
        if self.preset_id is None and not self.agent_command:
            raise ValueError("either preset_id or agent_command must be provided")
        if self.target is not None:
            raise ValueError("target must be null in v1 (remote launch is v2)")
        return self


class Session(BaseModel):
    """Portal-owned session state. The adapter handle is opaque here;
    only the adapter interprets its contents.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    user_email: str
    spec: LaunchSpec
    state: SessionState = SessionState.pending
    adapter_name: str = ""
    adapter_handle: Dict[str, Any] = Field(default_factory=dict)
    audit_path: Optional[str] = None
    termination_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        """Mark the session as recently active; used by the reaper."""
        now = datetime.now(timezone.utc)
        self.last_activity_at = now
        self.updated_at = now
