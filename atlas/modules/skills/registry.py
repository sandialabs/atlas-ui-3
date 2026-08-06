"""Discovery and validation of Agent Skills.

Implements the discovery half of Agent Skills support: locate ``SKILL.md`` files
across layered roots, validate their frontmatter against the Agent Skills
specification (https://agentskills.io/specification), and render the compact
index that gets injected into the system prompt.

Nothing here executes skill-bundled code. Scripts under a skill's ``scripts/``
directory are discovered as ordinary files and never run.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from atlas.modules.config import ConfigManager

from .models import TIER_PACKAGED, TIER_PROJECT, TIER_USER, Skill

logger = logging.getLogger(__name__)

SKILL_FILENAME = "SKILL.md"

# Constraints from the Agent Skills specification.
NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_COMPATIBILITY_LENGTH = 500

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\s*?(?:\r?\n|\Z)", re.DOTALL)

# Directory names searched under each user/project root, in the order the
# ecosystem conventions were adopted. `.atlas` is Atlas' own; the others let
# Atlas pick up skills authored for Claude Code / agents-compatible tools
# without the user having to duplicate them.
_SKILL_DIR_NAMES = (".atlas/skills", ".claude/skills", ".agents/skills")


class SkillValidationError(ValueError):
    """Raised internally when a SKILL.md fails spec validation."""


def _parse_frontmatter(text: str) -> Tuple[Dict, str]:
    """Split a SKILL.md into its YAML frontmatter mapping and markdown body."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise SkillValidationError("missing YAML frontmatter block")
    try:
        loaded = yaml.safe_load(match.group(1))
    except yaml.YAMLError as e:
        raise SkillValidationError(f"invalid YAML frontmatter: {e}") from e
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise SkillValidationError("frontmatter must be a mapping")
    return loaded, text[match.end():]


