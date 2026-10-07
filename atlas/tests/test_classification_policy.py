"""Explicit allowed data classifications for models, MCP servers and RAG (#1032).

The active conversation classification must be a member of every component's
``allowed_data_classifications``. These tests drive the issue's example matrix
through the real ``ComplianceLevelManager``, the config models and the
turn-level policy the chat service runs before anything executes.
"""

import json
from types import SimpleNamespace

import pytest

from atlas.application.chat.policies.classification_policy import (
    find_classification_violations,
)
from atlas.core.compliance import ComplianceLevelManager, declared_classifications
from atlas.modules.config.models import (
    LLMConfig,
    MCPConfig,
    RAGSourcesConfig,
)

LEVELS = ["UUR", "ITAR", "ECI"]


@pytest.fixture
def manager(tmp_path):
    path = tmp_path / "compliance-levels.json"
    path.write_text(json.dumps({
        "levels": [
            # ITAR's allowed_with lists UUR: under the old rule that made a
            # UUR-only model or MCP look valid in an ITAR session. It must no
            # longer widen anything.
            {"name": "UUR", "aliases": ["Unclassified"], "allowed_with": ["UUR"]},
            {"name": "ITAR", "allowed_with": ["ITAR", "UUR"]},
            {"name": "ECI", "allowed_with": ["ECI", "UUR"]},
        ],
    }))
    return ComplianceLevelManager(path)


@pytest.fixture
def config_manager():
    llm = LLMConfig(models={
        "model-x": {"model_name": "x", "model_url": "http://x",
                    "allowed_data_classifications": ["UUR", "ITAR", "ECI"]},
        "model-y": {"model_name": "y", "model_url": "http://y",
                    "allowed_data_classifications": ["UUR"]},
        "legacy-itar": {"model_name": "l", "model_url": "http://l", "compliance_level": "ITAR"},
        "undeclared": {"model_name": "u", "model_url": "http://u"},
    })
    mcp = MCPConfig(servers={
        "google_search": {"url": "http://g", "allowed_data_classifications": ["UUR"]},
        "internal_search": {"url": "http://i", "allowed_data_classifications": LEVELS},
        "bare": {"url": "http://b"},
    })
    rag = RAGSourcesConfig(sources={
        "uur_docs": {"type": "http", "url": "http://r", "allowed_data_classifications": ["UUR"]},
        "export_docs": {"type": "http", "url": "http://e", "allowed_data_classifications": ["ITAR", "ECI"]},
        "untagged_docs": {"type": "http", "url": "http://t"},
    })
    return SimpleNamespace(llm_config=llm, mcp_config=mcp, rag_sources_config=rag)


def _check(manager, config_manager, level, *, model="model-x", tools=None, sources=None):
    return find_classification_violations(
        manager,
        level,
        model=model,
        config_manager=config_manager,
        tool_manager=None,
        selected_tools=tools,
        selected_data_sources=sources,
    )


# The issue's acceptance table.
@pytest.mark.parametrize(
    "level,model_x,model_y,google,internal",
    [
        ("UUR", True, True, True, True),
        ("ITAR", True, False, False, True),
        ("ECI", True, False, False, True),
    ],
)
def test_issue_matrix(manager, config_manager, level, model_x, model_y, google, internal):
    assert (not _check(manager, config_manager, level, model="model-x")) is model_x
    assert (not _check(manager, config_manager, level, model="model-y")) is model_y
    assert (not _check(manager, config_manager, level, tools=["google_search_query"])) is google
    assert (not _check(manager, config_manager, level, tools=["internal_search_query"])) is internal


@pytest.mark.parametrize("level", LEVELS)
def test_rag_sources_follow_their_lists(manager, config_manager, level):
    uur_ok = not _check(manager, config_manager, level, sources=["uur_docs:handbook"])
    export_ok = not _check(manager, config_manager, level, sources=["export_docs:specs"])
    assert uur_ok is (level == "UUR")
    assert export_ok is (level in ("ITAR", "ECI"))


@pytest.mark.parametrize("level", LEVELS)
def test_undeclared_components_fail_closed(manager, config_manager, level):
    assert _check(manager, config_manager, level, model="undeclared") == [
        "the selected model (undeclared)"
    ]
    assert _check(manager, config_manager, level, tools=["bare_tool"]) == ["tool server bare"]
    assert _check(manager, config_manager, level, sources=["untagged_docs:x"]) == [
        "data source untagged_docs"
    ]


def test_no_active_classification_checks_nothing(manager, config_manager):
    assert _check(
        manager, config_manager, None, model="undeclared",
        tools=["bare_tool", "google_search_query"], sources=["untagged_docs:x"],
    ) == []


def test_legacy_compliance_level_is_a_one_element_list(manager, config_manager):
    assert _check(manager, config_manager, "ITAR", model="legacy-itar") == []
    assert _check(manager, config_manager, "UUR", model="legacy-itar") == [
        "the selected model (legacy-itar)"
    ]


def test_aliases_resolve_for_the_active_level(manager, config_manager):
    assert _check(manager, config_manager, "Unclassified", model="model-y") == []


def test_every_violation_is_reported_once(manager, config_manager):
    violations = _check(
        manager,
        config_manager,
        "ITAR",
        model="model-y",
        tools=["google_search_query", "google_search_fetch", "internal_search_query"],
        sources=["uur_docs:a", "uur_docs:b", "export_docs:c"],
    )
    assert violations == [
        "the selected model (model-y)",
        "tool server google_search",
        "data source uur_docs",
    ]


