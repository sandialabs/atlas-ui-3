"""Tests for Atlas RAG pseudo MCP tools used by agent mode."""

import importlib
import json
from types import SimpleNamespace

import pytest

from atlas.domain.messages.models import ToolCall
from atlas.modules.mcp_tools.client import MCPToolManager

# ``atlas.infrastructure`` re-exports the ``app_factory`` singleton, which
# rebinds the ``app_factory`` attribute on the package to the *instance*. That
# shadows the submodule for attribute-based lookups (``import x.y.z as m`` and
# monkeypatch's dotted-string resolver alike resolve to the instance). Pull the
# real module object out of ``sys.modules`` via importlib so we patch the same
# attribute the execution path reads with ``from ...app_factory import app_factory``.
app_factory_module = importlib.import_module("atlas.infrastructure.app_factory")


def _manager() -> MCPToolManager:
    return MCPToolManager(config_path="/tmp/atlas-noop-mcp.json")


def _enable_search(monkeypatch, enabled: bool = True) -> None:
    """Turn the RAG feature flags that gate ``atlas_search`` on (or off).

    The schema build omits a disabled built-in rather than advertising it, so
    the flags decide whether search reaches the model at all.
    """
    from atlas.modules.mcp_tools import client as mcp_client

    settings = mcp_client.config_manager.app_settings
    monkeypatch.setattr(settings, "feature_rag_enabled", enabled, raising=False)
    monkeypatch.setattr(settings, "feature_atlas_rag_tools_enabled", enabled, raising=False)


@pytest.fixture(autouse=True)
def _search_enabled(monkeypatch):
    """Every test here exercises the search tool, so run with RAG turned on.

    The flags gate both the advertised schema and execution, so leaving them at
    their (off) test defaults would make every case below assert against a
    refusal rather than the behaviour it is checking. Tests that want the
    disabled path call ``_enable_search(monkeypatch, enabled=False)``, which
    wins because it patches later.
    """
    _enable_search(monkeypatch)


class FakeUnifiedRAG:
    """Configurable fake unified RAG service for exercising the pseudo-tools.

    ``discovered`` controls the user's authorized/discovered source set.
    Calls are recorded so tests can assert routing (single vs batch).
    """

    def __init__(self, discovered=None):
        self.discovered = discovered if discovered is not None else ["technical-docs"]
        self.query_calls = []
        self.batch_calls = []
        self.search_kwargs_calls = []

    async def discover_data_sources(self, username, user_compliance_level=None):
        return [
            {
                "server": "atlas_rag",
                "sources": [{"id": src} for src in self.discovered],
            }
        ]

    async def query_rag(self, username, qualified_data_source, messages, query=None, mode=None, search_kwargs=None, _skip_hooks=False):
        self.query_calls.append(qualified_data_source)
        self.search_kwargs_calls.append(search_kwargs)
        return SimpleNamespace(
            content=f"Result from {qualified_data_source}", is_completion=False
        )

    async def query_rag_batch(self, username, qualified_data_sources, messages, query=None, mode=None, search_kwargs=None, _skip_hooks=False):
        self.batch_calls.append(list(qualified_data_sources))
        self.search_kwargs_calls.append(search_kwargs)
        return SimpleNamespace(
            content=f"Batched {','.join(qualified_data_sources)}", is_completion=True
        )


def _patch_app_factory(monkeypatch, unified_rag=None, rag_mcp=None):
    """Patch the app_factory singleton the execution path imports.

    NOTE: ``atlas.infrastructure`` re-exports the ``app_factory`` singleton, so
    the dotted string ``"atlas.infrastructure.app_factory.app_factory"`` is
    ambiguous to monkeypatch's resolver (it resolves to the instance, not the
    module). Patch the module object attribute directly instead.
    """

    class FakeAppFactory:
        def get_unified_rag_service(self):
            return unified_rag

        def get_rag_mcp_service(self):
            return rag_mcp

    monkeypatch.setattr(app_factory_module, "app_factory", FakeAppFactory())


