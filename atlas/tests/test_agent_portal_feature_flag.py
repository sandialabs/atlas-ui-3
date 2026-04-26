"""Feature-flag gating: the portal must be invisible by default."""

import pytest

from atlas.modules.agent_portal.models import LaunchSpec, SandboxTier
from atlas.modules.agent_portal.service import (
    AgentPortalDisabledError,
    AgentPortalService,
)


def test_default_flag_is_false_in_settings(monkeypatch):
    """FEATURE_AGENT_PORTAL_ENABLED must default to false so a fresh
    install has no portal surface mounted."""
    monkeypatch.delenv("FEATURE_AGENT_PORTAL_ENABLED", raising=False)
    # Import lazily to avoid polluting module-level state.
    from atlas.modules.config.config_manager import AppSettings

    settings = AppSettings()
    assert settings.feature_agent_portal_enabled is False


def test_default_tier_is_standard(monkeypatch):
    monkeypatch.delenv("AGENT_PORTAL_DEFAULT_SANDBOX_TIER", raising=False)
    from atlas.modules.config.config_manager import AppSettings

    settings = AppSettings()
    assert settings.agent_portal_default_sandbox_tier == "standard"


def test_permissive_opt_in_is_off(monkeypatch):
    monkeypatch.delenv("AGENT_PORTAL_ALLOW_PERMISSIVE_TIER", raising=False)
    from atlas.modules.config.config_manager import AppSettings

    settings = AppSettings()
    assert settings.agent_portal_allow_permissive_tier is False


def test_service_hard_rejects_all_ops_when_disabled(tmp_path):
    svc = AgentPortalService(enabled=False, audit_dir=tmp_path)
    spec = LaunchSpec(scope="s", agent_command=["/bin/true"], sandbox_tier=SandboxTier.standard)
    with pytest.raises(AgentPortalDisabledError):
        svc.validate_spec(spec)
    with pytest.raises(AgentPortalDisabledError):
        svc.prepare_profile(spec)
    with pytest.raises(AgentPortalDisabledError):
        svc.get_session("nope")
