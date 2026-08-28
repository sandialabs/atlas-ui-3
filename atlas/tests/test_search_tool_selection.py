"""Selecting data sources offers ``atlas_search``; it never runs a search.

Before this, a turn carrying ``data_sources`` had its sources queried before
the model was asked anything, with the passages injected as a system message.
Search is now an ordinary tool call. The selection still decides *which*
sources that tool may read -- and it also makes the tool available, so a user
who picks a source without ticking the tool is not left with a selection that
silently does nothing.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from atlas.application.chat.orchestrator import ChatOrchestrator
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


# -- Orchestrator wiring -----------------------------------------------------
#
# The helper is applied in the orchestrator, before the agent-mode guard.
# Applying it only inside the loops was not enough: "sources selected, no other
# tool" is downgraded to a plain chat turn by that guard, so the implied tool
# would never have existed by the time a loop could add it.

def _orchestrator(config):
    orch = ChatOrchestrator.__new__(ChatOrchestrator)
    orch.config_manager = config
    orch.event_publisher = AsyncMock()
    return orch


@pytest.mark.asyncio
async def test_orchestrator_adds_the_implied_tool_before_the_agent_guard():
    """A source-only agent turn is reachable: the guard sees ``atlas_search``."""
    orch = _orchestrator(_config())

    resolved = await orch._resolve_search_tool(None, ["srv:src"])

    assert resolved == ["atlas_search"], "agent mode would degrade to a plain chat turn"
    orch.event_publisher.publish_warning.assert_not_awaited()


@pytest.mark.asyncio
async def test_orchestrator_leaves_a_sourceless_turn_alone():
    orch = _orchestrator(_config())

    assert await orch._resolve_search_tool(["a_tool"], None) == ["a_tool"]
    orch.event_publisher.publish_warning.assert_not_awaited()


@pytest.mark.asyncio
async def test_sources_plus_tools_with_search_disabled_warns_the_user():
    """The one configuration that loses retrieval must not lose it silently.

    ``FEATURE_RAG_ENABLED=true`` with ``FEATURE_ATLAS_RAG_TOOLS_ENABLED=false``
    is a supported combination. A turn that also has tools selected runs in
    tools mode, where nothing reads the sources any more -- so the user is told
    rather than handed an answer that quietly skipped their evidence.
    """
    orch = _orchestrator(_config(tools=False))

    resolved = await orch._resolve_search_tool(["a_tool"], ["srv:src"])

    assert resolved == ["a_tool"]
    orch.event_publisher.publish_warning.assert_awaited_once()
    message = orch.event_publisher.publish_warning.await_args.kwargs["message"]
    assert "were not searched" in message
    assert "FEATURE_ATLAS_RAG_TOOLS_ENABLED" in message


@pytest.mark.asyncio
async def test_no_warning_when_the_turn_has_no_tools_to_run():
    """Sources with no tools routes to RAG mode, which still reads them."""
    orch = _orchestrator(_config(tools=False))

    assert await orch._resolve_search_tool(None, ["srv:src"]) is None
    orch.event_publisher.publish_warning.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_user_selected_search_tool_is_never_warned_about():
    """The warning is about a *missing* search tool, not a disabled feature flag.

    A turn can name the search tool under its pre-#855 spelling
    (``atlas_rag_query``, from a saved conversation or a stale selection). That
    turn has a search tool, so warning that the sources "were not searched"
    would be wrong -- and would fire on every replayed RAG conversation.
    """
    orch = _orchestrator(_config(tools=False))

    resolved = await orch._resolve_search_tool(["atlas_rag_query"], ["srv:src"])

    assert resolved == ["atlas_rag_query"]
    orch.event_publisher.publish_warning.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_config_manager_means_nothing_to_report():
    """Programmatic callers never had feature flags to consult."""
    orch = _orchestrator(None)

    assert await orch._resolve_search_tool(["a_tool"], ["srv:src"]) == ["a_tool"]
    orch.event_publisher.publish_warning.assert_not_awaited()
