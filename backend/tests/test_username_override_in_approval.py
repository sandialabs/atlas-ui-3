"""Tests for username override security in tool approval flow."""


from unittest.mock import Mock

from application.chat.utilities.tool_executor import inject_context_into_args, _filter_args_to_schema


class TestUsernameOverrideInApproval:
    """Test that username override cannot be bypassed through approval argument editing."""

    def test_username_override_after_user_edit(self):
        """Test that username is re-injected even after user edits it during approval."""
        # Setup session context with authenticated user
        session_context = {
            "user_email": "alice@example.com",
            "files": {}
        }

        # Simulate user editing username to a different value during approval
        user_edited_args = {
            "username": "malicious@example.com",  # User tried to change this
            "data": "test data"
        }

        # Mock tool manager that indicates tool accepts username
        mock_tool_manager = Mock()
        mock_tool_manager.get_tools_schema.return_value = [{
            "function": {
                "name": "create_record",
                "parameters": {
                    "properties": {
                        "username": {"type": "string"},
                        "data": {"type": "string"}
                    }
                }
            }
        }]

        # Re-inject context (simulating what should happen after user approval)
        re_injected_args = inject_context_into_args(
            user_edited_args,
            session_context,
            "create_record",
            mock_tool_manager
        )

        # Verify username was overridden back to authenticated user
        assert re_injected_args["username"] == "alice@example.com"
        assert re_injected_args["data"] == "test data"

        # Re-filter to schema to simulate complete flow
        filtered_args = _filter_args_to_schema(
            re_injected_args,
            "create_record",
            mock_tool_manager
        )

        # Final result should have correct username
        assert filtered_args["username"] == "alice@example.com"

    def test_username_override_with_tool_that_doesnt_accept_username(self):
        """Test that username is not injected for tools that don't accept it."""
        session_context = {
            "user_email": "alice@example.com",
            "files": {}
        }

        user_edited_args = {
            "query": "test query"
        }

        # Mock tool manager that indicates tool does NOT accept username
        mock_tool_manager = Mock()
        mock_tool_manager.get_tools_schema.return_value = [{
            "function": {
                "name": "search",
                "parameters": {
                    "properties": {
                        "query": {"type": "string"}
                    }
                }
            }
        }]

        # Inject context
        re_injected_args = inject_context_into_args(
            user_edited_args,
            session_context,
            "search",
            mock_tool_manager
        )

        # Verify username was NOT injected
        assert "username" not in re_injected_args
        assert re_injected_args["query"] == "test query"

    def test_username_override_with_no_tool_manager(self):
        """Test username injection when no tool manager is available (fallback)."""
        session_context = {
            "user_email": "bob@example.com",
            "files": {}
        }

        user_edited_args = {
            "data": "some data"
        }

        # Inject context with no tool manager (fallback mode)
        re_injected_args = inject_context_into_args(
            user_edited_args,
            session_context,
            "some_tool",
            None  # No tool manager
        )

        # Should still inject username in fallback mode
        assert re_injected_args["username"] == "bob@example.com"
        assert re_injected_args["data"] == "some data"

    def test_multiple_security_injections_after_edit(self):
        """Test that multiple security-critical parameters are protected."""
        session_context = {
            "user_email": "secure_user@example.com",
            "files": {
                "test.pdf": {"key": "file_key_123"}
            }
        }

        # User tries to edit both username and filename details
        user_edited_args = {
            "username": "hacked@example.com",  # Should be overridden
            "filename": "test.pdf",  # Valid filename
            "data": "edited data"
        }

        mock_tool_manager = Mock()
        mock_tool_manager.get_tools_schema.return_value = [{
            "function": {
                "name": "process_file",
                "parameters": {
                    "properties": {
                        "username": {"type": "string"},
                        "filename": {"type": "string"},
                        "data": {"type": "string"}
                    }
                }
            }
        }]

        re_injected_args = inject_context_into_args(
            user_edited_args,
            session_context,
            "process_file",
            mock_tool_manager
        )

        # Username should be corrected
        assert re_injected_args["username"] == "secure_user@example.com"
        # File handling should work normally
        assert "original_filename" in re_injected_args
        assert re_injected_args["original_filename"] == "test.pdf"
        # Data remains as user edited
        assert re_injected_args["data"] == "edited data"

    def test_prevented_impersonation_attack(self):
        """Test specific impersonation attack scenario from vulnerability."""
        session_context = {
            "user_email": "alice@example.com",
            "files": {}
        }

        # User (alice) tries to impersonate admin via approval dialog
        user_edited_args = {
            "username": "admin@example.com",  # Impersonation attempt
            "action": "delete_all_data"
        }

        mock_tool_manager = Mock()
        mock_tool_manager.get_tools_schema.return_value = [{
            "function": {
                "name": "admin_action",
                "parameters": {
                    "properties": {
                        "username": {"type": "string"},
                        "action": {"type": "string"}
                    }
                }
            }
        }]

        # Re-inject context (the security fix)
        re_injected_args = inject_context_into_args(
            user_edited_args,
            session_context,
            "admin_action",
            mock_tool_manager
        )

        # Re-filter for complete security
        filtered_args = _filter_args_to_schema(
            re_injected_args,
            "admin_action",
            mock_tool_manager
        )

        # Security enforced: attack prevented
        assert filtered_args["username"] == "alice@example.com"  # Not admin
        assert filtered_args["action"] == "delete_all_data"  # Non-security param unchanged

    def test_schema_filtering_preserves_security_injection(self):
        """Test that schema filtering works correctly with re-injected arguments."""
        session_context = {
            "user_email": "secure@example.com",
            "files": {}
        }

        # User tries to add schema-violating parameters
        user_edited_args = {
            "username": "hacked@example.com",
            "data": "legitimate data",
            "extra_param": "should_be_removed"  # Not in schema
        }

        mock_tool_manager = Mock()
        mock_tool_manager.get_tools_schema.return_value = [{
            "function": {
                "name": "limited_tool",
                "parameters": {
                    "properties": {
                        "username": {"type": "string"},
                        "data": {"type": "string"}
                        # extra_param is NOT in schema
                    }
                }
            }
        }]

        # Re-inject and re-filter (complete security flow)
        re_injected_args = inject_context_into_args(
            user_edited_args,
            session_context,
            "limited_tool",
            mock_tool_manager
        )

        filtered_args = _filter_args_to_schema(
            re_injected_args,
            "limited_tool",
            mock_tool_manager
        )

        # Correct username enforced
        assert filtered_args["username"] == "secure@example.com"
        # Legitimate data preserved
        assert filtered_args["data"] == "legitimate data"
        # Schema violation removed
        assert "extra_param" not in filtered_args