def test_get_tools_schema_exposes_consolidated_search_tool(monkeypatch):
    """#855: one ``atlas_search`` tool; ``query`` is the only required argument."""
    manager = _manager()
    _enable_search(monkeypatch)
    schemas = manager.get_tools_schema(["atlas_search"])

    assert [schema["function"]["name"] for schema in schemas] == ["atlas_search"]
    params = schemas[0]["function"]["parameters"]
    # ``max_results`` and ``depth`` tune retrieval; neither can name a source,
    # so the model still has exactly one thing it must decide.
    assert list(params["properties"]) == ["query", "max_results", "depth"]
    assert params["required"] == ["query"]
    assert manager.get_server_for_tool("atlas_search") == "atlas"


def test_legacy_rag_query_name_resolves_to_the_search_tool(monkeypatch):
    """Saved conversations and stored selections still carry the old name."""
    manager = _manager()
    _enable_search(monkeypatch)
    schemas = manager.get_tools_schema(["atlas_rag_query"])

    assert [schema["function"]["name"] for schema in schemas] == ["atlas_search"]
    assert manager.get_server_for_tool("atlas_rag_query") == "atlas"


@pytest.mark.asyncio
async def test_execute_atlas_rag_query_uses_selected_data_sources(monkeypatch):
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["technical-docs"])
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(
            id="call-1",
            name="atlas_rag_query",
            arguments={"query": "vacation policy"},
        ),
        context={
            "user_email": "test@example.com",
            "selected_data_sources": ["atlas_rag:technical-docs"],
        },
    )

    assert result.success is True
    payload = json.loads(result.content)
    assert payload["results"]["query"] == "vacation policy"
    assert payload["results"]["answers"][0]["data_sources"] == ["atlas_rag:technical-docs"]
    # A single source must route through query_rag, never the batch path.
    assert unified.query_calls == ["atlas_rag:technical-docs"]
    assert unified.batch_calls == []


@pytest.mark.asyncio
async def test_execute_atlas_rag_discover_returns_sources(monkeypatch):
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["technical-docs", "policies"])
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(
            id="disc-1",
            name="atlas_rag_discover_data_sources",
            arguments={},
        ),
        context={"user_email": "test@example.com"},
    )

    assert result.success is True
    payload = json.loads(result.content)
    assert payload["results"]["sources"] == [
        "atlas_rag:technical-docs",
        "atlas_rag:policies",
    ]


@pytest.mark.asyncio
async def test_execute_atlas_rag_query_batches_multiple_sources_on_same_server(monkeypatch):
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["technical-docs", "policies"])
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(
            id="call-batch",
            name="atlas_rag_query",
            arguments={
                "query": "leave policy",
                "data_sources": ["atlas_rag:technical-docs", "atlas_rag:policies"],
            },
        ),
        context={"user_email": "test@example.com"},
    )

    assert result.success is True
    payload = json.loads(result.content)
    # Two sources on the same server collapse into one batched call.
    assert unified.batch_calls == [["atlas_rag:technical-docs", "atlas_rag:policies"]]
    assert unified.query_calls == []
    assert payload["results"]["answers"][0]["data_sources"] == [
        "atlas_rag:technical-docs",
        "atlas_rag:policies",
    ]


@pytest.mark.asyncio
async def test_execute_atlas_rag_query_rejects_unauthorized_sources(monkeypatch):
    """A model/client may not query a configured source the user can't access.

    The user only discovered ``technical-docs``; a directly-named
    ``secret-docs`` must be dropped rather than forwarded to query_rag.
    """
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["technical-docs"])
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(
            id="call-evil",
            name="atlas_rag_query",
            arguments={
                "query": "exfiltrate",
                "data_sources": ["atlas_rag:secret-docs"],
            },
        ),
        context={"user_email": "test@example.com"},
    )

    assert result.success is False
    assert result.error == "No authorized RAG data sources"
    # The unauthorized source must never reach the RAG service.
    assert unified.query_calls == []
    assert unified.batch_calls == []


