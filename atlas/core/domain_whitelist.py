"""
Domain whitelist management for email access control.

Loads domain whitelist definitions from atlas.domain-whitelist.json and provides
validation for user email domains.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Set
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DomainWhitelistConfig:
    """Configuration for domain whitelist."""
    domains: Set[str]
    subdomain_matching: bool
    version: str
    description: str


class DomainWhitelistManager:
    """Manages domain whitelist configuration and validation."""
    
    def __init__(self, config_path: Optional[Path] = None):
        """Initialize the domain whitelist manager.
        
        Args:
            config_path: Path to domain-whitelist.json. If None, uses default location.
        """
        self.config: Optional[DomainWhitelistConfig] = None
        self.config_loaded: bool = False
        
        if config_path is None:
            # Try to find config in standard locations
            backend_root = Path(__file__).parent.parent
            project_root = backend_root.parent
            
            search_paths = [
                project_root / "config" / "overrides" / "domain-whitelist.json",
                project_root / "config" / "defaults" / "domain-whitelist.json",
                backend_root / "configfilesadmin" / "domain-whitelist.json",
                backend_root / "configfiles" / "domain-whitelist.json",
            ]
            
            for path in search_paths:
                if path.exists():
                    config_path = path
                    break
        
        if config_path and config_path.exists():
            self._load_config(config_path)
        else:
            logger.warning("No domain-whitelist.json found, whitelist validation disabled")
            self.config = DomainWhitelistConfig(
                domains=set(),
                subdomain_matching=True,
                version="1.0",
                description="No config loaded"
            )
            self.config_loaded = False
    
    def _load_config(self, config_path: Path):
        """Load domain whitelist configuration from JSON file."""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            
            # Extract domains from the list of domain objects
            domains = set()
            for domain_entry in config_data.get('domains', []):
                if isinstance(domain_entry, dict):
                    domains.add(domain_entry.get('domain', '').lower())
                elif isinstance(domain_entry, str):
                    domains.add(domain_entry.lower())
            
            self.config = DomainWhitelistConfig(
                domains=domains,
                subdomain_matching=config_data.get('subdomain_matching', True),
                version=config_data.get('version', '1.0'),
                description=config_data.get('description', '')
            )
            self.config_loaded = True
            
            logger.info(f"Loaded {len(self.config.domains)} domains from {config_path}")
            
        except Exception as e:
            logger.error(f"Error loading domain-whitelist.json: {e}")
            logger.warning("Whitelist validation disabled due to config error")
            # Use empty config on error
            self.config = DomainWhitelistConfig(
                domains=set(),
                subdomain_matching=True,
                version="1.0",
                description="Error loading config"
            )
            self.config_loaded = False
    
    def is_domain_allowed(self, email: str) -> bool:
        """Check if an email address is from an allowed domain.
        
        Note: This method only validates against the whitelist.
        The FEATURE_DOMAIN_WHITELIST_ENABLED flag controls whether
        the middleware uses this validation.
        
        Args:
            email: Email address to validate
            
        Returns:
            True if domain is allowed, False otherwise
        """
        # If config wasn't successfully loaded, allow all (fail open)
        if not self.config_loaded:
            return True
        
        if not email or "@" not in email:
            return False
        
        domain = email.split("@", 1)[1].lower()
        
        # Check if domain is in whitelist (O(1) lookup)
        if domain in self.config.domains:
            return True
        
        # Check subdomains if enabled - check each parent level
        if self.config.subdomain_matching:
            # Split domain and check each parent level
            # e.g., for "mail.dept.sandia.gov" check: "dept.sandia.gov", "sandia.gov"
            parts = domain.split(".")
            for i in range(1, len(parts)):
                parent_domain = ".".join(parts[i:])
                if parent_domain in self.config.domains:
                    return True
        
        return False
    
    def get_domains(self) -> Set[str]:
        """Get the set of whitelisted domains.
        
        Returns:
            Set of allowed domains
        """
        return self.config.domains if self.config else set()
