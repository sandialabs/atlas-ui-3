"""Regression tests: RAG MCP discovery must not pollute the tools inventory.

Acceptance criteria (from issue):
- RAG MCP servers never appear in tool discovery outputs or tool selection UI.
- _mcp_data does not include RAG-only servers.
"""

import os
import sys
import types
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class FakeTool:
    def __init__(self, name: str):
        self.name = name


class FakeMCPServerConfig:
    """Minimal Pydantic-like server config for testing."""

    def __init__(self, enabled=True, groups=None):
        self.enabled = enabled
        self.groups = groups or []

    def model_dump(self) -> Dict[str, Any]:
        return {"enabled": self.enabled, "groups": self.groups}


class FakeRagMcpConfig:
    def __init__(self, servers):
        self.servers = servers


class FakeConfigManager:
    """Fake config manager that exposes rag_mcp_config."""

    def __init__(self, rag_servers):
        self._rag = FakeRagMcpConfig(rag_servers)

    @property
    def rag_mcp_config(self):
        return self._rag


class FakeMCPManager:
    """Simulates MCPToolManager with separate rag_available_tools store."""

    def __init__(self, normal_tools, rag_tools):
        # servers_config only contains non-RAG servers
        self.servers_config: Dict[str, Any] = {k: {} for k in normal_tools}
        self.available_tools: Dict[str, Any] = dict(normal_tools)
        self.rag_available_tools: Dict[str, Any] = dict(rag_tools)
        self.clients: Dict[str, Any] = {}
        self._initialized_rag: List[str] = []

    async def initialize_rag_servers(self, rag_servers_config: Dict[str, Any]) -> None:
        """Record which servers were requested; no actual I/O in tests."""
        self._initialized_rag.extend(rag_servers_config.keys())

    async def call_tool(self, server_name, tool_name, arguments, *_, **__):
        if tool_name == "rag_discover_resources":
            return types.SimpleNamespace(structured_content={
                "results": {"resources": [{"id": "doc1", "name": "Doc 1"}]}
            })
        return types.SimpleNamespace(structured_content={})


async def _allow_all(user: str, group: str) -> bool:
    return True


# ---------------------------------------------------------------------------
# Test: RAG servers absent from build_mcp_data
# ---------------------------------------------------------------------------


class TestBuildMcpDataExcludesRagServers:
    """build_mcp_data must never include RAG-only servers."""

    def _manager_with_rag_leaked_into_available_tools(self):
        """Simulate the old buggy state where a RAG server ended up in available_tools."""
        from unittest.mock import MagicMock

        mgr = MagicMock()
        mgr.servers_config = {"normalServer": {}}
        mgr.available_tools = {
            "normalServer": {"tools": [FakeTool("do_work")], "config": {}},
            "ragServer": {"tools": [FakeTool("rag_discover_resources")], "config": {}},
        }
        return mgr

    def test_rag_server_excluded_from_mcp_data(self):
        from atlas.application.chat.utilities.tool_executor import build_mcp_data

        mgr = self._manager_with_rag_leaked_into_available_tools()
        result = build_mcp_data(mgr)

        server_names = [s["server_name"] for s in result["available_servers"]]
        assert "ragServer" not in server_names, (
            "RAG-only server must not appear in _mcp_data"
        )
        assert "normalServer" in server_names, (
            "Normal server must still appear in _mcp_data"
        )


# ---------------------------------------------------------------------------
# Test: RAG discovery uses rag_available_tools, not available_tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rag_discovery_uses_rag_available_tools():
    """discover_data_sources must read tool listings from rag_available_tools, not available_tools."""
    from atlas.domain.rag_mcp_service import RAGMCPService

    rag_tools = {
        "ragServer": {
            "tools": [FakeTool("rag_discover_resources")],
            "config": {},
        }
    }
    # available_tools intentionally empty to prove RAG service reads from rag_available_tools
    mgr = FakeMCPManager(normal_tools={}, rag_tools=rag_tools)

    rag_servers = {"ragServer": FakeMCPServerConfig(enabled=True)}
    config = FakeConfigManager(rag_servers=rag_servers)

    svc = RAGMCPService(mgr, config, _allow_all)
    sources = await svc.discover_data_sources("user@example.com")

    assert "ragServer:doc1" in sources, (
        "RAG discovery must find sources from rag_available_tools"
    )


