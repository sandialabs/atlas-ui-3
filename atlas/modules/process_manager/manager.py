"""In-memory process manager with async output broadcasting.

Launches subprocesses, keeps a ring buffer of recent output per process,
and fans that output out to any number of live WebSocket listeners via
per-subscriber asyncio.Queue instances.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import signal
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


class ProcessStatus(str, Enum):
    RUNNING = "running"
    EXITED = "exited"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class OutputChunk:
    stream: str  # "stdout" | "stderr" | "system"
    text: str
    timestamp: float


@dataclass
class ManagedProcess:
    id: str
    command: str
    args: List[str]
    cwd: Optional[str]
    user_email: str
    started_at: float
    status: ProcessStatus = ProcessStatus.RUNNING
    exit_code: Optional[int] = None
    ended_at: Optional[float] = None
    pid: Optional[int] = None
    sandboxed: bool = False
    sandbox_mode: str = "off"
    history: Deque[OutputChunk] = field(default_factory=lambda: deque(maxlen=2000))
    subscribers: List[asyncio.Queue] = field(default_factory=list)

    def to_summary(self) -> dict:
        return {
            "id": self.id,
            "command": self.command,
            "args": self.args,
            "cwd": self.cwd,
            "user_email": self.user_email,
            "pid": self.pid,
            "status": self.status.value,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "sandboxed": self.sandboxed,
            "sandbox_mode": self.sandbox_mode,
        }


class ProcessNotFoundError(KeyError):
    pass


class ProcessManager:
    """Tracks launched subprocesses and fans output to subscribers."""

    def __init__(self, max_processes: int = 50):
        self._processes: Dict[str, ManagedProcess] = {}
        self._asyncio_procs: Dict[str, asyncio.subprocess.Process] = {}
        self._lock = asyncio.Lock()
        self._max_processes = max_processes

    def list_processes(self, user_email: Optional[str] = None) -> List[dict]:
        items = list(self._processes.values())
        if user_email is not None:
            items = [p for p in items if p.user_email == user_email]
        items.sort(key=lambda p: p.started_at, reverse=True)
        return [p.to_summary() for p in items]

    def get(self, process_id: str) -> ManagedProcess:
        proc = self._processes.get(process_id)
        if proc is None:
            raise ProcessNotFoundError(process_id)
        return proc

    async def launch(
        self,
        command: str,
        args: Optional[List[str]] = None,
        cwd: Optional[str] = None,
        user_email: str = "",
        env: Optional[Dict[str, str]] = None,
        sandbox_mode: str = "off",
    ) -> ManagedProcess:
        """Launch a subprocess and register it.

        ``sandbox_mode`` controls Landlock filesystem sandboxing:

        - ``off``: no sandbox.
        - ``strict``: reads restricted to standard system roots + the
          target binary's directory; writes confined to ``cwd``.
        - ``workspace-write``: reads allowed anywhere, writes confined
          to ``cwd``.

        The sandboxed modes require ``cwd`` to be set and the kernel
        to support Landlock.
        """
        args = list(args or [])
        if not command or not command.strip():
            raise ValueError("command is required")

        # Soft cap on concurrent tracked processes
        live = sum(1 for p in self._processes.values() if p.status == ProcessStatus.RUNNING)
        if live >= self._max_processes:
            raise RuntimeError(
                f"Too many active processes ({live}/{self._max_processes}); "
                "cancel one before launching another"
            )

        resolved_cwd = cwd or None
        if resolved_cwd and not os.path.isdir(resolved_cwd):
            raise ValueError(f"cwd does not exist: {resolved_cwd}")

        # Sandboxed launches are routed through a Python wrapper that
        # applies Landlock after the first exec and before the second
        # execvp into the user's command. This avoids ``preexec_fn``,
        # which interacts badly with uvloop-backed subprocess creation.
        if sandbox_mode not in ("off", "strict", "workspace-write"):
            raise ValueError(f"unknown sandbox_mode: {sandbox_mode!r}")
        sandboxed = sandbox_mode != "off"

        spawn_cmd = command
        spawn_args: List[str] = args
        if sandboxed:
            if not resolved_cwd:
                raise ValueError("sandbox mode requires a cwd to be set")
            from atlas.modules.process_manager.landlock import (
                LandlockUnavailableError,
                is_supported,
            )
            if not is_supported():
                raise LandlockUnavailableError(
                    "Landlock is not available on this host kernel"
                )

            workdir_abs = os.path.abspath(resolved_cwd)
            wrapper_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "_sandbox_launch.py")
            )
            spawn_cmd = sys.executable
            spawn_args = [
                wrapper_path,
                sandbox_mode,
                workdir_abs,
                command,
                *args,
            ]

        proc_env = os.environ.copy()
        if env:
            proc_env.update(env)

        process_id = str(uuid.uuid4())
        managed = ManagedProcess(
            id=process_id,
            command=command,
            args=args,
            cwd=resolved_cwd,
            user_email=user_email,
            started_at=time.time(),
            sandboxed=sandboxed,
            sandbox_mode=sandbox_mode,
        )

        try:
            asyncio_proc = await asyncio.create_subprocess_exec(
                spawn_cmd,
                *spawn_args,
                cwd=resolved_cwd,
                env=proc_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError as e:
            managed.status = ProcessStatus.FAILED
            managed.ended_at = time.time()
            managed.exit_code = -1
            self._record_chunk(managed, "system", f"Failed to launch: {e}")
            async with self._lock:
                self._processes[process_id] = managed
            raise

        managed.pid = asyncio_proc.pid
        async with self._lock:
            self._processes[process_id] = managed
            self._asyncio_procs[process_id] = asyncio_proc

        launch_msg = f"Started pid={asyncio_proc.pid}: {command} {shlex.join(args)}".rstrip()
        if managed.sandboxed:
            launch_msg += f"  [Landlock sandbox: {managed.sandbox_mode}]"
        self._record_chunk(managed, "system", launch_msg)

        asyncio.create_task(self._pump_stream(managed, asyncio_proc.stdout, "stdout"))
        asyncio.create_task(self._pump_stream(managed, asyncio_proc.stderr, "stderr"))
        asyncio.create_task(self._wait_and_finalize(managed, asyncio_proc))

        logger.info(
            "agent_portal process launched id=%s pid=%s user=%s cmd=%s",
            process_id, asyncio_proc.pid, user_email, command,
        )
        return managed

    async def cancel(self, process_id: str, *, sigkill_after: float = 3.0) -> ManagedProcess:
        async with self._lock:
            managed = self._processes.get(process_id)
            asyncio_proc = self._asyncio_procs.get(process_id)
        if managed is None:
            raise ProcessNotFoundError(process_id)
        if managed.status != ProcessStatus.RUNNING or asyncio_proc is None:
            return managed

        self._record_chunk(managed, "system", "Cancellation requested (SIGTERM)")
        try:
            # Signal the whole process group so child shells etc. die too.
            if asyncio_proc.pid is not None:
                try:
                    os.killpg(os.getpgid(asyncio_proc.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                asyncio_proc.terminate()
        except Exception as e:
            logger.warning("Error sending SIGTERM to %s: %s", process_id, e)

        async def _kill_if_still_alive():
            try:
                await asyncio.wait_for(asyncio_proc.wait(), timeout=sigkill_after)
            except asyncio.TimeoutError:
                self._record_chunk(managed, "system", "Forced kill (SIGKILL)")
                try:
                    if asyncio_proc.pid is not None:
                        try:
                            os.killpg(os.getpgid(asyncio_proc.pid), signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        asyncio_proc.kill()
                except Exception as e:
                    logger.warning("Error sending SIGKILL to %s: %s", process_id, e)

        asyncio.create_task(_kill_if_still_alive())
        managed.status = ProcessStatus.CANCELLED
        return managed

    async def subscribe(self, process_id: str) -> AsyncIterator[OutputChunk]:
        """Yield historical chunks, then live chunks, then terminate when process ends."""
        managed = self.get(process_id)
        queue: asyncio.Queue = asyncio.Queue()
        # Snapshot history under lock to avoid mid-iteration mutation
        async with self._lock:
            history_snapshot = list(managed.history)
            managed.subscribers.append(queue)
            already_done = managed.status != ProcessStatus.RUNNING

        try:
            for chunk in history_snapshot:
                yield chunk
            if already_done:
                return
            while True:
                chunk = await queue.get()
                if chunk is None:  # sentinel = process ended
                    return
                yield chunk
        finally:
            async with self._lock:
                if queue in managed.subscribers:
                    managed.subscribers.remove(queue)

    def _record_chunk(self, managed: ManagedProcess, stream: str, text: str) -> None:
        chunk = OutputChunk(stream=stream, text=text, timestamp=time.time())
        managed.history.append(chunk)
        for q in list(managed.subscribers):
            try:
                q.put_nowait(chunk)
            except asyncio.QueueFull:
                logger.warning("Dropping chunk for slow subscriber on process %s", managed.id)

    async def _pump_stream(
        self,
        managed: ManagedProcess,
        stream: Optional[asyncio.StreamReader],
        label: str,
    ) -> None:
        if stream is None:
            return
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode(errors="replace").rstrip("\n")
                self._record_chunk(managed, label, text)
        except Exception as e:
            logger.warning("Error pumping %s for %s: %s", label, managed.id, e)

    async def _wait_and_finalize(
        self,
        managed: ManagedProcess,
        asyncio_proc: asyncio.subprocess.Process,
    ) -> None:
        exit_code = await asyncio_proc.wait()
        managed.exit_code = exit_code
        managed.ended_at = time.time()
        if managed.status == ProcessStatus.CANCELLED:
            pass
        elif exit_code == 0:
            managed.status = ProcessStatus.EXITED
        else:
            managed.status = ProcessStatus.FAILED
        self._record_chunk(
            managed,
            "system",
            f"Process ended status={managed.status.value} exit_code={exit_code}",
        )
        # Wake any live subscribers so they close their streams
        async with self._lock:
            for q in list(managed.subscribers):
                try:
                    q.put_nowait(None)
                except asyncio.QueueFull:
                    pass
            self._asyncio_procs.pop(managed.id, None)


_singleton: Optional[ProcessManager] = None


def get_process_manager() -> ProcessManager:
    global _singleton
    if _singleton is None:
        _singleton = ProcessManager()
    return _singleton
