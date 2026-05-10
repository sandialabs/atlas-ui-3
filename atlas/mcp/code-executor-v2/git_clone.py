"""Gated git_clone tool.

This is the only tool that runs with the network namespace disabled
(``allow_net=True``). All other layers stay on (Landlock, rlimits,
workspace-only writes, NO_NEW_PRIVS).

The PAT is injected into the URL at the last possible moment inside
the sandboxed child via its environment, so the parent's argv (visible
to ``ps`` on the host) never contains the credential. After ``git clone``
finishes, the wrapper rewrites the cloned repo's ``origin`` URL to strip
the credential — otherwise the PAT would persist in ``<repo>/.git/config``
and surface again on any subsequent ``git`` call against that repo.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

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
    """Build a small Python snippet that runs the actual clone.

    The PAT is read from ``$GIT_PAT`` (set by ``run_sandboxed``'s
    ``extra_env``) inside the sandboxed child, then injected into the URL
    at clone time. After the clone succeeds, the wrapper:

      1. Rewrites ``origin`` to the credential-free URL so the PAT does
         not persist in ``.git/config``.
      2. Disables credential helpers in the cloned repo so any later
         ``git fetch`` against ``origin`` cannot transparently re-auth.

    Stdout/stderr are scrubbed of the PAT in the parent for defense in
    depth (some git transports echo URLs to stderr).
    """
    return (
        "import os, subprocess, sys, urllib.parse as u\n"
        f"url = {repo_url!r}\n"
        f"ref = {ref!r}\n"
        f"target = {target!r}\n"
        f"depth = {depth}\n"
        "pat = os.environ.get('GIT_PAT', '')\n"
        "p = u.urlparse(url)\n"
        "clean_url = url\n"
        "url_with_pat = url\n"
        "if pat and p.scheme in ('http', 'https'):\n"
        "    netloc = f'oauth2:{pat}@{p.hostname}'\n"
        "    if p.port:\n"
        "        netloc += f':{p.port}'\n"
        "    url_with_pat = u.urlunparse(p._replace(netloc=netloc))\n"
        # Make sure cred helpers do not write the PAT anywhere on disk
        # and HOME-based config files do not leak in.
        "env = dict(os.environ)\n"
        "env['GIT_TERMINAL_PROMPT'] = '0'\n"
        "env['GIT_CONFIG_GLOBAL'] = '/dev/null'\n"
        "env['GIT_CONFIG_SYSTEM'] = '/dev/null'\n"
        "argv = ['git',\n"
        "        '-c', 'credential.helper=',\n"
        "        '-c', 'core.askpass=true',\n"
        "        'clone', '--depth', str(depth)]\n"
        "if ref and ref != 'HEAD':\n"
        "    argv += ['--branch', ref]\n"
        "argv += [url_with_pat, target]\n"
        "rc = subprocess.run(argv, env=env).returncode\n"
        # Strip the PAT out of the cloned repo's remote so it does not
        # survive in .git/config. Best-effort: if the clone failed, target
        # may not exist.\n"
        "if rc == 0:\n"
        "    try:\n"
        "        subprocess.run(\n"
        "            ['git', '-C', target, 'remote', 'set-url',\n"
        "             'origin', clean_url],\n"
        "            env=env, check=False,\n"
        "        )\n"
        "        subprocess.run(\n"
        "            ['git', '-C', target, 'config',\n"
        "             '--unset-all', 'credential.helper'],\n"
        "            env=env, check=False,\n"
        "        )\n"
        "    except Exception:\n"
        "        pass\n"
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
        mem_mb=1024, cpu_s=60, fsize_mb=128, nproc=64, wall_s=60,
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

    # Scrub anywhere the PAT could possibly echo (defense in depth — git
    # should not echo it via our path, but stderr from network errors and
    # progress output can be unpredictable).
    stderr = result.stderr or ""
    stdout = result.stdout or ""
    if pat:
        stderr = stderr.replace(pat, "***REDACTED***")
        stdout = stdout.replace(pat, "***REDACTED***")

    return {
        "ok": result.returncode == 0 and not result.timed_out,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "wall_seconds": result.wall_seconds,
        "target_subdir": name,
        "stdout": stdout[-2000:],
        "stderr": stderr[-2000:],
    }


def quote_for_log(repo_url: str) -> str:
    """Used by the tool layer to log without leaking creds."""
    return shlex.quote(repo_url)


__all__ = [
    "run_git_clone",
    "quote_for_log",
]
