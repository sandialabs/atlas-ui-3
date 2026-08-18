"""
Unit tests for the env-demo MCP server.
Tests the environment variable demonstration functionality.
"""
import os
from pathlib import Path

import pytest

ENV_DEMO_MAIN = (
    Path(__file__).resolve().parents[1] / "mcp" / "env-demo" / "main.py"
)


def test_server_imports():
    """The env-demo server module must actually import.

    The previous version pointed at a hard-coded ``/home/runner/work/...`` path
    and only built a spec -- ``spec_from_file_location`` does not stat the file
    and nothing executed the module, so the test passed everywhere regardless
    of whether the server existed or was importable.
    """
    if not ENV_DEMO_MAIN.exists():
        pytest.skip(f"env-demo server not present at {ENV_DEMO_MAIN}")

    import importlib.util

    spec = importlib.util.spec_from_file_location("env_demo_main", ENV_DEMO_MAIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        pytest.fail(f"Failed to import env-demo server: {e}")

    assert hasattr(module, "mcp"), "env-demo server must expose an MCP server object"


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
