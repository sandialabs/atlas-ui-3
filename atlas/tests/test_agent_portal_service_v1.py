"""Tests for AgentPortalService policy-aware validation (v1 refactor)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from atlas.modules.agent_portal.models import (
    LaunchSpec,
    SandboxTier,
    WorkspaceSpec,
)
from atlas.modules.agent_portal.policy import Policy, Preset, WorkspaceRoot
from atlas.modules.agent_portal.service import (
    AgentPortalService,
    PermissiveTierForbiddenError,
    PresetNotAllowedError,
    WorkspaceRootNotAllowedError,
)


def _policy():
    return Policy(
        mode="dev",
        presets=[
            Preset(
                id="public",
                label="p",
                command=["echo", "hi"],
                default_tier=SandboxTier.restrictive,
                allowed_tiers=[SandboxTier.restrictive, SandboxTier.standard],
                visible_to_groups=["*"],
            ),
            Preset(
                id="admin-only",
                label="a",
                command=["echo", "admin"],
                default_tier=SandboxTier.standard,
                allowed_tiers=[SandboxTier.standard, SandboxTier.permissive],
                visible_to_groups=["admin"],
                requires_root=False,  # for these tests we skip workspace
            ),
        ],
        workspace_roots=[
            WorkspaceRoot(group="*", paths=["/tmp/**"]),
        ],
    )


def _svc(tmp_path, mode="dev", allow_permissive=False, backend="none"):
    return AgentPortalService(
        enabled=True,
        default_tier=SandboxTier.restrictive,
        allow_permissive_tier=allow_permissive,
        sandbox_backend=backend,
        audit_dir=tmp_path,
        policy=_policy(),
        mode=mode,
    )


def test_resolve_spec_populates_command_from_preset(tmp_path):
    svc = _svc(tmp_path)
    spec = LaunchSpec(
        preset_id="public",
        scope="hello",
        sandbox_tier=SandboxTier.restrictive,
        workspace=WorkspaceSpec(root="/tmp"),
    )
    resolved, preset = svc.resolve_spec(spec, user_groups=[])
    assert resolved.agent_command == ["echo", "hi"]
    assert preset.id == "public"


def test_preset_not_visible_rejected(tmp_path):
    svc = _svc(tmp_path)
    spec = LaunchSpec(
        preset_id="admin-only",
        scope="x",
        sandbox_tier=SandboxTier.standard,
    )
    with pytest.raises(PresetNotAllowedError):
        svc.resolve_spec(spec, user_groups=["nobody"])


def test_tier_not_in_preset_allowed_rejected(tmp_path):
    svc = _svc(tmp_path, allow_permissive=True)
    spec = LaunchSpec(
        preset_id="public",
        scope="x",
        sandbox_tier=SandboxTier.permissive,  # not in [restrictive, standard]
        workspace=WorkspaceSpec(root="/tmp"),
    )
    with pytest.raises(PresetNotAllowedError):
        svc.resolve_spec(spec, user_groups=[])


def test_prod_mode_rejects_permissive(tmp_path):
    svc = _svc(tmp_path, mode="prod", allow_permissive=True)
    spec = LaunchSpec(
        preset_id="admin-only",
        scope="x",
        sandbox_tier=SandboxTier.permissive,
    )
    with pytest.raises(PermissiveTierForbiddenError, match="prod"):
        svc.resolve_spec(spec, user_groups=["admin"])


def test_prod_mode_rejects_none_backend(tmp_path):
    svc = _svc(tmp_path, mode="prod", backend="none")
    spec = LaunchSpec(
        preset_id="public",
        scope="x",
        sandbox_tier=SandboxTier.restrictive,
        workspace=WorkspaceSpec(root="/tmp"),
    )
    with pytest.raises(PermissiveTierForbiddenError, match="none"):
        svc.resolve_spec(spec, user_groups=[])


def test_dev_mode_permissive_requires_flag(tmp_path):
    svc = _svc(tmp_path, mode="dev", allow_permissive=False)
    spec = LaunchSpec(
        preset_id="admin-only",
        scope="x",
        sandbox_tier=SandboxTier.permissive,
    )
    with pytest.raises(PermissiveTierForbiddenError, match="AGENT_PORTAL_ALLOW_PERMISSIVE_TIER"):
        svc.resolve_spec(spec, user_groups=["admin"])


def test_workspace_root_not_in_allowed_rejected(tmp_path):
    svc = _svc(tmp_path)
    spec = LaunchSpec(
        preset_id="public",
        scope="x",
        sandbox_tier=SandboxTier.restrictive,
        workspace=WorkspaceSpec(root="/etc"),
    )
    with pytest.raises(WorkspaceRootNotAllowedError, match="/etc"):
        svc.resolve_spec(spec, user_groups=[])


def test_workspace_root_missing_directory_rejected(tmp_path):
    svc = _svc(tmp_path)
    fake = "/tmp/agent_portal_nonexistent_dir_xyz123"
    assert not os.path.exists(fake)
    spec = LaunchSpec(
        preset_id="public",
        scope="x",
        sandbox_tier=SandboxTier.restrictive,
        workspace=WorkspaceSpec(root=fake),
    )
    with pytest.raises(WorkspaceRootNotAllowedError, match="does not exist"):
        svc.resolve_spec(spec, user_groups=[])


def test_preset_without_requires_root_accepts_no_workspace(tmp_path):
    svc = _svc(tmp_path)
    spec = LaunchSpec(
        preset_id="admin-only",  # requires_root=False
        scope="x",
        sandbox_tier=SandboxTier.standard,
    )
    resolved, preset = svc.resolve_spec(spec, user_groups=["admin"])
    assert preset.requires_root is False
    assert resolved.workspace is None


def test_target_must_be_null_in_v1():
    with pytest.raises(ValueError, match="target must be null"):
        LaunchSpec(
            preset_id="public",
            scope="x",
            sandbox_tier=SandboxTier.restrictive,
            target="remote-host",
        )


def test_legacy_agent_command_still_accepted():
    """Tests and headless callers can still supply agent_command directly."""
    spec = LaunchSpec(
        scope="x",
        agent_command=["/bin/true"],
        sandbox_tier=SandboxTier.standard,
    )
    assert spec.agent_command == ["/bin/true"]
    assert spec.preset_id is None


def test_missing_both_preset_and_command_rejected():
    with pytest.raises(ValueError, match="either preset_id or agent_command"):
        LaunchSpec(scope="x", sandbox_tier=SandboxTier.standard)
