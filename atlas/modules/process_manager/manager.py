"""In-memory process manager with async output broadcasting.

Launches subprocesses, keeps a ring buffer of recent output per process,
and fans that output out to any number of live WebSocket listeners via
per-subscriber asyncio.Queue instances.
"""

from __future__ import annotations

import asyncio
import base64
import fcntl
import logging
import os
import pty
import re
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import termios
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Deque, Dict, List, Optional

from atlas.core.log_sanitizer import sanitize_for_logging

logger = logging.getLogger(__name__)

# Strip ANSI CSI, OSC, and other escape sequences. TUIs emit cursor
# moves, screen clears, and SGR color codes that the plain-text stream
# view cannot render, so pass cleaned text upstream.
_ANSI_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"   # CSI: ESC [ ... final
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC: ESC ] ... BEL | ST
    r"|\x1b[PX^_][^\x1b]*\x1b\\"  # DCS/SOS/PM/APC
    r"|\x1b[@-Z\\-_]"              # Single-char escapes (ESC c, ESC =, etc.)
)


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text for plain-text display."""
    # Drop CSI/OSC/etc, then drop lone C0 control chars that have no
    # visual meaning in a plain view (BS, VT, FF, etc.); keep tabs.
    cleaned = _ANSI_RE.sub("", text)
    cleaned = cleaned.replace("\x08", "").replace("\x0b", "").replace("\x0c", "")
    cleaned = cleaned.replace("\x07", "")  # BEL
    return cleaned


# Environment isolation for launched children. The backend process
# holds provider API keys, DB credentials, and cloud creds; passing
# os.environ.copy() leaks all of them to every subprocess a user
# launches. Build a minimal env from an allow-list instead, with a
# defense-in-depth deny-list to catch any secret-shaped variable a
# caller explicitly passes in.
_ENV_ALLOW_EXACT = (
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "TERM",
    "TZ",
    "TMPDIR",
)

# Fixed PATH so the server's venv and any tool dirs on the backend's
# PATH do not leak into children. Users must invoke tools by absolute
# path, or rely on what is installed in these standard locations.
_ENV_FIXED_PATH = "/usr/local/bin:/usr/bin:/bin"

# Deny-list of secret-shaped env vars. Applied after the allow-list
# and caller-supplied extras so that even if a future caller passes
# extra={"AWS_ACCESS_KEY_ID": "..."}, it gets stripped.
_ENV_DENY_SUFFIXES = ("_KEY", "_SECRET", "_TOKEN", "_PASSWORD", "_PASSWD")
_ENV_DENY_PREFIXES = (
    "AWS_",
    "GCP_",
    "ATLAS_",
    "ANTHROPIC_",
    "OPENAI_",
    "CONDA_",
)
_ENV_DENY_EXACT = frozenset(
    {
        "GOOGLE_APPLICATION_CREDENTIALS",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "NODE_PATH",
    }
)


def _is_denied_env_key(key: str) -> bool:
    k = key.upper()
    if k in _ENV_DENY_EXACT:
        return True
    if any(k.startswith(p) for p in _ENV_DENY_PREFIXES):
        return True
    if any(k.endswith(s) for s in _ENV_DENY_SUFFIXES):
        return True
    return False


def _build_child_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Build a minimal env for a launched child process.

    Copies a small allow-list of benign variables from ``os.environ``,
    pins ``PATH`` to a conservative default, layers any caller-supplied
    ``extra`` on top, then strips any key matching the secret-shaped
    deny-list. Denied keys are logged at INFO so a caller can tell
    their addition was dropped.

    TODO: expose a user-supplied env dict on the launch request schema
    once the UI needs it; wiring already accepts it through ``extra``.
    """
    env: Dict[str, str] = {}
    for key in _ENV_ALLOW_EXACT:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    for key, value in os.environ.items():
        if key.startswith("LC_"):
            env[key] = value
    env["PATH"] = _ENV_FIXED_PATH
    if extra:
        env.update(extra)

    dropped: List[str] = []
    for key in list(env.keys()):
        if _is_denied_env_key(key):
            dropped.append(key)
            env.pop(key, None)
    if dropped:
        logger.info(
            "agent_portal env isolation dropped %d key(s): %s",
            len(dropped),
            sanitize_for_logging(",".join(sorted(dropped))),
        )
    return env


