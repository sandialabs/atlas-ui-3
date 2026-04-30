"""Build and run the sandboxed subprocess.

The actual security work happens inside ``_sandbox_launch_v2.py`` (the
child). This module just builds the argv to invoke that wrapper, applies
a wall-clock timeout via ``subprocess.run``, and returns a structured
result.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


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
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=limits.wall_s,
            env=env,
            cwd=workdir,
        )
        elapsed = time.monotonic() - start
        return SandboxResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            timed_out=False,
            wall_seconds=round(elapsed, 4),
        )
    except subprocess.TimeoutExpired as e:
        elapsed = time.monotonic() - start
        return SandboxResult(
            returncode=-1,
            stdout=(e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
            stderr=(e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or ""),
            timed_out=True,
            wall_seconds=round(elapsed, 4),
        )


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
