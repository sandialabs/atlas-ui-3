"""Local-process RuntimeAdapter.

Runs the agent on the same host as the Atlas backend, wrapped in a
sandbox command built from the session's SandboxProfile. The adapter
is intentionally thin: it stores the argv in its handle and exposes
hooks for actually spawning a subprocess. v0 keeps subprocess spawning
optional (`execute=False` by default) so the unit tests exercise the
wiring without touching the kernel.

When `execute=True`, the adapter uses `asyncio.create_subprocess_exec`
and routes stdout/stderr frames into the supplied `AuditStream`. It is
the caller's responsibility to pre-check that bwrap is installed and
that the running kernel supports Landlock.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional

from atlas.modules.agent_portal.audit import AuditStream
from atlas.modules.agent_portal.models import AdapterStatus, SandboxProfile, Session
from atlas.modules.agent_portal.sandbox.launcher import build_sandbox_command


class LocalProcessAdapter:
    """Spawn the agent as a child process of the Atlas backend."""

    name = "local_process"

    def __init__(
        self,
        *,
        sandbox_backend: str = "bubblewrap",
        execute: bool = False,
        audit_stream: Optional[AuditStream] = None,
    ) -> None:
        self._backend = sandbox_backend
        self._execute = execute
        self._audit = audit_stream
        # handle_id -> Process (only populated when execute=True)
        self._processes: Dict[str, asyncio.subprocess.Process] = {}

    def launch(self, session: Session, profile: SandboxProfile) -> Dict[str, Any]:
        argv = build_sandbox_command(
            profile=profile,
            agent_command=list(session.spec.agent_command),
            backend=self._backend,
        )
        handle: Dict[str, Any] = {
            "adapter": self.name,
            "session_id": session.id,
            "argv": argv,
            "backend": self._backend,
            "pid": None,
            "executed": False,
        }
        if self._audit is not None:
            self._audit.append(
                "lifecycle",
                payload={
                    "event": "launch",
                    "adapter": self.name,
                    "backend": self._backend,
                    "tier": profile.tier.value,
                    "network": profile.network.value,
                    "argv_len": len(argv),
                },
            )
        if self._execute:
            asyncio.get_event_loop()  # fail fast if no loop for diagnostics
            handle["_needs_spawn"] = True
        return handle

    async def ensure_spawned(self, handle: Dict[str, Any]) -> None:
        """Start the subprocess if the handle was launched with execute=True.

        Kept as a separate async step so `launch()` can stay synchronous
        (many callers build the handle inside non-async code paths).
        """
        if not handle.get("_needs_spawn") or handle.get("pid") is not None:
            return
        argv: List[str] = handle["argv"]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        handle["pid"] = proc.pid
        handle["executed"] = True
        self._processes[handle["session_id"]] = proc
        if self._audit is not None:
            self._audit.append(
                "lifecycle",
                payload={"event": "spawned", "pid": proc.pid},
            )

    async def attach(self, handle: Dict[str, Any]) -> AsyncIterator[bytes]:
        """Yield stdout bytes. v0 returns an empty iterator unless the
        subprocess was actually spawned."""
        proc = self._processes.get(handle.get("session_id"))
        if proc is None or proc.stdout is None:
            return
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            if self._audit is not None:
                self._audit.append("stdout", data=chunk)
            yield chunk

    async def cancel(self, handle: Dict[str, Any], reason: str) -> None:
        proc = self._processes.get(handle.get("session_id"))
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
        if self._audit is not None:
            self._audit.append(
                "lifecycle",
                payload={"event": "cancelled", "reason": reason},
            )

    def status(self, handle: Dict[str, Any]) -> AdapterStatus:
        proc = self._processes.get(handle.get("session_id"))
        if proc is None:
            return AdapterStatus.unknown if not handle.get("executed") else AdapterStatus.exited
        if proc.returncode is None:
            return AdapterStatus.running
        return AdapterStatus.exited if proc.returncode == 0 else AdapterStatus.failed

    async def collect_artifacts(self, handle: Dict[str, Any]) -> List[Dict[str, Any]]:  # noqa: ARG002
        # Artifact packaging is a follow-up. v0 records nothing.
        return []
