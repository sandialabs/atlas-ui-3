import types
from typing import Any, Dict, List

import os
import sys
import pytest

# Ensure backend root is on path (same approach used in other tests)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class FakeTool:
    def __init__(self, name: str):
        self.name = name


class FakeMCP:
    def __init__(self):
        # Simulate available tools config per server
        self.available_tools: Dict[str, Dict[str, Any]] = {
            "docsRag": {"tools": [FakeTool("rag_discover_resources"), FakeTool("rag_get_raw_results")], "config": {"ui": {"icon": "book"}}},
            "searchRag": {"tools": [FakeTool("rag_discover_resources"), FakeTool("rag_get_raw_results"), FakeTool("rag_get_synthesized_results")], "config": {}},
            "misc": {"tools": [FakeTool("other")], "config": {}},
        }

    async def get_authorized_servers(self, user: str, _auth) -> List[str]:
        # User bob can see all, alice cannot see misc
        if user.startswith("alice"):
            return ["docsRag", "searchRag"]
        return list(self.available_tools.keys())

    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any], *_, **__):
        # Return minimal v2-structured payloads
        if tool_name == "rag_discover_resources":
            if server_name == "docsRag":
                return types.SimpleNamespace(structured_content={
                    "results": {"resources": [
                        {"id": "handbook", "name": "Employee Handbook", "authRequired": True, "groups": ["hr"], "defaultSelected": True},
                        {"id": "legal", "name": "Legal Docs", "authRequired": True, "groups": ["legal"]},
                    ]}
                })
            if server_name == "searchRag":
                return types.SimpleNamespace(structured_content={
                    "results": {"resources": [
                        {"id": "kb", "name": "KB", "authRequired": True, "groups": ["kb"]}
                    ]}
                })
        if tool_name == "rag_get_raw_results":
            q = arguments.get("query")
            srcs = arguments.get("sources", [])
            hits = []
            for i, s in enumerate(srcs):
                hits.append({
                    "id": f"{server_name}-{s}-{i}",
                    "score": 1.0 - i * 0.01,
                    "resourceId": f"{server_name}:{s}",
                    "title": f"{q} in {s}",
                })
            return types.SimpleNamespace(structured_content={"results": {"hits": hits}})
        if tool_name == "rag_get_synthesized_results":
            return types.SimpleNamespace(structured_content={
                "results": {"answer": f"Synth for {arguments.get('query')} by {server_name}"}
            })
        return types.SimpleNamespace(structured_content={})


class FakeMCPServerConfig:
    """Minimal server config for testing."""
    def __init__(self, enabled=True, groups=None):
        self.enabled = enabled
        self.groups = groups or []


class FakeMCPConfig:
    """Minimal MCP config for testing."""
    def __init__(self, servers=None):
        self.servers = servers or {}


class FakeConfig:
    """Fake config manager for testing RAGMCPService."""
    def __init__(self, rag_servers=None):
        # Default RAG servers matching FakeMCP.available_tools
        if rag_servers is None:
            rag_servers = {
                "docsRag": FakeMCPServerConfig(enabled=True, groups=["users"]),
                "searchRag": FakeMCPServerConfig(enabled=True, groups=["users"]),
                "misc": FakeMCPServerConfig(enabled=True, groups=["users"]),
            }
        self._rag_mcp_config = FakeMCPConfig(servers=rag_servers)

    @property
    def rag_mcp_config(self):
        return self._rag_mcp_config


async def fake_auth_check(user: str, group: str) -> bool:
    """Default auth check - everyone is in 'users' group."""
    return group == "users"


@pytest.mark.asyncio
async def test_discovery_flat_and_rich():
    from domain.rag_mcp_service import RAGMCPService

    svc = RAGMCPService(FakeMCP(), FakeConfig(), fake_auth_check)

    flat = await svc.discover_data_sources("bob@example.com")
    # misc has no rag_discover_resources, excluded
    assert set(flat) == {"docsRag:handbook", "docsRag:legal", "searchRag:kb"}

    rich = await svc.discover_servers("alice@example.com")
    servers = {d["server"] for d in rich}
    assert servers == {"docsRag", "searchRag"}
    # docsRag must include two sources with defaultSelected on handbook
    dr = next(s for s in rich if s["server"] == "docsRag")
    assert any(x.get("selected") for x in dr["sources"])  # handbook default selected


