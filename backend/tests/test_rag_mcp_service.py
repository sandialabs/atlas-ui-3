import asyncio
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

    def get_authorized_servers(self, user: str, _auth) -> List[str]:
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


class FakeConfig:
    pass


def fake_auth_check(user: str, group: str) -> bool:
    return True


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
