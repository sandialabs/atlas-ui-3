"""Data sources must be readable by the turn that carries them -- or said.

``atlas_search`` is only available when the user actually ticked it (#921): a
data source selection scopes what that tool may read, it no longer offers the
tool itself. A turn that selects sources plus other tools but not the search
tool runs in tools/agent mode, where nothing reads those sources, so the
orchestrator warns. A turn with no tools at all routes to RAG mode, which
reads the sources itself, and is never warned about.
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
async def test_no_tools_routes_to_rag_mode_and_is_never_warned_about():
    """Sources with no tools reach RAG mode, which reads them itself."""
    orch = _orchestrator(_config())

    await orch._check_data_sources_reachable(None, ["srv:src"])
    await orch._check_data_sources_reachable([], ["srv:src"])

    orch.event_publisher.publish_warning.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_sources_changes_nothing():
    orch = _orchestrator(_config())

    await orch._check_data_sources_reachable(["a_tool"], None)
    await orch._check_data_sources_reachable(["a_tool"], [])

    orch.event_publisher.publish_warning.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_rag_turns_read_their_sources_themselves():
    """``only_rag`` bypasses tools mode, so the sources are still read."""
    orch = _orchestrator(_config())

    await orch._check_data_sources_reachable(["a_tool"], ["srv:src"], only_rag=True)

    orch.event_publisher.publish_warning.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_config_manager_means_nothing_to_report():
    """Programmatic callers never had feature flags to consult."""
    orch = _orchestrator(None)

    await orch._check_data_sources_reachable(["a_tool"], ["srv:src"])

    orch.event_publisher.publish_warning.assert_not_awaited()


@pytest.mark.asyncio
async def test_flags_do_not_change_the_warning():
    """The warning is about the turn, not the feature flags.

    ``FEATURE_ATLAS_RAG_TOOLS_ENABLED`` off is one reason the search tool can
    be missing, but after #921 the ordinary reason is that the user did not
    tick it -- and both strand the sources in tools/agent mode the same way.
    """
    for config in (_config(rag=False), _config(tools=False), _config(rag=False, tools=False)):
        orch = _orchestrator(config)

        await orch._check_data_sources_reachable(["a_tool"], ["srv:src"])

        orch.event_publisher.publish_warning.assert_awaited_once()