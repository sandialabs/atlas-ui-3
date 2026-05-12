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
from typing import Any, AsyncIterator, Deque, Dict, List, Optional

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


def _build_child_env(
    extra: Optional[Dict[str, str]] = None,
    *,
    extra_path_dirs: Optional[List[str]] = None,
) -> Dict[str, str]:
    """Build a minimal env for a launched child process.

    Copies a small allow-list of benign variables from ``os.environ``,
    pins ``PATH`` to a conservative default, layers any caller-supplied
    ``extra`` on top, then strips any key matching the secret-shaped
    deny-list. Denied keys are logged at INFO so a caller can tell
    their addition was dropped.

    ``extra_path_dirs`` are prepended to the pinned ``PATH``. The launch
    path uses this to add the directory of the resolved command so that
    a shebang interpreter alongside the binary (``node`` for an
    nvm-installed CLI, ``python`` for a venv, etc.) can be found by
    ``/usr/bin/env <interp>`` — without that, well-formed CLIs from
    nvm/venv/uv fail with the misleading exit 127.

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
    path_parts: List[str] = []
    if extra_path_dirs:
        for d in extra_path_dirs:
            if d and d not in path_parts:
                path_parts.append(d)
    path_parts.extend(_ENV_FIXED_PATH.split(":"))
    env["PATH"] = ":".join(path_parts)
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
    # Group membership — set when the process is launched into a group
    # via the launch endpoint. Drives parent-cgroup placement (when
    # cgroups are available) and group-cancel reaping.
    group_id: Optional[str] = None
    # Last-activity timestamp (stdout/stderr/raw chunk seen). Used by
    # the idle-kill sweeper to reap silent processes after their
    # group's idle_kill_seconds has elapsed.
    last_activity: float = 0.0
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
            "group_id": self.group_id,
        }


# Group runtime registry — kept separate from PortalStore (which holds
# the *definitions*) because group membership of running processes is
# pure runtime state. Restart-survival for processes is explicitly out
# of scope (see Q3 in the action plan), so this lives in memory.
class GroupBudgetExceededError(RuntimeError):
    """Raised when adding a process to a group would push it past
    ``max_panes`` or another budget. Caller should map to HTTP 429."""


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

    def pause_group(self, group_id: str) -> List[str]:
        """SIGSTOP every running PTY-or-not member of ``group_id``.

        SIGSTOP halts the process but doesn't reap it; the audit log
        and process registry continue to show the pane. Pair with
        ``resume_group`` to undo. Returns the list of ids that were
        signalled.
        """
        return self._signal_group(group_id, signal.SIGSTOP)

    def resume_group(self, group_id: str) -> List[str]:
        """SIGCONT every member of ``group_id``."""
        return self._signal_group(group_id, signal.SIGCONT)

    def _signal_group(self, group_id: str, sig: int) -> List[str]:
        sent: List[str] = []
        for proc in self.list_processes_in_group(group_id):
            if proc.pid is None:
                continue
            try:
                os.killpg(os.getpgid(proc.pid), sig)
                sent.append(proc.id)
            except (ProcessLookupError, PermissionError) as exc:
                logger.info(
                    "signal %s to %s skipped: %s",
                    sig,
                    sanitize_for_logging(proc.id),
                    sanitize_for_logging(exc),
                )
        return sent

    def snapshot_group(self, group_id: str) -> Dict[str, Any]:
        """Build an in-memory snapshot of every member's scrollback.

        Returned shape is plain JSON-friendly dicts; the caller (route
        handler) decides whether to stream as JSON or pack into a
        tarball / zip. Keeping the marshalling out of here means a
        future container/remote executor can produce the same shape
        without dragging tar dependencies into the manager.
        """
        members = []
        for proc in self.list_processes_in_group(group_id):
            members.append({
                "process_id": proc.id,
                "display_name": proc.display_name,
                "command": proc.command,
                "args": list(proc.args),
                "status": proc.status.value,
                "history": [
                    {"stream": c.stream, "text": c.text, "timestamp": c.timestamp}
                    for c in list(proc.history)
                ],
            })
        return {"group_id": group_id, "captured_at": time.time(), "members": members}

    def broadcast_input(self, group_id: str, data: bytes) -> List[str]:
        """Fan ``data`` out to the pty master of every running member of
        ``group_id``. Returns the list of process_ids the bytes were
        written to so the caller can audit the broadcast.

        Server-side fan-out (vs the client mirroring N writes) means
        the audit log records a single broadcast event with N
        recipients rather than N independent input events. That is the
        difference between "I typed pwd into this group" and "the
        client wrote pwd to four sockets" in compliance review.
        """
        recipients: List[str] = []
        for proc in self.list_processes_in_group(group_id):
            if proc.use_pty and proc.id in self._pty_masters:
                self.write_input(proc.id, data)
                recipients.append(proc.id)
        return recipients

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

    def idle_seconds_for(self, process_id: str) -> Optional[float]:
        """Seconds since the process last emitted a non-system chunk.

        Returns None if the process is not tracked (e.g. already
        garbage-collected). Used by the idle-kill sweeper.
        """
        proc = self._processes.get(process_id)
        if proc is None or proc.status != ProcessStatus.RUNNING:
            return None
        return max(0.0, time.time() - proc.last_activity)

    async def reap_idle_in_group(
        self, group_id: str, idle_kill_seconds: float
    ) -> List[str]:
        """Cancel members of ``group_id`` whose ``last_activity`` is
        older than ``idle_kill_seconds``. Returns the cancelled ids so
        the caller can audit the sweep.
        """
        if idle_kill_seconds is None or idle_kill_seconds <= 0:
            return []
        cutoff = time.time() - idle_kill_seconds
        cancelled: List[str] = []
        for proc in list(self.list_processes_in_group(group_id)):
            if proc.last_activity <= cutoff:
                try:
                    await self.cancel(proc.id)
                    cancelled.append(proc.id)
                except ProcessNotFoundError:
                    continue
        return cancelled

    def list_processes_in_group(self, group_id: str) -> List[ManagedProcess]:
        """Return all *running* processes assigned to ``group_id``.

        Used by ``launch`` to enforce per-group ``max_panes`` budgets
        and by ``cancel_group`` to reap members.
        """
        return [
            p for p in self._processes.values()
            if p.group_id == group_id and p.status == ProcessStatus.RUNNING
        ]

    async def cancel_group(
        self, group_id: str, *, sigkill_after: float = 3.0
    ) -> List[ManagedProcess]:
        """SIGTERM every running member of ``group_id``; SIGKILL after a
        grace window per member. Idempotent — already-dead members are
        skipped silently. Returns the list of processes that were
        targeted (their statuses will flip asynchronously)."""
        members = self.list_processes_in_group(group_id)
        for member in members:
            try:
                await self.cancel(member.id, sigkill_after=sigkill_after)
            except ProcessNotFoundError:
                # Race with finalize — fine to skip.
                continue
        return members

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
        group_id: Optional[str] = None,
        group_max_panes: Optional[int] = None,
        group_slice: Optional[str] = None,
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

        # Group-budget enforcement happens server-side, before any work.
        # The caller (route handler) is expected to pass the looked-up
        # group's ``max_panes`` budget so this module stays decoupled
        # from PortalStore.
        if group_id and group_max_panes is not None and group_max_panes > 0:
            current = len(self.list_processes_in_group(group_id))
            if current >= group_max_panes:
                raise GroupBudgetExceededError(
                    f"group {group_id!r} is full "
                    f"({current}/{group_max_panes} panes); cancel one before "
                    f"launching another"
                )

        # The child's env is minimal and PATH is pinned
        # (/usr/local/bin:/usr/bin:/bin) so secrets cannot leak into
        # subprocesses. That also means the child cannot find binaries
        # under ~/.local/bin, a venv, Nix profiles, etc. Resolve
        # non-absolute commands here using the *server's* full PATH so
        # users can type ``claude`` or ``uvx`` without knowing the
        # absolute path — but the child still runs with the pinned PATH.
        if not os.path.isabs(command):
            resolved = shutil.which(command)
            if resolved is None:
                raise FileNotFoundError(
                    f"command not found in server PATH: {command!r}. "
                    f"Either pass an absolute path, or install it on "
                    f"the server PATH."
                )
            command = resolved

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

        # Wrap in systemd-run --user --scope when:
        #   * the user requested per-process resource limits, OR
        #   * the launch is into a group with a parent slice (so the
        #     scope nests under the slice for parent-cgroup
        #     enforcement / defense-in-depth).
        wants_systemd = bool(memory_limit or cpu_limit or pids_limit or group_slice)
        if wants_systemd:
            if not shutil.which("systemd-run"):
                raise RuntimeError(
                    "systemd-run is not available; resource limits "
                    "(and group cgroups) require systemd cgroups"
                )
            systemd_args = ["--user", "--scope", "--quiet", "--collect"]
            if group_slice:
                # Nesting under a slice gives the parent slice properties
                # (e.g. MemoryMax) authority over the sum of all child
                # scopes. The slice itself is created lazily by
                # systemd-run on first use, then ``set_group_slice_limits``
                # below pins the parent budgets.
                systemd_args += [f"--slice={group_slice}"]
            if memory_limit:
                systemd_args += ["--property", f"MemoryMax={memory_limit}"]
            if cpu_limit:
                systemd_args += ["--property", f"CPUQuota={cpu_limit}"]
            if pids_limit:
                systemd_args += ["--property", f"TasksMax={int(pids_limit)}"]
            spawn_args = systemd_args + ["--", spawn_cmd, *spawn_args]
            spawn_cmd = "systemd-run"

        # Add the resolved command's directory to the child PATH. This
        # lets a shebang interpreter alongside the binary (node for an
        # nvm CLI, python for a venv, uv-installed tools, ...) be found
        # by /usr/bin/env <interp>; without it, those CLIs hit a
        # misleading exit 127 because the pinned PATH doesn't see
        # ~/.nvm/.../bin or .venv/bin. ``command`` is absolute by this
        # point — either the user passed an absolute path, or shutil
        # .which resolved a bare name above.
        cmd_dir = os.path.dirname(command) if os.path.isabs(command) else None
        proc_env = _build_child_env(
            extra=env,
            extra_path_dirs=[cmd_dir] if cmd_dir else None,
        )
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
        now = time.time()
        managed = ManagedProcess(
            id=process_id,
            command=command,
            args=args,
            cwd=resolved_cwd,
            user_email=user_email,
            started_at=now,
            last_activity=now,
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
            group_id=group_id,
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
            except asyncio.CancelledError:
                # Loop is shutting down before we finished policing the
                # SIGTERM grace period. Force-kill the whole process
                # group and close the asyncio transport so its __del__
                # doesn't fire on a closed loop later. See
                # _wait_and_finalize for the longer explanation.
                self._kill_and_close_transport(managed, asyncio_proc)
                raise

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
        now = time.time()
        chunk = OutputChunk(stream=stream, text=text, timestamp=now)
        managed.history.append(chunk)
        # Stdout/stderr/raw chunks count as activity; system messages
        # don't, so a process that hasn't emitted any real output gets
        # idle-killed even if the launch banner is still in the buffer.
        if stream != "system":
            managed.last_activity = now
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

    def _kill_and_close_transport(
        self,
        managed: ManagedProcess,
        asyncio_proc: asyncio.subprocess.Process,
    ) -> None:
        """Force-kill ``asyncio_proc`` and close its asyncio transport.

        Used from the ``CancelledError`` paths in ``_wait_and_finalize``
        and the inner ``_kill_if_still_alive`` task. Mirrors the
        process-group kill that ``cancel()`` uses for normal teardown
        — ``launch()`` always starts children with
        ``start_new_session=True``, so killing only the session leader
        would orphan any descendants (e.g. ``bash -c 'sleep 999'``).
        Closing the asyncio transport while the loop is still alive
        flips ``transport._closed`` so a later ``__del__`` short-
        circuits before touching the closed loop.
        """
        if asyncio_proc.pid is not None:
            try:
                os.killpg(os.getpgid(asyncio_proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                # Process group already gone or we lost the right to
                # signal it — fall through to the direct-kill path.
                try:
                    asyncio_proc.kill()
                except (ProcessLookupError, OSError):
                    pass
        else:
            try:
                asyncio_proc.kill()
            except (ProcessLookupError, OSError):
                pass

        transport = getattr(asyncio_proc, "_transport", None)
        if transport is not None:
            try:
                transport.close()
            except (RuntimeError, OSError) as exc:
                # RuntimeError surfaces when the loop has already
                # closed; OSError covers pipe-fd churn during teardown.
                # Either way the transport is on its way out and the
                # only goal here is the ``_closed=True`` side effect.
                logger.debug(
                    "transport.close() during teardown of %s raised: %s",
                    sanitize_for_logging(managed.id),
                    sanitize_for_logging(exc),
                )

    async def _wait_and_finalize(
        self,
        managed: ManagedProcess,
        asyncio_proc: asyncio.subprocess.Process,
    ) -> None:
        try:
            exit_code = await asyncio_proc.wait()
        except asyncio.CancelledError:
            # The event loop is being torn down (typically a test ending
            # while the child is still running). If we let the task die
            # here, the asyncio subprocess transport is left "open" — its
            # __del__ then fires at some later GC point and, depending on
            # Python version, either emits a ResourceWarning or tries to
            # use the now-closed loop and raises RuntimeError("Event loop
            # is closed"), which pytest captures as
            # PytestUnraisableExceptionWarning. Closing the transport
            # synchronously while the loop is still alive flips
            # ``_closed`` to True so __del__ becomes a no-op. (Python
            # 3.13 partially fixes this via CPython gh-114177, but 3.12
            # still trips on it; both benefit from explicit close.)
            self._kill_and_close_transport(managed, asyncio_proc)
            self._asyncio_procs.pop(managed.id, None)
            raise
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


def make_group_slice_name(group_id: str) -> str:
    """Build a stable systemd slice name for a portal group.

    systemd slice names are constrained to a small character set; the
    UUID-shaped group_ids satisfy it after dash→underscore swap.
    """
    safe = group_id.replace("-", "_")
    return f"atlasportal_{safe}.slice"


def set_group_slice_limits(
    slice_name: str,
    *,
    mem_budget_bytes: Optional[int] = None,
    cpu_budget_pct: Optional[int] = None,
) -> bool:
    """Apply parent-slice resource limits via ``systemctl --user
    set-property``. Returns True on apparent success, False otherwise.

    Idempotent: re-applying the same limits is a no-op from systemd's
    perspective. Failures (no systemd, slice not yet realized) are
    logged at INFO and surface as False so the caller can decide
    whether the failure is fatal — for a parent cgroup it is best-
    effort defense-in-depth, not a hard requirement.
    """
    if not (mem_budget_bytes or cpu_budget_pct):
        return True
    if not shutil.which("systemctl"):
        return False
    props: List[str] = []
    if mem_budget_bytes:
        props.append(f"MemoryMax={int(mem_budget_bytes)}")
    if cpu_budget_pct:
        props.append(f"CPUQuota={int(cpu_budget_pct)}%")
    try:
        rc = subprocess.run(
            ["systemctl", "--user", "set-property", slice_name, *props],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        if rc.returncode != 0:
            logger.info(
                "set-property %s on slice %s failed (rc=%s): %s",
                ",".join(props),
                sanitize_for_logging(slice_name),
                rc.returncode,
                sanitize_for_logging((rc.stderr or b"").decode(errors="replace")),
            )
            return False
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        logger.info(
            "set-property failed for slice %s: %s",
            sanitize_for_logging(slice_name),
            sanitize_for_logging(exc),
        )
        return False


_singleton: Optional[ProcessManager] = None
_idle_sweeper_task: Optional["asyncio.Task[None]"] = None


def get_process_manager() -> ProcessManager:
    global _singleton
    if _singleton is None:
        _singleton = ProcessManager()
    return _singleton


async def _idle_sweep_loop(
    *, interval: float = 30.0,
) -> None:
    """Periodically reap idle members of every group with a positive
    idle_kill_seconds. Runs forever until cancelled.

    Imports PortalStore lazily so this module stays decoupled from the
    agent_portal package — handy for keeping the test surface small.
    """
    from atlas.modules.agent_portal.audit_log import record_event
    from atlas.modules.agent_portal.portal_store import get_portal_store
    pm = get_process_manager()
    while True:
        try:
            store = get_portal_store()
            # No "list all groups across all owners" surface today —
            # walk by-owner via the live processes' user_email set so
            # we never spy on owners with no live activity.
            owners = {p.user_email for p in pm._processes.values() if p.user_email}
            for owner in owners:
                for group in store.list_groups(owner):
                    idle_kill = group.get("idle_kill_seconds")
                    if not idle_kill:
                        continue
                    cancelled = await pm.reap_idle_in_group(group["id"], idle_kill)
                    if cancelled:
                        record_event(
                            owner,
                            "idle_kill",
                            group_id=group["id"],
                            detail={
                                "cancelled": cancelled,
                                "idle_kill_seconds": idle_kill,
                            },
                        )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("idle-sweep iteration failed: %s", exc)
        await asyncio.sleep(interval)


def ensure_idle_sweeper_running(interval: float = 30.0) -> None:
    """Start the idle-kill sweeper task if it's not already running.

    Safe to call repeatedly (no-ops on the second call). Called from the
    /processes launch handler so the sweeper only spins up when there's
    actually portal activity — keeps unit tests that never hit the
    route from leaking a perpetual task.
    """
    global _idle_sweeper_task
    if _idle_sweeper_task is not None and not _idle_sweeper_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _idle_sweeper_task = loop.create_task(_idle_sweep_loop(interval=interval))


def stop_idle_sweeper_for_tests() -> None:
    """Test-only — cancels the sweeper so a follow-up test starts clean."""
    global _idle_sweeper_task
    if _idle_sweeper_task is not None:
        _idle_sweeper_task.cancel()
        _idle_sweeper_task = None