@pytest.mark.asyncio
async def test_search_and_synthesize_merge():
    from domain.rag_mcp_service import RAGMCPService

    svc = RAGMCPService(FakeMCP(), FakeConfig(), fake_auth_check)
    res = await svc.search_raw(
        username="bob@example.com",
        query="policy",
        sources=["docsRag:handbook", "searchRag:kb"],
        top_k=2,
    )
    hits = res.get("results", {}).get("hits", [])
    assert len(hits) == 2
    # resourceId should be qualified
    assert all(":" in h.get("resourceId", "") for h in hits)

    syn = await svc.synthesize(
        username="alice@example.com",
        query="benefits",
        sources=["searchRag:kb"],
    )
    answer = syn.get("results", {}).get("answer")
    assert isinstance(answer, str) and "Synth for" in answer


@pytest.mark.asyncio
async def test_rag_authorization_uses_rag_config_not_mcp_servers_config():
    """
    Regression test: RAG authorization must use rag_mcp_config, not mcp_manager.servers_config.

    Bug context: RAGMCPService temporarily adds RAG servers to mcp_manager.servers_config
    for initialization, then restores the original config. If authorization checks
    use servers_config (which no longer has RAG servers), no RAG sources are returned.

    The fix is to check authorization directly against rag_mcp_config.servers.
    """
    from domain.rag_mcp_service import RAGMCPService

    class MCPWithEmptyServersConfig:
        """MCP manager with empty servers_config (RAG servers only in rag_mcp_config)."""
        def __init__(self):
            # Simulate RAG servers being initialized but NOT in servers_config
            self.servers_config = {}  # Empty! RAG servers were temporarily added then removed
            self.clients = {"ragServer": object()}  # Client exists (was initialized)
            self.available_tools = {
                "ragServer": {
                    "tools": [FakeTool("rag_discover_resources")],
                    "config": {}
                }
            }

        async def get_authorized_servers(self, user: str, _auth) -> List[str]:
            # This would return [] because servers_config is empty
            return list(self.servers_config.keys())

        async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any], *_, **__):
            if tool_name == "rag_discover_resources":
                return types.SimpleNamespace(structured_content={
                    "results": {"resources": [
                        {"id": "doc1", "name": "Document 1"}
                    ]}
                })
            return types.SimpleNamespace(structured_content={})

    # RAG server configured in rag_mcp_config
    rag_servers = {
        "ragServer": FakeMCPServerConfig(enabled=True, groups=["users"])
    }
    fake_config = FakeConfig(rag_servers=rag_servers)

    svc = RAGMCPService(MCPWithEmptyServersConfig(), fake_config, fake_auth_check)

    # This should find ragServer even though mcp_manager.servers_config is empty
    flat = await svc.discover_data_sources("user@example.com")

    # Before the fix, this would return [] because authorization checked servers_config
    # After the fix, this returns the RAG sources because authorization uses rag_mcp_config
    assert "ragServer:doc1" in flat, \
        "RAG authorization should use rag_mcp_config, not mcp_manager.servers_config"


@pytest.mark.asyncio
async def test_rag_group_filtering():
    """Test that RAG sources are properly filtered by group membership."""
    from domain.rag_mcp_service import RAGMCPService

    # Server requires 'admin' group, not 'users'
    rag_servers = {
        "docsRag": FakeMCPServerConfig(enabled=True, groups=["admin"]),
        "searchRag": FakeMCPServerConfig(enabled=True, groups=["users"]),
        "misc": FakeMCPServerConfig(enabled=True, groups=["users"]),
    }

    async def restricted_auth_check(user: str, group: str) -> bool:
        # User is only in 'users' group, not 'admin'
        return group == "users"

    svc = RAGMCPService(FakeMCP(), FakeConfig(rag_servers=rag_servers), restricted_auth_check)

    flat = await svc.discover_data_sources("user@example.com")

    # docsRag requires 'admin' group, user is not in admin
    assert "docsRag:handbook" not in flat, "Admin-only RAG server should not be visible"
    # searchRag is in 'users' group
    assert "searchRag:kb" in flat, "User-accessible RAG server should be visible"
