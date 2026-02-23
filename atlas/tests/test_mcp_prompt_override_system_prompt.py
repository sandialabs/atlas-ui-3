import json
from pathlib import Path

import pytest

from atlas.modules.config import ConfigManager
from atlas.modules.mcp_tools.client import MCPToolManager


@pytest.mark.asyncio
async def test_selected_mcp_prompt_overrides_system_prompt(monkeypatch):
    """
    Verify that when a prompt is selected (e.g., prompts_expert_dog_trainer),
    the backend injects it as a system message at the start of the LLM messages.
    We patch the LLM caller to capture the messages argument.
    """
    # Ensure MCP clients and prompts are ready
    # Set up MCP manager directly (avoid importing app_factory/litellm).
    # Use the example prompts MCP config file so this test uses
    # the same JSON configuration as other prompts tests.
    # tests run with cwd=atlas/, so resolve from atlas root
    atlas_root = Path(__file__).parent.parent
    config_path = atlas_root / "config" / "mcp-example-configs" / "mcp-prompts.json"
    assert config_path.exists(), f"Missing example prompts config: {config_path}"

    data = json.loads(config_path.read_text())
    assert "prompts" in data, "prompts server not defined in example config"

    mcp: MCPToolManager = MCPToolManager(config_path=str(config_path))
    await mcp.initialize_clients()
    await mcp.discover_prompts()
    assert "prompts" in mcp.available_prompts, "prompts server not discovered"

    captured = {}

    class DummyLLM:
        async def call_plain(self, model_name, messages, temperature=0.7, **kwargs):
            captured["messages"] = messages
            return "ok"

        async def stream_plain(self, model_name, messages, temperature=0.7, **kwargs):
            captured["messages"] = messages
            yield "ok"

        async def call_with_tools(self, model_name, messages, tools_schema, tool_choice="auto", temperature=0.7, **kwargs):
            captured["messages"] = messages
            class R:
                def __init__(self):
                    self.content = "ok"
                    self.tool_calls = []
                def has_tool_calls(self):
                    return False
            return R()

        async def call_with_rag(self, model_name, messages, data_sources, user_email, temperature=0.7):
            captured["messages"] = messages
            return "ok"

        async def call_with_rag_and_tools(self, model_name, messages, data_sources, tools_schema, user_email, tool_choice="auto", temperature=0.7):
            captured["messages"] = messages
            class R:
                def __init__(self):
                    self.content = "ok"
                    self.tool_calls = []
                def has_tool_calls(self):
                    return False
            return R()

    # Create a chat service wired with dummy LLM
    from atlas.application.chat.service import ChatService

    chat_service = ChatService(
        llm=DummyLLM(),
        tool_manager=mcp,
        connection=None,
        config_manager=ConfigManager(),
        file_manager=None,
    )

    # Create a session id
    import uuid
    session_id = uuid.uuid4()

    # Send a message with selected prompt
    await chat_service.handle_chat_message(
        session_id=session_id,
        content="Hello there",
        model="test-model",
        selected_tools=None,
        selected_prompts=["prompts_expert_dog_trainer"],
        selected_data_sources=None,
        user_email="tester@example.com",
        only_rag=False,
        tool_choice_required=False,
        agent_mode=False,
        temperature=0.7,
    )

    # Validate we injected a system message first
    msgs = captured.get("messages")
    assert msgs, "LLM was not called or messages not captured"
    assert msgs[0]["role"] == "system", f"Expected first message to be system, got: {msgs[0]}"
    # The expert_dog_trainer prompt includes key phrase "expert dog trainer"
    first_content = msgs[0]["content"].lower()
    assert "dog trainer" in first_content or "canine" in first_content, "Injected system prompt content not found"
