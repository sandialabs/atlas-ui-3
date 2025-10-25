import types
import pytest
import os
import sys

# Ensure backend root is on path (same approach used in other tests)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Patch fastmcp Client usage by MCPToolManager with a fake client/manager
@pytest.fixture(autouse=True)
def patch_mcp(monkeypatch):
    from modules.mcp_tools.client import MCPToolManager

    class FakeTool:
        def __init__(self, name, description="", inputSchema=None):
            self.name = name
            self.description = description
            self.inputSchema = inputSchema or {"type": "object", "properties": {"username": {"type": "string"}}}

    class FakeClient:
        def __init__(self, server_name):
            self.server_name = server_name
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def call_tool(self, tool_name, arguments, **kwargs):
            # Provide deterministic results for test
            if tool_name == "rag_discover_resources":
                if self.server_name == "docsRag":
                    return types.SimpleNamespace(
                        structured_content={
                            "results": {
                                "resources": [
                                    {"id": "handbook", "name": "Employee Handbook", "authRequired": True, "groups": ["hr"], "defaultSelected": True},
                                    {"id": "legal", "name": "Legal Docs", "authRequired": True, "groups": ["legal"]},
                                ]
                            }
                        }
                    )
                elif self.server_name == "notionRag":
                    return types.SimpleNamespace(
                        structured_content={
                            "results": {
                                "resources": [
                                    {"id": "notion-space", "name": "Notion Space", "authRequired": True, "groups": ["notion"]}
                                ]
                            }
                        }
                    )
            if tool_name == "rag_get_raw_results":
                # Return hits with scores
                return types.SimpleNamespace(
                    structured_content={
                        "results": {
                            "hits": [
                                {"id": f"{self.server_name}-1", "score": 0.9, "resourceId": arguments.get("sources", [""])[0]},
                                {"id": f"{self.server_name}-2", "score": 0.5, "resourceId": arguments.get("sources", [""])[-1]},
                            ],
                            "stats": {"top_k": arguments.get("top_k", 8)},
                        }
                    }
                )
            if tool_name == "rag_get_synthesized_results":
                return types.SimpleNamespace(
                    structured_content={
                        "results": {
                            "answer": f"Answer from {self.server_name}",
                            "citations": [{"resourceId": r} for r in arguments.get("sources", [])],
                        }
                    }
                )
            return types.SimpleNamespace(structured_content={})

    async def fake_initialize_clients(self):
        # Pretend both servers are configured and online
        self.clients = {"docsRag": FakeClient("docsRag"), "notionRag": FakeClient("notionRag")}

    async def fake_discover_tools(self):
        # Expose RAG tools on docsRag; notionRag only discovery/raw
        self.available_tools = {
            "docsRag": {
                "tools": [FakeTool("rag_discover_resources"), FakeTool("rag_get_raw_results"), FakeTool("rag_get_synthesized_results")],
                "config": {"description": "Docs RAG"},
            },
            "notionRag": {
                "tools": [FakeTool("rag_discover_resources"), FakeTool("rag_get_raw_results")],
                "config": {"description": "Notion RAG"},
            },
        }
        # Also set servers config for UI fields
        self.servers_config = {
            "docsRag": {"description": "Docs RAG", "ui": {"icon": "book"}},
            "notionRag": {"description": "Notion", "ui": {"icon": "notion"}},
        }

    async def fake_discover_prompts(self):
        self.available_prompts = {}

    def fake_get_authorized_servers(self, user_email, auth_check_func):
        # Simple ACL: allow both for @company.com; only docsRag otherwise
        return ["docsRag", "notionRag"] if user_email.endswith("@company.com") else ["docsRag"]

    async def fake_call_tool(self, server_name, tool_name, arguments, **kwargs):
        return await self.clients[server_name].call_tool(tool_name, arguments)

    monkeypatch.setattr(MCPToolManager, "initialize_clients", fake_initialize_clients, raising=False)
    monkeypatch.setattr(MCPToolManager, "discover_tools", fake_discover_tools, raising=False)
    monkeypatch.setattr(MCPToolManager, "discover_prompts", fake_discover_prompts, raising=False)
    monkeypatch.setattr(MCPToolManager, "get_authorized_servers", fake_get_authorized_servers, raising=False)
    monkeypatch.setattr(MCPToolManager, "call_tool", fake_call_tool, raising=False)


@pytest.mark.asyncio
async def test_discovery_across_multiple_servers():
    from infrastructure.app_factory import app_factory
    # Initialize MCP
    mcp = app_factory.get_mcp_manager()
    await mcp.initialize_clients()
    await mcp.discover_tools()
    await mcp.discover_prompts()

    from domain.rag_mcp_service import RAGMCPService
    from core.auth import is_user_in_group

    svc = RAGMCPService(mcp, app_factory.get_config_manager(), is_user_in_group)
    # user with @company.com gets both servers
    sources = await svc.discover_data_sources("alice@company.com")
    assert "docsRag:handbook" in sources
    assert "docsRag:legal" in sources
    assert "notionRag:notion-space" in sources

    # richer servers
    servers = await svc.discover_servers("alice@company.com")
    assert any(s["server"] == "docsRag" and s["sources"] for s in servers)


@pytest.mark.asyncio
async def test_acl_filtering():
    from infrastructure.app_factory import app_factory
    from domain.rag_mcp_service import RAGMCPService
    from core.auth import is_user_in_group

    mcp = app_factory.get_mcp_manager()
    await mcp.initialize_clients()
    await mcp.discover_tools()
    await mcp.discover_prompts()

    svc = RAGMCPService(mcp, app_factory.get_config_manager(), is_user_in_group)
    # Non-company user only sees docsRag
    sources = await svc.discover_data_sources("bob@public.net")
    assert all(s.startswith("docsRag:") for s in sources)


@pytest.mark.asyncio
async def test_search_and_synthesize_merge():
    from infrastructure.app_factory import app_factory
    from domain.rag_mcp_service import RAGMCPService
    from core.auth import is_user_in_group

    mcp = app_factory.get_mcp_manager()
    await mcp.initialize_clients()
    await mcp.discover_tools()
    await mcp.discover_prompts()

    svc = RAGMCPService(mcp, app_factory.get_config_manager(), is_user_in_group)
    sources = ["docsRag:handbook", "notionRag:notion-space"]
    res = await svc.search_raw("alice@company.com", "vacation policy", sources, top_k=1)
    assert "results" in res and "hits" in res["results"]
    assert len(res["results"]["hits"]) == 1  # limited by top_k

    syn = await svc.synthesize("alice@company.com", "vacation policy", sources, top_k=2)
    assert "results" in syn and "answer" in syn["results"]
