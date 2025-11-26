"""
Domain whitelist management for email access control.

Loads domain whitelist definitions from domain-whitelist.json and provides
validation for user email domains.
"""

import json
import logging
from pathlib import Path
from typing import List, Optional, Set
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DomainWhitelistConfig:
    """Configuration for domain whitelist."""
    enabled: bool
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
            logger.warning("No domain-whitelist.json found, domain whitelist disabled")
            self.config = DomainWhitelistConfig(
                enabled=False,
                domains=set(),
                subdomain_matching=True,
                version="1.0",
                description="No config loaded"
            )
    
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
                enabled=config_data.get('enabled', False),
                domains=domains,
                subdomain_matching=config_data.get('subdomain_matching', True),
                version=config_data.get('version', '1.0'),
                description=config_data.get('description', '')
            )
            
            logger.info(f"Loaded {len(self.config.domains)} domains from {config_path}")
            logger.debug(f"Domain whitelist enabled: {self.config.enabled}")
            
        except Exception as e:
            logger.error(f"Error loading domain-whitelist.json: {e}")
            # Use disabled config on error
            self.config = DomainWhitelistConfig(
                enabled=False,
                domains=set(),
                subdomain_matching=True,
                version="1.0",
                description="Error loading config"
            )
    
    def is_enabled(self) -> bool:
        """Check if domain whitelist is enabled.
        
        Returns:
            True if enabled, False otherwise
        """
        return self.config is not None and self.config.enabled
    
    def is_domain_allowed(self, email: str) -> bool:
        """Check if an email address is from an allowed domain.
        
        Args:
            email: Email address to validate
            
        Returns:
            True if domain is allowed, False otherwise
        """
        if not self.config or not self.config.enabled:
            # If not enabled or no config, allow all
            return True
        
        if not email or "@" not in email:
            return False
        
        domain = email.split("@", 1)[1].lower()
        
        # Check if domain is in whitelist
        if domain in self.config.domains:
            return True
        
        # Check subdomains if enabled
        if self.config.subdomain_matching:
            return any(domain.endswith("." + d) for d in self.config.domains)
        
        return False
    
    def get_domains(self) -> Set[str]:
        """Get the set of whitelisted domains.
        
        Returns:
            Set of allowed domains
        """
        return self.config.domains if self.config else set()
