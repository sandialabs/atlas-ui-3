"""Server-side classification gates beyond the per-component config check (#1032).

Covers the per-corpus gate end to end through ``ChatService``, MCP RAG
discovery (per-resource classifications, inheritance, failure marking, the
initialization lock), the undefined-level rejection, and the model floor that
query-time RAG enforcement keeps when no classification is active.
"""

import asyncio
import json
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from atlas.core.compliance import ComplianceLevelManager
from atlas.domain.errors import DataSourcePermissionError, ValidationError
from atlas.modules.config.models import LLMConfig, MCPConfig, RAGSourcesConfig


@pytest.fixture
def manager(tmp_path):
    path = tmp_path / "compliance-levels.json"
    path.write_text(json.dumps({"levels": [
        {"name": "UUR", "allowed_with": ["UUR"]},
        {"name": "ITAR", "allowed_with": ["ITAR", "UUR"]},
    ]}))
    return ComplianceLevelManager(path)


@pytest.fixture
def use_manager(manager, monkeypatch):
    for target in (
        "atlas.core.compliance.get_compliance_manager",
        "atlas.domain.rag_mcp_service.get_compliance_manager",
        "atlas.domain.unified_rag_service.get_compliance_manager",
    ):
        monkeypatch.setattr(target, lambda: manager)
    return manager


# --- MCP RAG discovery -------------------------------------------------------


class _Tool:
    def __init__(self, name):
        self.name = name


class _FakeMCP:
    def __init__(self, resources_by_server, failing=()):
        self.clients = {name: object() for name in resources_by_server}
        self.servers_config = {}
        self.failing = set(failing)
        self.resources = resources_by_server
        self.available_tools = {
            # The runtime entry carries no classifications (a reload clears
            # it); the service must read them from rag_mcp_config.
            name: {"tools": [_Tool("rag_discover_resources")], "config": {}}
            for name in resources_by_server
        }

    async def call_tool(self, server_name, tool_name, arguments, *_, **__):
        if server_name in self.failing:
            raise RuntimeError("backend down")
        return types.SimpleNamespace(
            structured_content={"results": {"resources": self.resources[server_name]}}
        )


def _rag_mcp(fake):
    from atlas.domain.rag_mcp_service import RAGMCPService

    cfg = SimpleNamespace(rag_mcp_config=SimpleNamespace(servers={
        name: SimpleNamespace(
            enabled=True, groups=[], allowed_data_classifications=["UUR", "ITAR"],
            compliance_level=None,
        )
        for name in fake.resources
    }))

    async def auth(user, group):
        return True

    return RAGMCPService(fake, cfg, auth)


@pytest.mark.asyncio
async def test_mcp_resources_filter_per_resource_and_inherit(use_manager):
    fake = _FakeMCP({"docs": [
        {"id": "narrow", "allowedDataClassifications": ["UUR"]},
        {"id": "inherits"},
        {"id": "itar", "allowed_data_classifications": ["ITAR"]},
        # Legacy per-resource level is display-only (as for HTTP corpora).
        {"id": "cui_tagged", "complianceLevel": "CUI"},
    ]})
    servers = await _rag_mcp(fake).discover_servers("u@test.com", user_compliance_level="ITAR")
    (docs,) = servers
    assert [s["id"] for s in docs["sources"]] == ["inherits", "itar", "cui_tagged"]
    inherited = next(s for s in docs["sources"] if s["id"] == "inherits")
    assert inherited["allowedDataClassifications"] == ["UUR", "ITAR"]
    assert docs["discoveryFailed"] is False


@pytest.mark.asyncio
async def test_mcp_discovery_marks_failure_and_honours_only_servers(use_manager):
    fake = _FakeMCP({"up": [{"id": "a"}], "down": [{"id": "b"}]}, failing={"down"})
    servers = await _rag_mcp(fake).discover_servers(
        "u@test.com", user_compliance_level="ITAR", only_servers=["down"]
    )
    assert [(s["server"], s["discoveryFailed"]) for s in servers] == [("down", True)]


