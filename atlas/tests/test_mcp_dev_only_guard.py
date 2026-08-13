"""Startup guard for development-only MCP servers.

file_viewer, filesystem, code-executor and transfer hand an authenticated user
arbitrary host file access or outbound requests. They ship as example configs
for local development; the risk is an operator copying one into a shared
deployment, where nothing previously objected.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from atlas.core.mcp_dev_only_guard import (
    MCPDevOnlyServerError,
    annotate_dev_only,
    enforce_dev_only_mcp_servers,
    find_enabled_dev_only_servers,
)

SAFE = {"calculator": {"command": ["python", "x.py"], "groups": ["users"]}}


def _settings(environment="production", debug_mode=False):
    return SimpleNamespace(environment=environment, debug_mode=debug_mode)


# --- detection ------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    ["file_viewer", "filesystem", "code_executor", "code-executor", "code_executor_v2", "transfer"],
)
def test_known_dangerous_servers_are_dev_only_by_default(name):
    """An operator copying an example config will not add a marker."""
    assert find_enabled_dev_only_servers({name: {"command": ["x"]}}) == [name]


def test_hyphen_and_underscore_are_equivalent():
    assert find_enabled_dev_only_servers({"code-executor": {}}) == ["code-executor"]
    assert find_enabled_dev_only_servers({"CODE_EXECUTOR": {}}) == ["CODE_EXECUTOR"]


def test_ordinary_servers_are_not_flagged():
    assert find_enabled_dev_only_servers(SAFE) == []


def test_explicit_marker_flags_any_server():
    assert find_enabled_dev_only_servers(
        {"my_custom_tool": {"dev_only": True}}
    ) == ["my_custom_tool"]


def test_explicit_false_opts_a_known_server_out():
    """An operator who has reviewed and hardened one keeps an escape hatch."""
    assert find_enabled_dev_only_servers({"filesystem": {"dev_only": False}}) == []


def test_disabled_servers_are_ignored():
    assert find_enabled_dev_only_servers({"file_viewer": {"enabled": False}}) == []


def test_non_mapping_entries_do_not_crash_detection():
    assert find_enabled_dev_only_servers({"weird": "not-a-dict"}) == []


def test_empty_and_missing_config():
    assert find_enabled_dev_only_servers({}) == []
    assert find_enabled_dev_only_servers(None) == []


# --- enforcement ----------------------------------------------------------

def test_production_refuses_to_start():
    with pytest.raises(MCPDevOnlyServerError, match="file_viewer"):
        enforce_dev_only_mcp_servers({"file_viewer": {}}, _settings())


def test_debug_off_alone_is_enough_to_refuse():
    """A deployment that sets only DEBUG_MODE=false is still covered."""
    with pytest.raises(MCPDevOnlyServerError):
        enforce_dev_only_mcp_servers(
            {"filesystem": {}}, _settings(environment="staging", debug_mode=False)
        )


def test_environment_production_alone_is_enough_to_refuse():
    """As is one that sets only ENVIRONMENT=production."""
    with pytest.raises(MCPDevOnlyServerError):
        enforce_dev_only_mcp_servers(
            {"filesystem": {}}, _settings(environment="production", debug_mode=True)
        )


def test_development_is_allowed_with_a_warning(caplog):
    with caplog.at_level("WARNING"):
        enforce_dev_only_mcp_servers(
            {"file_viewer": {}}, _settings(environment="development", debug_mode=True)
        )
    assert "file_viewer" in caplog.text


def test_safe_config_starts_in_production():
    enforce_dev_only_mcp_servers(SAFE, _settings())


def test_error_names_every_offender():
    with pytest.raises(MCPDevOnlyServerError) as excinfo:
        enforce_dev_only_mcp_servers(
            {"file_viewer": {}, "transfer": {}, "calculator": {}}, _settings()
        )
    message = str(excinfo.value)
    assert "file_viewer" in message and "transfer" in message
    assert "calculator" not in message


# --- annotation -----------------------------------------------------------

def test_annotate_marks_each_server_explicitly():
    annotated = annotate_dev_only({"file_viewer": {}, "calculator": {}})
    assert annotated["file_viewer"]["dev_only"] is True
    assert annotated["calculator"]["dev_only"] is False


def test_annotate_preserves_existing_keys():
    annotated = annotate_dev_only({"calculator": {"groups": ["users"]}})
    assert annotated["calculator"]["groups"] == ["users"]
