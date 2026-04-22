"""Unit tests for default sandbox profiles."""

from atlas.modules.agent_portal.models import NetworkPolicy, SandboxTier
from atlas.modules.agent_portal.sandbox.profiles import DEFAULT_PROFILES, get_default_profile


def test_all_tiers_have_defaults():
    for tier in SandboxTier:
        assert tier in DEFAULT_PROFILES
        profile = DEFAULT_PROFILES[tier]
        assert profile.tier is tier


def test_restrictive_denies_network():
    profile = get_default_profile(SandboxTier.restrictive)
    assert profile.network is NetworkPolicy.denied
    assert profile.egress_allowlist == []


def test_standard_uses_allowlist_proxy():
    profile = get_default_profile(SandboxTier.standard)
    assert profile.network is NetworkPolicy.allowlist_proxy
    assert profile.egress_allowlist, "standard tier must ship with a non-empty allowlist"


def test_permissive_is_unrestricted():
    profile = get_default_profile(SandboxTier.permissive)
    assert profile.network is NetworkPolicy.unrestricted
    assert profile.clear_env is False


def test_get_default_profile_returns_copy():
    """Callers mutating the returned profile must not affect the module defaults."""
    first = get_default_profile(SandboxTier.standard)
    first.egress_allowlist.append("evil.example.com")
    second = get_default_profile(SandboxTier.standard)
    assert "evil.example.com" not in second.egress_allowlist
