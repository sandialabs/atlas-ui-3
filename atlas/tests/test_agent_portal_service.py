"""Unit tests for the AgentPortalService facade (feature-flag + policy)."""

import pytest

from atlas.modules.agent_portal.models import LaunchSpec, SandboxTier, SessionState
from atlas.modules.agent_portal.service import (
    AgentPortalDisabledError,
    AgentPortalService,
    PermissiveTierForbiddenError,
)


def _spec(tier: SandboxTier = SandboxTier.standard) -> LaunchSpec:
    return LaunchSpec(scope="test", agent_command=["/bin/true"], sandbox_tier=tier)


def test_disabled_service_raises_on_any_operation(tmp_path):
    svc = AgentPortalService(enabled=False, audit_dir=tmp_path)
    with pytest.raises(AgentPortalDisabledError):
        svc.create_session("u@x", _spec())
    with pytest.raises(AgentPortalDisabledError):
        svc.list_sessions("u@x")


def test_enabled_service_creates_session_with_audit(tmp_path):
    svc = AgentPortalService(enabled=True, audit_dir=tmp_path)
    session, profile, audit = svc.create_session("u@x", _spec())
    assert session.state is SessionState.pending
    assert profile.tier is SandboxTier.standard
    assert audit.path.exists()
    # The service emits a `policy` frame on creation.
    contents = audit.path.read_text()
    assert "session_created" in contents
    assert "sandbox_tier" in contents


def test_permissive_rejected_without_opt_in(tmp_path):
    svc = AgentPortalService(
        enabled=True,
        audit_dir=tmp_path,
        allow_permissive_tier=False,
    )
    with pytest.raises(PermissiveTierForbiddenError):
        svc.create_session("u@x", _spec(SandboxTier.permissive))


def test_permissive_allowed_with_opt_in(tmp_path):
    svc = AgentPortalService(
        enabled=True,
        audit_dir=tmp_path,
        allow_permissive_tier=True,
        sandbox_backend="none",
    )
    session, profile, _ = svc.create_session("u@x", _spec(SandboxTier.permissive))
    assert session.state is SessionState.pending
    assert profile.tier is SandboxTier.permissive


def test_effective_config_shape(tmp_path):
    svc = AgentPortalService(
        enabled=True,
        audit_dir=tmp_path,
        default_tier=SandboxTier.restrictive,
        allow_permissive_tier=False,
        sandbox_backend="bubblewrap",
    )
    cfg = svc.effective_config()
    assert cfg == {
        "enabled": True,
        "default_tier": "restrictive",
        "allow_permissive_tier": False,
        "sandbox_backend": "bubblewrap",
        "audit_dir": str(tmp_path),
    }
