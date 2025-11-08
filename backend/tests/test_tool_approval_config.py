"""Tests for tool approval configuration loading and management."""

from modules.config.config_manager import ConfigManager


class TestToolApprovalConfig:
    """Test tool approval configuration loading."""

    def test_tool_approvals_config_loads(self):
        """Test that tool approvals config loads successfully."""
        cm = ConfigManager()
        approval_config = cm.tool_approvals_config

        assert approval_config is not None
        assert hasattr(approval_config, "require_approval_by_default")
        assert hasattr(approval_config, "tools")

    def test_default_approval_config_structure(self):
        """Test the structure of default approval config."""
        cm = ConfigManager()
        approval_config = cm.tool_approvals_config

        # Default config should have require_approval_by_default (check it's boolean)
        assert isinstance(approval_config.require_approval_by_default, bool)
        # Default config should have tools dict (may or may not be empty)
        assert isinstance(approval_config.tools, dict)

    def test_tool_specific_config(self):
        """Test that tool-specific configurations can be loaded."""
        cm = ConfigManager()
        approval_config = cm.tool_approvals_config

        # Test basic structure - config may have tool-specific configs from overrides
        assert hasattr(approval_config, 'tools')
        assert isinstance(approval_config.tools, dict)

        # If there are any tool configs, verify they have the right structure
        for tool_name, tool_config in approval_config.tools.items():
            assert hasattr(tool_config, 'require_approval')
            assert hasattr(tool_config, 'allow_edit')
            assert isinstance(tool_config.require_approval, bool)
            assert isinstance(tool_config.allow_edit, bool)

    def test_config_has_boolean_default(self):
        """Test that require_approval_by_default is a boolean."""
        cm = ConfigManager()
        approval_config = cm.tool_approvals_config

        assert isinstance(approval_config.require_approval_by_default, bool)

    def test_tools_config_structure(self):
        """Test that tools in config have correct structure."""
        cm = ConfigManager()
        approval_config = cm.tool_approvals_config

        # Each tool config should have require_approval and allow_edit
        for tool_name, tool_config in approval_config.tools.items():
            assert hasattr(tool_config, 'require_approval')
            assert hasattr(tool_config, 'allow_edit')
            assert isinstance(tool_config.require_approval, bool)
            assert isinstance(tool_config.allow_edit, bool)

    def test_config_manager_provides_approvals_config(self):
        """Test that ConfigManager provides tool_approvals_config."""
        cm = ConfigManager()

        assert hasattr(cm, 'tool_approvals_config')
        assert cm.tool_approvals_config is not None

    def test_multiple_config_manager_instances(self):
        """Test that multiple ConfigManager instances can coexist."""
        cm1 = ConfigManager()
        cm2 = ConfigManager()

        config1 = cm1.tool_approvals_config
        config2 = cm2.tool_approvals_config

        # Both should have valid configs
        assert config1 is not None
        assert config2 is not None

    def test_config_contains_expected_fields(self):
        """Test that approval config has all expected fields."""
        cm = ConfigManager()
        approval_config = cm.tool_approvals_config

        # Should have these attributes
        assert hasattr(approval_config, 'require_approval_by_default')
        assert hasattr(approval_config, 'tools')

        # Types should be correct
        assert isinstance(approval_config.require_approval_by_default, bool)
        assert isinstance(approval_config.tools, dict)
