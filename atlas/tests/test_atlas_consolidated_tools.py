"""The consolidated built-in ``atlas`` server (issue #855).

Canvas, sleep and search used to be three pseudo-servers. These tests pin the
consolidated surface: one server, three tools, single-argument search, and the
old fully-qualified names still resolving so persisted selections and saved
conversations keep working.
"""

import pytest

from atlas.domain.messages.models import ToolCall
from atlas.modules.mcp_tools.atlas_server import (
    ATLAS_SERVER_NAME,
    CANVAS_TOOL_NAME,
    SEARCH_TOOL_NAME,
    SLEEP_TOOL_NAME,
    atlas_tool_schemas,
    is_atlas_tool,
    normalize_tool_name,
    normalize_tool_names,
)
from atlas.modules.mcp_tools.client import MCPToolManager


def _manager() -> MCPToolManager:
    return MCPToolManager(config_path="/tmp/atlas-noop-mcp.json")


@pytest.mark.parametrize(
    "legacy, expected",
    [
        ("canvas_canvas", CANVAS_TOOL_NAME),
        ("atlas_agent_sleep", SLEEP_TOOL_NAME),
        ("atlas_rag_query", SEARCH_TOOL_NAME),
    ],
)
def test_legacy_names_resolve_to_consolidated_tools(legacy, expected):
    assert normalize_tool_name(legacy) == expected
    assert is_atlas_tool(legacy)


def test_unrelated_names_pass_through_untouched():
    assert normalize_tool_name("pptx_generator_create") == "pptx_generator_create"
    assert not is_atlas_tool("pptx_generator_create")
    # Tool calls carry model-supplied names, which need not be strings.
    assert normalize_tool_name(None) is None
    assert not is_atlas_tool(None)


def test_normalize_tool_names_dedupes_aliases():
    assert normalize_tool_names(["canvas_canvas", CANVAS_TOOL_NAME, "math_add"]) == [
        CANVAS_TOOL_NAME,
        "math_add",
    ]
    assert normalize_tool_names(None) == []


def test_search_takes_only_a_query():
    """Sources, identity and mode are server-side; the model picks the query."""
    (schema,) = atlas_tool_schemas([SEARCH_TOOL_NAME])
    params = schema["function"]["parameters"]

    assert list(params["properties"]) == ["query"]
    assert params["required"] == ["query"]


def test_disabled_built_ins_are_omitted_from_the_schema():
    names = [
        schema["function"]["name"]
        for schema in atlas_tool_schemas(
            [CANVAS_TOOL_NAME, SLEEP_TOOL_NAME, SEARCH_TOOL_NAME],
            sleep_enabled=False,
            search_enabled=False,
        )
    ]

    assert names == [CANVAS_TOOL_NAME]


def test_all_built_ins_report_the_single_atlas_server():
    manager = _manager()

    for name in (CANVAS_TOOL_NAME, SLEEP_TOOL_NAME, SEARCH_TOOL_NAME):
        assert manager.get_server_for_tool(name) == ATLAS_SERVER_NAME
    assert manager.get_server_for_tool("canvas_canvas") == ATLAS_SERVER_NAME


@pytest.mark.asyncio
async def test_canvas_executes_under_both_names():
    """A saved conversation replaying ``canvas_canvas`` must still render."""
    manager = _manager()

    for name in (CANVAS_TOOL_NAME, "canvas_canvas"):
        result = await manager.execute_tool(
            ToolCall(id="call-1", name=name, arguments={"content": "# Report"}),
            {"user_email": "user@example.com"},
        )
        assert result.success
        assert "# Report" in result.content