@pytest.mark.asyncio
async def test_execute_atlas_rag_query_intersects_mixed_sources(monkeypatch):
    """When some requested sources are authorized and some are not, only the
    authorized subset is queried."""
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["technical-docs"])
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(
            id="call-mixed",
            name="atlas_rag_query",
            arguments={
                "query": "policy",
                "data_sources": ["atlas_rag:technical-docs", "atlas_rag:secret-docs"],
            },
        ),
        context={"user_email": "test@example.com"},
    )

    assert result.success is True
    assert unified.query_calls == ["atlas_rag:technical-docs"]
    assert unified.batch_calls == []
    # Dropped unauthorized sources must be surfaced so the model can disclose
    # partial coverage rather than summarizing a narrowed corpus as complete.
    payload = json.loads(result.content)
    assert payload["results"]["ignored_sources"] == ["atlas_rag:secret-docs"]


@pytest.mark.asyncio
async def test_execute_atlas_rag_query_requires_query_string(monkeypatch):
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["technical-docs"])
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(id="call-noq", name="atlas_rag_query", arguments={"query": "   "}),
        context={"user_email": "test@example.com"},
    )

    assert result.success is False
    assert result.error == "Missing query"


@pytest.mark.asyncio
async def test_execute_atlas_rag_tool_requires_user_context(monkeypatch):
    manager = _manager()
    _patch_app_factory(monkeypatch, unified_rag=FakeUnifiedRAG())

    result = await manager.execute_tool(
        ToolCall(id="call-nouser", name="atlas_rag_query", arguments={"query": "hi"}),
        context={},
    )

    assert result.success is False
    assert result.error == "Missing user context"


class ComplianceAwareRAG:
    """Fake unified RAG that enforces a compliance level at discovery.

    ``by_level`` maps a compliance level (or None) to the source ids returned.
    Records the compliance level discovery was called with.
    """

    def __init__(self, by_level):
        self.by_level = by_level
        self.discover_compliance_calls = []
        self.query_calls = []

    async def discover_data_sources(self, username, user_compliance_level=None):
        self.discover_compliance_calls.append(user_compliance_level)
        ids = self.by_level.get(user_compliance_level, [])
        return [{"server": "atlas_rag", "sources": [{"id": i} for i in ids]}]

    async def query_rag(self, username, qualified_data_source, messages, query=None, mode=None, search_kwargs=None, _skip_hooks=False):
        self.query_calls.append(qualified_data_source)
        return SimpleNamespace(content=f"ok {qualified_data_source}", is_completion=False)

    async def query_rag_batch(self, username, qualified_data_sources, messages, query=None, mode=None, search_kwargs=None, _skip_hooks=False):
        raise AssertionError("batch not expected in this test")


@pytest.mark.asyncio
async def test_execute_atlas_rag_query_enforces_trusted_compliance_level(monkeypatch):
    """The compliance level comes from the trusted context and bounds the
    allow-list, so a model cannot query a source outside its level."""
    manager = _manager()
    # At "Public" only public-docs is discoverable; secret-docs exists only at
    # a higher level and must be unreachable when operating at Public.
    unified = ComplianceAwareRAG(
        by_level={"Public": ["public-docs"], "Secret": ["public-docs", "secret-docs"]}
    )
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(
            id="call-compliance",
            name="atlas_rag_query",
            arguments={
                "query": "leak it",
                # Model tries to reach a higher-compliance source directly.
                "data_sources": ["atlas_rag:public-docs", "atlas_rag:secret-docs"],
            },
        ),
        context={"user_email": "u@example.com", "compliance_level": "Public"},
    )

    assert result.success is True
    # Discovery was bounded by the trusted context level, not a model value.
    assert unified.discover_compliance_calls == ["Public"]
    # Only the in-level source was queried; the higher-level one was dropped.
    assert unified.query_calls == ["atlas_rag:public-docs"]
    payload = json.loads(result.content)
    assert payload["results"]["ignored_sources"] == ["atlas_rag:secret-docs"]


@pytest.mark.asyncio
async def test_execute_atlas_rag_tool_ignores_model_supplied_identity(monkeypatch):
    """A model-supplied _atlas_user must never authenticate the caller: with no
    trusted context user_email, the tool fails closed and never queries."""
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["technical-docs"])
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(
            id="call-spoof",
            name="atlas_rag_query",
            arguments={"query": "hi", "_atlas_user": "attacker@example.com"},
        ),
        context={},  # no trusted user identity
    )

    assert result.success is False
    assert result.error == "Missing user context"
    assert unified.query_calls == []
    assert unified.batch_calls == []


