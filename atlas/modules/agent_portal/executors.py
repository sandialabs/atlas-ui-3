"""Executor abstraction used by AgentPortalService.

v1 ships with `LocalExecutor` which spawns the agent as a local process
(optionally under a PTY for TUI apps like cline --tui). The protocol is
defined so v2 can drop in an `SSHWorkerExecutor` without touching the
service, routes, or frontend.

The executor is responsible for:
  - spawning the agent subprocess
  - piping its stdout/stderr into the audit stream as base64 frames
  - driving session state transitions (pending -> launching -> running
    -> ended/failed)
  - canceling a running session on request

Streaming to the UI is *not* the executor's job; the SSE endpoint tails
the audit JSONL. Writing frames there is the only integration surface.
"""

from __future__ import annotations

import os
import pty
import select
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol

from atlas.modules.agent_portal.audit import AuditStream
from atlas.modules.agent_portal.models import (
    LaunchSpec,
    SandboxProfile,
    Session,
    SessionState,
    WorkspaceSpec,
)
from atlas.modules.agent_portal.policy import Preset
from atlas.modules.agent_portal.session_manager import SessionManager


# stdout/stderr frames exceeding this size are split across frames. Keeps
# individual SSE messages small enough for browsers / proxies.
_MAX_FRAME_BYTES = 8192
# Poll interval for the reader thread when selecting on the PTY/pipe.
_READ_TIMEOUT_S = 0.2


@dataclass
class SpawnResult:
    """Returned by `Executor.spawn` - used by the service to record the handle."""

    pid: int
    started_at: float
    extras: dict  # adapter-specific data; stored on session.adapter_handle


class Executor(Protocol):
    """Executor protocol - v2 will add SSHWorkerExecutor alongside LocalExecutor."""

    name: str

    def validate_workspace(self, ws: WorkspaceSpec) -> None:
        """Raise on invalid workspace. Local impls check os.path.isdir;
        remote impls would RPC to the worker."""
        ...

    def spawn(
        self,
        session: Session,
        spec: LaunchSpec,
        profile: SandboxProfile,
        audit: AuditStream,
        preset: Optional[Preset],
    ) -> SpawnResult:
        ...

    def cancel(self, session: Session, grace_s: int = 5) -> None:
        ...


