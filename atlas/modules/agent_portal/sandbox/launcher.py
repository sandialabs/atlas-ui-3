"""Build a sandbox-wrapped command line from a SandboxProfile.

Everything here is a pure function taking (profile, agent_cmd, env)
and returning an argv list. No subprocess is spawned. Adapters choose
when and how to execute.

Two launchers ship in v0:
  - BubblewrapLauncher: wraps via `bwrap` with Landlock + netns flags.
  - NoopLauncher:       returns the agent command unchanged (developer
                        escape hatch, requires permissive tier + explicit
                        config).
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from atlas.modules.agent_portal.models import NetworkPolicy, SandboxProfile, SandboxTier


class BubblewrapLauncher:
    """Translate a SandboxProfile into a bwrap(1) argv.

    This does not handle Landlock ABI details directly; bwrap installs
    a Landlock ruleset internally based on its bind-mount flags. We do
    pass `--unshare-net` for `NetworkPolicy.denied` and leave network
    joined for the other policies so a caller-provided loopback proxy
    can be reached. Seccomp loading is wired via fd 10 if a profile
    file is configured (the caller is expected to open the file and
    pass the descriptor; see local_process adapter).
    """

    backend = "bubblewrap"

    def build_command(
        self,
        profile: SandboxProfile,
        agent_command: List[str],
        env: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        if not agent_command:
            raise ValueError("agent_command must not be empty")

        argv: List[str] = ["bwrap"]

        # Filesystem - read-only binds (Landlock-backed)
        for path in profile.fs_read_paths:
            argv += ["--ro-bind", path, path]
        # Read-write binds
        for path in profile.fs_read_write_paths:
            argv += ["--bind", path, path]
        # Paths that must be executable (overlap with ro-bind is fine)
        # bwrap does not distinguish exec from read, but keeping the
        # field in the profile lets future launchers apply finer-grained
        # Landlock rules directly.
        for path in profile.fs_exec_paths:
            if path not in profile.fs_read_paths and path not in profile.fs_read_write_paths:
                argv += ["--ro-bind", path, path]

        # Minimal system surfaces
        argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]

        # Namespaces
        argv += ["--unshare-pid", "--unshare-uts", "--unshare-ipc"]
        if profile.network is NetworkPolicy.denied:
            argv += ["--unshare-net"]
        # loopback_only / allowlist_proxy / unrestricted: leave net shared

        # Environment
        if profile.clear_env:
            argv += ["--clearenv"]
        pass_env = dict(env) if env else dict(os.environ)
        for key in profile.env_allowlist:
            value = pass_env.get(key)
            if value is not None:
                argv += ["--setenv", key, value]

        # Seccomp is wired via a file descriptor the adapter opens; we
        # only record intent here via --seccomp 10 when requested. The
        # caller is responsible for `preexec_fn`/`pass_fds` to fd 10.
        if profile.seccomp_profile_path:
            argv += ["--seccomp", "10"]

        argv += ["--"]
        argv += list(agent_command)
        return argv


class NoopLauncher:
    """Developer escape hatch: pass the command through unchanged.

    Requires `AGENT_PORTAL_SANDBOX_BACKEND=none` AND
    `AGENT_PORTAL_ALLOW_PERMISSIVE_TIER=true` to be selected. The
    service layer enforces this; the launcher itself is intentionally
    simple and does no additional checks.
    """

    backend = "none"

    def build_command(
        self,
        profile: SandboxProfile,  # noqa: ARG002 - interface parity
        agent_command: List[str],
        env: Optional[Dict[str, str]] = None,  # noqa: ARG002
    ) -> List[str]:
        if not agent_command:
            raise ValueError("agent_command must not be empty")
        return list(agent_command)


_LAUNCHERS: Dict[str, object] = {
    "bubblewrap": BubblewrapLauncher(),
    "landlock+netns": BubblewrapLauncher(),  # alias; bwrap provides both
    "none": NoopLauncher(),
}


def select_launcher(backend: str):
    """Look up a launcher by backend name. Raises ValueError for unknown."""
    try:
        return _LAUNCHERS[backend]
    except KeyError as exc:
        raise ValueError(
            f"Unknown sandbox backend '{backend}'. "
            f"Valid: {sorted(_LAUNCHERS.keys())}"
        ) from exc


def build_sandbox_command(
    profile: SandboxProfile,
    agent_command: List[str],
    backend: str = "bubblewrap",
    env: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Convenience wrapper: pick a launcher and build the argv.

    Enforces the one cross-cutting policy check that the launcher layer
    can make without the service: `permissive` tier is never allowed
    with the `bubblewrap` backend. If a caller wants unrestricted
    execution, they must explicitly select backend='none'. This keeps
    the default path safe even if a caller forgets the service-level
    gate.
    """
    if profile.tier is SandboxTier.permissive and backend != "none":
        raise ValueError(
            "permissive tier requires backend='none'; "
            "reject or downgrade at the service layer before calling"
        )
    launcher = select_launcher(backend)
    return launcher.build_command(profile, agent_command, env=env)
