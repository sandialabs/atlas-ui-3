"""The consolidated built-in ``atlas`` server (issue #855).

Canvas, sleep, search and source discovery used to be spread across three
pseudo-servers. These tests pin the consolidated surface: one server, four
tools, a search whose only required argument is the query, and the old
fully-qualified names still resolving so persisted selections and saved
conversations keep working.
"""

import pytest

from atlas.domain.messages.models import ToolCall
from atlas.modules.mcp_tools.atlas_server import (
    ATLAS_SERVER_NAME,
    CANVAS_TOOL_NAME,
    DISCOVER_TOOL_NAME,
    MAX_SEARCH_RESULTS,
    SEARCH_TOOL_NAME,
    SLEEP_TOOL_NAME,
    atlas_tool_schemas,
    is_atlas_tool,
    normalize_tool_name,
    normalize_tool_names,
    search_kwargs_for,
)
from atlas.modules.mcp_tools.client import MCPToolManager, _drop_reserved_servers
from atlas.hooks.models import HookConfig


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

    assert list(params["properties"]) == ["query", "max_results", "depth"]
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


def test_a_hook_matcher_written_against_the_old_name_still_fires():
    """Renaming a tool must never silently retire an operator's deny policy."""
    hook = HookConfig(
        name="block-canvas",
        event="PreToolUse",
        command=["true"],
        matcher="^canvas_canvas$",
    )

    assert hook.matches(CANVAS_TOOL_NAME)
    assert hook.matches("canvas_canvas")
    assert not hook.matches("pptx_generator_create")


def test_a_hook_matcher_on_the_new_name_does_not_widen():
    """Alias matching runs one way: the old spelling is not a new wildcard."""
    hook = HookConfig(
        name="watch-canvas",
        event="PreToolUse",
        command=["true"],
        matcher=f"^{CANVAS_TOOL_NAME}$",
    )

    assert hook.matches(CANVAS_TOOL_NAME)
    assert not hook.matches("canvas_canvas")


def test_reserved_server_names_are_dropped_from_mcp_config():
    """A configured server named ``atlas`` would be shadowed, not merged."""
    kept = _drop_reserved_servers({
        "atlas": {"url": "http://example"},
        "canvas": {"url": "http://example"},
        "pptx_generator": {"url": "http://example"},
    })

    assert list(kept) == ["pptx_generator"]


def test_drop_reserved_servers_leaves_a_clean_config_untouched():
    config = {"pptx_generator": {"url": "http://example"}}

    assert _drop_reserved_servers(config) is config


def test_discover_sources_is_advertised_again_under_the_atlas_server():
    """It answers "which corpus?" and "is this even indexed?" -- both worth a tool."""
    schemas = atlas_tool_schemas([DISCOVER_TOOL_NAME])

    assert [schema["function"]["name"] for schema in schemas] == [DISCOVER_TOOL_NAME]
    # No arguments at all: the authenticated user decides the answer, and the
    # user is never a model input.
    assert schemas[0]["function"]["parameters"]["properties"] == {}


def test_the_legacy_discover_name_resolves_to_the_new_one():
    assert normalize_tool_name("atlas_rag_discover_data_sources") == DISCOVER_TOOL_NAME
    assert is_atlas_tool("atlas_rag_discover_data_sources")


def test_discovery_is_gated_with_search():
    """Both read the RAG sources, so one flag governs both."""
    assert atlas_tool_schemas([DISCOVER_TOOL_NAME], search_enabled=False) == []


def test_depth_and_max_results_map_onto_v2_search_kwargs():
    assert search_kwargs_for(depth="quick")["rerank"] is False
    assert search_kwargs_for(depth="deep")["top_k_vector"] == 20
    assert search_kwargs_for(max_results=7)["top_k_final"] == 7
    # ``standard`` alone changes nothing, leaving the source's own config in
    # charge rather than pinning it to our idea of a default.
    assert search_kwargs_for(depth="standard") is None
    assert search_kwargs_for() is None


def test_model_supplied_search_knobs_are_coerced_and_clamped():
    """Both values come from the model, so nothing unvetted reaches the backend."""
    # Out of range is clamped, not rejected: the call still returns evidence.
    assert search_kwargs_for(max_results=9999)["top_k_final"] == MAX_SEARCH_RESULTS
    assert search_kwargs_for(max_results=0)["top_k_final"] == 1
    # A number sent as a string is a common model slip and is worth accepting.
    assert search_kwargs_for(max_results="5")["top_k_final"] == 5
    # Nonsense degrades to the source default instead of being forwarded.
    assert search_kwargs_for(max_results="lots") is None
    assert search_kwargs_for(max_results=True) is None
    assert search_kwargs_for(depth="exhaustive") is None


def test_the_server_label_under_a_tool_call_is_asked_for_not_guessed():
    """Splitting on the last underscore mislabels any multi-word tool name."""
    from atlas.application.chat.utilities.event_notifier import _server_name_for_display

    manager = _manager()

    assert _server_name_for_display(DISCOVER_TOOL_NAME, manager) == ATLAS_SERVER_NAME
    assert _server_name_for_display(SEARCH_TOOL_NAME, manager) == ATLAS_SERVER_NAME
    # Without a manager the old guess still applies -- it is a display label,
    # never a lookup, so a wrong fallback must not fail the call.
    assert _server_name_for_display(DISCOVER_TOOL_NAME) == "atlas_discover"
    assert _server_name_for_display("noserver") == "unknown"