@pytest.mark.asyncio
async def test_concurrent_client_initialization_runs_once():
    """Concurrent callers must not interleave the servers_config swap."""
    from atlas.domain.rag_mcp_service import RAGMCPService

    calls = []

    class SlowMCP:
        def __init__(self):
            self.clients = {}
            self.servers_config = {"tools_server": {}}

        async def initialize_clients(self):
            calls.append(dict(self.servers_config))
            await asyncio.sleep(0.01)
            self.clients["rag"] = object()

        async def discover_tools(self):
            await asyncio.sleep(0.01)

    mcp = SlowMCP()
    cfg = SimpleNamespace(rag_mcp_config=SimpleNamespace(
        servers={"rag": SimpleNamespace(model_dump=lambda: {"url": "x"})}
    ))
    svc = RAGMCPService(mcp, cfg, None)
    await asyncio.gather(svc._ensure_rag_clients(), svc._ensure_rag_clients())
    assert len(calls) == 1
    assert mcp.servers_config == {"tools_server": {}}


# --- ChatService drives the per-corpus gate ----------------------------------


def _service(monkeypatch, *, unified_rag=None, rag_mcp=None):
    from atlas.application.chat.service import ChatService
    from atlas.infrastructure.app_factory import app_factory

    monkeypatch.setattr(app_factory, "get_unified_rag_service", lambda: unified_rag)
    monkeypatch.setattr(app_factory, "get_rag_mcp_service", lambda: rag_mcp)

    repo = MagicMock()
    repo.get = AsyncMock(return_value=None)
    repo.create = AsyncMock()
    cm = MagicMock()
    cm.app_settings.feature_compliance_levels_enabled = True
    cm.app_settings.compliance_level_required_effective = False
    cm.llm_config = LLMConfig(models={"m": {
        "model_name": "m", "model_url": "http://m", "allowed_data_classifications": ["UUR", "ITAR"],
    }})
    cm.mcp_config = MCPConfig()
    cm.rag_sources_config = RAGSourcesConfig(sources={
        "docs": {"type": "http", "url": "http://d", "allowed_data_classifications": ["UUR", "ITAR"]},
    })
    tool_manager = MagicMock()
    tool_manager.servers_config = {}
    return ChatService(
        llm=MagicMock(), tool_manager=tool_manager, connection=MagicMock(),
        config_manager=cm, session_repository=repo,
    ), repo


class _HttpDiscovery:
    def __init__(self, sources=None, fail=False):
        self.sources = sources or []
        self.fail = fail
        self.seen = None

    async def discover_data_sources(self, user, user_compliance_level=None, only_servers=None):
        self.seen = (user_compliance_level, only_servers)
        if self.fail:
            return []  # the HTTP source returned None: nothing answered
        return [{"server": "docs", "sources": [{"id": s} for s in self.sources]}]


async def _send(service, sources, level="ITAR"):
    return await service.handle_chat_message(
        session_id=uuid4(), content="hi", model="m", user_email="u@test.com",
        selected_data_sources=sources, compliance_level=level,
    )


@pytest.mark.asyncio
async def test_narrower_corpus_refuses_the_turn(use_manager, monkeypatch):
    discovery = _HttpDiscovery(sources=["open"])
    service, repo = _service(monkeypatch, unified_rag=discovery)
    with pytest.raises(ValidationError, match="Not approved for ITAR data: data source docs:uur_only"):
        await _send(service, ["docs:open", "docs:uur_only"])
    assert discovery.seen == ("ITAR", ["docs"])
    repo.get.assert_not_called()


@pytest.mark.asyncio
async def test_unanswered_discovery_is_not_reported_as_unapproved(use_manager, monkeypatch):
    service, repo = _service(monkeypatch, unified_rag=_HttpDiscovery(fail=True))
    with pytest.raises(ValidationError) as exc:
        await _send(service, ["docs:open"])
    assert "did not answer" in str(exc.value)
    assert "Not approved" not in str(exc.value)
    repo.get.assert_not_called()


