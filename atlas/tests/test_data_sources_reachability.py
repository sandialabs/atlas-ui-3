"""Data sources must be readable by the turn that carries them -- or said.

``atlas_search`` is only available when the user actually ticked it (#921): a
data source selection scopes what that tool may read, it no longer offers the
tool itself. The reachability check runs inside the tool-running branches of
the orchestrator (agent branch; tools branch after authorization filtering),
so it judges the tool list the LLM will really see. A turn that runs tools
with sources nothing can read warns; RAG-mode turns (no tools, ``only_rag``)
read the sources themselves and never reach the check.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from atlas.application.chat.orchestrator import ChatOrchestrator


def _config(rag=True, tools=True):
    return SimpleNamespace(app_settings=SimpleNamespace(
        feature_rag_enabled=rag, feature_atlas_rag_tools_enabled=tools,
    ))


def _orchestrator(config):
    orch = ChatOrchestrator.__new__(ChatOrchestrator)
    orch.config_manager = config
    orch.event_publisher = AsyncMock()
    return orch


@pytest.mark.asyncio
async def test_sources_with_tools_but_no_search_tool_warns():
    """The user ticked other tools and picked sources; nothing reads them."""
    orch = _orchestrator(_config())

    await orch._check_data_sources_reachable(["a_tool"], ["srv:src"])

    orch.event_publisher.publish_warning.assert_awaited_once()
    message = orch.event_publisher.publish_warning.await_args.kwargs["message"]
    assert "were not searched" in message
    assert "atlas_search" in message


@pytest.mark.asyncio
async def test_a_selected_search_tool_satisfies_the_sources():
    """A turn that names ``atlas_search`` can read its sources."""
    orch = _orchestrator(_config())

    await orch._check_data_sources_reachable(["atlas_search", "a_tool"], ["srv:src"])

    orch.event_publisher.publish_warning.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_search_tool_named_while_the_flags_are_off_still_warns():
    """A named search tool that cannot reach the schema strands the sources.

    ``FEATURE_ATLAS_RAG_TOOLS_ENABLED`` off keeps ``atlas_search`` out of the
    LLM schema even when the user ticked it, so a tools turn with sources is
    just as stranded as one that never named the tool.
    """
    orch = _orchestrator(_config(rag=False))
    await orch._check_data_sources_reachable(["atlas_search"], ["srv:src"])
    orch.event_publisher.publish_warning.assert_awaited_once()

    orch = _orchestrator(_config(tools=False))
    await orch._check_data_sources_reachable(["atlas_search"], ["srv:src"])
    orch.event_publisher.publish_warning.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_legacy_search_tool_name_satisfies_the_sources():
    """A saved conversation still names the tool ``atlas_rag_query`` (pre-#855)."""
    orch = _orchestrator(_config())

    await orch._check_data_sources_reachable(["atlas_rag_query"], ["srv:src"])

    orch.event_publisher.publish_warning.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_expanded_sources_are_never_warned_about():
    """Sources the client expanded on its own were never deliberately chosen.

    With the RAG toggle on and no source picked, the client sends every
    reachable source id -- the user did not select anything, so warning that
    "your data sources were not searched" on every tools turn is noise.
    """
    for sources, auto in ((["srv:src"], True), (None, True), ([], True)):
        orch = _orchestrator(_config())

        await orch._check_data_sources_reachable(["a_tool"], sources, sources_auto=auto)

        orch.event_publisher.publish_warning.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_sources_changes_nothing():
    orch = _orchestrator(_config())

    await orch._check_data_sources_reachable(["a_tool"], None)
    await orch._check_data_sources_reachable(["a_tool"], [])

    orch.event_publisher.publish_warning.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_config_manager_means_nothing_to_report():
    """Programmatic callers never had feature flags to consult."""
    orch = _orchestrator(None)

    await orch._check_data_sources_reachable(["a_tool"], ["srv:src"])

    orch.event_publisher.publish_warning.assert_not_awaited()
