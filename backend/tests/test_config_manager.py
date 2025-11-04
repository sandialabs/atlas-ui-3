"""Unit tests for ConfigManager.

Tests the centralized configuration management system without
modifying the actual environment or configuration files.
"""

import pytest
from pathlib import Path
from backend.modules.config.config_manager import (
    ConfigManager,
    AppSettings,
    LLMConfig,
    MCPConfig,
)


class TestConfigManager:
    """Test ConfigManager initialization and basic functionality."""

    def test_config_manager_initialization(self):
        """ConfigManager should initialize without errors."""
        cm = ConfigManager()
        assert cm is not None
        assert cm._backend_root.name == "backend"

    def test_app_settings_loads(self):
        """AppSettings should load with defaults or environment values."""
        cm = ConfigManager()
        settings = cm.app_settings

        assert settings is not None
        assert isinstance(settings, AppSettings)
        assert settings.app_name is not None
        assert settings.port > 0
        assert settings.log_level in ["DEBUG", "INFO", "WARNING", "ERROR"]

    def test_llm_config_loads(self):
        """LLM config should load from config files."""
        cm = ConfigManager()
        llm_config = cm.llm_config

        assert llm_config is not None
        assert isinstance(llm_config, LLMConfig)
        # Should have at least some models configured
        assert hasattr(llm_config, "models")

    def test_mcp_config_loads(self):
        """MCP config should load from config files."""
        cm = ConfigManager()
        mcp_config = cm.mcp_config

        assert mcp_config is not None
        assert isinstance(mcp_config, MCPConfig)
        assert hasattr(mcp_config, "servers")

    def test_config_manager_caches_settings(self):
        """ConfigManager should cache settings and return same instance."""
        cm = ConfigManager()

        # Get settings twice
        settings1 = cm.app_settings
        settings2 = cm.app_settings

        # Should be the exact same object (cached)
        assert settings1 is settings2

    def test_config_manager_caches_llm_config(self):
        """ConfigManager should cache LLM config."""
        cm = ConfigManager()

        config1 = cm.llm_config
        config2 = cm.llm_config

        assert config1 is config2

    def test_search_paths_returns_list(self):
        """Search paths should return a list of Path objects."""
        cm = ConfigManager()

        paths = cm._search_paths("llmconfig.yml")

        assert isinstance(paths, list)
        assert len(paths) > 0
        assert all(isinstance(p, Path) for p in paths)

    def test_search_paths_includes_overrides_and_defaults(self):
        """Search paths should include both overrides and defaults directories."""
        cm = ConfigManager()

        paths = cm._search_paths("mcp.json")
        path_strings = [str(p) for p in paths]

        # Should include overrides directory
        assert any("overrides" in p for p in path_strings)
        # Should include defaults directory
        assert any("defaults" in p for p in path_strings)

    def test_validate_config_returns_dict(self):
        """Validate config should return a dictionary of validation results."""
        cm = ConfigManager()

        result = cm.validate_config()

        assert isinstance(result, dict)
        assert "app_settings" in result
        assert "llm_config" in result
        assert "mcp_config" in result
        # All should be boolean values
        assert all(isinstance(v, bool) for v in result.values())

    def test_reload_configs_works(self):
        """Reload configs should clear cache and reload."""
        cm = ConfigManager()

        # Load configs first
        _ = cm.app_settings
        _ = cm.llm_config

        # Reload should not raise errors
        cm.reload_configs()

        # Configs should still be accessible
        assert cm.app_settings is not None
        assert cm.llm_config is not None


class TestAppSettings:
    """Test AppSettings model."""

    def test_app_settings_has_required_fields(self):
        """AppSettings should have all required configuration fields."""
        settings = AppSettings()

        # Basic app settings
        assert hasattr(settings, "app_name")
        assert hasattr(settings, "port")
        assert hasattr(settings, "debug_mode")
        assert hasattr(settings, "log_level")

        # Feature flags
        assert hasattr(settings, "feature_rag_enabled")
        assert hasattr(settings, "feature_tools_enabled")
        assert hasattr(settings, "feature_marketplace_enabled")

        # S3 settings
        assert hasattr(settings, "s3_endpoint")
        assert hasattr(settings, "s3_bucket_name")

        # Config paths
        assert hasattr(settings, "app_config_overrides")
        assert hasattr(settings, "app_config_defaults")

    def test_app_settings_defaults(self):
        """AppSettings should have sensible defaults."""
        settings = AppSettings()

        assert settings.port == 8000
        assert settings.log_level in ["DEBUG", "INFO", "WARNING", "ERROR"]
        assert isinstance(settings.debug_mode, bool)
        assert isinstance(settings.banner_enabled, bool)

    def test_app_settings_agent_backward_compatibility(self):
        """Agent mode available should maintain backward compatibility."""
        settings = AppSettings()

        # Both new and old property should work
        assert hasattr(settings, "feature_agent_mode_available")
        assert hasattr(settings, "agent_mode_available")
        assert settings.agent_mode_available == settings.feature_agent_mode_available


class TestConfigManagerCustomRoot:
    """Test ConfigManager with custom backend root."""

    def test_custom_backend_root(self):
        """ConfigManager should accept custom backend root path."""
        custom_root = Path(__file__).parent.parent
        cm = ConfigManager(backend_root=custom_root)

        assert cm._backend_root == custom_root

    def test_custom_root_still_loads_configs(self):
        """ConfigManager with custom root should still load configs."""
        custom_root = Path(__file__).parent.parent
        cm = ConfigManager(backend_root=custom_root)

        # Should still be able to load configs
        assert cm.app_settings is not None
        assert cm.llm_config is not None
