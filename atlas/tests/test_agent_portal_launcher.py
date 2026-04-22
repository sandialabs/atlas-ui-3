"""Unit tests for the sandbox launcher (pure-function argv construction)."""

import pytest

from atlas.modules.agent_portal.models import NetworkPolicy, SandboxProfile, SandboxTier
from atlas.modules.agent_portal.sandbox.launcher import (
    BubblewrapLauncher,
    NoopLauncher,
    build_sandbox_command,
    select_launcher,
)
from atlas.modules.agent_portal.sandbox.profiles import get_default_profile


def test_empty_agent_command_rejected():
    launcher = BubblewrapLauncher()
    profile = get_default_profile(SandboxTier.restrictive)
    with pytest.raises(ValueError):
        launcher.build_command(profile, [])


def test_restrictive_includes_unshare_net():
    profile = get_default_profile(SandboxTier.restrictive)
    argv = build_sandbox_command(profile, ["/bin/echo", "hi"], backend="bubblewrap")
    assert argv[0] == "bwrap"
    assert "--unshare-net" in argv
    assert argv[-2:] == ["/bin/echo", "hi"]


def test_standard_does_not_unshare_net():
    """Standard tier relies on a filtering egress proxy; the network stack
    must be reachable so loopback-bound proxies work."""
    profile = get_default_profile(SandboxTier.standard)
    argv = build_sandbox_command(profile, ["/usr/bin/python3", "-c", "pass"], backend="bubblewrap")
    assert "--unshare-net" not in argv


def test_read_only_binds_present_for_system_paths():
    profile = get_default_profile(SandboxTier.restrictive)
    argv = build_sandbox_command(profile, ["/bin/true"], backend="bubblewrap")
    # /usr must appear as an --ro-bind pair
    idx = argv.index("/usr")
    assert argv[idx - 1] == "--ro-bind"
    assert argv[idx + 1] == "/usr"


def test_clearenv_and_env_allowlist():
    profile = get_default_profile(SandboxTier.standard)
    profile.env_allowlist = ["ATLAS_TEST_ENV"]
    argv = build_sandbox_command(
        profile,
        ["/bin/true"],
        backend="bubblewrap",
        env={"ATLAS_TEST_ENV": "value-1", "SECRET": "never-passed"},
    )
    assert "--clearenv" in argv
    # setenv for the allowed key
    i = argv.index("--setenv")
    assert argv[i + 1] == "ATLAS_TEST_ENV"
    assert argv[i + 2] == "value-1"
    # the disallowed key must not appear anywhere
    assert "SECRET" not in argv
    assert "never-passed" not in argv


def test_permissive_rejected_by_bubblewrap_backend():
    profile = get_default_profile(SandboxTier.permissive)
    with pytest.raises(ValueError):
        build_sandbox_command(profile, ["/bin/true"], backend="bubblewrap")


def test_permissive_passthrough_via_none_backend():
    profile = get_default_profile(SandboxTier.permissive)
    argv = build_sandbox_command(profile, ["/usr/bin/env"], backend="none")
    assert argv == ["/usr/bin/env"]


def test_select_launcher_unknown_backend_raises():
    with pytest.raises(ValueError):
        select_launcher("no-such-backend")


def test_noop_launcher_returns_command_unchanged():
    launcher = NoopLauncher()
    profile = SandboxProfile(tier=SandboxTier.permissive, network=NetworkPolicy.unrestricted)
    assert launcher.build_command(profile, ["/bin/ls", "-la"]) == ["/bin/ls", "-la"]
