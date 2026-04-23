"""Agent Portal policy loader.

Single source of truth for launchable presets, workspace roots, tier
descriptions, and mode. Loaded at backend startup from
`atlas/config/agent_portal.yaml` (overridable via AGENT_PORTAL_POLICY_FILE).

Failure modes all surface at load time so the server refuses to start
with a broken policy rather than half-mounting the portal.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

from atlas.modules.agent_portal.models import SandboxTier


class TierInfo(BaseModel):
    """Human-readable tier description surfaced via /config."""

    summary: str
    network: str = ""
    filesystem: str = ""
    env: str = ""


class WorkspaceRoot(BaseModel):
    """One entry mapping a group to allowed workspace-root glob patterns."""

    group: str
    paths: List[str] = Field(default_factory=list)


class BudgetOverrides(BaseModel):
    """Subset of Budget fields a preset may override.

    Values here *replace* defaults but are still capped by the pydantic
    field bounds on `Budget` when applied (see validate_spec).
    """

    wall_clock_seconds: Optional[int] = None
    tool_calls: Optional[int] = None
    tokens: Optional[int] = None
    idle_timeout_seconds: Optional[int] = None
    hard_ttl_seconds: Optional[int] = None


class Preset(BaseModel):
    """A fixed launchable agent configuration."""

    id: str
    label: str
    description: str = ""
    executor: Literal["local"] = "local"  # v2 will accept "remote"; v1 rejects anything else
    command: List[str] = Field(..., min_length=1)
    pty: bool = False
    default_tier: SandboxTier
    allowed_tiers: List[SandboxTier] = Field(..., min_length=1)
    visible_to_groups: List[str] = Field(default_factory=lambda: ["*"])
    requires_root: bool = True
    budget_overrides: BudgetOverrides = Field(default_factory=BudgetOverrides)

    @field_validator("allowed_tiers")
    @classmethod
    def _default_must_be_allowed(cls, v: List[SandboxTier], info):
        # info.data won't have default_tier yet if we're called first,
        # but pydantic v2 runs validators in field order; default_tier
        # is declared above so it is populated by now.
        default = info.data.get("default_tier")
        if default is not None and default not in v:
            raise ValueError(
                f"default_tier {default.value!r} must appear in allowed_tiers {[t.value for t in v]}"
            )
        return v


class Policy(BaseModel):
    """Parsed agent_portal.yaml."""

    mode: Literal["dev", "prod"] = "prod"
    tiers: Dict[str, TierInfo] = Field(default_factory=dict)
    workspace_roots: List[WorkspaceRoot] = Field(default_factory=list)
    presets: List[Preset] = Field(default_factory=list)

    @field_validator("presets")
    @classmethod
    def _unique_preset_ids(cls, v: List[Preset]):
        seen = set()
        for p in v:
            if p.id in seen:
                raise ValueError(f"duplicate preset id {p.id!r}")
            seen.add(p.id)
        return v

    # --- Query helpers (group-aware) -----------------------------------------

    def visible_presets(self, user_groups: List[str]) -> List[Preset]:
        """Return the presets this user's groups can see."""
        user_set = set(user_groups)
        result: List[Preset] = []
        for p in self.presets:
            if any(g == "*" or g in user_set for g in p.visible_to_groups):
                result.append(p)
        return result

    def preset_by_id(self, preset_id: str) -> Optional[Preset]:
        for p in self.presets:
            if p.id == preset_id:
                return p
        return None

    def allowed_roots_for(self, user_groups: List[str]) -> List[str]:
        """Flat list of glob patterns the user can pick workspace roots from."""
        user_set = set(user_groups)
        patterns: List[str] = []
        for rule in self.workspace_roots:
            if rule.group == "*" or rule.group in user_set:
                patterns.extend(rule.paths)
        # Preserve order, drop duplicates.
        seen = set()
        deduped: List[str] = []
        for p in patterns:
            if p not in seen:
                seen.add(p)
                deduped.append(p)
        return deduped

    def root_allowed(self, path: str, user_groups: List[str]) -> bool:
        """True if `path` matches any allowed glob for the user's groups."""
        for pattern in self.allowed_roots_for(user_groups):
            if fnmatch.fnmatch(path, pattern):
                return True
            # fnmatch doesn't do ** recursively across separators on some
            # platforms; handle the common "/**" suffix by matching any
            # descendant.
            if pattern.endswith("/**"):
                prefix = pattern[:-3]
                if path == prefix.rstrip("/") or path.startswith(prefix):
                    return True
        return False


class PolicyLoadError(RuntimeError):
    """Raised on any load/parse/validate failure."""


def load_policy(path: Path) -> Policy:
    """Parse + validate a policy yaml. Raises PolicyLoadError on failure."""
    p = Path(path)
    if not p.exists():
        raise PolicyLoadError(f"policy file not found: {p}")
    try:
        with p.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise PolicyLoadError(f"policy yaml parse error in {p}: {exc}") from exc
    if raw is None:
        raise PolicyLoadError(f"policy file {p} is empty")
    if not isinstance(raw, dict):
        raise PolicyLoadError(f"policy file {p} must be a mapping at top level")
    try:
        return Policy.model_validate(raw)
    except Exception as exc:
        raise PolicyLoadError(f"policy validation error in {p}: {exc}") from exc