class LocalExecutor:
    """Spawns the agent on the same host as the backend, in a background thread.

    Thread model:
      - one subprocess per session
      - one reader thread per session; it owns the PTY master / pipe FDs,
        reads until EOF or the session transitions to a terminal state,
        writes stdout/stderr frames to the audit stream, then waits on
        the process and transitions to ended/failed
      - cancel() signals the child (SIGTERM -> SIGKILL); the reader
        thread drives the transition when the process exits

    v1 does not attempt to run under bwrap/landlock - the sandbox wrapper
    composition is scheduled for a later pass. The sandbox tier is recorded
    on the session so audit/policy logging is accurate even though the
    enforcement is advisory in this build.
    """

    name = "local"

    def __init__(
        self,
        session_manager: SessionManager,
        *,
        extra_env: Optional[dict] = None,
    ) -> None:
        self._sessions = session_manager
        self._extra_env = dict(extra_env or {})
        # session_id -> (process, master_fd, reader_thread, cancel_event)
        self._live: dict = {}
        self._lock = threading.Lock()

    # --- Executor protocol ---------------------------------------------------

    def validate_workspace(self, ws: WorkspaceSpec) -> None:
        if not os.path.isdir(ws.root):
            raise FileNotFoundError(f"workspace root {ws.root!r} is not a directory")

    def spawn(
        self,
        session: Session,
        spec: LaunchSpec,
        profile: SandboxProfile,
        audit: AuditStream,
        preset: Optional[Preset],
    ) -> SpawnResult:
        if not spec.agent_command:
            raise ValueError("agent_command is required")

        # Transition pending -> launching before the fork so a failed
        # spawn leaves the session in a recoverable state.
        self._sessions.transition(session.id, SessionState.launching)

        env = self._build_env(spec, preset)
        cwd = spec.workspace.root if spec.workspace else os.getcwd()
        use_pty = bool(preset.pty) if preset is not None else False

        audit.append(
            "lifecycle",
            payload={
                "event": "spawn_attempt",
                "command": list(spec.agent_command),
                "cwd": cwd,
                "pty": use_pty,
            },
        )

        master_fd: Optional[int] = None
        try:
            if use_pty:
                master_fd, slave_fd = pty.openpty()
                try:
                    proc = subprocess.Popen(
                        list(spec.agent_command),
                        stdin=slave_fd,
                        stdout=slave_fd,
                        stderr=slave_fd,
                        env=env,
                        cwd=cwd,
                        close_fds=True,
                        start_new_session=True,
                    )
                finally:
                    # Parent side closes the slave; the child inherited it.
                    os.close(slave_fd)
            else:
                proc = subprocess.Popen(
                    list(spec.agent_command),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    cwd=cwd,
                    bufsize=0,
                    close_fds=True,
                    start_new_session=True,
                )
        except FileNotFoundError as exc:
            audit.append(
                "lifecycle",
                payload={"event": "spawn_failed", "error": f"command not found: {exc}"},
            )
            self._sessions.transition(session.id, SessionState.failed, reason=f"command not found: {exc}")
            raise
        except Exception as exc:  # pragma: no cover - any other spawn error
            audit.append("lifecycle", payload={"event": "spawn_failed", "error": str(exc)})
            self._sessions.transition(session.id, SessionState.failed, reason=str(exc))
            raise

        self._sessions.set_adapter(
            session.id,
            adapter_name=self.name,
            handle={"pid": proc.pid, "pty": use_pty},
        )
        self._sessions.transition(session.id, SessionState.running)
        audit.append("lifecycle", payload={"event": "running", "pid": proc.pid})

        cancel_event = threading.Event()
        reader = threading.Thread(
            target=self._reader_loop,
            args=(session.id, proc, master_fd, audit, cancel_event),
            daemon=True,
            name=f"agent-portal-reader-{session.id[:8]}",
        )
        with self._lock:
            self._live[session.id] = (proc, master_fd, reader, cancel_event)
        reader.start()

        return SpawnResult(
            pid=proc.pid,
            started_at=time.time(),
            extras={"pty": use_pty},
        )

    def cancel(self, session: Session, grace_s: int = 5) -> None:
        with self._lock:
            entry = self._live.get(session.id)
        if entry is None:
            # Process already finished or never spawned; let the caller
            # handle the state transition directly.
            return
        proc, _master_fd, _reader, cancel_event = entry
        cancel_event.set()
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        # Give it a grace period, then SIGKILL.
        deadline = time.time() + max(0, grace_s)
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        if proc.poll() is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    # --- Internals -----------------------------------------------------------

    def _build_env(self, spec: LaunchSpec, preset: Optional[Preset]) -> dict:
        """Minimal environment for the child process.

        v1 passes a conservative baseline + scope. Tier-driven env
        allowlists are honored via the sandbox profile in later passes.
        """
        base = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "en_US.UTF-8"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "TERM": "xterm-256color",
        }
        base["AGENT_PORTAL_SCOPE"] = spec.scope
        if spec.workspace:
            base["AGENT_PORTAL_WORKSPACE"] = spec.workspace.root
        if spec.preset_id:
            base["AGENT_PORTAL_PRESET"] = spec.preset_id
        base.update(self._extra_env)
        return base

    def _reader_loop(
        self,
        session_id: str,
        proc: subprocess.Popen,
        master_fd: Optional[int],
        audit: AuditStream,
        cancel_event: threading.Event,
    ) -> None:
        """Read stdout/stderr; write frames; wait; transition on exit."""
        try:
            if master_fd is not None:
                self._pty_reader(session_id, proc, master_fd, audit, cancel_event)
            else:
                self._pipe_reader(session_id, proc, audit, cancel_event)
        finally:
            # Wait for the child to fully exit so we can get the return code.
            try:
                rc = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                rc = proc.wait()

            if master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass

            # Pick a terminal state based on exit code + cancel flag.
            reason: Optional[str] = None
            if cancel_event.is_set():
                target = SessionState.ended
                reason = "user_cancel"
            elif rc == 0:
                target = SessionState.ended
                reason = "exit_ok"
            else:
                target = SessionState.failed
                reason = f"exit_code={rc}"

            # State machine requires running -> ending -> ended. Move
            # through intermediates if we're still in running.
            try:
                current = self._sessions.get(session_id).state
            except KeyError:
                current = None
            if current == SessionState.running:
                try:
                    self._sessions.transition(session_id, SessionState.ending, reason=reason)
                except Exception:  # pragma: no cover - defensive
                    pass
            try:
                self._sessions.transition(session_id, target, reason=reason)
            except Exception:  # pragma: no cover - defensive
                pass

            audit.append(
                "lifecycle",
                payload={
                    "event": "exited",
                    "return_code": rc,
                    "reason": reason,
                },
            )

            with self._lock:
                self._live.pop(session_id, None)

    def _pty_reader(
        self,
        session_id: str,
        proc: subprocess.Popen,
        master_fd: int,
        audit: AuditStream,
        cancel_event: threading.Event,
    ) -> None:
        """PTY-aware reader: stdout and stderr are merged onto the master FD."""
        while not cancel_event.is_set():
            try:
                rlist, _, _ = select.select([master_fd], [], [], _READ_TIMEOUT_S)
            except (OSError, ValueError):
                break
            if not rlist:
                if proc.poll() is not None:
                    break
                continue
            try:
                chunk = os.read(master_fd, _MAX_FRAME_BYTES)
            except OSError:
                break
            if not chunk:
                break
            audit.append("stdout", data=chunk)
            self._sessions.mark_activity(session_id)

    def _pipe_reader(
        self,
        session_id: str,
        proc: subprocess.Popen,
        audit: AuditStream,
        cancel_event: threading.Event,
    ) -> None:
        """Plain pipe reader: stdout and stderr read independently via select."""
        fds: List[int] = []
        fd_to_stream = {}
        if proc.stdout is not None:
            fd = proc.stdout.fileno()
            fds.append(fd)
            fd_to_stream[fd] = ("stdout", proc.stdout)
        if proc.stderr is not None:
            fd = proc.stderr.fileno()
            fds.append(fd)
            fd_to_stream[fd] = ("stderr", proc.stderr)
        while fds and not cancel_event.is_set():
            try:
                rlist, _, _ = select.select(fds, [], [], _READ_TIMEOUT_S)
            except (OSError, ValueError):
                break
            if not rlist and proc.poll() is not None:
                break
            for fd in rlist:
                name, stream = fd_to_stream[fd]
                try:
                    chunk = os.read(fd, _MAX_FRAME_BYTES)
                except OSError:
                    fds = [x for x in fds if x != fd]
                    continue
                if not chunk:
                    fds = [x for x in fds if x != fd]
                    continue
                audit.append(name, data=chunk)
                self._sessions.mark_activity(session_id)
