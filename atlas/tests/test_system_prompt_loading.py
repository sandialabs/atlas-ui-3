import tempfile
import uuid
from pathlib import Path

import pytest

from atlas.application.chat.preprocessors.message_builder import MessageBuilder
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.modules.config import ConfigManager
from atlas.modules.prompts.prompt_provider import PromptProvider


@pytest.mark.asyncio
async def test_prompt_provider_loads_system_prompt(tmp_path):
    """Test that PromptProvider correctly loads and formats system_prompt.md"""
    # Create a temporary system prompt file
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    system_prompt_file = prompts_dir / "system_prompt.md"
    system_prompt_content = "You are a helpful assistant for user {user_email}."
    system_prompt_file.write_text(system_prompt_content)

    # Create a config manager with custom prompt base path
    config_manager = ConfigManager()
    config_manager.app_settings.prompt_base_path = str(prompts_dir)
    config_manager.app_settings.system_prompt_filename = "system_prompt.md"

    # Create prompt provider
    prompt_provider = PromptProvider(config_manager)

    # Test loading system prompt
    result = prompt_provider.get_system_prompt(user_email="test@example.com")

    assert result is not None
    assert "test@example.com" in result
    assert "helpful assistant" in result


@pytest.mark.asyncio
async def test_prompt_provider_handles_missing_system_prompt():
    """Test that PromptProvider returns None when system_prompt.md is missing"""
    # Create a config manager pointing to non-existent directory
    config_manager = ConfigManager()
    config_manager.app_settings.prompt_base_path = "/nonexistent/path"
    config_manager.app_settings.system_prompt_filename = "system_prompt.md"

    # Create prompt provider
    prompt_provider = PromptProvider(config_manager)

    # Test loading system prompt
    result = prompt_provider.get_system_prompt(user_email="test@example.com")

    assert result is None


@pytest.mark.asyncio
async def test_message_builder_includes_system_prompt(tmp_path):
    """Test that MessageBuilder includes system prompt in messages"""
    # Create a temporary system prompt file
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    system_prompt_file = prompts_dir / "system_prompt.md"
    system_prompt_content = "You are a helpful assistant for user {user_email}."
    system_prompt_file.write_text(system_prompt_content)

    # Create a config manager with custom prompt base path
    config_manager = ConfigManager()
    config_manager.app_settings.prompt_base_path = str(prompts_dir)
    config_manager.app_settings.system_prompt_filename = "system_prompt.md"

    # Create prompt provider and message builder
    prompt_provider = PromptProvider(config_manager)
    message_builder = MessageBuilder(prompt_provider=prompt_provider)

    # Create a session with some history
    session = Session(user_email="test@example.com")
    session.history.add_message(Message(role=MessageRole.USER, content="Hello"))

    # Build messages
    messages = await message_builder.build_messages(
        session=session,
        include_files_manifest=False,
        include_system_prompt=True,
    )

    # Verify system prompt is first message
    assert len(messages) >= 2  # system prompt + user message
    assert messages[0]["role"] == "system"
    assert "helpful assistant" in messages[0]["content"]
    assert "test@example.com" in messages[0]["content"]

    # Verify user message is second
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Hello"


@pytest.mark.asyncio
async def test_message_builder_without_system_prompt(tmp_path):
    """Test that MessageBuilder works without system prompt when disabled"""
    # Create prompt provider without system prompt file
    config_manager = ConfigManager()
    config_manager.app_settings.prompt_base_path = "/nonexistent"
    prompt_provider = PromptProvider(config_manager)
    message_builder = MessageBuilder(prompt_provider=prompt_provider)

    # Create a session with some history
    session = Session(user_email="test@example.com")
    session.history.add_message(Message(role=MessageRole.USER, content="Hello"))

    # Build messages with system prompt disabled
    messages = await message_builder.build_messages(
        session=session,
        include_files_manifest=False,
        include_system_prompt=False,
    )

    # Verify no system prompt
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Hello"


@pytest.mark.asyncio
async def test_system_prompt_sent_to_llm():
    """Test that system prompt is sent to LLM in chat flow"""
    # Create a temporary directory for prompts
    with tempfile.TemporaryDirectory() as tmp_dir:
        prompts_dir = Path(tmp_dir) / "prompts"
        prompts_dir.mkdir()
        system_prompt_file = prompts_dir / "system_prompt.md"
        system_prompt_content = "You are a helpful AI assistant for user {user_email}."
        system_prompt_file.write_text(system_prompt_content)

        # Create config manager
        config_manager = ConfigManager()
        config_manager.app_settings.prompt_base_path = str(prompts_dir)
        config_manager.app_settings.system_prompt_filename = "system_prompt.md"

        # Capture messages sent to LLM
        captured = {}

        class DummyLLM:
            async def call_plain(self, model_name, messages, temperature=0.7, **kwargs):
                captured["messages"] = messages
                return "Hello! I'm here to help."

            async def stream_plain(self, model_name, messages, temperature=0.7, **kwargs):
                captured["messages"] = messages
                yield "Hello! I'm here to help."

        # Create chat service
        from atlas.application.chat.service import ChatService

        chat_service = ChatService(
            llm=DummyLLM(),
            tool_manager=None,
            connection=None,
            config_manager=config_manager,
            file_manager=None,
        )

        # Create session and send message
        session_id = uuid.uuid4()
        await chat_service.handle_chat_message(
            session_id=session_id,
            content="Hello",
            model="test-model",
            user_email="tester@example.com",
            selected_tools=None,
            selected_prompts=None,
            selected_data_sources=None,
            only_rag=False,
            tool_choice_required=False,
            agent_mode=False,
            temperature=0.7,
        )

        # Verify system prompt was sent to LLM
        msgs = captured.get("messages")
        assert msgs, "LLM was not called or messages not captured"
        assert len(msgs) >= 2  # system prompt + user message
        assert msgs[0]["role"] == "system", f"Expected first message to be system, got: {msgs[0]}"
        assert "helpful AI assistant" in msgs[0]["content"]
        assert "tester@example.com" in msgs[0]["content"]
