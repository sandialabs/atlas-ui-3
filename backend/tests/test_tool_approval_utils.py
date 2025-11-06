"""Tests for tool approval utilities in tool_utils.py"""

from unittest.mock import Mock
from application.chat.utilities.tool_utils import (
    requires_approval,
    tool_accepts_username,
    _sanitize_args_for_ui,
    _filter_args_to_schema
)


class MockToolConfig:
    """Mock tool configuration."""
    def __init__(self, require_approval, allow_edit):
        self.require_approval = require_approval
        self.allow_edit = allow_edit


class MockApprovalsConfig:
    """Mock approvals configuration."""
    def __init__(self, require_by_default=True, tools=None):
        self.require_approval_by_default = require_by_default
        self.tools = tools or {}


class TestRequiresApproval:
    """Test the requires_approval function."""

    def test_requires_approval_no_config_manager(self):
        """Test requires_approval with no config manager."""
        needs_approval, allow_edit, admin_required = requires_approval("test_tool", None)

        assert needs_approval is True
        assert allow_edit is True
        assert admin_required is False

    def test_requires_approval_tool_specific_config(self):
        """Test requires_approval with tool-specific configuration."""
        config_manager = Mock()
        config_manager.tool_approvals_config = MockApprovalsConfig(
            require_by_default=False,
            tools={
                "dangerous_tool": MockToolConfig(require_approval=True, allow_edit=False)
            }
        )

        needs_approval, allow_edit, admin_required = requires_approval("dangerous_tool", config_manager)

        assert needs_approval is True
        assert allow_edit is False
        assert admin_required is True

    def test_requires_approval_default_true(self):
        """Test requires_approval with default set to require approval."""
        config_manager = Mock()
        config_manager.tool_approvals_config = MockApprovalsConfig(
            require_by_default=True,
            tools={}
        )

        needs_approval, allow_edit, admin_required = requires_approval("any_tool", config_manager)

        assert needs_approval is True
        assert allow_edit is True
        assert admin_required is True

    def test_requires_approval_default_false(self):
        """Test requires_approval with default set to not require approval."""
        config_manager = Mock()
        config_manager.tool_approvals_config = MockApprovalsConfig(
            require_by_default=False,
            tools={}
        )

        needs_approval, allow_edit, admin_required = requires_approval("any_tool", config_manager)

        # Default is False but function returns True with user-level approval
        assert needs_approval is True
        assert allow_edit is True
        assert admin_required is False

    def test_requires_approval_exception_handling(self):
        """Test requires_approval handles exceptions gracefully."""
        config_manager = Mock()
        config_manager.tool_approvals_config = None

        # Should not raise, should return default
        needs_approval, allow_edit, admin_required = requires_approval("test_tool", config_manager)

        assert needs_approval is True
        assert allow_edit is True
        assert admin_required is False

    def test_requires_approval_multiple_tools(self):
        """Test requires_approval with multiple tool configurations."""
        config_manager = Mock()
        config_manager.tool_approvals_config = MockApprovalsConfig(
            require_by_default=False,
            tools={
                "tool_a": MockToolConfig(require_approval=True, allow_edit=True),
                "tool_b": MockToolConfig(require_approval=True, allow_edit=False),
                "tool_c": MockToolConfig(require_approval=False, allow_edit=True)
            }
        )

        # Tool A
        needs_approval, allow_edit, admin_required = requires_approval("tool_a", config_manager)
        assert needs_approval is True
        assert allow_edit is True
        assert admin_required is True

        # Tool B
        needs_approval, allow_edit, admin_required = requires_approval("tool_b", config_manager)
        assert needs_approval is True
        assert allow_edit is False
        assert admin_required is True

        # Tool C
        needs_approval, allow_edit, admin_required = requires_approval("tool_c", config_manager)
        assert needs_approval is False
        assert allow_edit is True
        assert admin_required is True


class TestToolAcceptsUsername:
    """Test the tool_accepts_username function."""

    def test_tool_accepts_username_true(self):
        """Test tool that accepts username parameter."""
        tool_manager = Mock()
        tool_manager.get_tools_schema.return_value = [
            {
                "function": {
                    "name": "test_tool",
                    "parameters": {
                        "properties": {
                            "username": {"type": "string"},
                            "other_param": {"type": "string"}
                        }
                    }
                }
            }
        ]

        result = tool_accepts_username("test_tool", tool_manager)
        assert result is True

    def test_tool_accepts_username_false(self):
        """Test tool that does not accept username parameter."""
        tool_manager = Mock()
        tool_manager.get_tools_schema.return_value = [
            {
                "function": {
                    "name": "test_tool",
                    "parameters": {
                        "properties": {
                            "other_param": {"type": "string"}
                        }
                    }
                }
            }
        ]

        result = tool_accepts_username("test_tool", tool_manager)
        assert result is False

    def test_tool_accepts_username_no_tool_manager(self):
        """Test with no tool manager."""
        result = tool_accepts_username("test_tool", None)
        assert result is False

    def test_tool_accepts_username_no_schema(self):
        """Test when tool schema is not found."""
        tool_manager = Mock()
        tool_manager.get_tools_schema.return_value = []

        result = tool_accepts_username("test_tool", tool_manager)
        assert result is False

    def test_tool_accepts_username_exception(self):
        """Test exception handling."""
        tool_manager = Mock()
        tool_manager.get_tools_schema.side_effect = Exception("Schema error")

        result = tool_accepts_username("test_tool", tool_manager)
        assert result is False


