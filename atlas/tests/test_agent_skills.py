"""Tests for the agent skills feature.

Validates:
- SkillConfig and SkillsConfig Pydantic models
- ConfigManager.skills_config property
- Skill prompt injection in message builder
- Skill resolution in ChatService
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.application.chat.preprocessors.message_builder import MessageBuilder
from atlas.modules.config.config_manager import (
    ConfigManager,
    SkillConfig,
    SkillsConfig,
)


class TestSkillConfig:
    """Test SkillConfig Pydantic model."""

    def test_minimal_skill_config(self):
        """SkillConfig with only required fields should load."""
        skill = SkillConfig(name="Test Skill")
        assert skill.name == "Test Skill"
        assert skill.description == ""
        assert skill.prompt == ""
        assert skill.version == "1.0.0"
        assert skill.enabled is True
        assert skill.required_tools == []
        assert skill.groups == []

    def test_full_skill_config(self):
        """SkillConfig with all fields should load correctly."""
        skill = SkillConfig(
            name="Literature Researcher",
            description="Searches scientific literature",
            prompt="You are an expert researcher...",
            version="2.0.0",
            author="Test Author",
            help_email="test@example.com",
            required_tools=["search_pubmed"],
            compliance_level="Public",
            groups=["users"],
            enabled=True,
        )
        assert skill.name == "Literature Researcher"
        assert skill.prompt == "You are an expert researcher..."
        assert skill.version == "2.0.0"
        assert skill.author == "Test Author"
        assert skill.required_tools == ["search_pubmed"]
        assert skill.compliance_level == "Public"
        assert skill.groups == ["users"]

    def test_disabled_skill_config(self):
        """Disabled SkillConfig should have enabled=False."""
        skill = SkillConfig(name="Disabled Skill", enabled=False)
        assert skill.enabled is False


class TestSkillsConfig:
    """Test SkillsConfig Pydantic model."""

    def test_empty_skills_config(self):
        """SkillsConfig with no skills should load."""
        config = SkillsConfig()
        assert config.skills == {}

    def test_skills_config_from_dict(self):
        """SkillsConfig should convert raw dicts to SkillConfig objects."""
        config = SkillsConfig(
            skills={
                "researcher": {
                    "name": "Researcher",
                    "description": "Research assistant",
                    "prompt": "You are a researcher.",
                }
            }
        )
        assert "researcher" in config.skills
        skill = config.skills["researcher"]
        assert isinstance(skill, SkillConfig)
        assert skill.name == "Researcher"
        assert skill.prompt == "You are a researcher."

    def test_multiple_skills(self):
        """SkillsConfig should support multiple skills."""
        config = SkillsConfig(
            skills={
                "skill_a": {"name": "Skill A", "prompt": "Prompt A"},
                "skill_b": {"name": "Skill B", "prompt": "Prompt B"},
            }
        )
        assert len(config.skills) == 2
        assert "skill_a" in config.skills
        assert "skill_b" in config.skills


class TestConfigManagerSkills:
    """Test ConfigManager.skills_config property."""

    def test_skills_config_defaults_to_empty(self):
        """When no skills.json exists, config should default to empty SkillsConfig."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = ConfigManager(atlas_root=Path(tmpdir))
            # Create minimal atlas/config directory structure but no skills.json
            (Path(tmpdir) / "config").mkdir(parents=True, exist_ok=True)
            skills = cm.skills_config
            assert isinstance(skills, SkillsConfig)
            assert skills.skills == {}

    def test_skills_config_loads_from_file(self):
        """skills_config should load from skills.json file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create atlas directory structure
            atlas_dir = Path(tmpdir) / "atlas"
            config_dir = atlas_dir / "config"
            config_dir.mkdir(parents=True)

            # Write skills.json to the package-defaults location
            skills_data = {
                "test_skill": {
                    "name": "Test Skill",
                    "description": "A test skill",
                    "prompt": "You are a test agent.",
                    "enabled": True,
                }
            }
            (config_dir / "skills.json").write_text(json.dumps(skills_data))

            cm = ConfigManager(atlas_root=atlas_dir)
            # Patch app_config_dir to a nonexistent path so only the atlas/config/
            # package-default is found (avoids picking up the real empty skills.json
            # from the cwd-relative "config/skills.json" search path).
            app_settings_mock = MagicMock()
            app_settings_mock.app_config_dir = str(Path(tmpdir) / "nonexistent_config")
            app_settings_mock.skills_config_file = "skills.json"
            cm._app_settings = app_settings_mock

            skills = cm.skills_config
            assert isinstance(skills, SkillsConfig)
            assert "test_skill" in skills.skills
            assert skills.skills["test_skill"].name == "Test Skill"
            assert skills.skills["test_skill"].prompt == "You are a test agent."

    def test_skills_config_is_cached(self):
        """skills_config should return the same object on repeated calls."""
        cm = ConfigManager()
        skills1 = cm.skills_config
        skills2 = cm.skills_config
        assert skills1 is skills2

    def test_reload_configs_clears_skills_cache(self):
        """reload_configs should clear the skills config cache."""
        cm = ConfigManager()
        _ = cm.skills_config  # Prime the cache
        assert cm._skills_config is not None
        cm.reload_configs()
        assert cm._skills_config is None


class TestMessageBuilderSkillPrompt:
    """Test skill_prompt injection in MessageBuilder."""

    def _make_session(self, user_email: str = "test@test.com"):
        """Create a minimal mock session."""
        session = MagicMock()
        session.user_email = user_email
        session.context = {"files": {}}
        session.history.get_messages_for_llm.return_value = [
            {"role": "user", "content": "Hello"}
        ]
        return session

    @pytest.mark.asyncio
    async def test_build_messages_without_skill_prompt(self):
        """build_messages without skill_prompt should not add extra content."""
        builder = MessageBuilder()
        session = self._make_session()
        messages = await builder.build_messages(
            session=session,
            include_files_manifest=False,
            include_system_prompt=False,
        )
        assert len(messages) == 1
        assert messages[0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_build_messages_with_skill_prompt_no_system_prompt(self):
        """skill_prompt alone should create a system message when no other system prompt."""
        builder = MessageBuilder(prompt_provider=None)
        session = self._make_session()
        skill_text = "You are a specialized research agent."
        messages = await builder.build_messages(
            session=session,
            include_files_manifest=False,
            include_system_prompt=True,
            skill_prompt=skill_text,
        )
        # First message should be the skill as system prompt
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == skill_text

    @pytest.mark.asyncio
    async def test_build_messages_skill_appended_to_system_prompt(self):
        """When both system prompt and skill_prompt are present, skill is appended."""
        mock_provider = MagicMock()
        mock_provider.get_system_prompt.return_value = "Base system prompt."

        builder = MessageBuilder(prompt_provider=mock_provider)
        session = self._make_session()
        skill_text = "Skill instructions."
        messages = await builder.build_messages(
            session=session,
            include_files_manifest=False,
            include_system_prompt=True,
            skill_prompt=skill_text,
        )
        assert messages[0]["role"] == "system"
        assert "Base system prompt." in messages[0]["content"]
        assert "Skill instructions." in messages[0]["content"]
        # Skill should come after the base system prompt
        assert messages[0]["content"].index("Base system prompt.") < messages[0]["content"].index("Skill instructions.")

    @pytest.mark.asyncio
    async def test_build_messages_no_skill_prompt_keeps_base_system_prompt(self):
        """Without skill_prompt, system prompt should be unchanged."""
        mock_provider = MagicMock()
        mock_provider.get_system_prompt.return_value = "Only base prompt."

        builder = MessageBuilder(prompt_provider=mock_provider)
        session = self._make_session()
        messages = await builder.build_messages(
            session=session,
            include_files_manifest=False,
            include_system_prompt=True,
            skill_prompt=None,
        )
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "Only base prompt."


class TestChatServiceSkillResolution:
    """Test skill resolution in ChatService.handle_chat_message."""

    def _make_config_manager_with_skill(self, skill_id: str, prompt: str) -> MagicMock:
        """Create a mock ConfigManager with a configured skill."""
        skill = SkillConfig(name="Test Skill", prompt=prompt, enabled=True)
        skills_config = SkillsConfig(skills={skill_id: skill})
        config_manager = MagicMock()
        config_manager.skills_config = skills_config
        return config_manager

    @pytest.mark.asyncio
    async def test_selected_skill_resolves_to_prompt(self):
        """When selected_skill is set, it should be resolved to skill_prompt in kwargs."""
        from atlas.application.chat.service import ChatService

        skill_prompt_text = "You are a specialized agent."
        config_manager = self._make_config_manager_with_skill("my_skill", skill_prompt_text)

        service = ChatService(llm=MagicMock(), config_manager=config_manager)

        captured_kwargs = {}

        async def mock_orchestrate(**kwargs):
            captured_kwargs.update(kwargs)
            return {"type": "chat_response", "content": "ok"}

        with patch.object(service, "_get_orchestrator") as mock_orch_factory:
            mock_orch = MagicMock()
            mock_orch.execute = AsyncMock(side_effect=mock_orchestrate)
            mock_orch_factory.return_value = mock_orch

            with patch.object(service, "create_session", new_callable=AsyncMock):
                with patch.object(service, "_save_conversation"):
                    import uuid
                    session_id = uuid.uuid4()
                    service.sessions[session_id] = MagicMock()
                    try:
                        await service.handle_chat_message(
                            session_id=session_id,
                            content="test",
                            model="test-model",
                            selected_skill="my_skill",
                        )
                    except Exception:
                        pass  # Ignore downstream errors; we only care about kwargs

        assert captured_kwargs.get("skill_prompt") == skill_prompt_text

    @pytest.mark.asyncio
    async def test_unknown_skill_does_not_set_skill_prompt(self):
        """When selected_skill references an unknown skill, skill_prompt is not set."""
        from atlas.application.chat.service import ChatService

        config_manager = self._make_config_manager_with_skill("known_skill", "Some prompt")
        service = ChatService(llm=MagicMock(), config_manager=config_manager)

        captured_kwargs = {}

        async def mock_orchestrate(**kwargs):
            captured_kwargs.update(kwargs)
            return {"type": "chat_response", "content": "ok"}

        with patch.object(service, "_get_orchestrator") as mock_orch_factory:
            mock_orch = MagicMock()
            mock_orch.execute = AsyncMock(side_effect=mock_orchestrate)
            mock_orch_factory.return_value = mock_orch

            with patch.object(service, "create_session", new_callable=AsyncMock):
                import uuid
                session_id = uuid.uuid4()
                service.sessions[session_id] = MagicMock()
                try:
                    await service.handle_chat_message(
                        session_id=session_id,
                        content="test",
                        model="test-model",
                        selected_skill="nonexistent_skill",
                    )
                except Exception:
                    pass

        assert "skill_prompt" not in captured_kwargs

    @pytest.mark.asyncio
    async def test_disabled_skill_does_not_set_skill_prompt(self):
        """When selected_skill references a disabled skill, skill_prompt is not set."""
        from atlas.application.chat.service import ChatService

        skill = SkillConfig(name="Disabled Skill", prompt="Some prompt", enabled=False)
        skills_config = SkillsConfig(skills={"disabled_skill": skill})
        config_manager = MagicMock()
        config_manager.skills_config = skills_config

        service = ChatService(llm=MagicMock(), config_manager=config_manager)

        captured_kwargs = {}

        async def mock_orchestrate(**kwargs):
            captured_kwargs.update(kwargs)
            return {"type": "chat_response", "content": "ok"}

        with patch.object(service, "_get_orchestrator") as mock_orch_factory:
            mock_orch = MagicMock()
            mock_orch.execute = AsyncMock(side_effect=mock_orchestrate)
            mock_orch_factory.return_value = mock_orch

            with patch.object(service, "create_session", new_callable=AsyncMock):
                import uuid
                session_id = uuid.uuid4()
                service.sessions[session_id] = MagicMock()
                try:
                    await service.handle_chat_message(
                        session_id=session_id,
                        content="test",
                        model="test-model",
                        selected_skill="disabled_skill",
                    )
                except Exception:
                    pass

        assert "skill_prompt" not in captured_kwargs
