"""Effective tool resolution: selected data sources imply the search tool.

Issue #1102. Picking a data source and ticking the built-in search tool used
to be two separate mechanisms, and a user who did only the first got a turn
that read nothing plus a warning about it. A selection of data sources now
implies ``atlas_search``: the tool is offered to the model, and the model
decides whether to call it.

The rules this file pins:

- sources selected + RAG tools enabled -> ``atlas_search`` is in the turn's
  tool list, whether or not the user ticked it;
- no sources -> the user's selection is passed through untouched;
- the search tool is never duplicated (neither under its current name nor its
  pre-#855 one);
- the feature flags keep gating it: with them off the turn sees exactly what
  the user selected;
- there is no opt-out and nothing persisted -- deselecting the sources is
  what removes the implication, and an explicit selection of the search tool
  survives either way.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from atlas.application.chat.orchestrator import ChatOrchestrator
from atlas.application.chat.utilities.effective_tools import (
    has_search_tool,
    resolve_effective_tools,
    search_tools_enabled,
)
from atlas.domain.sessions.models import Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository


def _config(rag=True, tools=True):
    return SimpleNamespace(app_settings=SimpleNamespace(
        feature_rag_enabled=rag, feature_atlas_rag_tools_enabled=tools,
    ))


def _orchestrator(config):
    orch = ChatOrchestrator.__new__(ChatOrchestrator)
    orch.config_manager = config
    orch.event_publisher = AsyncMock()
    return orch


# -- The resolution itself ---------------------------------------------------

def test_selected_sources_add_the_search_tool():
    tools = resolve_effective_tools(["calc"], ["srv:src"], _config())
    assert tools == ["calc", "atlas_search"]


def test_no_sources_leaves_the_selection_alone():
    assert resolve_effective_tools(["calc"], None, _config()) == ["calc"]
    assert resolve_effective_tools(["calc"], [], _config()) == ["calc"]


def test_the_search_tool_is_never_duplicated():
    tools = resolve_effective_tools(["atlas_search"], ["srv:src"], _config())
    assert tools == ["atlas_search"]


def test_the_legacy_search_tool_name_is_not_duplicated():
    """A saved conversation still names the tool ``atlas_rag_query`` (pre-#855)."""
    tools = resolve_effective_tools(["atlas_rag_query"], ["srv:src"], _config())
    assert tools == ["atlas_rag_query"]


def test_the_flags_off_keep_the_search_tool_out():
    """With the feature off the turn sees exactly what the user selected."""
    assert resolve_effective_tools(["calc"], ["srv:src"], _config(rag=False)) == ["calc"]
    assert resolve_effective_tools(["calc"], ["srv:src"], _config(tools=False)) == ["calc"]


def test_no_config_manager_means_no_implied_tool():
    """Programmatic callers never had feature flags to consult."""
    assert resolve_effective_tools(["calc"], ["srv:src"], None) == ["calc"]


def test_a_malformed_selection_does_not_break_the_resolution():
    """Non-string entries are dropped rather than becoming schema lookups."""
    tools = resolve_effective_tools(["calc", None, 7], ["srv:src"], _config())
    assert tools == ["calc", "atlas_search"]


def test_search_tools_enabled_reads_both_flags():
    assert search_tools_enabled(_config()) is True
    assert search_tools_enabled(_config(rag=False)) is False
    assert search_tools_enabled(_config(tools=False)) is False
    assert search_tools_enabled(None) is False


def test_has_search_tool_recognizes_the_legacy_name():
    assert has_search_tool(["atlas_search"]) is True
    assert has_search_tool(["atlas_rag_query"]) is True
    assert has_search_tool(["calc"]) is False
    assert has_search_tool(None) is False


# -- The warning that replaced "your data sources were not searched" ---------
#
# The normal case is silent: a selection of sources implies the search tool,
# so the model is offered it and decides whether to call it. The warning is
# for the case where the search tool genuinely cannot run.

@pytest.mark.asyncio
async def test_sources_with_tools_and_search_available_are_silent():
    """The ordinary case: the search tool is there, so nothing is reported."""
    orch = _orchestrator(_config())

    await orch._warn_if_sources_unsearchable(
        ["calc", "atlas_search"], ["srv:src"],
    )

    orch.event_publisher.publish_warning.assert_not_awaited()


@pytest.mark.asyncio
async def test_sources_with_the_flags_off_warn():
    """The search tool cannot run, so the sources really are stranded."""
    orch = _orchestrator(_config(tools=False))

    await orch._warn_if_sources_unsearchable(["calc"], ["srv:src"])

    orch.event_publisher.publish_warning.assert_awaited_once()
    message = orch.event_publisher.publish_warning.await_args.kwargs["message"]
    assert "could not be searched" in message
    assert "FEATURE_ATLAS_RAG_TOOLS_ENABLED" in message


@pytest.mark.asyncio
async def test_a_search_tool_stripped_by_authorization_warns():
    """The flags are on, but the turn's tool list no longer names the tool."""
    orch = _orchestrator(_config())

    await orch._warn_if_sources_unsearchable(["calc"], ["srv:src"])

    orch.event_publisher.publish_warning.assert_awaited_once()
    message = orch.event_publisher.publish_warning.await_args.kwargs["message"]
    assert "could not be searched" in message
    # The flags are on, so the message must not blame a disabled feature.
    assert "FEATURE_ATLAS_RAG_TOOLS_ENABLED" not in message


@pytest.mark.asyncio
async def test_a_legacy_search_tool_name_satisfies_the_sources():
    orch = _orchestrator(_config())

    await orch._warn_if_sources_unsearchable(["atlas_rag_query"], ["srv:src"])

    orch.event_publisher.publish_warning.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_expanded_sources_are_never_warned_about():
    """Sources the client expanded on its own were never deliberately chosen.

    With the RAG toggle on and no source picked, the client sends every
    reachable source id -- the user did not select anything, so warning that
    "your data sources could not be searched" on every tools turn is noise.
    """
    for sources, auto in ((["srv:src"], True), (None, True), ([], True)):
        orch = _orchestrator(_config(tools=False))

        await orch._warn_if_sources_unsearchable(
            ["a_tool"], sources, sources_auto=auto,
        )

        orch.event_publisher.publish_warning.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_sources_changes_nothing():
    orch = _orchestrator(_config(tools=False))

    await orch._warn_if_sources_unsearchable(["a_tool"], None)
    await orch._warn_if_sources_unsearchable(["a_tool"], [])

    orch.event_publisher.publish_warning.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_config_manager_means_nothing_to_report():
    """Programmatic callers never had feature flags to consult."""
    orch = _orchestrator(None)

    await orch._warn_if_sources_unsearchable(["a_tool"], ["srv:src"])

    orch.event_publisher.publish_warning.assert_not_awaited()


# -- Routing-level wiring -----------------------------------------------------
#
# The check must run *after* tool authorization in the tools branch: the ACL
# can strip ``atlas_search`` (the deployment has the search tool switched
# off), which would strand the sources silently if the check ran any earlier.
# These tests pin the position through execute(), not just the private helper.

class _StubAuthorization:
    """ACL that strips the built-in atlas server's tools, keeps the rest."""

    def __init__(self, strip_prefixes):
        self.strip_prefixes = strip_prefixes

    async def filter_authorized_tools(self, selected_tools, user_email=None):
        return [t for t in selected_tools if not t.startswith(self.strip_prefixes)]


def _routing_orchestrator(config, authorization, tools_mode):
    """Build an execute()-ready orchestrator with mocked mode runners."""
    orch = ChatOrchestrator(
        llm=MagicMock(),
        event_publisher=AsyncMock(),
        session_repository=InMemorySessionRepository(),
        tools_mode=SimpleNamespace(run_streaming=tools_mode),
        config_manager=config,
    )
    orch.tool_authorization = authorization
    return orch


@pytest.mark.asyncio
async def test_execute_warns_when_authorization_strips_the_search_tool():
    """The check judges the post-ACL tool list, not the raw selection."""
    tools_mode = AsyncMock(return_value={"mode": "tools"})
    orch = _routing_orchestrator(
        _config(),
        _StubAuthorization(strip_prefixes=("atlas_",)),
        tools_mode,
    )
    sid = uuid4()
    await orch.session_repository.create(Session(id=sid, user_email="u@example.com"))

    await orch.execute(
        session_id=sid,
        content="find the policy",
        model="test-model",
        selected_tools=["atlas_search", "calc"],
        selected_data_sources=["srv:src"],
        files=None,
    )

    orch.event_publisher.publish_warning.assert_awaited_once()
    message = orch.event_publisher.publish_warning.await_args.kwargs["message"]
    assert "could not be searched" in message
    # The stripped list is what the check saw: tools mode got the ACL result.
    tools_mode.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_offers_the_search_tool_for_selected_sources():
    """Sources selected, search never ticked: the model still gets the tool."""
    tools_mode = AsyncMock(return_value={"mode": "tools"})
    orch = _routing_orchestrator(
        _config(),
        _StubAuthorization(strip_prefixes=()),
        tools_mode,
    )
    sid = uuid4()
    await orch.session_repository.create(Session(id=sid, user_email="u@example.com"))

    await orch.execute(
        session_id=sid,
        content="find the policy",
        model="test-model",
        selected_tools=["calc"],
        selected_data_sources=["srv:src"],
        files=None,
    )

    orch.event_publisher.publish_warning.assert_not_awaited()
    assert tools_mode.await_args.kwargs["selected_tools"] == ["calc", "atlas_search"]


@pytest.mark.asyncio
async def test_execute_stays_silent_for_auto_expanded_sources():
    """The client's expanded source list must not warn through execute()."""
    tools_mode = AsyncMock(return_value={"mode": "tools"})
    orch = _routing_orchestrator(
        _config(),
        _StubAuthorization(strip_prefixes=()),
        tools_mode,
    )
    sid = uuid4()
    await orch.session_repository.create(Session(id=sid, user_email="u@example.com"))

    await orch.execute(
        session_id=sid,
        content="hello",
        model="test-model",
        selected_tools=["calc"],
        selected_data_sources=["srv:src"],
        data_sources_auto=True,
        files=None,
    )

    orch.event_publisher.publish_warning.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_drops_the_implied_tool_when_no_source_is_selected():
    """Deselecting every source removes the implicit search availability."""
    tools_mode = AsyncMock(return_value={"mode": "tools"})
    orch = _routing_orchestrator(
        _config(),
        _StubAuthorization(strip_prefixes=()),
        tools_mode,
    )
    sid = uuid4()
    await orch.session_repository.create(Session(id=sid, user_email="u@example.com"))

    await orch.execute(
        session_id=sid,
        content="hello",
        model="test-model",
        selected_tools=["calc"],
        selected_data_sources=[],
        files=None,
    )

    assert tools_mode.await_args.kwargs["selected_tools"] == ["calc"]


@pytest.mark.asyncio
async def test_execute_keeps_the_explicit_search_tool_without_sources():
    """An explicit selection survives: the implication only ever adds."""
    tools_mode = AsyncMock(return_value={"mode": "tools"})
    orch = _routing_orchestrator(
        _config(),
        _StubAuthorization(strip_prefixes=()),
        tools_mode,
    )
    sid = uuid4()
    await orch.session_repository.create(Session(id=sid, user_email="u@example.com"))

    await orch.execute(
        session_id=sid,
        content="hello",
        model="test-model",
        selected_tools=["atlas_search"],
        selected_data_sources=[],
        files=None,
    )

    assert tools_mode.await_args.kwargs["selected_tools"] == ["atlas_search"]


@pytest.mark.asyncio
async def test_execute_does_not_imply_the_tool_when_the_flags_are_off():
    """The feature flag keeps gating the schema."""
    tools_mode = AsyncMock(return_value={"mode": "tools"})
    orch = _routing_orchestrator(
        _config(tools=False),
        _StubAuthorization(strip_prefixes=()),
        tools_mode,
    )
    sid = uuid4()
    await orch.session_repository.create(Session(id=sid, user_email="u@example.com"))

    await orch.execute(
        session_id=sid,
        content="find the policy",
        model="test-model",
        selected_tools=["calc"],
        selected_data_sources=["srv:src"],
        files=None,
    )

    assert tools_mode.await_args.kwargs["selected_tools"] == ["calc"]