class FakeRagMCP:
    """Fake rag_mcp service exposing MCP-only RAG servers."""

    def __init__(self, discovered_servers=None):
        # e.g. {"docsRag": ["handbook"]}
        self.discovered_servers = discovered_servers or {"docsRag": ["handbook"]}
        self.synthesize_calls = []

    async def discover_servers(self, username, user_compliance_level=None):
        return [
            {"server": srv, "sources": [{"id": sid} for sid in sids]}
            for srv, sids in self.discovered_servers.items()
        ]

    async def synthesize(self, username, query, sources, **kwargs):
        self.synthesize_calls.append(list(sources))
        return {"results": {"answer": f"MCP answer for {','.join(sources)}"}}


@pytest.mark.asyncio
async def test_execute_atlas_rag_query_routes_mcp_sources_through_rag_mcp(monkeypatch):
    """MCP-discovered sources must route through rag_mcp.synthesize, not
    unified_rag.query_rag (which cannot resolve them and would 'not found')."""
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["technical-docs"])  # HTTP source
    rag_mcp = FakeRagMCP({"docsRag": ["handbook"]})           # MCP source
    _patch_app_factory(monkeypatch, unified_rag=unified, rag_mcp=rag_mcp)

    result = await manager.execute_tool(
        ToolCall(
            id="call-mcp",
            name="atlas_rag_query",
            arguments={
                "query": "handbook policy",
                "data_sources": ["atlas_rag:technical-docs", "docsRag:handbook"],
            },
        ),
        context={"user_email": "test@example.com"},
    )

    assert result.success is True
    # HTTP source routed through unified_rag; MCP source through rag_mcp.
    assert unified.query_calls == ["atlas_rag:technical-docs"]
    assert rag_mcp.synthesize_calls == [["docsRag:handbook"]]
    payload = json.loads(result.content)
    contents = {a["content"] for a in payload["results"]["answers"]}
    assert "MCP answer for docsRag:handbook" in contents


@pytest.mark.asyncio
async def test_execute_atlas_rag_query_isolates_partial_failures(monkeypatch):
    """One backend error must not discard another server's answer. Failures are
    isolated per server-group (each server is queried independently)."""
    manager = _manager()

    class TwoServerRAG:
        # Two distinct HTTP servers so each is queried on its own.
        async def discover_data_sources(self, username, user_compliance_level=None):
            return [
                {"server": "srvA", "sources": [{"id": "docs"}]},
                {"server": "srvB", "sources": [{"id": "broken"}]},
            ]

        async def query_rag(self, username, qualified_data_source, messages, query=None, mode=None, search_kwargs=None, _skip_hooks=False):
            if qualified_data_source == "srvB:broken":
                raise RuntimeError("backend down")
            return SimpleNamespace(content=f"ok {qualified_data_source}", is_completion=False)

        async def query_rag_batch(self, username, qualified_data_sources, messages, query=None, mode=None, search_kwargs=None, _skip_hooks=False):
            raise AssertionError("each server has a single source; batch not expected")

    _patch_app_factory(monkeypatch, unified_rag=TwoServerRAG())

    result = await manager.execute_tool(
        ToolCall(
            id="call-partial",
            name="atlas_rag_query",
            arguments={
                "query": "policy",
                "data_sources": ["srvA:docs", "srvB:broken"],
            },
        ),
        context={"user_email": "test@example.com"},
    )

    assert result.success is True  # partial success
    payload = json.loads(result.content)
    answers = payload["results"]["answers"]
    assert [a["data_sources"] for a in answers] == [["srvA:docs"]]
    errors = payload["results"]["errors"]
    assert errors[0]["data_sources"] == ["srvB:broken"]
    assert "backend down" in errors[0]["error"]


