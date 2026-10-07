"""
Compliance level management and validation.

Loads compliance level definitions from compliance-levels.json and provides
validation and allowlist checking.
"""

import json
import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from atlas.core.log_sanitizer import sanitize_for_logging

logger = logging.getLogger(__name__)


COMPLIANCE_LEVELS_FILE = "compliance-levels.json"


def _default_search_paths() -> List[Path]:
    """Where compliance-levels.json is looked up when no path is given.

    Uses the same two-layer lookup as every other config file -- the user
    config dir (APP_CONFIG_DIR, default ``config/``) then the package
    defaults in ``atlas/config/`` -- so the documented locations actually
    work. Before this, only pre-#275 paths (``config/overrides``,
    ``atlas/configfiles``...) were searched, none of which exist after the
    package rename, so the levels never loaded: the header compliance
    selector never rendered and validation silently ran permissive.
    The legacy override paths are still checked after the user config dir
    so an older deployment that kept its file there keeps it.
    """
    atlas_root = Path(__file__).parent.parent
    project_root = atlas_root.parent
    legacy = [
        project_root / "config" / "overrides" / COMPLIANCE_LEVELS_FILE,
        project_root / "config" / "defaults" / COMPLIANCE_LEVELS_FILE,
    ]
    package_default = atlas_root / "config" / COMPLIANCE_LEVELS_FILE
    try:
        from atlas.modules.config.config_manager import config_manager

        paths = config_manager._search_paths(COMPLIANCE_LEVELS_FILE)
    except Exception as e:  # never let a lookup problem break startup
        logger.warning("Could not resolve config search paths for compliance levels: %s", e)
        paths = [package_default]
    # Keep the package default last so a legacy override still wins over it.
    user_paths = [p for p in paths if p != package_default]
    return user_paths + legacy + [package_default]


@dataclass
class ComplianceLevel:
    """Represents a single compliance level definition."""
    name: str
    description: str
    aliases: List[str]
    allowed_with: List[str]  # List of compliance levels that can be used together


