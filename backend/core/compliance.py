"""
Compliance level management and validation.

Loads compliance level definitions from compliance-levels.json and provides
validation and allowlist checking.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass

logger = logging.getLogger(__name__)


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
            # Try to find config in standard locations
            backend_root = Path(__file__).parent.parent
            project_root = backend_root.parent
            
            search_paths = [
                project_root / "config" / "overrides" / "compliance-levels.json",
                project_root / "config" / "defaults" / "compliance-levels.json",
                backend_root / "configfilesadmin" / "compliance-levels.json",
                backend_root / "configfiles" / "compliance-levels.json",
            ]
            
            for path in search_paths:
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
            
            # Unknown compliance level
            valid_levels = list(self.levels.keys())
            logger.warning(
                f"Invalid compliance level '{level_name}' {context}. "
                f"Valid levels: {', '.join(valid_levels)}. "
                f"Setting to None."
            )
            return None
        
        if canonical != level_name:
            logger.debug(f"Resolved alias '{level_name}' to '{canonical}' {context}")
        
        return canonical
    
    def is_accessible(self, user_level: Optional[str], resource_level: Optional[str]) -> bool:
        """Check if a resource at resource_level is accessible given user_level.
        
        In explicit allowlist mode:
        - Each level defines which other levels can be used together
        - For example, HIPAA might allow HIPAA and SOC2, but not Public
        - None (unset) is accessible by all and can access all
        
        Args:
            user_level: User's selected compliance level
            resource_level: Resource's compliance level
            
        Returns:
            True if resource is accessible, False otherwise
        """
        # If either is None/unset, resource is accessible (backward compatibility)
        if not user_level or not resource_level:
            return True
        
        # Get canonical names
        user_canonical = self.get_canonical_name(user_level)
        resource_canonical = self.get_canonical_name(resource_level)
        
        # If we don't have level info, be permissive
        if not user_canonical or not resource_canonical:
            return True
        
        # Get level object for user
        user_level_obj = self.levels.get(user_canonical)
        
        if not user_level_obj:
            return True
        
        # Check if resource_level is in the user's allowed_with list
        return resource_canonical in user_level_obj.allowed_with
    
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
        
        user_level_obj = self.levels[user_canonical]
        
        # Return the allowed_with list for this level
        return set(user_level_obj.allowed_with)
    
    def get_all_levels(self) -> List[str]:
        """Get all defined compliance level names (canonical).
        
        Returns:
            List of compliance level names in definition order
        """
        return list(self.levels.keys())


# Global instance
_compliance_manager: Optional[ComplianceLevelManager] = None


def get_compliance_manager() -> ComplianceLevelManager:
    """Get the global compliance level manager instance."""
    global _compliance_manager
    if _compliance_manager is None:
        _compliance_manager = ComplianceLevelManager()
    return _compliance_manager