@pytest.mark.asyncio
async def test_execute_atlas_rag_query_all_failures_reports_failure(monkeypatch):
    """If every source query fails, the tool result is unsuccessful."""
    manager = _manager()

    class BrokenUnifiedRAG(FakeUnifiedRAG):
        async def query_rag(self, username, qualified_data_source, messages, query=None, mode=None, search_kwargs=None, _skip_hooks=False):
            raise RuntimeError("total outage")

    unified = BrokenUnifiedRAG(discovered=["technical-docs"])
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(id="call-allfail", name="atlas_rag_query", arguments={"query": "x"}),
        context={"user_email": "test@example.com", "selected_data_sources": ["atlas_rag:technical-docs"]},
    )

    assert result.success is False
    assert result.error == "All RAG source queries failed"


@pytest.mark.asyncio
async def test_execute_atlas_rag_query_no_sources_available(monkeypatch):
    """No discovered sources and no explicit selection -> graceful failure."""
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=[])
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(id="call-empty", name="atlas_rag_query", arguments={"query": "hi"}),
        context={"user_email": "test@example.com"},
    )

    assert result.success is False
    assert unified.query_calls == []
    assert unified.batch_calls == []


@pytest.mark.asyncio
async def test_execute_atlas_rag_query_falls_back_when_no_selection(monkeypatch):
    """No ``selected_data_sources`` key at all means "the user chose nothing
    specific" -- query everything they are authorized for.

    This is the common agent-mode case; collapsing it to ``[]`` upstream would
    take the "explicitly no sources" branch and break RAG for every such turn.
    """
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["technical-docs", "policies"])
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(id="call-fb", name="atlas_rag_query", arguments={"query": "q"}),
        context={"user_email": "test@example.com"},
    )

    assert result.success is True
    queried = sorted(unified.query_calls + [s for c in unified.batch_calls for s in c])
    assert queried == ["atlas_rag:policies", "atlas_rag:technical-docs"]


@pytest.mark.asyncio
async def test_execute_atlas_rag_query_honors_explicit_empty_selection(monkeypatch):
    """An explicit empty list is a ceiling of zero, not "unset".

    A UserPromptSubmit hook narrowing the turn to no sources must not be
    widened back to the user's full authorized set.
    """
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["technical-docs", "policies"])
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(id="call-empty", name="atlas_rag_query", arguments={"query": "q"}),
        context={"user_email": "test@example.com", "selected_data_sources": []},
    )

    assert result.success is False
    assert unified.query_calls == []
    assert unified.batch_calls == []


def test_search_is_not_advertised_when_rag_is_disabled(monkeypatch):
    """A disabled built-in is omitted, not advertised-and-refused."""
    manager = _manager()
    _enable_search(monkeypatch, enabled=False)

    assert manager.get_tools_schema(["atlas_search"]) == []


@pytest.mark.asyncio
async def test_atlas_search_uses_the_selected_sources(monkeypatch):
    """#855: the UI selection is what search reads."""
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["technical-docs", "policies"])
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(id="s-1", name="atlas_search", arguments={"query": "vacation policy"}),
        context={
            "user_email": "test@example.com",
            "selected_data_sources": ["atlas_rag:policies"],
        },
    )

    assert result.success is True
    assert unified.query_calls == ["atlas_rag:policies"]


@pytest.mark.asyncio
async def test_atlas_search_ignores_model_supplied_sources(monkeypatch):
    """Search takes a query only; a hallucinated ``data_sources`` cannot widen it.

    The authorization gate already intersects with the user's discovered set,
    so this is defence in depth rather than the only barrier -- but a model must
    not be able to reach past the user's selection to another authorized source.
    """
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["technical-docs", "policies"])
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(
            id="s-2",
            name="atlas_search",
            arguments={
                "query": "vacation policy",
                "data_sources": ["atlas_rag:technical-docs"],
                "mode": "synthesized",
            },
        ),
        context={
            "user_email": "test@example.com",
            "selected_data_sources": ["atlas_rag:policies"],
        },
    )

    assert result.success is True
    assert unified.query_calls == ["atlas_rag:policies"]


