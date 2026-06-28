"""Tests for Atlas RAG pseudo MCP tools used by agent mode."""

import json
from types import SimpleNamespace

import pytest

from atlas.domain.messages.models import ToolCall
from atlas.modules.mcp_tools.client import MCPToolManager


def _manager() -> MCPToolManager:
    return MCPToolManager(config_path="/tmp/atlas-noop-mcp.json")


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

    class FakeUnifiedRAG:
        async def discover_data_sources(self, username, user_compliance_level=None):
            return [
                {
                    "server": "atlas_rag",
                    "sources": [{"id": "technical-docs"}],
                }
            ]

        async def query_rag(self, username, qualified_data_source, messages):
            return SimpleNamespace(content=f"Result from {qualified_data_source}", is_completion=False)

        async def query_rag_batch(self, username, qualified_data_sources, messages):
            raise AssertionError("query_rag_batch should not be called for one source")

    class FakeAppFactory:
        def get_unified_rag_service(self):
            return FakeUnifiedRAG()

        def get_rag_mcp_service(self):
            return None

    monkeypatch.setattr("atlas.infrastructure.app_factory.app_factory", FakeAppFactory())

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
