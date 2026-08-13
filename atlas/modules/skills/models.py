"""Data model for a discovered Agent Skill.

Field names follow the Agent Skills specification (https://agentskills.io/specification).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

# Tier labels, ordered from lowest to highest precedence. A skill discovered in a
# later tier overrides a same-named skill from an earlier one.
TIER_PACKAGED = "packaged"
TIER_USER = "user"
TIER_PROJECT = "project"

TIER_ORDER = (TIER_PACKAGED, TIER_USER, TIER_PROJECT)


@dataclass(frozen=True)
class Skill:
    """A single skill discovered on disk.

    Only ``name`` and ``description`` are required by the spec; everything else
    is optional metadata that Atlas preserves but does not currently act on.
    """

    name: str
    description: str
    path: Path
    tier: str = TIER_PACKAGED
    license: Optional[str] = None
    compatibility: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)
    allowed_tools: Optional[str] = None

    @property
    def directory(self) -> Path:
        """Directory containing this skill's SKILL.md and bundled resources."""
        return self.path.parent
