"""Tests for model tool capability enforcement (GH #442 / PR #491).

Verifies that:
- ModelConfig correctly recognizes supports_tools field
- _model_supports_tools() reads config correctly
- Orchestrator strips tools when model lacks tool support
- Orchestrator blocks agent mode when model lacks tool support
- Warning messages are sent via publish_warning (not publish_chat_response)
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.application.chat.orchestrator import ChatOrchestrator
from atlas.domain.sessions.models import Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository
from atlas.modules.config.config_manager import LLMConfig, ModelConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config_manager(models_dict):
    """Build a mock config_manager with the given model configs."""
    cm = MagicMock()
    cm.llm_config = LLMConfig(models=models_dict)
    return cm


def _make_orchestrator(config_manager=None):
    """Build a ChatOrchestrator with mocked runners and optional config_manager."""
    llm = MagicMock()
    event_pub = MagicMock()
    event_pub.publish_warning = AsyncMock()
    event_pub.publish_chat_response = AsyncMock()
    repo = InMemorySessionRepository()

    plain_runner = MagicMock()
    plain_runner.run_streaming = AsyncMock(return_value={"mode": "plain"})
    tools_runner = MagicMock()
    tools_runner.run_streaming = AsyncMock(return_value={"mode": "tools"})
    agent_runner = MagicMock()
    agent_runner.run = AsyncMock(return_value={"mode": "agent"})

    orch = ChatOrchestrator(
        llm=llm,
        event_publisher=event_pub,
        session_repository=repo,
        plain_mode=plain_runner,
        tools_mode=tools_runner,
        agent_mode=agent_runner,
        config_manager=config_manager,
    )
    return orch, repo, event_pub


async def _seed_session(repo):
    sid = uuid.uuid4()
    session = Session(id=sid, user_email="test@example.com")
    await repo.create(session)
    return sid


# ---------------------------------------------------------------------------
# ModelConfig field tests
# ---------------------------------------------------------------------------

class TestModelConfigSupportsTools:
    def test_default_is_true(self):
        cfg = ModelConfig(model_name="gpt-4", model_url="http://x")
        assert cfg.supports_tools is True

    def test_can_be_set_false(self):
        cfg = ModelConfig(model_name="no-tools", model_url="http://x", supports_tools=False)
        assert cfg.supports_tools is False

    def test_model_card_field(self):
        cfg = ModelConfig(model_name="gpt-4", model_url="http://x", model_card="A great model")
        assert cfg.model_card == "A great model"

    def test_model_card_default_none(self):
        cfg = ModelConfig(model_name="gpt-4", model_url="http://x")
        assert cfg.model_card is None

    def test_llm_config_roundtrip(self):
        llm_cfg = LLMConfig(models={
            "tool-model": ModelConfig(
                model_name="gpt-4", model_url="http://x", supports_tools=True
            ),
            "no-tool-model": ModelConfig(
                model_name="basic", model_url="http://x", supports_tools=False
            ),
        })
        assert llm_cfg.models["tool-model"].supports_tools is True
        assert llm_cfg.models["no-tool-model"].supports_tools is False

    def test_serialization_includes_fields(self):
        cfg = ModelConfig(
            model_name="test", model_url="http://x",
            supports_tools=False, model_card="Info"
        )
        d = cfg.model_dump()
        assert "supports_tools" in d
        assert d["supports_tools"] is False
        assert "model_card" in d
        assert d["model_card"] == "Info"


# ---------------------------------------------------------------------------
# _model_supports_tools() unit tests
# ---------------------------------------------------------------------------

class TestModelSupportsTools:
    def test_returns_true_when_no_config_manager(self):
        orch, _, _ = _make_orchestrator(config_manager=None)
        assert orch._model_supports_tools("any-model") is True

    def test_returns_true_for_unknown_model(self):
        cm = _make_config_manager({
            "known": ModelConfig(model_name="known", model_url="http://x"),
        })
        orch, _, _ = _make_orchestrator(config_manager=cm)
        assert orch._model_supports_tools("unknown-model") is True

    def test_returns_true_for_tool_capable_model(self):
        cm = _make_config_manager({
            "gpt-4": ModelConfig(model_name="gpt-4", model_url="http://x", supports_tools=True),
        })
        orch, _, _ = _make_orchestrator(config_manager=cm)
        assert orch._model_supports_tools("gpt-4") is True

    def test_returns_false_for_non_tool_model(self):
        cm = _make_config_manager({
            "basic": ModelConfig(model_name="basic", model_url="http://x", supports_tools=False),
        })
        orch, _, _ = _make_orchestrator(config_manager=cm)
        assert orch._model_supports_tools("basic") is False


# ---------------------------------------------------------------------------
# Tool stripping integration tests
# ---------------------------------------------------------------------------

class TestToolStripping:
    @pytest.mark.asyncio
    async def test_tools_stripped_for_non_tool_model(self):
        """Tools should be removed and warning sent when model lacks tool support."""
        cm = _make_config_manager({
            "no-tools": ModelConfig(model_name="no-tools", model_url="http://x", supports_tools=False),
        })
        orch, repo, event_pub = _make_orchestrator(config_manager=cm)
        orch.tool_authorization = MagicMock()
        orch.tool_authorization.filter_authorized_tools = AsyncMock(return_value=[])
        sid = await _seed_session(repo)

        await orch.execute(
            session_id=sid,
            content="hello",
            model="no-tools",
            selected_tools=["some_tool"],
        )

        # Warning should be sent via publish_warning, not publish_chat_response
        event_pub.publish_warning.assert_awaited_once()
        assert "does not support tool" in event_pub.publish_warning.call_args.kwargs["message"]

        # Should have routed to plain mode (tools stripped)
        orch.plain_mode.run_streaming.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tools_preserved_for_tool_capable_model(self):
        """Tools should NOT be stripped when model supports tools."""
        cm = _make_config_manager({
            "gpt-4": ModelConfig(model_name="gpt-4", model_url="http://x", supports_tools=True),
        })
        orch, repo, event_pub = _make_orchestrator(config_manager=cm)
        orch.tool_authorization = MagicMock()
        orch.tool_authorization.filter_authorized_tools = AsyncMock(
            return_value=["server_tool1"]
        )
        sid = await _seed_session(repo)

        await orch.execute(
            session_id=sid,
            content="use a tool",
            model="gpt-4",
            selected_tools=["server_tool1"],
        )

        # No warning should be sent
        event_pub.publish_warning.assert_not_awaited()
        # Should route to tools mode
        orch.tools_mode.run_streaming.assert_awaited_once()


# ---------------------------------------------------------------------------
# Agent mode blocking tests
# ---------------------------------------------------------------------------

class TestAgentModeBlocking:
    @pytest.mark.asyncio
    async def test_agent_mode_blocked_for_non_tool_model(self):
        """Agent mode should be disabled with warning when model lacks tool support."""
        cm = _make_config_manager({
            "no-tools": ModelConfig(model_name="no-tools", model_url="http://x", supports_tools=False),
        })
        orch, repo, event_pub = _make_orchestrator(config_manager=cm)
        sid = await _seed_session(repo)

        await orch.execute(
            session_id=sid,
            content="do something complex",
            model="no-tools",
            agent_mode=True,
        )

        # Warning about agent mode should be sent
        event_pub.publish_warning.assert_awaited_once()
        assert "Agent mode" in event_pub.publish_warning.call_args.kwargs["message"]

        # Should fall back to plain mode, not agent mode
        orch.plain_mode.run_streaming.assert_awaited_once()
        orch.agent_mode.run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_agent_mode_allowed_for_tool_capable_model(self):
        """Agent mode should work normally for tool-capable models."""
        cm = _make_config_manager({
            "gpt-4": ModelConfig(model_name="gpt-4", model_url="http://x", supports_tools=True),
        })
        orch, repo, event_pub = _make_orchestrator(config_manager=cm)
        sid = await _seed_session(repo)

        await orch.execute(
            session_id=sid,
            content="do something complex",
            model="gpt-4",
            agent_mode=True,
        )

        # No warning
        event_pub.publish_warning.assert_not_awaited()
        # Should route to agent mode
        orch.agent_mode.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tools_and_agent_mode_combined_single_warning(self):
        """When both tools and agent mode are used with a non-tool model,
        only one publish_warning call should be made containing both messages."""
        cm = _make_config_manager({
            "no-tools": ModelConfig(model_name="no-tools", model_url="http://x", supports_tools=False),
        })
        orch, repo, event_pub = _make_orchestrator(config_manager=cm)
        orch.tool_authorization = MagicMock()
        orch.tool_authorization.filter_authorized_tools = AsyncMock(return_value=[])
        sid = await _seed_session(repo)

        await orch.execute(
            session_id=sid,
            content="do it all",
            model="no-tools",
            selected_tools=["some_tool"],
            agent_mode=True,
        )

        # Exactly one warning covering both tools and agent mode
        event_pub.publish_warning.assert_awaited_once()
        msg = event_pub.publish_warning.call_args.kwargs["message"]
        assert "tools have been disabled" in msg
        assert "Agent mode has been disabled" in msg

        # Should fall back to plain mode
        orch.plain_mode.run_streaming.assert_awaited_once()
        orch.agent_mode.run.assert_not_awaited()


# ---------------------------------------------------------------------------
# File processor vision warning via event_publisher tests
# ---------------------------------------------------------------------------

class TestFileProcessorVisionWarning:
    @pytest.mark.asyncio
    async def test_vision_warning_uses_publish_warning(self):
        """file_processor should route vision warnings through event_publisher.publish_warning."""
        from atlas.application.chat.utilities.file_processor import handle_session_files

        event_pub = MagicMock()
        event_pub.publish_warning = AsyncMock()

        file_manager = MagicMock()
        file_manager.upload_file = AsyncMock(return_value={
            "key": "uploads/photo.png",
            "content_type": "image/png",
            "size": 1234,
            "last_modified": "2026-04-03",
        })

        await handle_session_files(
            session_context={},
            user_email="test@example.com",
            files_map={"photo.png": {"content": "iVBORw0KGgo="}},
            file_manager=file_manager,
            model_supports_vision=False,
            event_publisher=event_pub,
        )

        event_pub.publish_warning.assert_awaited_once()
        msg = event_pub.publish_warning.call_args.kwargs["message"]
        assert "does not support image/vision" in msg
        assert "photo.png" in msg

    @pytest.mark.asyncio
    async def test_vision_warning_falls_back_to_callback(self):
        """Without event_publisher, file_processor should fall back to update_callback."""
        from atlas.application.chat.utilities.file_processor import handle_session_files

        callback = AsyncMock()
        file_manager = MagicMock()
        file_manager.upload_file = AsyncMock(return_value={
            "key": "uploads/photo.png",
            "content_type": "image/png",
            "size": 1234,
            "last_modified": "2026-04-03",
        })

        await handle_session_files(
            session_context={},
            user_email="test@example.com",
            files_map={"photo.png": {"content": "iVBORw0KGgo="}},
            file_manager=file_manager,
            model_supports_vision=False,
            update_callback=callback,
            event_publisher=None,
        )

        # callback may be called for status updates too; find the warning call
        warning_calls = [
            c for c in callback.call_args_list
            if c[0][0].get("type") == "warning"
        ]
        assert len(warning_calls) == 1
        assert "photo.png" in warning_calls[0][0][0]["message"]
