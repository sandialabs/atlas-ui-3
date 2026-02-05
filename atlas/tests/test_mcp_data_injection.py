"""
Tests for _mcp_data injection into MCP tool arguments.

Validates that tools declaring an _mcp_data parameter in their schema
receive structured metadata about all available MCP tools, following
the same pattern as the username injection feature.
"""

from unittest.mock import MagicMock

from atlas.application.chat.utilities.tool_executor import (
    tool_accepts_mcp_data,
    build_mcp_data,
    inject_context_into_args,
)


class FakeTool:
    """Minimal tool object matching the MCP tool interface."""

    def __init__(self, name, description="", inputSchema=None):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema or {"type": "object", "properties": {}}


def _make_tool_manager(available_tools=None, schema_override=None):
    """Create a mock tool manager with the given available_tools dict."""
    manager = MagicMock()
    manager.available_tools = available_tools or {}

    def get_tools_schema(tool_names):
        if schema_override is not None:
            return schema_override
        schemas = []
        for server_name, server_data in manager.available_tools.items():
            for tool in server_data.get("tools", []):
                fq_name = f"{server_name}_{tool.name}"
                if fq_name in tool_names:
                    schemas.append({
                        "type": "function",
                        "function": {
                            "name": fq_name,
                            "description": tool.description or "",
                            "parameters": tool.inputSchema or {},
                        },
                    })
        return schemas

    manager.get_tools_schema = MagicMock(side_effect=get_tools_schema)
    return manager


# -- tool_accepts_mcp_data tests --


class TestToolAcceptsMcpData:
    """Tests for tool_accepts_mcp_data detection function."""

    def test_returns_true_when_schema_has_mcp_data(self):
        tool = FakeTool(
            "planner",
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "_mcp_data": {"type": "object"},
                },
            },
        )
        manager = _make_tool_manager({"demo": {"tools": [tool], "config": {}}})
        assert tool_accepts_mcp_data("demo_planner", manager) is True

    def test_returns_false_when_schema_lacks_mcp_data(self):
        tool = FakeTool(
            "search",
            inputSchema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        )
        manager = _make_tool_manager({"demo": {"tools": [tool], "config": {}}})
        assert tool_accepts_mcp_data("demo_search", manager) is False

    def test_returns_false_with_no_tool_name(self):
        assert tool_accepts_mcp_data("", MagicMock()) is False

    def test_returns_false_with_no_tool_manager(self):
        assert tool_accepts_mcp_data("some_tool", None) is False

    def test_returns_false_when_schema_lookup_fails(self):
        manager = MagicMock()
        manager.get_tools_schema = MagicMock(side_effect=RuntimeError("fail"))
        assert tool_accepts_mcp_data("any_tool", manager) is False


# -- build_mcp_data tests --


class TestBuildMcpData:
    """Tests for build_mcp_data output structure."""

    def test_returns_empty_when_no_tools(self):
        manager = _make_tool_manager({})
        result = build_mcp_data(manager)
        assert result == {"available_servers": []}

    def test_returns_empty_when_no_manager(self):
        result = build_mcp_data(None)
        assert result == {"available_servers": []}

    def test_skips_canvas_server(self):
        tool = FakeTool("canvas")
        manager = _make_tool_manager({
            "canvas": {"tools": [tool], "config": {}},
        })
        result = build_mcp_data(manager)
        assert result["available_servers"] == []

    def test_includes_server_and_tool_metadata(self):
        tool_a = FakeTool(
            "search",
            description="Search documents",
            inputSchema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        )
        tool_b = FakeTool("list", description="List items")
        manager = _make_tool_manager({
            "myserver": {
                "tools": [tool_a, tool_b],
                "config": {"description": "My Server"},
            },
        })

        result = build_mcp_data(manager)
        assert len(result["available_servers"]) == 1
        server = result["available_servers"][0]
        assert server["server_name"] == "myserver"
        assert server["description"] == "My Server"
        assert len(server["tools"]) == 2

        tool_entry = server["tools"][0]
        assert tool_entry["name"] == "myserver_search"
        assert tool_entry["description"] == "Search documents"
        assert "properties" in tool_entry["parameters"]

    def test_multiple_servers(self):
        tool1 = FakeTool("t1")
        tool2 = FakeTool("t2")
        manager = _make_tool_manager({
            "server_a": {"tools": [tool1], "config": {}},
            "server_b": {"tools": [tool2], "config": {}},
        })
        result = build_mcp_data(manager)
        names = [s["server_name"] for s in result["available_servers"]]
        assert "server_a" in names
        assert "server_b" in names

    def test_handles_missing_description(self):
        tool = FakeTool("t", description=None, inputSchema=None)
        manager = _make_tool_manager({
            "s": {"tools": [tool], "config": {}},
        })
        result = build_mcp_data(manager)
        server = result["available_servers"][0]
        assert server["description"] == ""
        assert server["tools"][0]["description"] == ""
        assert server["tools"][0]["parameters"] == {"type": "object", "properties": {}}

    def test_uses_short_description_fallback(self):
        tool = FakeTool("t")
        manager = _make_tool_manager({
            "s": {"tools": [tool], "config": {"short_description": "Short desc"}},
        })
        result = build_mcp_data(manager)
        assert result["available_servers"][0]["description"] == "Short desc"


# -- inject_context_into_args with _mcp_data tests --


class TestInjectMcpData:
    """Tests for _mcp_data injection in inject_context_into_args."""

    def test_injects_mcp_data_when_tool_accepts_it(self):
        tool = FakeTool(
            "planner",
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "_mcp_data": {"type": "object"},
                },
            },
        )
        manager = _make_tool_manager({
            "demo": {"tools": [tool], "config": {"description": "Demo"}},
        })

        result = inject_context_into_args(
            {"task": "do something"},
            {"user_email": "user@test.com"},
            "demo_planner",
            manager,
        )

        assert "_mcp_data" in result
        assert "available_servers" in result["_mcp_data"]
        assert len(result["_mcp_data"]["available_servers"]) == 1

    def test_does_not_inject_mcp_data_when_tool_lacks_param(self):
        tool = FakeTool(
            "search",
            inputSchema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        )
        manager = _make_tool_manager({
            "demo": {"tools": [tool], "config": {}},
        })

        result = inject_context_into_args(
            {"query": "hello"},
            {"user_email": "user@test.com"},
            "demo_search",
            manager,
        )

        assert "_mcp_data" not in result

    def test_mcp_data_reinjected_after_edit(self):
        """Simulates the re-injection path after user edits tool arguments."""
        tool = FakeTool(
            "planner",
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "_mcp_data": {"type": "object"},
                },
            },
        )
        manager = _make_tool_manager({
            "demo": {"tools": [tool], "config": {}},
        })

        # Simulate user editing args (removing _mcp_data)
        edited_args = {"task": "edited task"}
        result = inject_context_into_args(
            edited_args,
            {"user_email": "user@test.com"},
            "demo_planner",
            manager,
        )

        # _mcp_data should be re-injected
        assert "_mcp_data" in result
        assert result["task"] == "edited task"

    def test_mcp_data_not_injected_without_tool_manager(self):
        result = inject_context_into_args(
            {"task": "test"},
            {"user_email": "user@test.com"},
            "some_tool",
            None,
        )
        assert "_mcp_data" not in result