class ComplianceLevelManager:
    """Manages compliance level definitions and validation."""

    def __init__(self, config_path: Optional[Path] = None):
        """Initialize the compliance level manager.

        Args:
            config_path: Path to compliance-levels.json. If None, uses default location.
        """
        self.levels: Dict[str, ComplianceLevel] = {}
        self.mode: str = "explicit_allowlist"
        self._name_to_canonical: Dict[str, str] = {}  # Maps aliases to canonical names

        if config_path is None:
            for path in _default_search_paths():
                if path.exists():
                    config_path = path
                    break

        if config_path and config_path.exists():
            self._load_config(config_path)
        else:
            logger.warning("No compliance-levels.json found, using permissive validation")

    def _load_config(self, config_path: Path):
        """Load compliance level configuration from JSON file."""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)

            self.mode = config.get('mode', 'explicit_allowlist')

            for level_data in config.get('levels', []):
                level = ComplianceLevel(
                    name=level_data['name'],
                    description=level_data.get('description', ''),
                    aliases=level_data.get('aliases', []),
                    allowed_with=level_data.get('allowed_with', [level_data['name']])
                )
                self.levels[level.name] = level

                # Map canonical name to itself
                self._name_to_canonical[level.name] = level.name

                # Map aliases to canonical name
                for alias in level.aliases:
                    self._name_to_canonical[alias] = level.name

            widening = [
                name for name, level in self.levels.items()
                if any(other != name for other in level.allowed_with)
            ]
            if widening:
                # Kept loadable for compatibility, but it no longer grants
                # access across levels (issue #1032).
                logger.info(
                    "compliance-levels.json: allowed_with on %d level(s) lists other "
                    "levels; allowed_with is deprecated and no longer grants access "
                    "across levels. List every classification a component may "
                    "receive in its allowed_data_classifications instead.",
                    len(widening),
                )

            logger.info(f"Loaded {len(self.levels)} compliance levels from {config_path}")
            logger.debug(f"Compliance levels: {list(self.levels.keys())}")

        except Exception as e:
            logger.error(f"Error loading compliance-levels.json: {e}")
            # Continue with empty config for permissive validation

    def get_canonical_name(self, name: Optional[str]) -> Optional[str]:
        """Get the canonical name for a compliance level (resolves aliases).

        Args:
            name: Compliance level name or alias

        Returns:
            Canonical name, or None if not found
        """
        if not name:
            return None
        return self._name_to_canonical.get(name)

    def is_valid_level(self, level_name: Optional[str]) -> bool:
        """Whether ``level_name`` names a defined level or alias.

        With no definitions loaded every name is accepted (permissive mode).
        """
        if not level_name:
            return False
        if not self.levels:
            return True
        return self.get_canonical_name(level_name) is not None

    def validate_compliance_level(self, level_name: Optional[str], context: str = "") -> Optional[str]:
        """Validate a compliance level name.

        Args:
            level_name: The compliance level to validate
            context: Context for logging (e.g., "MCP server 'calculator'")

        Returns:
            Canonical name if valid, None if invalid (with warning logged)
        """
        if not level_name:
            return None

        canonical = self.get_canonical_name(level_name)

        if canonical is None:
            # No compliance config loaded - permissive mode
            if not self.levels:
                return level_name

            # Unknown compliance level. Neither the rejected level name nor the
            # caller-supplied context is echoed verbatim: the level name can come
            # from a client payload (log injection) and the context can name the
            # selected model, which must not appear in compliance warnings.
            valid_levels = list(self.levels.keys())
            logger.warning(
                "Invalid compliance level in %s. %d valid level(s) are configured. "
                "Setting to None.",
                sanitize_for_logging(context) if context else "request",
                len(valid_levels),
            )
            return None

        if canonical != level_name:
            logger.debug(
                "Resolved compliance level alias in %s",
                sanitize_for_logging(context) if context else "request",
            )

        return canonical

    def validate_classifications(
        self, classifications: Optional[Sequence[str]], context: str = ""
    ) -> Optional[List[str]]:
        """Canonicalize an ``allowed_data_classifications`` list.

        Unknown names are dropped (with the same warning as an unknown
        ``compliance_level``) rather than kept, so a typo can only narrow what
        a component is approved for, never widen it. ``None`` (not declared)
        stays ``None``; a list whose every entry was unknown becomes ``[]``,
        which approves the component for no classified session.
        """
        if classifications is None:
            return None
        out: List[str] = []
        for name in classifications:
            if not isinstance(name, str) or not name:
                continue
            canonical = self.validate_compliance_level(name, context=context)
            if canonical and canonical not in out:
                out.append(canonical)
        return out

    def classification_permits(
        self,
        active_level: Optional[str],
        classifications: Union[None, str, Sequence[str]],
    ) -> bool:
        """Whether a component may receive data of the active classification.

        The one access rule (issue #1032): the active conversation
        classification must be a member of the component's explicitly
        declared ``allowed_data_classifications``.

        - No active classification: nothing to protect, so permitted.
        - Nothing declared (``None`` or ``[]``): denied. A component without a
          declaration is approved for no classified session -- fail closed.
        - Otherwise membership after alias resolution. ``allowed_with`` in
          compliance-levels.json plays no part: a level never makes another
          level's components valid by implication.

        ``classifications`` may be a bare string (a legacy ``compliance_level``),
        which is read as a one-element list.
        """
        if not active_level:
            return True
        if isinstance(classifications, str):
            classifications = [classifications]
        if not classifications:
            return False
        active = self.get_canonical_name(active_level) or (
            None if self.levels else active_level
        )
        if not active:
            # An undefined active level can match nothing.
            return False
        for name in classifications:
            if not isinstance(name, str) or not name:
                continue
            canonical = self.get_canonical_name(name) or (None if self.levels else name)
            if canonical == active:
                return True
        return False

    def is_accessible(
        self,
        user_level: Optional[str],
        resource_level: Union[None, str, Sequence[str]],
    ) -> bool:
        """Whether a resource is usable at ``user_level``.

        Kept as the historical entry point; it now applies the explicit
        membership rule of :meth:`classification_permits` (issue #1032).
        ``resource_level`` may be a legacy single level or a list of allowed
        data classifications. An untagged resource is no longer accessible
        under a selected level.
        """
        return self.classification_permits(user_level, resource_level)

    def get_accessible_levels(self, user_level: Optional[str]) -> Set[str]:
        """Get all compliance levels accessible to a user.

        Args:
            user_level: User's selected compliance level

        Returns:
            Set of accessible compliance level names (canonical)
        """
        if not user_level or not self.levels:
            # Return all levels if no user level or no config
            return set(self.levels.keys()) if self.levels else set()

        user_canonical = self.get_canonical_name(user_level)
        if not user_canonical or user_canonical not in self.levels:
            return set(self.levels.keys())

        # Under the explicit membership rule a level reaches only components
        # that list it; ``allowed_with`` no longer widens that.
        return {user_canonical}

    def resolve_default_level(self, preferred: Optional[str]) -> Optional[str]:
        """The level a session starts on when a level is required.

        ``preferred`` (the operator's ``COMPLIANCE_DEFAULT_LEVEL``) wins when it
        names a defined level or alias; otherwise the first defined level is
        used. None when no levels are defined.
        """
        canonical = self.get_canonical_name(preferred) if preferred else None
        if canonical:
            return canonical
        if preferred:
            logger.warning(
                "COMPLIANCE_DEFAULT_LEVEL names no defined compliance level; "
                "using the first defined level instead"
            )
        levels = self.get_all_levels()
        return levels[0] if levels else None

    def get_all_levels(self) -> List[str]:
        """Get all defined compliance level names (canonical).

        Returns:
            List of compliance level names in definition order
        """
        return list(self.levels.keys())


