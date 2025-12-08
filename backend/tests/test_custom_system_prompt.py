import tempfile
import uuid
from pathlib import Path

import pytest

from modules.config import ConfigManager
from modules.prompts.prompt_provider import PromptProvider
from application.chat.preprocessors.message_builder import MessageBuilder
from domain.sessions.models import Session
from domain.messages.models import Message, MessageRole


@pytest.mark.asyncio
async def test_message_builder_uses_custom_system_prompt(tmp_path):
    """Test that MessageBuilder uses custom system prompt when provided"""
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

    # Build messages with custom system prompt
    custom_prompt = "You are a specialized coding assistant."
    messages = await message_builder.build_messages(
        session=session,
        include_files_manifest=False,
        include_system_prompt=True,
        custom_system_prompt=custom_prompt,
    )

    # Verify custom system prompt is used instead of default
    assert len(messages) >= 2  # system prompt + user message
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == custom_prompt
    assert "helpful assistant" not in messages[0]["content"]

    # Verify user message is second
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Hello"


@pytest.mark.asyncio
async def test_message_builder_falls_back_to_default_when_custom_empty(tmp_path):
    """Test that MessageBuilder falls back to default when custom prompt is empty"""
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

    # Build messages with empty custom system prompt
    messages = await message_builder.build_messages(
        session=session,
        include_files_manifest=False,
        include_system_prompt=True,
        custom_system_prompt="",
    )

    # Verify default system prompt is used when custom is empty
    assert len(messages) >= 2  # system prompt + user message
    assert messages[0]["role"] == "system"
    assert "helpful assistant" in messages[0]["content"]
    assert "test@example.com" in messages[0]["content"]


@pytest.mark.asyncio
async def test_custom_system_prompt_sent_to_llm():
    """Test that custom system prompt is sent to LLM in chat flow"""
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
            async def call_plain(self, model_name, messages, temperature=0.7):
                captured["messages"] = messages
                return "Hello! I'm here to help."

        # Create chat service
        from application.chat.service import ChatService

        chat_service = ChatService(
            llm=DummyLLM(),
            tool_manager=None,
            connection=None,
            config_manager=config_manager,
            file_manager=None,
        )

        # Create session and send message with custom system prompt
        session_id = uuid.uuid4()
        custom_prompt = "You are a specialized Python coding assistant."
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
            custom_system_prompt=custom_prompt,
        )

        # Verify custom system prompt was sent to LLM
        msgs = captured.get("messages")
        assert msgs, "LLM was not called or messages not captured"
        assert len(msgs) >= 2  # system prompt + user message
        assert msgs[0]["role"] == "system", f"Expected first message to be system, got: {msgs[0]}"
        assert msgs[0]["content"] == custom_prompt
        assert "helpful AI assistant" not in msgs[0]["content"]


@pytest.mark.asyncio
async def test_message_builder_custom_prompt_without_provider():
    """Test that MessageBuilder uses custom prompt even without prompt provider"""
    # Create message builder without prompt provider
    message_builder = MessageBuilder(prompt_provider=None)

    # Create a session with some history
    session = Session(user_email="test@example.com")
    session.history.add_message(Message(role=MessageRole.USER, content="Hello"))

    # Build messages with custom system prompt
    custom_prompt = "You are a specialized assistant."
    messages = await message_builder.build_messages(
        session=session,
        include_files_manifest=False,
        include_system_prompt=True,
        custom_system_prompt=custom_prompt,
    )

    # Verify custom system prompt is used
    assert len(messages) >= 2  # system prompt + user message
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == custom_prompt

    # Verify user message is second
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Hello"
