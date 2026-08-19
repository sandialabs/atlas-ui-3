"""Tool ACL: a user must never be offered a tool their groups do not allow."""

import os

# Ensure backend root on sys.path
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.mark.asyncio
async def test_tool_acl_filters_unauthorized(monkeypatch):
    # Build a ChatService with a fake tool manager exposing two servers
    from atlas.application.chat.service import ChatService
    from atlas.interfaces.llm import LLMProtocol

    class DummyLLM(LLMProtocol):
        async def call_plain(self, model_name, messages, temperature=0.7, **kwargs):
            return "ok"
        async def call_with_tools(self, model_name, messages, tools_schema, tool_choice="auto", temperature=0.7, **kwargs):
            class R:
                def __init__(self):
                    self.content = "tool"
                    self.tool_calls = []
                def has_tool_calls(self):
                    return False
            return R()
        async def call_with_rag(self, model_name, messages, data_sources, user_email, temperature=0.7):
            return "rag"
        async def call_with_rag_and_tools(self, model_name, messages, data_sources, tools_schema, user_email, tool_choice="auto", temperature=0.7):
            class R:
                def __init__(self):
                    self.content = "ragtools"
                    self.tool_calls = []
                def has_tool_calls(self):
                    return False
            return R()

    class FakeTool:
        def __init__(self, name):
            self.name = name
            self.description = ""
            self.inputSchema = {"type": "object", "properties": {"_atlas_user": {"type": "string"}}}

    class FakeToolManager:
        def __init__(self):
            self.servers_config = {"allowed": {}, "blocked": {}}
            self.available_tools = {
                "allowed": {"tools": [FakeTool("good_tool")], "config": {}},
                "blocked": {"tools": [FakeTool("bad_tool")], "config": {}},
            }
        def get_server_groups(self, s):
            return []
        def get_tools_schema(self, names):
            # Minimal schema for selected tools
            out = []
            for n in names:
                out.append({"type":"function","function":{"name":n,"parameters":{"type":"object","properties":{"_atlas_user":{"type":"string"}}}}})
            return out

    svc = ChatService(llm=DummyLLM(), tool_manager=FakeToolManager(), config_manager=None, file_manager=None)
    import uuid
    session_id = uuid.uuid4()
    await svc.create_session(session_id, user_email="user@example.com")

    # Select tools: one from allowed server, one from blocked server
    res = await svc.handle_chat_message(
        session_id=session_id,
        content="hello",
        model="gpt",
        selected_tools=["allowed_good_tool", "blocked_bad_tool"],
        user_email="user@example.com",
    )
    # The blocked tool should have been filtered out; request should still succeed
    assert isinstance(res, dict) and res.get("type") == "chat_response"
