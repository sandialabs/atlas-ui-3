"""Comprehensive tests for ComplianceLevelManager."""

import json
import pytest
from pathlib import Path
from tempfile import TemporaryDirectory
from backend.core.compliance import ComplianceLevelManager, ComplianceLevel


@pytest.fixture
def sample_compliance_config():
    """Sample compliance level configuration."""
    return {
        "version": "2.0",
        "mode": "explicit_allowlist",
        "levels": [
            {
                "name": "Public",
                "description": "Publicly accessible",
                "aliases": ["public"],
                "allowed_with": ["Public"]
            },
            {
                "name": "External",
                "description": "External services",
                "aliases": ["ext"],
                "allowed_with": ["External"]
            },
            {
                "name": "Internal",
                "description": "Internal systems",
                "aliases": ["int"],
                "allowed_with": ["Internal"]
            },
            {
                "name": "SOC2",
                "description": "SOC 2 compliant",
                "aliases": ["SOC-2", "SOC 2"],
                "allowed_with": ["SOC2"]
            },
            {
                "name": "HIPAA",
                "description": "HIPAA compliant",
                "aliases": ["HIPAA-Compliant"],
                "allowed_with": ["HIPAA", "SOC2"]
            },
            {
                "name": "FedRAMP",
                "description": "FedRAMP authorized",
                "aliases": ["FedRAMP-Moderate"],
                "allowed_with": ["FedRAMP", "SOC2"]
            }
        ]
    }


@pytest.fixture
def temp_compliance_config(sample_compliance_config):
    """Create a temporary compliance config file."""
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "compliance-levels.json"
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(sample_compliance_config, f)
        yield config_path


