"""Selecting data sources offers ``atlas_search``; it never runs a search.

Before this, a turn carrying ``data_sources`` had its sources queried before
the model was asked anything, with the passages injected as a system message.
Search is now an ordinary tool call. The selection still decides *which*
sources that tool may read -- and it also makes the tool available, so a user
who picks a source without ticking the tool is not left with a selection that
silently does nothing.
"""

from types import SimpleNamespace

from atlas.application.chat.utilities.search_tool_selection import (
    search_tools_enabled,
    with_search_tool,
)


def _config(rag=True, tools=True):
    return SimpleNamespace(app_settings=SimpleNamespace(
        feature_rag_enabled=rag, feature_atlas_rag_tools_enabled=tools,
    ))


def test_sources_add_the_search_tool():
    assert with_search_tool(["a_tool"], ["srv:src"], _config()) == ["a_tool", "atlas_search"]


def test_no_sources_changes_nothing():
    assert with_search_tool(["a_tool"], None, _config()) == ["a_tool"]
    assert with_search_tool(["a_tool"], [], _config()) == ["a_tool"]


def test_already_selected_is_not_duplicated():
    assert with_search_tool(["atlas_search"], ["srv:src"], _config()) == ["atlas_search"]


def test_legacy_name_counts_as_already_selected():
    """A saved conversation still names the tool ``atlas_rag_query`` (pre-#855)."""
    assert with_search_tool(["atlas_rag_query"], ["srv:src"], _config()) == ["atlas_rag_query"]


def test_disabled_features_do_not_add_the_tool():
    assert with_search_tool([], ["srv:src"], _config(rag=False)) == []
    assert with_search_tool([], ["srv:src"], _config(tools=False)) == []
    assert with_search_tool([], ["srv:src"], None) == []


def test_empty_selection_still_gets_the_tool():
    assert with_search_tool(None, ["srv:src"], _config()) == ["atlas_search"]


def test_search_tools_enabled_needs_both_flags():
    assert search_tools_enabled(_config()) is True
    assert search_tools_enabled(_config(rag=False)) is False
    assert search_tools_enabled(SimpleNamespace(app_settings=None)) is False
