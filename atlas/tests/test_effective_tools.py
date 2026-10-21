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
)
from atlas.domain.sessions.models import Session
from atlas.hooks import HookOutcome
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository
from atlas.modules.config.models import LLMConfig
from atlas.modules.mcp_tools.atlas_server import search_tools_enabled


def _settings(rag=True, tools=True):
    return SimpleNamespace(
        feature_rag_enabled=rag, feature_atlas_rag_tools_enabled=tools,
    )


def _config(rag=True, tools=True):
    return SimpleNamespace(app_settings=_settings(rag=rag, tools=tools))


def _orchestrator(config):
    orch = ChatOrchestrator.__new__(ChatOrchestrator)
    orch.config_manager = config
    orch.event_publisher = AsyncMock()
    return orch


# -- The resolution itself ---------------------------------------------------

def test_selected_sources_add_the_search_tool():
    tools = resolve_effective_tools(["calc"], ["srv:src"], _settings())
    assert tools == ["calc", "atlas_search"]


def test_no_sources_leaves_the_selection_alone():
    assert resolve_effective_tools(["calc"], None, _settings()) == ["calc"]
    assert resolve_effective_tools(["calc"], [], _settings()) == ["calc"]


def test_the_search_tool_is_never_duplicated():
    tools = resolve_effective_tools(["atlas_search"], ["srv:src"], _settings())
    assert tools == ["atlas_search"]


def test_the_legacy_search_tool_name_is_not_duplicated():
    """A saved conversation still names the tool ``atlas_rag_query`` (pre-#855)."""
    tools = resolve_effective_tools(["atlas_rag_query"], ["srv:src"], _settings())
    assert tools == ["atlas_rag_query"]


def test_the_flags_off_keep_the_search_tool_out():
    """With the feature off the turn sees exactly what the user selected."""
    assert resolve_effective_tools(["calc"], ["srv:src"], _settings(rag=False)) == ["calc"]
    assert resolve_effective_tools(["calc"], ["srv:src"], _settings(tools=False)) == ["calc"]


def test_no_settings_mean_no_implied_tool():
    """Programmatic callers never had feature flags to consult."""
    assert resolve_effective_tools(["calc"], ["srv:src"], None) == ["calc"]


def test_the_implication_can_be_switched_off():
    """A turn that must not gain a tool keeps exactly what it arrived with.

    A model that cannot call tools, and a policy hook that has narrowed the
    tool list, both need this: the search tool the sources imply must not
    appear behind their back.
    """
    assert resolve_effective_tools(
        ["calc"], ["srv:src"], _settings(), imply=False,
    ) == ["calc"]
    assert resolve_effective_tools(
        None, ["srv:src"], _settings(), imply=False,
    ) == []


def test_a_malformed_selection_does_not_break_the_resolution():
    """Non-string entries are dropped rather than becoming schema lookups."""
    tools = resolve_effective_tools(["calc", None, 7], ["srv:src"], _settings())
    assert tools == ["calc", "atlas_search"]


def test_search_tools_enabled_reads_both_flags():
    assert search_tools_enabled(_settings()) is True
    assert search_tools_enabled(_settings(rag=False)) is False
    assert search_tools_enabled(_settings(tools=False)) is False
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
async def test_a_search_tool_named_while_the_flags_are_off_still_warns():
    """Naming the tool does not make it run when the feature is off.

    The tool is dropped from the schema under flags-off, so the sources it
    was supposed to read stay unread -- and the user is told so.
    """
    orch = _orchestrator(_config(tools=False))

    await orch._warn_if_sources_unsearchable(["atlas_search"], ["srv:src"])

    orch.event_publisher.publish_warning.assert_awaited_once()
    message = orch.event_publisher.publish_warning.await_args.kwargs["message"]
    assert "could not be searched" in message


@pytest.mark.asyncio
async def test_an_agent_turn_with_the_flags_off_warns():
    """The agent branch judges the same way: no search tool, no sources read."""
    orch = _orchestrator(_config(tools=False))

    await orch._warn_if_sources_unsearchable(["atlas_search"], ["srv:src"])

    orch.event_publisher.publish_warning.assert_awaited_once()


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


