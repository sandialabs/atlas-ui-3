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


class FakeUnifiedRAG:
    """Configurable fake unified RAG service for exercising the pseudo-tools.

    ``discovered`` controls the user's authorized/discovered source set.
    Calls are recorded so tests can assert routing (single vs batch).
    """

    def __init__(self, discovered=None):
        self.discovered = discovered if discovered is not None else ["technical-docs"]
        self.query_calls = []
        self.batch_calls = []

    async def discover_data_sources(self, username, user_compliance_level=None):
        return [
            {
                "server": "atlas_rag",
                "sources": [{"id": src} for src in self.discovered],
            }
        ]

    async def query_rag(self, username, qualified_data_source, messages):
        self.query_calls.append(qualified_data_source)
        return SimpleNamespace(
            content=f"Result from {qualified_data_source}", is_completion=False
        )

    async def query_rag_batch(self, username, qualified_data_sources, messages):
        self.batch_calls.append(list(qualified_data_sources))
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


def test_get_tools_schema_includes_atlas_rag_pseudo_tools():
    manager = _manager()
    schemas = manager.get_tools_schema(
        ["atlas_rag_discover_data_sources", "atlas_rag_query"]
    )

    names = {schema["function"]["name"] for schema in schemas}
    assert "atlas_rag_discover_data_sources" in names
    assert "atlas_rag_query" in names
    assert manager.get_server_for_tool("atlas_rag_query") == "atlas_rag"


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

        async def query_rag(self, username, qualified_data_source, messages):
            if qualified_data_source == "srvB:broken":
                raise RuntimeError("backend down")
            return SimpleNamespace(content=f"ok {qualified_data_source}", is_completion=False)

        async def query_rag_batch(self, username, qualified_data_sources, messages):
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
        async def query_rag(self, username, qualified_data_source, messages):
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