@pytest.mark.asyncio
async def test_discovery_error_refuses_as_unchecked(use_manager, monkeypatch):
    class Broken:
        async def discover_data_sources(self, *a, **k):
            raise RuntimeError("boom")

    service, _ = _service(monkeypatch, unified_rag=Broken())
    with pytest.raises(ValidationError, match="could not be checked"):
        await _send(service, ["docs:open"])


@pytest.mark.asyncio
async def test_offered_corpus_proceeds(use_manager, monkeypatch):
    service, _ = _service(monkeypatch, unified_rag=_HttpDiscovery(sources=["open"]))
    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(return_value={"type": "done"})
    monkeypatch.setattr(service, "_get_orchestrator", lambda: orchestrator)
    await _send(service, ["docs:open"])
    orchestrator.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_undefined_level_is_rejected(use_manager, monkeypatch):
    """A misspelt level must not switch every check off."""
    service, repo = _service(monkeypatch)
    with pytest.raises(ValidationError, match="not defined"):
        await _send(service, None, level="ITRA")
    repo.get.assert_not_called()


# --- Model floor at query time when no classification is active --------------


@pytest.mark.asyncio
async def test_model_floor_applies_without_an_active_classification(use_manager):
    from atlas.core.compliance import (
        reset_model_classification_floor,
        set_model_classification_floor,
    )
    from atlas.domain.unified_rag_service import UnifiedRAGService

    service = object.__new__(UnifiedRAGService)

    async def authorized(*_):
        return True

    service._is_user_authorized = authorized

    def source(classifications):
        return SimpleNamespace(
            enabled=True, groups=[], compliance_level=None,
            allowed_data_classifications=classifications,
        )

    async def check(cfg):
        await service._ensure_source_query_allowed("u@test.com", "docs", cfg)

    token = set_model_classification_floor(["UUR"])
    try:
        await check(source(["UUR", "ITAR"]))
        await check(source(None))  # undeclared: the floor does not apply
        with pytest.raises(DataSourcePermissionError, match="any classification the selected model"):
            await check(source(["ITAR"]))
    finally:
        reset_model_classification_floor(token)
    # No floor (the model declares nothing): permissive, as before.
    await check(source(["ITAR"]))


@pytest.mark.asyncio
async def test_corpus_gate_never_reinitializes_rag_mcp_clients(use_manager):
    """A down MCP RAG server is reported unverified, not reconnected per turn."""
    from atlas.application.chat.policies.classification_policy import find_unapproved_corpora

    fake = _FakeMCP({"docs": [{"id": "a"}]})
    fake.clients = {}  # never connected
    fake.available_tools = {}

    async def explode():
        raise AssertionError("the per-turn gate must not initialize clients")

    fake.initialize_clients = explode
    cfg = SimpleNamespace(rag_sources_config=RAGSourcesConfig(sources={
        "docs": {"type": "mcp", "url": "http://m", "allowed_data_classifications": ["ITAR"]},
    }))
    unapproved, unverified = await find_unapproved_corpora(
        "ITAR", "u@test.com", ["docs:a"], rag_mcp=_rag_mcp(fake), config_manager=cfg,
    )
    assert (unapproved, unverified) == ([], ["docs:a"])


@pytest.mark.asyncio
async def test_mixed_unapproved_and_unverified_are_reported_together(use_manager, monkeypatch):
    class Mixed:
        async def discover_data_sources(self, user, user_compliance_level=None, only_servers=None):
            return [{"server": "docs", "sources": [{"id": "open"}]}]

    service, _ = _service(monkeypatch, unified_rag=Mixed())
    service.config_manager.rag_sources_config = RAGSourcesConfig(sources={
        "docs": {"type": "http", "url": "http://d", "allowed_data_classifications": ["UUR", "ITAR"]},
        "other": {"type": "http", "url": "http://o", "allowed_data_classifications": ["UUR", "ITAR"]},
    })
    with pytest.raises(ValidationError) as exc:
        await _send(service, ["docs:open", "docs:narrow", "other:x"])
    message = str(exc.value)
    assert "Not approved for ITAR data: data source docs:narrow" in message
    assert "Could not confirm that data source other:x" in message