def test_builtin_and_unknown_tools_are_not_reported(manager, config_manager):
    # atlas_* run in-process; atlas_search reads sources checked on their own.
    # A name matching no configured server reaches no component.
    assert _check(
        manager, config_manager, "ITAR",
        tools=["atlas_canvas", "atlas_search", "canvas_canvas", "nonexistent_tool"],
        sources=["missing_server:x"],
    ) == []


def test_longest_server_prefix_wins(manager, config_manager):
    # "internal_search_query" must resolve to internal_search, not a shorter
    # server named "internal" with a different list.
    config_manager.mcp_config.servers["internal"] = config_manager.mcp_config.servers["bare"]
    assert _check(manager, config_manager, "ITAR", tools=["internal_search_query"]) == []


def test_tool_manager_servers_are_preferred(manager, config_manager):
    """The live tool manager's view (dicts) is used when present."""
    tool_manager = SimpleNamespace(servers_config={
        "google_search": {"allowed_data_classifications": ["UUR", "ITAR"]},
    })
    assert find_classification_violations(
        manager, "ITAR", model="model-x", config_manager=config_manager,
        tool_manager=tool_manager, selected_tools=["google_search_query"],
    ) == []


class TestDeclaredClassifications:
    def test_new_field_wins_over_legacy(self):
        assert declared_classifications(
            SimpleNamespace(allowed_data_classifications=["ITAR"], compliance_level="UUR")
        ) == ["ITAR"]
        assert declared_classifications(
            {"allowedDataClassifications": ["ECI"], "complianceLevel": "UUR"}
        ) == ["ECI"]

    def test_legacy_and_undeclared(self):
        assert declared_classifications({"compliance_level": "UUR"}) == ["UUR"]
        assert declared_classifications({"complianceLevel": "UUR"}) == ["UUR"]
        assert declared_classifications({}) is None
        assert declared_classifications(None) is None

    def test_explicit_empty_list_is_kept(self):
        assert declared_classifications({"allowed_data_classifications": [], "compliance_level": "UUR"}) == []


class TestConfigLoadValidation:
    def test_classifications_are_canonicalized_and_unknown_dropped(self, manager, caplog):
        from atlas.modules.config.config_loader import ConfigManager

        server = SimpleNamespace(
            allowed_data_classifications=["Unclassified", "Bogus", "ITAR"],
            compliance_level="UUR",
        )
        with caplog.at_level("WARNING"):
            ConfigManager._validate_data_classifications(manager, server, "for MCP server 's'")
        assert server.allowed_data_classifications == ["UUR", "ITAR"]
        # Both fields set: the deprecated one is called out.
        assert any("deprecated compliance_level" in r.getMessage() for r in caplog.records)

    def test_rag_mcp_servers_carry_the_list(self, manager, monkeypatch):
        """MCP-type RAG sources keep their classifications when converted."""
        from atlas.modules.config.config_loader import ConfigManager

        loader = object.__new__(ConfigManager)
        loader._rag_mcp_config = None
        loader._rag_sources_config = RAGSourcesConfig(sources={
            "rag_mcp": {"type": "mcp", "url": "http://m", "allowed_data_classifications": ["ITAR"]},
        })
        loader._app_settings = SimpleNamespace(feature_rag_enabled=True, feature_atlas_rag_tools_enabled=True)
        monkeypatch.setattr(ConfigManager, "rag_sources_config", property(lambda self: self._rag_sources_config))
        monkeypatch.setattr(ConfigManager, "app_settings", property(lambda self: self._app_settings))
        monkeypatch.setattr("atlas.core.compliance.get_compliance_manager", lambda: manager)
        servers = loader.rag_mcp_config.servers
        assert servers["rag_mcp"].allowed_data_classifications == ["ITAR"]


class TestChatServiceRefusesTheTurn:
    """The service runs the policy before touching the session."""

    @staticmethod
    def _service(config_manager, tool_servers):
        from unittest.mock import AsyncMock, MagicMock

        from atlas.application.chat.service import ChatService

        repo = MagicMock()
        repo.get = AsyncMock(return_value=None)
        repo.create = AsyncMock()
        cm = MagicMock()
        cm.app_settings.feature_compliance_levels_enabled = True
        cm.app_settings.compliance_level_required_effective = False
        cm.llm_config = config_manager.llm_config
        cm.mcp_config = config_manager.mcp_config
        cm.rag_sources_config = config_manager.rag_sources_config
        tool_manager = MagicMock()
        tool_manager.servers_config = tool_servers
        return ChatService(
            llm=MagicMock(), tool_manager=tool_manager, connection=MagicMock(),
            config_manager=cm, session_repository=repo,
        ), repo

    @pytest.mark.asyncio
    async def test_unapproved_tool_and_source_refuse_the_turn(self, manager, config_manager, monkeypatch):
        from uuid import uuid4

        from atlas.domain.errors import ValidationError

        monkeypatch.setattr("atlas.core.compliance.get_compliance_manager", lambda: manager)
        service, repo = self._service(
            config_manager, {name: s.model_dump() for name, s in config_manager.mcp_config.servers.items()}
        )
        with pytest.raises(ValidationError) as exc:
            await service.handle_chat_message(
                session_id=uuid4(), content="hi", model="model-x", user_email="u@test.com",
                selected_tools=["google_search_query", "internal_search_query"],
                selected_data_sources=["uur_docs:handbook"],
                compliance_level="ITAR",
            )
        assert "tool server google_search" in str(exc.value)
        assert "data source uur_docs" in str(exc.value)
        assert "internal_search" not in str(exc.value)
        repo.get.assert_not_called()