def _validate_name(raw: object, directory_name: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise SkillValidationError("'name' is required and must be a non-empty string")
    name = raw.strip()
    if len(name) > MAX_NAME_LENGTH:
        raise SkillValidationError(
            f"'name' exceeds {MAX_NAME_LENGTH} characters"
        )
    if not NAME_PATTERN.match(name):
        raise SkillValidationError(
            "'name' must be lowercase alphanumeric with single hyphens, "
            "and may not start or end with a hyphen"
        )
    if name != directory_name:
        raise SkillValidationError(
            f"'name' ({name}) must match its parent directory ({directory_name})"
        )
    return name


def _validate_description(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise SkillValidationError(
            "'description' is required and must be a non-empty string"
        )
    description = raw.strip()
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise SkillValidationError(
            f"'description' exceeds {MAX_DESCRIPTION_LENGTH} characters"
        )
    return description


def _validate_optional_str(raw: object, field: str, max_length: Optional[int]) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise SkillValidationError(f"'{field}' must be a string")
    value = raw.strip()
    if not value:
        return None
    if max_length is not None and len(value) > max_length:
        raise SkillValidationError(f"'{field}' exceeds {max_length} characters")
    return value


def _validate_metadata(raw: object) -> Dict[str, str]:
    """Coerce the optional metadata mapping to string keys and values.

    The spec defines metadata as a string->string map. YAML happily produces
    ints and bools for unquoted values (``version: 1.0``), which is a common
    authoring slip, so coerce scalars rather than rejecting the whole skill.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SkillValidationError("'metadata' must be a mapping")
    coerced: Dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(value, (dict, list)):
            raise SkillValidationError(
                f"'metadata.{key}' must be a scalar value, not a collection"
            )
        coerced[str(key)] = "" if value is None else str(value)
    return coerced


def parse_skill_file(path: Path, tier: str = TIER_PACKAGED) -> Skill:
    """Parse and validate a single SKILL.md. Raises SkillValidationError."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise SkillValidationError(f"could not read file: {e}") from e

    frontmatter, _body = _parse_frontmatter(text)

    name = _validate_name(frontmatter.get("name"), path.parent.name)
    description = _validate_description(frontmatter.get("description"))

    return Skill(
        name=name,
        description=description,
        path=path,
        tier=tier,
        license=_validate_optional_str(frontmatter.get("license"), "license", None),
        compatibility=_validate_optional_str(
            frontmatter.get("compatibility"), "compatibility", MAX_COMPATIBILITY_LENGTH
        ),
        metadata=_validate_metadata(frontmatter.get("metadata")),
        allowed_tools=_validate_optional_str(
            frontmatter.get("allowed-tools"), "allowed-tools", None
        ),
    )


class SkillRegistry:
    """Discovers skills across layered roots and renders the injectable index.

    The discovered set is cached in memory, but unlike prompt templates the cache
    is explicitly invalidated when a new chat session starts, so dropping a skill
    directory on disk takes effect without a server restart.
    """

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self._cache: Optional[List[Skill]] = None

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config_manager.app_settings, "feature_skills_enabled", False))

    def _configured_roots(self) -> Optional[List[Path]]:
        """Explicit roots from SKILLS_PATHS, which replace the defaults entirely."""
        raw = getattr(self.config_manager.app_settings, "skills_paths", "") or ""
        if not raw.strip():
            return None
        # Accept both os.pathsep and comma so a single value works on Windows,
        # POSIX, and in docker-compose env files.
        parts: List[str] = []
        for chunk in raw.split(os.pathsep):
            parts.extend(chunk.split(","))
        roots = [Path(p.strip()).expanduser() for p in parts if p.strip()]
        return roots or None

    def _default_roots(self) -> List[Tuple[Path, str]]:
        """Layered defaults, ordered lowest to highest precedence."""
        atlas_root = self.config_manager._atlas_root
        project_root = atlas_root.parent
        home = Path.home()

        roots: List[Tuple[Path, str]] = [
            (atlas_root / "config" / "skills", TIER_PACKAGED),
        ]
        roots.extend((home / name, TIER_USER) for name in _SKILL_DIR_NAMES)
        roots.extend((project_root / name, TIER_PROJECT) for name in _SKILL_DIR_NAMES)
        return roots

    def resolve_roots(self) -> List[Tuple[Path, str]]:
        """Return (root, tier) pairs to scan, lowest precedence first."""
        configured = self._configured_roots()
        if configured is not None:
            # Explicit configuration is treated as a single project-level tier;
            # order within the list still decides precedence.
            return [(root, TIER_PROJECT) for root in configured]

        seen = set()
        resolved: List[Tuple[Path, str]] = []
        for root, tier in self._default_roots():
            if root in seen:
                continue
            seen.add(root)
            resolved.append((root, tier))
        return resolved

    def discover(self) -> List[Skill]:
        """Scan all roots and return the merged skill set, sorted by name.

        Later roots override same-named skills from earlier roots, so a project
        skill shadows a user skill, which shadows a packaged one. Invalid skills
        are logged and skipped rather than failing the whole scan.
        """
        merged: Dict[str, Skill] = {}

        for root, tier in self.resolve_roots():
            try:
                if not root.is_dir():
                    continue
                entries = sorted(root.iterdir())
            except OSError as e:
                logger.warning("Could not read skills root %s: %s", root, e)
                continue

            for entry in entries:
                skill_file = entry / SKILL_FILENAME
                try:
                    if not entry.is_dir() or not skill_file.is_file():
                        continue
                except OSError:
                    continue
                try:
                    skill = parse_skill_file(skill_file, tier=tier)
                except SkillValidationError as e:
                    logger.warning("Skipping invalid skill at %s: %s", skill_file, e)
                    continue
                if skill.name in merged:
                    logger.debug(
                        "Skill '%s' from %s overrides %s",
                        skill.name,
                        skill_file,
                        merged[skill.name].path,
                    )
                merged[skill.name] = skill

        return sorted(merged.values(), key=lambda s: s.name)

    def get_skills(self) -> List[Skill]:
        """Return the cached skill set, scanning on first use after invalidation."""
        if not self.enabled:
            return []
        if self._cache is None:
            self._cache = self.discover()
            logger.info("Discovered %d skill(s)", len(self._cache))
        return self._cache

    def get_skill(self, name: str) -> Optional[Skill]:
        for skill in self.get_skills():
            if skill.name == name:
                return skill
        return None

    def invalidate(self) -> None:
        """Drop the cached skill set so the next access rescans disk."""
        self._cache = None

    def render_index(self) -> Optional[str]:
        """Render the system-prompt block listing available skills.

        Returns None when the feature is off or no valid skills were found, so
        callers can skip injection entirely rather than adding an empty section.
        """
        skills = self.get_skills()
        if not skills:
            return None

        lines = [
            "# Available Skills",
            "",
            "The following skills are available. Each entry gives a skill name and a "
            "description of what it does and when it applies. The descriptions below "
            "are the complete listing — the full instructions for a skill are not "
            "loaded yet.",
            "",
            "When a user's request matches a skill's description, read that skill's "
            "instructions before acting on the request, using the skills tool if one "
            "is available. Do not guess at a skill's contents from its description.",
            "",
        ]
        for skill in skills:
            lines.append(f"- **{skill.name}**: {skill.description}")
        return "\n".join(lines)