@pytest.mark.asyncio
async def test_rag_servers_never_leak_into_available_tools():
    """After RAG discovery, available_tools must NOT contain RAG servers."""
    from atlas.domain.rag_mcp_service import RAGMCPService

    rag_tools = {
        "ragServer": {
            "tools": [FakeTool("rag_discover_resources")],
            "config": {},
        }
    }
    normal_tools = {
        "normalServer": {"tools": [FakeTool("do_work")], "config": {}},
    }
    mgr = FakeMCPManager(normal_tools=normal_tools, rag_tools=rag_tools)

    rag_servers = {"ragServer": FakeMCPServerConfig(enabled=True)}
    config = FakeConfigManager(rag_servers=rag_servers)

    svc = RAGMCPService(mgr, config, _allow_all)
    await svc.discover_data_sources("user@example.com")

    # servers_config must not have been modified
    assert "ragServer" not in mgr.servers_config, (
        "RAG server must not appear in servers_config after discovery"
    )
    # available_tools must not have been modified
    assert "ragServer" not in mgr.available_tools, (
        "RAG server must not appear in available_tools after discovery"
    )
    assert "normalServer" in mgr.available_tools, (
        "Normal server must remain in available_tools"
    )


@pytest.mark.asyncio
async def test_initialize_rag_servers_called_not_discover_tools():
    """RAG discovery must call initialize_rag_servers, not the global discover_tools."""
    from atlas.domain.rag_mcp_service import RAGMCPService

    class TrackingMCPManager(FakeMCPManager):
        def __init__(self):
            super().__init__(
                normal_tools={},
                rag_tools={"ragServer": {"tools": [FakeTool("rag_discover_resources")], "config": {}}},
            )
            self.discover_tools_called = False
            self.initialize_rag_called = False

        async def initialize_rag_servers(self, rag_servers_config):
            self.initialize_rag_called = True

        async def discover_tools(self):
            self.discover_tools_called = True

    mgr = TrackingMCPManager()
    rag_servers = {"ragServer": FakeMCPServerConfig(enabled=True)}
    config = FakeConfigManager(rag_servers=rag_servers)

    svc = RAGMCPService(mgr, config, _allow_all)
    await svc.discover_data_sources("user@example.com")

    assert mgr.initialize_rag_called, "initialize_rag_servers must be called"
    assert not mgr.discover_tools_called, (
        "discover_tools must NOT be called during RAG discovery (would reset available_tools)"
    )


@pytest.mark.asyncio
async def test_discover_servers_rag_isolation():
    """discover_servers must use rag_available_tools and not modify servers_config."""
    from atlas.domain.rag_mcp_service import RAGMCPService

    rag_tools = {
        "ragServer": {
            "tools": [FakeTool("rag_discover_resources")],
            "config": {"displayName": "RAG Server"},
        }
    }
    normal_tools = {
        "normalServer": {"tools": [FakeTool("do_work")], "config": {}},
    }
    mgr = FakeMCPManager(normal_tools=normal_tools, rag_tools=rag_tools)

    rag_servers = {"ragServer": FakeMCPServerConfig(enabled=True)}
    config = FakeConfigManager(rag_servers=rag_servers)

    svc = RAGMCPService(mgr, config, _allow_all)
    servers = await svc.discover_servers("user@example.com")

    server_names = [s["server"] for s in servers]
    assert "ragServer" in server_names, "RAG server must appear in discover_servers output"

    # servers_config must remain unchanged
    assert "ragServer" not in mgr.servers_config, (
        "RAG server must not be added to servers_config"
    )
    # available_tools must remain unchanged
    assert "ragServer" not in mgr.available_tools, (
        "RAG server must not appear in available_tools"
    )
