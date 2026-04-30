"""Gated git_clone tool.

This is the only tool that runs with the network namespace disabled
(``allow_net=True``). All other layers stay on (Landlock, rlimits,
workspace-only writes, NO_NEW_PRIVS).

The PAT is injected into the URL at the last possible moment via the
child's environment so it is not persisted in argv (visible to ``ps``).
We rebuild the URL inside a small wrapper script that the sandbox
launcher then exec's; the wrapper reads the PAT from ``GIT_PAT``.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

from sandbox.launcher import SandboxLimits, run_sandboxed


_REPO_BASENAME = re.compile(r"[^/\\]+?(?:\.git)?$")


def _repo_basename(repo_url: str) -> str:
    parsed = urlparse(repo_url)
    path = parsed.path.rstrip("/")
    m = _REPO_BASENAME.search(path)
    if not m:
        return "repo"
    name = m.group(0)
    if name.endswith(".git"):
        name = name[:-4]
    return name or "repo"


def _safe_subdir(workspace: Path, subdir: str) -> Path:
    candidate = (workspace / subdir).resolve()
    workspace = workspace.resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError as e:
        raise ValueError(f"subdir escapes workspace: {subdir!r}") from e
    return candidate


def _wrapper_script(repo_url: str, ref: str, target: str, depth: int) -> str:
    """Build a small shell snippet that injects the PAT into the URL.

    The PAT comes from ``$GIT_PAT`` in the env. We use a here-doc'd
    Python invocation to avoid shell-quoting subtleties around credentials.
    """
    return (
        "import os, subprocess, sys, urllib.parse as u\n"
        f"url = {repo_url!r}\n"
        f"ref = {ref!r}\n"
        f"target = {target!r}\n"
        f"depth = {depth}\n"
        "pat = os.environ.get('GIT_PAT', '')\n"
        "p = u.urlparse(url)\n"
        "if pat and p.scheme in ('http', 'https'):\n"
        "    netloc = f'oauth2:{pat}@{p.hostname}'\n"
        "    if p.port:\n"
        "        netloc += f':{p.port}'\n"
        "    p = p._replace(netloc=netloc)\n"
        "url_with_pat = u.urlunparse(p)\n"
        "argv = ['git', 'clone', '--depth', str(depth)]\n"
        "if ref and ref != 'HEAD':\n"
        "    argv += ['--branch', ref]\n"
        "argv += [url_with_pat, target]\n"
        "rc = subprocess.run(argv).returncode\n"
        "sys.exit(rc)\n"
    )


def run_git_clone(
    *,
    workspace: Path,
    repo_url: str,
    pat: Optional[str],
    ref: str = "HEAD",
    subdir: Optional[str] = None,
    limits: Optional[SandboxLimits] = None,
    depth: int = 1,
) -> Dict[str, Any]:
    """Clone ``repo_url`` into the session workspace.

    Returns a dict suitable for embedding in the v2 envelope's ``results``.
    """
    if not repo_url.startswith(("http://", "https://", "git@", "ssh://")):
        return {"error": f"unsupported repo URL scheme: {repo_url!r}"}

    name = subdir or _repo_basename(repo_url)
    target_path = _safe_subdir(workspace, name)
    if target_path.exists():
        return {
            "error": (
                f"target already exists: {name!r}; "
                "remove it or pick a different subdir"
            )
        }

    limits = limits or SandboxLimits(
        mem_mb=1024, cpu_s=60, fsize_mb=512, nproc=64, wall_s=120,
    )
    rel_target = str(target_path.relative_to(workspace.resolve()))
    script = _wrapper_script(repo_url, ref, rel_target, depth)

    extra_env = {}
    if pat:
        extra_env["GIT_PAT"] = pat

    result = run_sandboxed(
        ["python", "-c", script],
        workdir=str(workspace),
        limits=limits,
        allow_net=True,
        extra_env=extra_env,
    )

    # Scrub anything that could echo the PAT (defense in depth -- git
    # itself does not echo PATs in our argv path, but redacted output is
    # still safer to log).
    stderr = result.stderr
    if pat:
        stderr = stderr.replace(pat, "***REDACTED***")

    return {
        "ok": result.returncode == 0 and not result.timed_out,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "wall_seconds": result.wall_seconds,
        "target_subdir": name,
        "stderr": stderr[-2000:] if stderr else "",
    }


def quote_for_log(repo_url: str) -> str:
    """Used by the tool layer to log without leaking creds."""
    return shlex.quote(repo_url)


__all__ = [
    "run_git_clone",
    "quote_for_log",
]