@pytest.mark.asyncio
async def test_atlas_search_requires_a_query(monkeypatch):
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["policies"])
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(id="s-3", name="atlas_search", arguments={"query": "   "}),
        context={"user_email": "test@example.com"},
    )

    assert result.success is False
    assert "atlas_search" in result.content


@pytest.mark.asyncio
async def test_atlas_search_refuses_to_execute_when_rag_is_disabled(monkeypatch):
    """Schema omission stops the model being offered it; this stops a replay.

    A saved conversation or a non-UI client can name a tool the schema never
    advertised, so the feature flag is enforced at execution too.
    """
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["policies"])
    _patch_app_factory(monkeypatch, unified_rag=unified)
    _enable_search(monkeypatch, enabled=False)

    result = await manager.execute_tool(
        ToolCall(id="s-off", name="atlas_search", arguments={"query": "anything"}),
        context={"user_email": "test@example.com"},
    )

    assert result.success is False
    assert "disabled" in result.content
    assert unified.query_calls == []


@pytest.mark.asyncio
async def test_discover_sources_executes_under_the_consolidated_name(monkeypatch):
    """#855 follow-up: discovery is a first-class ``atlas`` tool again."""
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["technical-docs", "hr-policies"])
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(id="call-1", name="atlas_discover_sources", arguments={}),
        context={"user_email": "test@example.com"},
    )

    assert result.success is True
    payload = json.loads(result.content)
    assert payload["results"]["sources"] == [
        "atlas_rag:technical-docs",
        "atlas_rag:hr-policies",
    ]
    # Discovery lists; it must not query.
    assert unified.query_calls == []


@pytest.mark.asyncio
async def test_discover_sources_still_executes_under_the_legacy_name(monkeypatch):
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["technical-docs"])
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(id="call-1", name="atlas_rag_discover_data_sources", arguments={}),
        context={"user_email": "test@example.com"},
    )

    assert result.success is True
    assert json.loads(result.content)["results"]["sources"] == [
        "atlas_rag:technical-docs"
    ]


@pytest.mark.asyncio
async def test_discover_sources_is_refused_when_rag_is_disabled(monkeypatch):
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["technical-docs"])
    _patch_app_factory(monkeypatch, unified_rag=unified)
    _enable_search(monkeypatch, enabled=False)

    result = await manager.execute_tool(
        ToolCall(id="call-1", name="atlas_discover_sources", arguments={}),
        context={"user_email": "test@example.com"},
    )

    assert result.success is False
    # Nothing about the user's sources leaks from a refused call.
    assert unified.query_calls == []


@pytest.mark.asyncio
async def test_atlas_search_passes_depth_and_max_results_to_the_backend(monkeypatch):
    """The two optional knobs reach retrieval as v2 ``search_kwargs``."""
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["technical-docs"])
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(
            id="call-1",
            name="atlas_search",
            arguments={"query": "vacation policy", "max_results": 12, "depth": "deep"},
        ),
        context={
            "user_email": "test@example.com",
            "selected_data_sources": ["atlas_rag:technical-docs"],
        },
    )

    assert result.success is True
    assert unified.search_kwargs_calls[0]["top_k_final"] == 12
    assert unified.search_kwargs_calls[0]["top_k_vector"] == 20


@pytest.mark.asyncio
async def test_atlas_search_without_knobs_leaves_the_source_config_in_charge(monkeypatch):
    manager = _manager()
    unified = FakeUnifiedRAG(discovered=["technical-docs"])
    _patch_app_factory(monkeypatch, unified_rag=unified)

    result = await manager.execute_tool(
        ToolCall(
            id="call-1",
            name="atlas_search",
            arguments={"query": "vacation policy"},
        ),
        context={
            "user_email": "test@example.com",
            "selected_data_sources": ["atlas_rag:technical-docs"],
        },
    )

    assert result.success is True
    assert unified.search_kwargs_calls == [None]


# ---------------------------------------------------------------------------
# Citations (issue #874)
# ---------------------------------------------------------------------------


