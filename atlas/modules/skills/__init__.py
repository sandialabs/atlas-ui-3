"""Agent Skills discovery for Atlas.

Loads skills authored to the Agent Skills specification
(https://agentskills.io/specification) and exposes their name/description index
for injection into the system prompt.
"""
from .models import TIER_PACKAGED, TIER_PROJECT, TIER_USER, Skill
from .registry import (
    SKILL_FILENAME,
    SkillRegistry,
    SkillValidationError,
    parse_skill_file,
)

__all__ = [
    "Skill",
    "SkillRegistry",
    "SkillValidationError",
    "parse_skill_file",
    "SKILL_FILENAME",
    "TIER_PACKAGED",
    "TIER_USER",
    "TIER_PROJECT",
]