def _routing_orchestrator(config, authorization, tools_mode, rag_mode=None):
    """Build an execute()-ready orchestrator with mocked mode runners."""
    orch = ChatOrchestrator(
        llm=MagicMock(),
        event_publisher=AsyncMock(),
        session_repository=InMemorySessionRepository(),
        tools_mode=SimpleNamespace(run_streaming=tools_mode),
        rag_mode=SimpleNamespace(run_streaming=rag_mode or AsyncMock(return_value={"mode": "rag"})),
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


@pytest.mark.asyncio
async def test_an_agent_turn_with_the_flags_off_warns_about_the_sources():
    """An agent turn whose search tool cannot run says so.

    The agent branch does no authorization filtering, so the tool list still
    names the search tool the user ticked -- but under flags-off it never
    reaches the schema, so the sources it should have read stay unread.
    """
    agent_mode = AsyncMock(return_value={"mode": "agent"})
    orch = ChatOrchestrator(
        llm=MagicMock(),
        event_publisher=AsyncMock(),
        session_repository=InMemorySessionRepository(),
        agent_mode=SimpleNamespace(run=agent_mode),
        config_manager=_config(tools=False),
    )
    sid = uuid4()
    await orch.session_repository.create(Session(id=sid, user_email="u@example.com"))

    await orch.execute(
        session_id=sid,
        content="find the policy",
        model="test-model",
        selected_tools=["atlas_search"],
        selected_data_sources=["srv:src"],
        agent_mode=True,
        files=None,
    )

    agent_mode.assert_awaited_once()
    orch.event_publisher.publish_warning.assert_awaited_once()
    message = orch.event_publisher.publish_warning.await_args.kwargs["message"]
    assert "could not be searched" in message


@pytest.mark.asyncio
async def test_execute_falls_back_when_authorization_takes_every_tool():
    """A turn whose every tool is unauthorized keeps its sources.

    Authorization stripped the search tool (the deployment has it switched
    off), so the tools branch would run with nothing to run. The turn falls
    through to the route that reads the sources instead of dropping them --
    and says so, because the user asked for tools that did not run.
    """
    tools_mode = AsyncMock(return_value={"mode": "tools"})
    rag_mode = AsyncMock(return_value={"mode": "rag"})
    orch = _routing_orchestrator(
        _config(),
        # Authorization takes every tool the turn had.
        _StubAuthorization(strip_prefixes=("calc", "atlas_")),
        tools_mode,
        rag_mode=rag_mode,
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

    tools_mode.assert_not_awaited()
    rag_mode.assert_awaited_once()
    assert rag_mode.await_args.kwargs["data_sources"] == ["srv:src"]
    orch.event_publisher.publish_warning.assert_awaited_once()
    message = orch.event_publisher.publish_warning.await_args.kwargs["message"]
    assert "not available" in message


@pytest.mark.asyncio
async def test_auto_expanded_sources_do_not_imply_the_search_tool():
    """Sources the client expanded on its own do not imply the tool.

    The user picked nothing: the client filled the list with every source it
    could reach (the RAG toggle, or the search tool ticked on its own).
    Implying the tool here would put an internal corpus search next to the
    user's outbound tools without them asking for it.
    """
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
        data_sources_auto=True,
        files=None,
    )

    assert tools_mode.await_args.kwargs["selected_tools"] == ["calc"]


@pytest.mark.asyncio
async def test_the_fallback_warning_stays_quiet_for_auto_expanded_sources():
    """Nothing was picked, so there is nothing to tell the user about."""
    tools_mode = AsyncMock(return_value={"mode": "tools"})
    rag_mode = AsyncMock(return_value={"mode": "rag"})
    orch = _routing_orchestrator(
        _config(),
        _StubAuthorization(strip_prefixes=("calc", "atlas_")),
        tools_mode,
        rag_mode=rag_mode,
    )
    sid = uuid4()
    await orch.session_repository.create(Session(id=sid, user_email="u@example.com"))

    await orch.execute(
        session_id=sid,
        content="find the policy",
        model="test-model",
        selected_tools=["calc"],
        selected_data_sources=["srv:src"],
        data_sources_auto=True,
        files=None,
    )

    orch.event_publisher.publish_warning.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_fallback_blames_the_feature_flag_when_it_is_off():
    """The deployment took the tool away, not authorization."""
    tools_mode = AsyncMock(return_value={"mode": "tools"})
    rag_mode = AsyncMock(return_value={"mode": "rag"})
    orch = _routing_orchestrator(
        _config(tools=False),
        _StubAuthorization(strip_prefixes=("calc", "atlas_")),
        tools_mode,
        rag_mode=rag_mode,
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

    orch.event_publisher.publish_warning.assert_awaited_once()
    message = orch.event_publisher.publish_warning.await_args.kwargs["message"]
    assert "FEATURE_ATLAS_RAG_TOOLS_ENABLED" in message
    assert "Authorization" not in message


@pytest.mark.asyncio
async def test_the_fallback_does_not_run_the_rag_reader_when_rag_is_off():
    """With RAG off there is nothing to read, so the turn is a plain turn."""
    tools_mode = AsyncMock(return_value={"mode": "tools"})
    rag_mode = AsyncMock(return_value={"mode": "rag"})
    plain_mode = AsyncMock(return_value={"mode": "plain"})
    orch = ChatOrchestrator(
        llm=MagicMock(),
        event_publisher=AsyncMock(),
        session_repository=InMemorySessionRepository(),
        tools_mode=SimpleNamespace(run_streaming=tools_mode),
        rag_mode=SimpleNamespace(run_streaming=rag_mode),
        plain_mode=SimpleNamespace(run_streaming=plain_mode),
        config_manager=_config(rag=False),
    )
    orch.tool_authorization = _StubAuthorization(strip_prefixes=("calc", "atlas_"))
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

    rag_mode.assert_not_awaited()
    plain_mode.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_model_without_tool_support_stays_out_of_the_tools_branch():
    """A model that cannot call tools gets no schema, implied or not.

    The tools are stripped for a model without tool support, so the search
    tool the sources imply must not be added back behind that: the turn
    keeps the sources and runs where it always did.
    """
    rag_mode = AsyncMock(return_value={"mode": "rag"})
    tools_mode = AsyncMock(return_value={"mode": "tools"})
    orch = _routing_orchestrator(
        _config(),
        _StubAuthorization(strip_prefixes=()),
        tools_mode,
        rag_mode=rag_mode,
    )
    orch.config_manager.llm_config = LLMConfig(models={
        "plain-model": {"model_name": "p", "model_url": "http://p", "supports_tools": False},
    })
    sid = uuid4()
    await orch.session_repository.create(Session(id=sid, user_email="u@example.com"))

    await orch.execute(
        session_id=sid,
        content="find the policy",
        model="plain-model",
        selected_tools=["calc"],
        selected_data_sources=["srv:src"],
        files=None,
    )

    tools_mode.assert_not_awaited()
    rag_mode.assert_awaited_once()


class _NarrowingHookManager:
    """A UserPromptSubmit hook that keeps only the tools it was told to."""

    def __init__(self, keep):
        self.keep = keep

    def has_hooks(self, event):
        return True

    async def run_event(self, event, payload, session_context=None):
        return HookOutcome(
            verdict="modify",
            modified=True,
            payload={**payload, "selected_tools": list(self.keep)},
        )


class _SourcesHookManager:
    """A hook that clears the data sources and leaves the tools alone."""

    def __init__(self):
        self.seen_tools = None

    def has_hooks(self, event):
        return True

    async def run_event(self, event, payload, session_context=None):
        self.seen_tools = list(payload.get("selected_tools") or [])
        return HookOutcome(
            verdict="modify",
            modified=True,
            payload={**payload, "selected_data_sources": []},
        )


class _EchoHookManager:
    """A hook that rewrites the prompt and leaves the tool list alone."""

    def __init__(self):
        self.seen_tools = None

    def has_hooks(self, event):
        return True

    async def run_event(self, event, payload, session_context=None):
        self.seen_tools = list(payload.get("selected_tools") or [])
        return HookOutcome(
            verdict="modify",
            modified=True,
            payload={**payload, "prompt": payload.get("prompt")},
        )


@pytest.mark.asyncio
async def test_a_policy_hook_that_drops_the_search_tool_wins(monkeypatch):
    """A hook that narrows the tool list has the last word over it.

    The hook sees the tools the turn would really offer -- the search tool
    the data sources imply included -- so it can strip it. The stripped list
    is what the turn runs with.
    """
    tools_mode = AsyncMock(return_value={"mode": "tools"})
    orch = _routing_orchestrator(
        _config(),
        _StubAuthorization(strip_prefixes=()),
        tools_mode,
    )
    hook = _NarrowingHookManager(keep=[])
    monkeypatch.setattr(
        "atlas.application.chat.orchestrator.get_hook_manager", lambda: hook,
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

    # The hook emptied the tool list: no tool reaches the runner, implied
    # or otherwise.
    tools_mode.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_policy_hook_that_drops_the_search_tool_from_a_sources_only_turn(
    monkeypatch,
):
    """A sources-only turn offers the search tool, and the hook can take it.

    The user selected no tools at all, so the search tool the sources imply
    is the only tool in the list the hook sees -- and a hook that strips it
    leaves the turn with nothing.
    """
    tools_mode = AsyncMock(return_value={"mode": "tools"})
    rag_mode = AsyncMock(return_value={"mode": "rag"})
    orch = _routing_orchestrator(
        _config(),
        _StubAuthorization(strip_prefixes=()),
        tools_mode,
        rag_mode=rag_mode,
    )
    hook = _NarrowingHookManager(keep=[])
    monkeypatch.setattr(
        "atlas.application.chat.orchestrator.get_hook_manager", lambda: hook,
    )
    sid = uuid4()
    await orch.session_repository.create(Session(id=sid, user_email="u@example.com"))

    await orch.execute(
        session_id=sid,
        content="find the policy",
        model="test-model",
        selected_tools=[],
        selected_data_sources=["srv:src"],
        files=None,
    )

    tools_mode.assert_not_awaited()
    rag_mode.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_policy_hook_that_keeps_another_tool_still_drops_the_search_tool(
    monkeypatch,
):
    """A hook that keeps one tool drops the search tool with it.

    The point is that the hook's decision is about the search tool
    specifically, not about emptying the whole list.
    """
    tools_mode = AsyncMock(return_value={"mode": "tools"})
    orch = _routing_orchestrator(
        _config(),
        _StubAuthorization(strip_prefixes=()),
        tools_mode,
    )
    monkeypatch.setattr(
        "atlas.application.chat.orchestrator.get_hook_manager",
        lambda: _NarrowingHookManager(keep=["calc"]),
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

    # The search tool is gone; the tool the hook kept is what runs.
    assert tools_mode.await_args.kwargs["selected_tools"] == ["calc"]


@pytest.mark.asyncio
async def test_a_policy_hook_that_clears_the_sources_drops_the_implied_tool(
    monkeypatch,
):
    """The implication rests on the sources: no sources, no implied tool.

    A hook that clears the data sources takes the sources away, so the
    search tool they implied goes with them -- otherwise the turn would run
    with a search tool and nothing to search.
    """
    tools_mode = AsyncMock(return_value={"mode": "tools"})
    plain_mode = AsyncMock(return_value={"mode": "plain"})
    orch = ChatOrchestrator(
        llm=MagicMock(),
        event_publisher=AsyncMock(),
        session_repository=InMemorySessionRepository(),
        tools_mode=SimpleNamespace(run_streaming=tools_mode),
        plain_mode=SimpleNamespace(run_streaming=plain_mode),
        config_manager=_config(),
    )
    orch.tool_authorization = _StubAuthorization(strip_prefixes=())
    monkeypatch.setattr(
        "atlas.application.chat.orchestrator.get_hook_manager",
        lambda: _SourcesHookManager(),
    )
    sid = uuid4()
    await orch.session_repository.create(Session(id=sid, user_email="u@example.com"))

    await orch.execute(
        session_id=sid,
        content="find the policy",
        model="test-model",
        selected_tools=[],
        selected_data_sources=["srv:src"],
        files=None,
    )

    tools_mode.assert_not_awaited()
    plain_mode.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_policy_hook_that_clears_the_sources_keeps_an_explicit_search_tool(
    monkeypatch,
):
    """A tool the user ticked is theirs, whatever the hook does to the sources."""
    tools_mode = AsyncMock(return_value={"mode": "tools"})
    orch = _routing_orchestrator(
        _config(),
        _StubAuthorization(strip_prefixes=()),
        tools_mode,
    )
    monkeypatch.setattr(
        "atlas.application.chat.orchestrator.get_hook_manager",
        lambda: _SourcesHookManager(),
    )
    sid = uuid4()
    await orch.session_repository.create(Session(id=sid, user_email="u@example.com"))

    await orch.execute(
        session_id=sid,
        content="find the policy",
        model="test-model",
        selected_tools=["atlas_search"],
        selected_data_sources=["srv:src"],
        files=None,
    )

    assert tools_mode.await_args.kwargs["selected_tools"] == ["atlas_search"]


@pytest.mark.asyncio
async def test_a_policy_hook_that_leaves_the_tools_alone_changes_nothing(monkeypatch):
    """A hook that does not touch the tool list keeps the implied tool.

    The implication is resolved before the hook runs, so a hook that only
    rewrites the prompt leaves the tool list -- implied search tool
    included -- exactly as it was.
    """
    tools_mode = AsyncMock(return_value={"mode": "tools"})
    orch = _routing_orchestrator(
        _config(),
        _StubAuthorization(strip_prefixes=()),
        tools_mode,
    )
    hook = _EchoHookManager()
    monkeypatch.setattr(
        "atlas.application.chat.orchestrator.get_hook_manager", lambda: hook,
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

    # The hook saw the tools the turn would offer, implied tool included.
    assert hook.seen_tools == ["calc", "atlas_search"]
    assert tools_mode.await_args.kwargs["selected_tools"] == ["calc", "atlas_search"]