class CitingRAG(FakeUnifiedRAG):
    """Unified RAG fake whose answers carry document metadata."""

    def __init__(self, docs_by_source, discovered=None):
        super().__init__(discovered=discovered)
        self.docs_by_source = docs_by_source

    def _response(self, source):
        docs = [
            SimpleNamespace(
                title=title, source=source, url=url, citation=None,
                document_ref=ref, sections=[],
            )
            for title, url, ref in self.docs_by_source.get(source, [])
        ]
        return SimpleNamespace(
            content=f"Result from {source}",
            is_completion=False,
            metadata=SimpleNamespace(documents_found=docs),
        )

    async def query_rag(self, username, qualified_data_source, messages, query=None, mode=None, search_kwargs=None, _skip_hooks=False):
        self.query_calls.append(qualified_data_source)
        return self._response(qualified_data_source)


async def _search(manager, register, query="q", tool_id="c1"):
    from atlas.domain.chat.citation_register import CITATION_REGISTER_KEY

    return await manager.execute_tool(
        ToolCall(id=tool_id, name="atlas_search", arguments={"query": query}),
        context={
            "user_email": "u@example.com",
            CITATION_REGISTER_KEY: register,
        },
    )


@pytest.mark.asyncio
async def test_search_numbers_its_references_and_shows_the_model_the_numbers(monkeypatch):
    """The model can only cite a number it has been shown, so the tool result
    carries both the numbered reference list and a header naming them."""
    from atlas.domain.chat.citation_register import CitationRegister

    manager = _manager()
    _patch_app_factory(monkeypatch, unified_rag=CitingRAG(
        {"atlas_rag:technical-docs": [("Runbook.md", "https://x/runbook", 11)]},
    ))
    register = CitationRegister()

    result = await _search(manager, register)

    assert result.success is True
    payload = json.loads(result.content)
    answer = payload["results"]["answers"][0]
    assert answer["references"] == [
        {"n": 1, "filename": "Runbook.md", "url": "https://x/runbook"}
    ]
    # The header the model reads before the passages.
    assert "[1] Runbook.md" in answer["content"]
    assert "Result from atlas_rag:technical-docs" in answer["content"]


@pytest.mark.asyncio
async def test_two_searches_in_one_turn_share_one_numbering(monkeypatch):
    """The multi-call case: a document found twice keeps its number, and a new
    one continues the count rather than restarting at [1]."""
    from atlas.domain.chat.citation_register import CitationRegister

    manager = _manager()
    rag = CitingRAG(
        {"atlas_rag:technical-docs": [("Runbook.md", "https://x/runbook", 11)]},
    )
    _patch_app_factory(monkeypatch, unified_rag=rag)
    register = CitationRegister()

    first = await _search(manager, register, query="one", tool_id="c1")
    # The second search finds the same document plus a new one.
    rag.docs_by_source["atlas_rag:technical-docs"] = [
        ("Runbook.md", "https://x/runbook", 11),
        ("Policy.pdf", "https://x/policy", 12),
    ]
    second = await _search(manager, register, query="two", tool_id="c2")

    first_refs = json.loads(first.content)["results"]["answers"][0]["references"]
    second_refs = json.loads(second.content)["results"]["answers"][0]["references"]
    assert [r["n"] for r in first_refs] == [1]
    assert [r["n"] for r in second_refs] == [1, 2]
    assert [e["n"] for e in register.entries()] == [1, 2]
    assert register.entries()[1]["filename"] == "Policy.pdf"


@pytest.mark.asyncio
async def test_search_without_a_register_still_returns_unnumbered_references(monkeypatch):
    """A direct call (no turn, so no register) must not lose the identity list
    it returned before #874."""
    manager = _manager()
    _patch_app_factory(monkeypatch, unified_rag=CitingRAG(
        {"atlas_rag:technical-docs": [("Runbook.md", "https://x/runbook", 11)]},
    ))

    result = await manager.execute_tool(
        ToolCall(id="c-plain", name="atlas_search", arguments={"query": "q"}),
        context={"user_email": "u@example.com"},
    )

    answer = json.loads(result.content)["results"]["answers"][0]
    assert answer["references"] == [
        {"document_ref": 11, "filename": "Runbook.md", "url": "https://x/runbook"}
    ]
    # Nothing was numbered, so nothing claims a number.
    assert "[1]" not in answer["content"]
