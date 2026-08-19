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
    from atlas.modules.llm.models import LLMResponse

    class DummyLLM(LLMProtocol):
        def __init__(self):
            # What the service actually offered the model, after ACL filtering.
            # Asserting on this is the only way to tell "the blocked tool was
            # filtered" apart from "every tool was dropped".
            self.offered_tools = None

        async def call_plain(self, model_name, messages, temperature=0.7, **kwargs):
            return "ok"
        async def stream_with_tools(self, model_name, messages, tools_schema, tool_choice="auto", temperature=0.7, user_email=None):
            self.offered_tools = [t["function"]["name"] for t in (tools_schema or [])]
            yield "tool"
            yield LLMResponse(content="tool", tool_calls=None)

        async def call_with_tools(self, model_name, messages, tools_schema, tool_choice="auto", temperature=0.7, **kwargs):
            self.offered_tools = [t["function"]["name"] for t in (tools_schema or [])]
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
        async def get_authorized_servers(self, user_email, auth_check_func):
            # Without this method ToolAuthorizationService authorizes *nothing*
            # and drops every tool, so the test would pass even with ACL
            # filtering completely broken.
            return ["allowed"]
        def get_tools_schema(self, names):
            # Minimal schema for selected tools
            out = []
            for n in names:
                out.append({"type":"function","function":{"name":n,"parameters":{"type":"object","properties":{"_atlas_user":{"type":"string"}}}}})
            return out

    llm = DummyLLM()
    svc = ChatService(llm=llm, tool_manager=FakeToolManager(), config_manager=None, file_manager=None)
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
    # The blocked tool is gone and the allowed one survived. Checking both
    # directions matters: an ACL that drops everything is also broken, and
    # asserting only the response envelope cannot tell the two apart.
    assert isinstance(res, dict) and res.get("type") == "chat_response"
    assert llm.offered_tools == ["allowed_good_tool"]
