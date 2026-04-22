"""Agent Portal - governed, launchable agent work sessions.

Generalized substrate for launching agents behind kernel-level sandboxing
(Landlock + network restriction + optional seccomp), with a pluggable
RuntimeAdapter interface so local-process, SSH+tmux, Kubernetes, and
SLURM backends can share the same control plane.

Entirely gated by `FEATURE_AGENT_PORTAL_ENABLED` (default: false).
See `docs/planning/agent-portal-2026-04-20.md`.
"""

from atlas.modules.agent_portal.models import (
    AdapterStatus,
    Budget,
    LaunchSpec,
    NetworkPolicy,
    SandboxProfile,
    SandboxTier,
    Session,
    SessionState,
)

__all__ = [
    "AdapterStatus",
    "Budget",
    "LaunchSpec",
    "NetworkPolicy",
    "SandboxProfile",
    "SandboxTier",
    "Session",
    "SessionState",
]
