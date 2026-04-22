"""Default sandbox profiles for each tier.

Profiles are adapter-agnostic. A local_process adapter translates them
into bwrap flags; a kubernetes adapter translates them into
NetworkPolicy + SecurityContext. New tiers or overrides should go
through admin configuration rather than hard-coding here.
"""

from __future__ import annotations

from typing import Dict

from atlas.modules.agent_portal.models import NetworkPolicy, SandboxProfile, SandboxTier


def _restrictive() -> SandboxProfile:
    """Untrusted prompt, read-only analysis. No network, no writable host paths."""
    return SandboxProfile(
        tier=SandboxTier.restrictive,
        fs_read_paths=["/usr", "/etc", "/lib", "/lib64", "/bin"],
        fs_read_write_paths=[],           # workspace bound separately by caller
        fs_exec_paths=["/usr/bin", "/bin"],
        network=NetworkPolicy.denied,
        egress_allowlist=[],
        seccomp_profile_path=None,        # caller may add a bpf file
        env_allowlist=["PATH", "LANG", "LC_ALL"],
        clear_env=True,
    )


def _standard() -> SandboxProfile:
    """Normal dev work: egress via an allowlist proxy, writable workspace."""
    return SandboxProfile(
        tier=SandboxTier.standard,
        fs_read_paths=["/usr", "/etc", "/lib", "/lib64", "/bin"],
        fs_read_write_paths=[],
        fs_exec_paths=["/usr/bin", "/bin"],
        network=NetworkPolicy.allowlist_proxy,
        # Placeholder allow-list; admin config overrides this in follow-up work.
        egress_allowlist=[
            "pypi.org",
            "files.pythonhosted.org",
            "api.anthropic.com",
            "api.openai.com",
            "generativelanguage.googleapis.com",
        ],
        seccomp_profile_path=None,
        env_allowlist=[
            "PATH", "LANG", "LC_ALL",
            "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
        ],
        clear_env=True,
    )


def _permissive() -> SandboxProfile:
    """Developer escape hatch. Gated additionally by
    AGENT_PORTAL_ALLOW_PERMISSIVE_TIER=true at the service layer.
    """
    return SandboxProfile(
        tier=SandboxTier.permissive,
        fs_read_paths=[],                 # no read-only restriction
        fs_read_write_paths=[],           # caller may bind-mount host $HOME
        fs_exec_paths=[],
        network=NetworkPolicy.unrestricted,
        egress_allowlist=[],
        seccomp_profile_path=None,
        env_allowlist=[],
        clear_env=False,                  # inherit parent env verbatim
    )


DEFAULT_PROFILES: Dict[SandboxTier, SandboxProfile] = {
    SandboxTier.restrictive: _restrictive(),
    SandboxTier.standard: _standard(),
    SandboxTier.permissive: _permissive(),
}


def get_default_profile(tier: SandboxTier) -> SandboxProfile:
    """Return a fresh copy of the default profile for a tier.

    Returning a copy (model_copy) ensures callers can mutate without
    affecting the module-level defaults.
    """
    return DEFAULT_PROFILES[tier].model_copy(deep=True)