class TestComplianceLevelManager:
    """Tests for ComplianceLevelManager class."""
    
    def test_load_config_success(self, temp_compliance_config):
        """Test loading a valid compliance configuration."""
        manager = ComplianceLevelManager(temp_compliance_config)
        
        assert len(manager.levels) == 6
        assert "Public" in manager.levels
        assert "HIPAA" in manager.levels
        assert "FedRAMP" in manager.levels
        assert manager.mode == "explicit_allowlist"
    
    def test_load_config_missing_file(self):
        """Test handling of missing config file."""
        manager = ComplianceLevelManager(Path("/nonexistent/path.json"))
        
        # Should not crash, should have empty levels
        assert len(manager.levels) == 0
    
    def test_get_canonical_name_exact_match(self, temp_compliance_config):
        """Test getting canonical name with exact match."""
        manager = ComplianceLevelManager(temp_compliance_config)
        
        assert manager.get_canonical_name("Public") == "Public"
        assert manager.get_canonical_name("HIPAA") == "HIPAA"
        assert manager.get_canonical_name("SOC2") == "SOC2"
    
    def test_get_canonical_name_alias(self, temp_compliance_config):
        """Test getting canonical name from alias."""
        manager = ComplianceLevelManager(temp_compliance_config)
        
        assert manager.get_canonical_name("SOC 2") == "SOC2"
        assert manager.get_canonical_name("SOC-2") == "SOC2"
        assert manager.get_canonical_name("HIPAA-Compliant") == "HIPAA"
        assert manager.get_canonical_name("FedRAMP-Moderate") == "FedRAMP"
    
    def test_get_canonical_name_invalid(self, temp_compliance_config):
        """Test getting canonical name for invalid level."""
        manager = ComplianceLevelManager(temp_compliance_config)
        
        assert manager.get_canonical_name("Invalid") is None
        assert manager.get_canonical_name("SOCII") is None
        assert manager.get_canonical_name(None) is None
    
    def test_validate_compliance_level_valid(self, temp_compliance_config):
        """Test validation of valid compliance levels."""
        manager = ComplianceLevelManager(temp_compliance_config)
        
        assert manager.validate_compliance_level("Public") == "Public"
        assert manager.validate_compliance_level("HIPAA") == "HIPAA"
        assert manager.validate_compliance_level("SOC2") == "SOC2"
    
    def test_validate_compliance_level_alias(self, temp_compliance_config):
        """Test validation with alias (should return canonical name)."""
        manager = ComplianceLevelManager(temp_compliance_config)
        
        assert manager.validate_compliance_level("SOC 2") == "SOC2"
        assert manager.validate_compliance_level("SOC-2") == "SOC2"
        assert manager.validate_compliance_level("HIPAA-Compliant") == "HIPAA"
    
    def test_validate_compliance_level_invalid(self, temp_compliance_config):
        """Test validation of invalid compliance level (should return None and log)."""
        manager = ComplianceLevelManager(temp_compliance_config)
        
        assert manager.validate_compliance_level("InvalidLevel") is None
        assert manager.validate_compliance_level("SOCII") is None
        assert manager.validate_compliance_level("") is None
        assert manager.validate_compliance_level(None) is None
    
    def test_is_accessible_same_level(self, temp_compliance_config):
        """Test access control when user and resource have same level."""
        manager = ComplianceLevelManager(temp_compliance_config)
        
        # Each level can access itself
        assert manager.is_accessible("Public", "Public") is True
        assert manager.is_accessible("Internal", "Internal") is True
        assert manager.is_accessible("HIPAA", "HIPAA") is True
    
    def test_is_accessible_different_level_not_allowed(self, temp_compliance_config):
        """Test access control when different levels are not in allowlist."""
        manager = ComplianceLevelManager(temp_compliance_config)
        
        # Public cannot access HIPAA
        assert manager.is_accessible("Public", "HIPAA") is False
        
        # HIPAA cannot access Public (security!)
        assert manager.is_accessible("HIPAA", "Public") is False
        
        # Internal cannot access External
        assert manager.is_accessible("Internal", "External") is False
    
    def test_is_accessible_allowlist_grants_access(self, temp_compliance_config):
        """Test access control when allowlist grants access."""
        manager = ComplianceLevelManager(temp_compliance_config)
        
        # HIPAA can access SOC2 (in allowlist)
        assert manager.is_accessible("HIPAA", "SOC2") is True
        
        # FedRAMP can access SOC2 (in allowlist)
        assert manager.is_accessible("FedRAMP", "SOC2") is True
    
    def test_is_accessible_one_sided_allowlist(self, temp_compliance_config):
        """Test that allowlist is not bidirectional."""
        manager = ComplianceLevelManager(temp_compliance_config)
        
        # HIPAA can access SOC2
        assert manager.is_accessible("HIPAA", "SOC2") is True
        
        # But SOC2 cannot access HIPAA (not in SOC2's allowlist)
        assert manager.is_accessible("SOC2", "HIPAA") is False
    
    def test_is_accessible_none_is_permissive(self, temp_compliance_config):
        """Test that None (unset) compliance level is always accessible."""
        manager = ComplianceLevelManager(temp_compliance_config)
        
        # None user can access any resource
        assert manager.is_accessible(None, "HIPAA") is True
        assert manager.is_accessible(None, "Public") is True
        
        # Any user can access None resource
        assert manager.is_accessible("HIPAA", None) is True
        assert manager.is_accessible("Public", None) is True
        
        # None to None
        assert manager.is_accessible(None, None) is True
    
    def test_get_accessible_levels_public(self, temp_compliance_config):
        """Test getting accessible levels for Public."""
        manager = ComplianceLevelManager(temp_compliance_config)
        
        accessible = manager.get_accessible_levels("Public")
        
        assert accessible == {"Public"}
    
    def test_get_accessible_levels_hipaa(self, temp_compliance_config):
        """Test getting accessible levels for HIPAA."""
        manager = ComplianceLevelManager(temp_compliance_config)
        
        accessible = manager.get_accessible_levels("HIPAA")
        
        assert accessible == {"HIPAA", "SOC2"}
    
    def test_get_accessible_levels_fedramp(self, temp_compliance_config):
        """Test getting accessible levels for FedRAMP."""
        manager = ComplianceLevelManager(temp_compliance_config)
        
        accessible = manager.get_accessible_levels("FedRAMP")
        
        assert accessible == {"FedRAMP", "SOC2"}
    
    def test_get_accessible_levels_none(self, temp_compliance_config):
        """Test getting accessible levels when user level is None."""
        manager = ComplianceLevelManager(temp_compliance_config)
        
        accessible = manager.get_accessible_levels(None)
        
        # Should return all levels
        assert len(accessible) == 6
        assert "Public" in accessible
        assert "HIPAA" in accessible
    
    def test_get_all_levels(self, temp_compliance_config):
        """Test getting all defined compliance levels."""
        manager = ComplianceLevelManager(temp_compliance_config)
        
        all_levels = manager.get_all_levels()
        
        assert len(all_levels) == 6
        assert "Public" in all_levels
        assert "External" in all_levels
        assert "Internal" in all_levels
        assert "SOC2" in all_levels
        assert "HIPAA" in all_levels
        assert "FedRAMP" in all_levels
    
    def test_permissive_mode_no_config(self):
        """Test permissive mode when no config is loaded."""
        manager = ComplianceLevelManager(Path("/nonexistent"))
        
        # Should validate anything in permissive mode
        assert manager.validate_compliance_level("AnyLevel") == "AnyLevel"
        
        # Should allow all access
        assert manager.is_accessible("Level1", "Level2") is True
