"""Tests for atlas.modules.agent_portal.policy."""

from __future__ import annotations

import pytest

from atlas.modules.agent_portal.models import SandboxTier
from atlas.modules.agent_portal.policy import (
    Policy,
    PolicyLoadError,
    Preset,
    TierInfo,
    WorkspaceRoot,
    load_policy,
)


def _write(tmp_path, text):
    p = tmp_path / "policy.yaml"
    p.write_text(text, encoding="utf-8")
    return p


MINIMAL_YAML = """
mode: dev
tiers:
  restrictive:
    summary: "no net"
presets:
  - id: demo
    label: Demo
    executor: local
    command: ["echo", "hi"]
    default_tier: restrictive
    allowed_tiers: [restrictive]
    visible_to_groups: ["*"]
workspace_roots:
  - group: "*"
    paths: ["/tmp/**"]
"""


def test_loads_minimal_yaml(tmp_path):
    path = _write(tmp_path, MINIMAL_YAML)
    p = load_policy(path)
    assert p.mode == "dev"
    assert p.presets[0].id == "demo"
    assert SandboxTier.restrictive in p.presets[0].allowed_tiers


def test_missing_file_raises(tmp_path):
    with pytest.raises(PolicyLoadError, match="not found"):
        load_policy(tmp_path / "missing.yaml")


def test_bad_yaml_raises(tmp_path):
    path = _write(tmp_path, "not: [valid: yaml")
    with pytest.raises(PolicyLoadError, match="parse error"):
        load_policy(path)


def test_empty_file_raises(tmp_path):
    path = _write(tmp_path, "")
    with pytest.raises(PolicyLoadError, match="empty"):
        load_policy(path)


def test_non_mapping_top_level_raises(tmp_path):
    path = _write(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(PolicyLoadError, match="mapping"):
        load_policy(path)


def test_default_tier_must_be_in_allowed(tmp_path):
    bad = """
mode: dev
presets:
  - id: x
    label: x
    command: ["echo"]
    default_tier: permissive
    allowed_tiers: [restrictive]
"""
    path = _write(tmp_path, bad)
    with pytest.raises(PolicyLoadError, match="default_tier"):
        load_policy(path)


def test_duplicate_preset_id_rejected(tmp_path):
    dup = """
mode: dev
presets:
  - id: a
    label: A
    command: ["echo"]
    default_tier: restrictive
    allowed_tiers: [restrictive]
  - id: a
    label: A again
    command: ["echo"]
    default_tier: restrictive
    allowed_tiers: [restrictive]
"""
    path = _write(tmp_path, dup)
    with pytest.raises(PolicyLoadError, match="duplicate"):
        load_policy(path)


def test_non_local_executor_rejected(tmp_path):
    remote = """
mode: dev
presets:
  - id: r
    label: r
    executor: remote
    command: ["echo"]
    default_tier: restrictive
    allowed_tiers: [restrictive]
"""
    path = _write(tmp_path, remote)
    with pytest.raises(PolicyLoadError):
        load_policy(path)


def test_visible_presets_filters_by_group():
    p = Policy(
        mode="dev",
        presets=[
            Preset(
                id="public",
                label="p",
                command=["x"],
                default_tier=SandboxTier.restrictive,
                allowed_tiers=[SandboxTier.restrictive],
                visible_to_groups=["*"],
            ),
            Preset(
                id="admin-only",
                label="a",
                command=["x"],
                default_tier=SandboxTier.restrictive,
                allowed_tiers=[SandboxTier.restrictive],
                visible_to_groups=["admin"],
            ),
        ],
    )
    assert [x.id for x in p.visible_presets([])] == ["public"]
    assert [x.id for x in p.visible_presets(["admin"])] == ["public", "admin-only"]


def test_root_allowed_globbing():
    p = Policy(
        mode="dev",
        workspace_roots=[
            WorkspaceRoot(group="*", paths=["/tmp/**"]),
            WorkspaceRoot(group="admin", paths=["/**"]),
        ],
    )
    assert p.root_allowed("/tmp/project", [])
    assert p.root_allowed("/tmp/nested/sub", [])
    assert not p.root_allowed("/etc", [])
    assert p.root_allowed("/etc", ["admin"])


def test_tier_info_serializable():
    ti = TierInfo(summary="x", network="denied", filesystem="ro", env="none")
    dumped = ti.model_dump()
    assert dumped["summary"] == "x"
