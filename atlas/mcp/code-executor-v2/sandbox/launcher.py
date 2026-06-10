"""Build and run the sandboxed subprocess.

The actual security work happens inside ``_sandbox_launch_v2.py`` (the
child). This module just builds the argv to invoke that wrapper, applies
a wall-clock timeout, bounds the captured stdout/stderr, and returns a
structured result.
"""

from __future__ import annotations

import os
import selectors
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


# Hard cap on captured bytes per stream. Without this, a runaway
# `print('x' * 10**9)` in user code would balloon the parent's memory
# (RLIMIT_AS bounds the *child*; the parent buffers what comes out of
# the pipe). 8 MiB is enough for typical stdout while killing pathological
# floods. Parent truncates to ``_TRUNCATED_NOTE`` when reached.
_CAPTURE_CAP_BYTES = 8 * 1024 * 1024
_TRUNCATED_NOTE = b"\n[capture truncated]\n"


_LAUNCHER_PATH = str(
    (Path(__file__).resolve().parent / "_sandbox_launch_v2.py")
)


@dataclass(frozen=True)
class SandboxLimits:
    mem_mb: int = 2048
    cpu_s: int = 30
    fsize_mb: int = 256
    nproc: int = 64
    wall_s: int = 60


@dataclass
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    wall_seconds: float


def run_sandboxed(
    cmd: Iterable[str],
    workdir: str,
    limits: SandboxLimits,
    *,
    allow_net: bool = False,
    extra_env: Optional[dict] = None,
) -> SandboxResult:
    """Run ``cmd`` under the v2 sandbox and return the result.

    ``allow_net`` is reserved for the gated ``git_clone`` tool. All
    other callers should leave it ``False``.
    """
    cmd = list(cmd)
    if not cmd:
        raise ValueError("cmd must be non-empty")
    workdir = str(workdir)
    if not os.path.isdir(workdir):
        raise FileNotFoundError(workdir)

    argv: List[str] = [
        sys.executable,
        _LAUNCHER_PATH,
        "--workdir", workdir,
        "--mem-mb", str(limits.mem_mb),
        "--cpu-s", str(limits.cpu_s),
        "--fsize-mb", str(limits.fsize_mb),
        "--nproc", str(limits.nproc),
    ]
    if allow_net:
        argv.append("--allow-net")
    argv.append("--")
    argv.extend(cmd)

    env = _scrub_env(extra_env or {})

    start = time.monotonic()
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=workdir,
    )
    try:
        stdout_bytes, stderr_bytes, timed_out = _read_with_caps(
            proc, deadline=start + max(limits.wall_s, 1),
        )
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                # Race: child exited between poll() and kill(). Nothing
                # to do — the process is already gone.
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                # Already SIGKILL'd; if reaping somehow stalls beyond 2s
                # we accept the leak rather than block the request loop.
                pass
    elapsed = time.monotonic() - start
    rc = proc.returncode if proc.returncode is not None else -1
    return SandboxResult(
        returncode=-1 if timed_out else rc,
        stdout=stdout_bytes.decode("utf-8", "replace"),
        stderr=stderr_bytes.decode("utf-8", "replace"),
        timed_out=timed_out,
        wall_seconds=round(elapsed, 4),
    )


def _read_with_caps(
    proc: "subprocess.Popen[bytes]",
    *,
    deadline: float,
) -> Tuple[bytes, bytes, bool]:
    """Read bounded bytes from ``proc.stdout``/``stderr`` until exit/deadline.

    Each stream stops accumulating once it reaches ``_CAPTURE_CAP_BYTES``;
    further bytes from the child are still drained from the pipe (so the
    child does not block on a full pipe) but discarded. If the deadline
    passes, the child is killed and ``timed_out=True`` is returned.
    """
    sel = selectors.DefaultSelector()
    if proc.stdout is not None:
        sel.register(proc.stdout, selectors.EVENT_READ, "stdout")
    if proc.stderr is not None:
        sel.register(proc.stderr, selectors.EVENT_READ, "stderr")

    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    open_streams = len(sel.get_map())

    timed_out = False
    while open_streams > 0:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        for key, _ in sel.select(timeout=min(remaining, 0.5)):
            chunk = key.fileobj.read1(64 * 1024)  # type: ignore[union-attr]
            name = key.data
            if not chunk:
                sel.unregister(key.fileobj)
                open_streams -= 1
                continue
            buf = buffers[name]
            if len(buf) < _CAPTURE_CAP_BYTES:
                room = _CAPTURE_CAP_BYTES - len(buf)
                buf.extend(chunk[:room])
                if len(chunk) > room and not truncated[name]:
                    buf.extend(_TRUNCATED_NOTE)
                    truncated[name] = True
            # Else: drop on the floor but keep draining the pipe so the
            # child does not block on a full pipe.

    return bytes(buffers["stdout"]), bytes(buffers["stderr"]), timed_out


_KEEP_ENV = {
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "HOME",
    "TMPDIR",
    "PYTHONHASHSEED",
    "MPLBACKEND",
}


def _scrub_env(extra: dict) -> dict:
    """Pass only a small allow-list plus caller-supplied extras.

    We do not propagate the parent's full environment because secrets
    (DB URLs, API keys) routinely live there.
    """
    base = {k: v for k, v in os.environ.items() if k in _KEEP_ENV}
    base.setdefault("MPLBACKEND", "Agg")
    base.setdefault("HOME", "/tmp")
    for k, v in extra.items():
        base[k] = str(v)
    return base