class TestSanitizeArgsForUI:
    """Test the _sanitize_args_for_ui function."""

    def test_sanitize_simple_args(self):
        """Test sanitizing simple arguments."""
        args = {"param1": "value1", "param2": "value2"}
        result = _sanitize_args_for_ui(args)

        assert result == args

    def test_sanitize_filename(self):
        """Test sanitizing filename with URL."""
        args = {"filename": "http://example.com/path/file.txt?token=secret"}
        result = _sanitize_args_for_ui(args)

        # Should extract just the filename
        assert "token" not in result["filename"]
        assert "file.txt" in result["filename"]

    def test_sanitize_file_names_list(self):
        """Test sanitizing list of filenames."""
        args = {
            "file_names": [
                "http://example.com/file1.txt?token=abc",
                "http://example.com/file2.txt?token=def"
            ]
        }
        result = _sanitize_args_for_ui(args)

        assert len(result["file_names"]) == 2
        for filename in result["file_names"]:
            assert "token" not in filename

    def test_sanitize_file_url(self):
        """Test sanitizing file_url field."""
        args = {"file_url": "http://example.com/path/file.txt?token=secret"}
        result = _sanitize_args_for_ui(args)

        assert "token" not in result["file_url"]

    def test_sanitize_file_urls_list(self):
        """Test sanitizing file_urls list."""
        args = {
            "file_urls": [
                "http://example.com/file1.txt?token=abc",
                "http://example.com/file2.txt?token=def"
            ]
        }
        result = _sanitize_args_for_ui(args)

        assert len(result["file_urls"]) == 2
        for url in result["file_urls"]:
            assert "token" not in url

    def test_sanitize_mixed_args(self):
        """Test sanitizing mixed arguments."""
        args = {
            "filename": "http://example.com/file.txt?token=secret",
            "other_param": "normal_value",
            "file_names": ["file1.txt", "file2.txt"]
        }
        result = _sanitize_args_for_ui(args)

        assert "token" not in result["filename"]
        assert result["other_param"] == "normal_value"
        assert len(result["file_names"]) == 2


class TestFilterArgsToSchema:
    """Test the _filter_args_to_schema function."""

    def test_filter_with_schema(self):
        """Test filtering arguments with available schema."""
        tool_manager = Mock()
        tool_manager.get_tools_schema.return_value = [
            {
                "function": {
                    "name": "test_tool",
                    "parameters": {
                        "properties": {
                            "allowed_param": {"type": "string"},
                            "another_param": {"type": "number"}
                        }
                    }
                }
            }
        ]

        args = {
            "allowed_param": "value",
            "another_param": 42,
            "original_filename": "old.txt",
            "file_url": "http://example.com/file.txt",
            "extra_param": "should_be_removed"
        }

        result = _filter_args_to_schema(args, "test_tool", tool_manager)

        assert "allowed_param" in result
        assert "another_param" in result
        assert "original_filename" not in result
        assert "file_url" not in result
        assert "extra_param" not in result

    def test_filter_without_schema(self):
        """Test filtering when schema is unavailable."""
        tool_manager = Mock()
        tool_manager.get_tools_schema.return_value = []

        args = {
            "param": "value",
            "original_filename": "old.txt",
            "file_url": "http://example.com/file.txt"
        }

        result = _filter_args_to_schema(args, "test_tool", tool_manager)

        # Should keep param but drop original_* and file_url(s)
        assert "param" in result
        assert "original_filename" not in result
        assert "file_url" not in result

    def test_filter_no_tool_manager(self):
        """Test filtering with no tool manager."""
        args = {
            "param": "value",
            "original_something": "should_be_removed",
            "file_urls": ["url1", "url2"]
        }

        result = _filter_args_to_schema(args, "test_tool", None)

        assert "param" in result
        assert "original_something" not in result
        assert "file_urls" not in result

    def test_filter_exception_handling(self):
        """Test filtering handles exceptions gracefully."""
        tool_manager = Mock()
        tool_manager.get_tools_schema.side_effect = Exception("Schema error")

        args = {
            "param": "value",
            "original_param": "remove_me"
        }

        result = _filter_args_to_schema(args, "test_tool", tool_manager)

        # Should fall back to conservative filtering
        assert "param" in result
        assert "original_param" not in result