def declared_classifications(resource: Any) -> Optional[List[str]]:
    """The data classifications a component is explicitly approved for.

    ``resource`` is a config object (LLM model, MCP server, RAG source) or a
    dict of the same shape, including discovery payloads that use camelCase.
    ``allowed_data_classifications`` wins when present; otherwise a legacy
    ``compliance_level`` is read as a one-element list (the migration path of
    issue #1032). ``None`` when neither is declared.
    """
    if resource is None:
        return None
    if isinstance(resource, dict):
        allowed = resource.get("allowed_data_classifications")
        if allowed is None:
            allowed = resource.get("allowedDataClassifications")
        legacy = resource.get("compliance_level") or resource.get("complianceLevel")
    else:
        allowed = getattr(resource, "allowed_data_classifications", None)
        legacy = getattr(resource, "compliance_level", None)
    if isinstance(allowed, str):
        allowed = [allowed]
    if isinstance(allowed, (list, tuple)):
        return [c for c in allowed if isinstance(c, str) and c]
    if isinstance(legacy, str) and legacy:
        return [legacy]
    return None


# Global instance
_compliance_manager: Optional[ComplianceLevelManager] = None
_active_compliance_context: ContextVar[Tuple[Optional[str], bool]] = ContextVar(
    "active_compliance_context",
    default=(None, False),
)


def get_compliance_manager() -> ComplianceLevelManager:
    """Get the global compliance level manager instance."""
    global _compliance_manager
    if _compliance_manager is None:
        _compliance_manager = ComplianceLevelManager()
    return _compliance_manager


def set_active_compliance_context(
    level: Optional[str],
    *,
    enforce: bool,
) -> Token[Tuple[Optional[str], bool]]:
    """Set the per-turn compliance context used by query-time enforcement."""
    return _active_compliance_context.set((level, enforce))


def reset_active_compliance_context(token: Token[Tuple[Optional[str], bool]]) -> None:
    """Restore the previous per-turn compliance context."""
    _active_compliance_context.reset(token)


def get_active_compliance_context() -> Tuple[Optional[str], bool]:
    """Return ``(active_level, enforce)`` for the current async context."""
    return _active_compliance_context.get()