_ISOLATION_CAPS: Optional[Dict[str, bool]] = None


def probe_isolation_capabilities() -> Dict[str, bool]:
    """Detect what process-isolation facilities are usable on this host.

    Checked lazily but memoized on first call.
    """
    global _ISOLATION_CAPS
    if _ISOLATION_CAPS is not None:
        return _ISOLATION_CAPS

    caps: Dict[str, bool] = {
        "namespaces": False,
        "cgroups": False,
    }

    # unprivileged user+pid namespaces via unshare(1)
    if shutil.which("unshare"):
        try:
            rc = subprocess.run(
                ["unshare", "--user", "--map-root-user", "--", "true"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            caps["namespaces"] = rc.returncode == 0
        except (subprocess.SubprocessError, OSError):
            caps["namespaces"] = False

    # cgroup resource limits via systemd-run --user --scope
    if shutil.which("systemd-run"):
        try:
            rc = subprocess.run(
                ["systemd-run", "--user", "--quiet", "--scope", "--collect", "--", "true"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            caps["cgroups"] = rc.returncode == 0
        except (subprocess.SubprocessError, OSError):
            caps["cgroups"] = False

    _ISOLATION_CAPS = caps
    return caps


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
    extra_writable_paths: List[str] = field(default_factory=list)
    use_pty: bool = False
    namespaces: bool = False
    isolate_network: bool = False
    memory_limit: Optional[str] = None
    cpu_limit: Optional[str] = None
    pids_limit: Optional[int] = None
    display_name: str = ""
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
            "extra_writable_paths": list(self.extra_writable_paths),
            "use_pty": self.use_pty,
            "namespaces": self.namespaces,
            "isolate_network": self.isolate_network,
            "memory_limit": self.memory_limit,
            "cpu_limit": self.cpu_limit,
            "pids_limit": self.pids_limit,
            "display_name": self.display_name,
        }


class ProcessNotFoundError(KeyError):
    pass


class ProcessManager:
    """Tracks launched subprocesses and fans output to subscribers."""

    def __init__(self, max_processes: int = 50):
        self._processes: Dict[str, ManagedProcess] = {}
        self._asyncio_procs: Dict[str, asyncio.subprocess.Process] = {}
        self._pty_masters: Dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._max_processes = max_processes

    def write_input(self, process_id: str, data: bytes) -> None:
        """Write bytes to the pty master end of a running process."""
        master_fd = self._pty_masters.get(process_id)
        if master_fd is None:
            return
        try:
            os.write(master_fd, data)
        except OSError as e:
            logger.warning(
                "pty write failed for %s: %s",
                sanitize_for_logging(process_id),
                sanitize_for_logging(e),
            )

    def resize_pty(self, process_id: str, cols: int, rows: int) -> None:
        """Resize the pty window for a running process."""
        master_fd = self._pty_masters.get(process_id)
        if master_fd is None:
            return
        cols = max(1, min(1000, int(cols)))
        rows = max(1, min(1000, int(rows)))
        try:
            fcntl.ioctl(
                master_fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", rows, cols, 0, 0),
            )
        except OSError as e:
            logger.debug(
                "pty resize failed for %s: %s",
                sanitize_for_logging(process_id),
                sanitize_for_logging(e),
            )

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

    def rename(self, process_id: str, display_name: str) -> ManagedProcess:
        proc = self._processes.get(process_id)
        if proc is None:
            raise ProcessNotFoundError(process_id)
        proc.display_name = (display_name or "").strip()
        return proc

    async def launch(
        self,
        command: str,
        args: Optional[List[str]] = None,
        cwd: Optional[str] = None,
        user_email: str = "",
        env: Optional[Dict[str, str]] = None,
        sandbox_mode: str = "off",
        extra_writable_paths: Optional[List[str]] = None,
        use_pty: bool = False,
        namespaces: bool = False,
        isolate_network: bool = False,
        memory_limit: Optional[str] = None,
        cpu_limit: Optional[str] = None,
        pids_limit: Optional[int] = None,
        display_name: str = "",
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

        # Stack isolation layers from innermost to outermost:
        #   cgroup-wrap -> namespace-wrap -> landlock-wrap -> command
        # which at exec time unrolls left-to-right.
        if namespaces:
            if not shutil.which("unshare"):
                raise RuntimeError("unshare(1) is not installed on this host")
            unshare_args = [
                "--user", "--map-root-user",
                "--pid", "--fork", "--mount-proc",
                "--uts", "--ipc",
            ]
            if isolate_network:
                unshare_args.append("--net")
            spawn_args = unshare_args + ["--", spawn_cmd, *spawn_args]
            spawn_cmd = "unshare"

        if memory_limit or cpu_limit or pids_limit:
            if not shutil.which("systemd-run"):
                raise RuntimeError(
                    "systemd-run is not available; resource limits require systemd cgroups"
                )
            systemd_args = ["--user", "--scope", "--quiet", "--collect"]
            if memory_limit:
                systemd_args += ["--property", f"MemoryMax={memory_limit}"]
            if cpu_limit:
                systemd_args += ["--property", f"CPUQuota={cpu_limit}"]
            if pids_limit:
                systemd_args += ["--property", f"TasksMax={int(pids_limit)}"]
            spawn_args = systemd_args + ["--", spawn_cmd, *spawn_args]
            spawn_cmd = "systemd-run"

        proc_env = _build_child_env(extra=env)
        # Pass extra writable paths through to the sandbox wrapper via
        # env var so they can be granted write access alongside cwd.
        normalized_extra: List[str] = []
        if sandboxed and extra_writable_paths:
            for raw in extra_writable_paths:
                if not raw or not raw.strip():
                    continue
                expanded = os.path.abspath(os.path.expanduser(raw.strip()))
                normalized_extra.append(expanded)
            if normalized_extra:
                proc_env["ATLAS_SANDBOX_EXTRA_WRITE_PATHS"] = ":".join(normalized_extra)

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
            extra_writable_paths=normalized_extra,
            use_pty=bool(use_pty),
            namespaces=bool(namespaces),
            isolate_network=bool(isolate_network),
            memory_limit=memory_limit,
            cpu_limit=cpu_limit,
            pids_limit=pids_limit,
            display_name=(display_name or "").strip(),
        )

        master_fd: Optional[int] = None
        slave_fd: Optional[int] = None
        if use_pty:
            # When the child expects a TTY (TUIs, progress bars, most
            # interactive tools), pipe-based stdout is line-buffered by
            # libc and nothing streams out until big chunks accumulate.
            # Allocate a pseudo-tty so isatty(1) returns true in the
            # child, and read the master end asynchronously.
            master_fd, slave_fd = pty.openpty()
            flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            # Set a wide/tall window so TUIs don't wrap at the libc
            # default 24x80. 160x40 fits cline, progress bars, and
            # log viewers without being absurd.
            try:
                fcntl.ioctl(
                    slave_fd,
                    termios.TIOCSWINSZ,
                    struct.pack("HHHH", 40, 160, 0, 0),
                )
            except OSError as e:
                logger.debug("TIOCSWINSZ failed: %s", e)
            if "TERM" not in proc_env:
                proc_env["TERM"] = "xterm-256color"

        try:
            if use_pty:
                asyncio_proc = await asyncio.create_subprocess_exec(
                    spawn_cmd,
                    *spawn_args,
                    cwd=resolved_cwd,
                    env=proc_env,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    stdin=slave_fd,
                    start_new_session=True,
                )
            else:
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

        # Parent no longer needs the slave end; the child has it.
        if slave_fd is not None:
            try:
                os.close(slave_fd)
            except OSError:
                # Already closed or race with child — nothing to do.
                pass

        managed.pid = asyncio_proc.pid
        async with self._lock:
            self._processes[process_id] = managed
            self._asyncio_procs[process_id] = asyncio_proc

        launch_msg = f"Started pid={asyncio_proc.pid}: {command} {shlex.join(args)}".rstrip()
        if managed.sandboxed:
            launch_msg += f"  [Landlock sandbox: {managed.sandbox_mode}]"
            if managed.extra_writable_paths:
                launch_msg += f" +write: {', '.join(managed.extra_writable_paths)}"
        if managed.use_pty:
            launch_msg += "  [pty]"
        if managed.namespaces:
            launch_msg += "  [namespaces"
            if managed.isolate_network:
                launch_msg += "+net"
            launch_msg += "]"
        limits: List[str] = []
        if managed.memory_limit:
            limits.append(f"mem={managed.memory_limit}")
        if managed.cpu_limit:
            limits.append(f"cpu={managed.cpu_limit}")
        if managed.pids_limit:
            limits.append(f"pids={managed.pids_limit}")
        if limits:
            launch_msg += f"  [cgroup: {', '.join(limits)}]"
        self._record_chunk(managed, "system", launch_msg)

        if use_pty and master_fd is not None:
            self._pty_masters[process_id] = master_fd
            asyncio.create_task(self._pump_pty(managed, master_fd))
        else:
            asyncio.create_task(self._pump_stream(managed, asyncio_proc.stdout, "stdout"))
            asyncio.create_task(self._pump_stream(managed, asyncio_proc.stderr, "stderr"))
        asyncio.create_task(self._wait_and_finalize(managed, asyncio_proc))

        logger.info(
            "agent_portal process launched id=%s pid=%s user=%s cmd=%s",
            sanitize_for_logging(process_id),
            asyncio_proc.pid,
            sanitize_for_logging(user_email),
            sanitize_for_logging(command),
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
                    # Process group already gone; treat cancellation as done.
                    pass
            else:
                asyncio_proc.terminate()
        except Exception as e:
            logger.warning(
                "Error sending SIGTERM to %s: %s",
                sanitize_for_logging(process_id),
                sanitize_for_logging(e),
            )

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
                            # Process group already gone; no-op.
                            pass
                    else:
                        asyncio_proc.kill()
                except Exception as e:
                    logger.warning(
                        "Error sending SIGKILL to %s: %s",
                        sanitize_for_logging(process_id),
                        sanitize_for_logging(e),
                    )

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

    async def _pump_pty(self, managed: ManagedProcess, master_fd: int) -> None:
        """Read bytes from a pty master, relay as base64-encoded raw chunks.

        For pty-backed processes we keep the raw bytes -- including
        ANSI escape sequences -- so the frontend can render them in
        xterm.js. Text is encoded base64 to travel through the JSON
        WebSocket without losing high bits.
        """
        loop = asyncio.get_event_loop()
        done = asyncio.Event()

        def _on_ready():
            try:
                data = os.read(master_fd, 4096)
            except BlockingIOError:
                return
            except OSError:
                # Child closed the pty (usual path on exit)
                done.set()
                return
            if not data:
                done.set()
                return
            encoded = base64.b64encode(data).decode("ascii")
            self._record_chunk(managed, "raw", encoded)

        try:
            loop.add_reader(master_fd, _on_ready)
        except Exception as e:
            logger.warning(
                "pty add_reader failed for %s: %s",
                sanitize_for_logging(managed.id),
                sanitize_for_logging(e),
            )
            try:
                os.close(master_fd)
            except OSError:
                # fd already closed — expected during teardown races.
                pass
            return

        try:
            await done.wait()
        finally:
            try:
                loop.remove_reader(master_fd)
            except Exception:
                # Reader may already be removed if the child closed the pty first.
                pass
            self._pty_masters.pop(managed.id, None)
            try:
                os.close(master_fd)
            except OSError:
                # fd already closed — expected during teardown.
                pass

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
                    # Subscriber queue full; slow consumer will notice on next get().
                    pass
            self._asyncio_procs.pop(managed.id, None)


_singleton: Optional[ProcessManager] = None


def get_process_manager() -> ProcessManager:
    global _singleton
    if _singleton is None:
        _singleton = ProcessManager()
    return _singleton
