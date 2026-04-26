"""Protocols for the Agent Portal.

Keeping protocols in `atlas/interfaces/` follows the project's
clean-architecture convention: no concrete implementation or framework
dependency may be imported here.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional, Protocol, runtime_checkable

from atlas.modules.agent_portal.models import (
    AdapterStatus,
    SandboxProfile,
    Session,
)


@runtime_checkable
class RuntimeAdapter(Protocol):
    """Backend that actually materializes an agent session somewhere.

    Every adapter (local_process, ssh_tmux, kubernetes, slurm) implements
    this same small surface. The session manager and audit store never
    see adapter-specific types.
    """

    name: str

    def launch(self, session: Session, profile: SandboxProfile) -> Dict[str, Any]:
        """Start the session. Return an opaque handle stored on the session.

        Implementations should NOT block on the process completing; return
        after the child is alive and its stdio/tee is wired to the audit
        stream. Blocking work belongs in the caller's event loop.
        """
        ...

    async def attach(self, handle: Dict[str, Any]) -> AsyncIterator[bytes]:
        """Yield stdout/stderr bytes as they arrive. v0 implementations
        may be a no-op; browser-attach is a later-phase feature."""
        ...

    async def cancel(self, handle: Dict[str, Any], reason: str) -> None:
        """Request graceful termination. The manager escalates to kill
        if `status()` still reports `running` after a grace period."""
        ...

    def status(self, handle: Dict[str, Any]) -> AdapterStatus:
        """Report what the backing process is doing right now."""
        ...

    async def collect_artifacts(self, handle: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return artifact descriptors (path + sha256 + size) after end.
        v0 may return []; artifact packaging is a later-phase feature."""
        ...


@runtime_checkable
class SandboxLauncher(Protocol):
    """Pure function: turn a profile + agent command into an argv list.

    Kept as a protocol so future backends (e.g. a pyseccomp-native
    launcher without bwrap) can plug in without branching at call sites.
    """

    backend: str

    def build_command(
        self,
        profile: SandboxProfile,
        agent_command: List[str],
        env: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        ...
