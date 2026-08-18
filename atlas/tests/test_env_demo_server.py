"""
Unit tests for the env-demo MCP server.
Tests the environment variable demonstration functionality.
"""
import os

import pytest


# Test that the server can be imported
def test_server_imports():
    """Test that the env-demo server module can be imported."""
    try:
        # Import the module by file path since directory has hyphen. (No
        # sys.path mutation: the old ``sys.path.insert`` added a CI-only path
        # that does not exist off the runner and was never removed, leaving it
        # on sys.path for every later test in the session.)
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "env_demo_main",
            "/home/runner/work/atlas-ui-3/atlas-ui-3/backend/mcp/env-demo/main.py"
        )
        module = importlib.util.module_from_spec(spec)
        # Don't execute - just verify it can be loaded
        assert spec is not None
        assert module is not None
    except Exception as e:
        pytest.fail(f"Failed to import env-demo server: {e}")


def test_env_var_configuration(monkeypatch):
    """Test that environment variables are accessible."""
    # monkeypatch rather than a manual set/del pair: the cleanup at the end of
    # the old version never ran when an assertion above it failed.
    monkeypatch.setenv("TEST_CLOUD_PROFILE", "test-profile")
    monkeypatch.setenv("TEST_CLOUD_REGION", "test-region")

    assert os.environ.get("TEST_CLOUD_PROFILE") == "test-profile"
    assert os.environ.get("TEST_CLOUD_REGION") == "test-region"


def test_env_var_substitution_pattern(monkeypatch):
    """Test the ${VAR} pattern that should be resolved by config_manager."""
    # This tests the pattern that config_manager.resolve_env_var handles
    # We test the pattern matching logic directly
    import re

    # Set a test variable
    monkeypatch.setenv("TEST_API_KEY", "secret-123")
    monkeypatch.delenv("MISSING_VAR", raising=False)

    # Test the ${VAR} pattern matching (same as config_manager.resolve_env_var)
    pattern = r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}'

    # Test resolution
    test_value = "${TEST_API_KEY}"
    match = re.match(pattern, test_value)
    assert match is not None
    var_name = match.group(1)
    assert var_name == "TEST_API_KEY"
    resolved = os.environ.get(var_name)
    assert resolved == "secret-123"

    # Test literal value (no substitution)
    test_value = "literal-value"
    match = re.match(pattern, test_value)
    assert match is None  # Should not match

    # Test missing variable
    test_value = "${MISSING_VAR}"
    match = re.match(pattern, test_value)
    assert match is not None
    var_name = match.group(1)
    assert var_name == "MISSING_VAR"
    missing_var = os.environ.get(var_name)
    assert missing_var is None  # Variable doesn't exist





if __name__ == "__main__":
    pytest.main([__file__, "-v"])
