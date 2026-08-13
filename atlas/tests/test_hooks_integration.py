"""Integration tests for the wired hook events (GH #713).

These exercise the actual call-site wiring (not just the engine): each event
is fired at its real chokepoint with a real subprocess hook, asserting the
verdict translation into the host behavior (deny -> blocked response/result,
modify -> applied mutation, require_approval -> escalation, continue ->
transparent). The zero-overhead no-config path is also asserted per event so a
missing hooks.json provably never spawns a process.
"""

import asyncio
import sys
import textwrap
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.application.chat.orchestrator import ChatOrchestrator
from atlas.application.chat.utilities.tool_executor import execute_single_tool
from atlas.domain.messages.models import ToolResult
from atlas.domain.sessions.models import Session
from atlas.domain.unified_rag_service import UnifiedRAGService
from atlas.hooks import HookBlockedError, HookConfig, HooksConfig, set_hook_manager_for_testing
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository
from atlas.modules.config.config_loader import ConfigManager


def _real_litellm_caller_cls():
    """Return the real LiteLLMCaller class, working around a pre-existing
    test-isolation quirk: ``test_capability_tokens_and_injection`` replaces
    ``sys.modules["atlas.modules.llm.litellm_caller"]`` with a fake at import
    time and only restores it via a module-scoped fixture that runs after its
    own tests. When those tests are deselected (e.g. ``-k``), the fake persists
    for the whole session, so a plain ``from ... import LiteLLMCaller`` binds to
    ``_FakeLLM``. We detect that and fall back to the saved original module.
    """
    import atlas.modules.llm.litellm_caller as mod

    cls = getattr(mod, "LiteLLMCaller", None)
    if cls is not None and "test_capability_tokens_and_injection" not in getattr(cls, "__module__", ""):
        return cls
    try:
        from atlas.tests.test_capability_tokens_and_injection import _ORIGINAL_LITELLM_MODULE

        return _ORIGINAL_LITELLM_MODULE.LiteLLMCaller
    except Exception:
        return cls  # last resort: whatever is bound


def _write_hook(tmp_path: Path, name: str, body: str) -> str:
    p = tmp_path / f"{name}.py"
    p.write_text(textwrap.dedent(body))
    p.chmod(0o755)
    return str(p)


def _install_hooks(tmp_path: Path, hooks: dict) -> None:
    """Install a HookManager singleton with the given {event: [HookConfig]}."""
    cm = ConfigManager(atlas_root=Path(__file__).resolve().parents[1])
    cm._hooks_config = HooksConfig(hooks=hooks)
    from atlas.hooks.manager import HookManager

    real = HookManager(cm)
    set_hook_manager_for_testing(real)


@pytest.fixture(autouse=True)
def _reset_hooks():
    yield
    set_hook_manager_for_testing(None)


# ---------------------------------------------------------- SessionStart/End


def _make_chat_service(tmp_path):
    from atlas.application.chat.service import ChatService

    cm = ConfigManager(atlas_root=Path(__file__).resolve().parents[1])
    repo = InMemorySessionRepository()
    svc = ChatService(llm=MagicMock(), session_repository=repo, config_manager=cm)
    return svc, repo


class TestSessionStart:
    async def test_no_config_transparent(self):
        svc, repo = _make_chat_service(Path("/tmp"))
        sid = uuid.uuid4()
        session = await svc.create_session(sid, "u@example.gov")
        assert session.id == sid
        assert session.user_email == "u@example.gov"

    async def test_deny_blocks_creation(self, tmp_path):
        deny = _write_hook(tmp_path, "deny", f'#!{sys.executable}\nimport sys; sys.stderr.write("denied by policy"); sys.exit(2)')
        _install_hooks(tmp_path, {"SessionStart": [HookConfig(name="d", command=[deny])]})
        svc, repo = _make_chat_service(tmp_path)
        with pytest.raises(Exception):
            await svc.create_session(uuid.uuid4(), "u@example.gov")

    async def test_modify_attaches_session_metadata(self, tmp_path):
        body = f"""\
        #!{sys.executable}
        import json, sys
        print(json.dumps({{"decision": "modify", "payload": {{"tenant": "doe"}}}}))
        sys.exit(0)
        """
        h = _write_hook(tmp_path, "meta", body)
        _install_hooks(tmp_path, {"SessionStart": [HookConfig(name="m", command=[h])]})
        svc, repo = _make_chat_service(tmp_path)
        sid = uuid.uuid4()
        session = await svc.create_session(sid, "u@example.gov")
        assert session.context.get("tenant") == "doe"


class TestSessionEnd:
    async def test_no_config_transparent(self):
        svc, repo = _make_chat_service(Path("/tmp"))
        sid = uuid.uuid4()
        await svc.create_session(sid, "u@example.gov")
        await svc.end_session(sid)  # no error
        session = await repo.get(sid)
        assert session.active is False

    async def test_failing_hook_does_not_break_teardown(self, tmp_path):
        bad = _write_hook(tmp_path, "bad", f'#!{sys.executable}\nimport sys; sys.exit(1)')
        _install_hooks(tmp_path, {"SessionEnd": [HookConfig(name="b", command=[bad])]})
        svc, repo = _make_chat_service(tmp_path)
        sid = uuid.uuid4()
        await svc.create_session(sid, "u@example.gov")
        await svc.end_session(sid)  # must not raise
        session = await repo.get(sid)
        assert session.active is False


# ---------------------------------------------------------- UserPromptSubmit


def _make_orchestrator():
    repo = InMemorySessionRepository()
    plain = MagicMock()
    plain.run_streaming = AsyncMock(return_value={"type": "chat_response", "message": "plain"})
    orch = ChatOrchestrator(
        llm=MagicMock(),
        event_publisher=MagicMock(),
        session_repository=repo,
        plain_mode=plain,
        rag_mode=MagicMock(),
        tools_mode=MagicMock(),
        agent_mode=None,
    )
    orch.event_publisher.publish_response_complete = AsyncMock()
    return orch, repo, plain


class TestUserPromptSubmit:
    async def test_no_config_routes_normally(self):
        orch, repo, plain = _make_orchestrator()
        sid = uuid.uuid4()
        await repo.create(Session(id=sid, user_email="u@example.gov"))
        result = await orch.execute(session_id=sid, content="Hello", model="m")
        plain.run_streaming.assert_awaited_once()
        assert result["type"] == "chat_response"

    async def test_deny_blocks_turn_with_message(self, tmp_path):
        deny = _write_hook(tmp_path, "deny", f'#!{sys.executable}\nimport sys; sys.stderr.write("prompt blocked"); sys.exit(2)')
        _install_hooks(tmp_path, {"UserPromptSubmit": [HookConfig(name="d", command=[deny])]})
        orch, repo, plain = _make_orchestrator()
        sid = uuid.uuid4()
        await repo.create(Session(id=sid, user_email="u@example.gov"))
        result = await orch.execute(session_id=sid, content="secret", model="m")
        assert result["message"] == "prompt blocked"
        plain.run_streaming.assert_not_awaited()
        orch.event_publisher.publish_response_complete.assert_awaited_once()

    async def test_modify_rewrites_prompt(self, tmp_path):
        # The rewritten prompt should reach the mode runner's messages.
        body = f"""\
        #!{sys.executable}
        import json, sys
        print(json.dumps({{"decision": "modify", "payload": {{"prompt": "[REDACTED] hello"}}}}))
        sys.exit(0)
        """
        h = _write_hook(tmp_path, "redact", body)
        _install_hooks(tmp_path, {"UserPromptSubmit": [HookConfig(name="r", command=[h])]})
        orch, repo, plain = _make_orchestrator()
        sid = uuid.uuid4()
        await repo.create(Session(id=sid, user_email="u@example.gov"))
        await orch.execute(session_id=sid, content="secret", model="m")
        # The mode runner received messages with the rewritten last user content.
        _kwargs = plain.run_streaming.call_args.kwargs
        messages = _kwargs.get("messages") or plain.run_streaming.call_args.args
        # messages is a list of dicts; last user message should be redacted.
        user_msgs = [m for m in messages if m.get("role") == "user"]
        assert user_msgs and "[REDACTED] hello" in user_msgs[-1]["content"]


# --------------------------------------------------------- PreToolUse/etc.


class TestToolEvents:
    def _setup(self, tmp_path, hooks):
        _install_hooks(tmp_path, hooks)
        # build a tool_manager mock + tool_call + session_context
        tool_call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(name="filesystem__write_file", arguments='{"path": "/etc/passwd"}'),
        )
        tool_manager = MagicMock()
        tool_manager.get_server_for_tool.return_value = "filesystem"
        tool_manager.get_tools_schema.return_value = [
            {"function": {"name": "filesystem__write_file", "parameters": {"properties": {"path": {}, "content": {}}}}}
        ]
        tool_manager.execute_tool = AsyncMock(return_value=ToolResult(
            tool_call_id="call-1", content="wrote file", success=True
        ))
        session_context = {"session_id": "s1", "user_email": "u@example.gov", "compliance_level": 3}
        return tool_call, tool_manager, session_context

    async def test_no_config_executes_normally(self, tmp_path):
        tool_call, tool_manager, sc = self._setup(tmp_path, {})
        result = await execute_single_tool(
            tool_call, sc, tool_manager, update_callback=AsyncMock(),
            config_manager=ConfigManager(atlas_root=Path(__file__).resolve().parents[1]),
            skip_approval=True,
        )
        assert result.success is True
        tool_manager.execute_tool.assert_awaited_once()

    async def test_pre_tool_use_deny_blocks_execution(self, tmp_path):
        deny = _write_hook(tmp_path, "d", f'#!{sys.executable}\nimport sys; sys.stderr.write("fs blocked"); sys.exit(2)')
        tool_call, tool_manager, sc = self._setup(tmp_path, {"PreToolUse": [HookConfig(name="d", command=[deny])]})
        result = await execute_single_tool(
            tool_call, sc, tool_manager, update_callback=AsyncMock(),
            config_manager=None, skip_approval=True,
        )
        assert result.success is False
        assert "fs blocked" in result.content
        tool_manager.execute_tool.assert_not_awaited()

    async def test_pre_tool_use_modify_rewrites_args_and_reinjects_user(self, tmp_path):
        # The tool schema declares _atlas_user? No -> re-injection is a no-op here,
        # but the path arg should be rewritten by the hook.
        body = f"""\
        #!{sys.executable}
        import json, sys
        env = json.load(sys.stdin)
        env["payload"]["tool_args"] = {{"path": "/tmp/safe.txt"}}
        print(json.dumps({{"decision": "modify", "payload": env["payload"]}}))
        sys.exit(0)
        """
        h = _write_hook(tmp_path, "mod", body)
        tool_call, tool_manager, sc = self._setup(tmp_path, {"PreToolUse": [HookConfig(name="m", command=[h])]})
        result = await execute_single_tool(
            tool_call, sc, tool_manager, update_callback=AsyncMock(),
            config_manager=None, skip_approval=True,
        )
        assert result.success is True
        executed = tool_manager.execute_tool.call_args.args[0]
        assert executed.arguments["path"] == "/tmp/safe.txt"

    async def test_post_tool_use_modify_replaces_result_content(self, tmp_path):
        body = f"""\
        #!{sys.executable}
        import json, sys
        env = json.load(sys.stdin)
        env["payload"]["result_content"] = "[REDACTED RESULT]"
        print(json.dumps({{"decision": "modify", "payload": env["payload"]}}))
        sys.exit(0)
        """
        h = _write_hook(tmp_path, "post", body)
        tool_call, tool_manager, sc = self._setup(tmp_path, {"PostToolUse": [HookConfig(name="p", command=[h])]})
        result = await execute_single_tool(
            tool_call, sc, tool_manager, update_callback=AsyncMock(),
            config_manager=None, skip_approval=True,
        )
        assert result.success is True
        assert "[REDACTED RESULT]" in result.content

    async def test_post_tool_use_deny_replaces_with_error(self, tmp_path):
        deny = _write_hook(tmp_path, "d", f'#!{sys.executable}\nimport sys; sys.stderr.write("result blocked"); sys.exit(2)')
        tool_call, tool_manager, sc = self._setup(tmp_path, {"PostToolUse": [HookConfig(name="d", command=[deny])]})
        result = await execute_single_tool(
            tool_call, sc, tool_manager, update_callback=AsyncMock(),
            config_manager=None, skip_approval=True,
        )
        assert result.success is False
        assert "result blocked" in result.content

    async def test_permission_request_require_approval_forces_gate(self, tmp_path):
        body = f'#!{sys.executable}\nimport json,sys; print(json.dumps({{"decision":"require_approval"}})); sys.exit(0)'
        h = _write_hook(tmp_path, "esc", body)
        tool_call, tool_manager, sc = self._setup(tmp_path, {"PermissionRequest": [HookConfig(name="e", command=[h])]})
        # With skip_approval=True, normally no gate; the hook should force it.
        # The approval manager will wait; we short-circuit by patching it to auto-approve.
        from atlas.application.chat.utilities import tool_executor as te
        fake_request = SimpleNamespace(
            wait_for_response=AsyncMock(return_value={"approved": True, "arguments": None, "reason": ""}),
        )
        fake_mgr = MagicMock()
        fake_mgr.create_approval_request.return_value = fake_request
        fake_mgr.cleanup_request = MagicMock()
        te.get_approval_manager = lambda: fake_mgr
        cb = AsyncMock()
        result = await execute_single_tool(
            tool_call, sc, tool_manager, update_callback=cb, config_manager=None, skip_approval=True,
        )
        assert result.success is True
        fake_mgr.create_approval_request.assert_called_once()  # gate was entered


# ------------------------------------------------------------- PreLlmCall


class TestPreLlmCall:
    def _caller(self, tmp_path, monkeypatch):
        # Build a LiteLLMCaller and stub model resolution so the test does not
        # depend on which models happen to be in the active llmconfig (which
        # varies with collection context). The hook fires after resolution, so
        # we make resolution a deterministic no-op.
        caller = _real_litellm_caller_cls()()
        monkeypatch.setattr(caller, "_get_litellm_model_name", lambda name: name)
        monkeypatch.setattr(caller, "_get_model_kwargs", lambda name, t=None, user_email=None: {})
        return caller

    async def test_no_config_proceeds(self, tmp_path, monkeypatch):
        caller = self._caller(tmp_path, monkeypatch)
        async def fake_acompletion(**kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])
        monkeypatch.setattr("atlas.modules.llm.litellm_caller.acompletion", fake_acompletion)
        out = await caller.call_plain("gpt-4o", [{"role": "user", "content": "hi"}], user_email="u@example.gov")
        assert out == "ok"

    async def test_deny_raises_hook_blocked(self, tmp_path, monkeypatch):
        deny = _write_hook(tmp_path, "d", f'#!{sys.executable}\nimport sys; sys.stderr.write("llm blocked"); sys.exit(2)')
        _install_hooks(tmp_path, {"PreLlmCall": [HookConfig(name="d", command=[deny])]})
        caller = self._caller(tmp_path, monkeypatch)
        async def fake_acompletion(**kwargs):
            raise AssertionError("must not reach provider")
        monkeypatch.setattr("atlas.modules.llm.litellm_caller.acompletion", fake_acompletion)
        with pytest.raises(HookBlockedError) as ei:
            await caller.call_plain("gpt-4o", [{"role": "user", "content": "hi"}], user_email="u@example.gov")
        assert "llm blocked" in ei.value.reason

    async def test_modify_rewrites_messages(self, tmp_path, monkeypatch):
        body = f"""\
        #!{sys.executable}
        import json, sys
        env = json.load(sys.stdin)
        env["payload"]["messages"] = [{{"role": "user", "content": "[REDACTED]"}}]
        print(json.dumps({{"decision": "modify", "payload": env["payload"]}}))
        sys.exit(0)
        """
        h = _write_hook(tmp_path, "redact", body)
        _install_hooks(tmp_path, {"PreLlmCall": [HookConfig(name="r", command=[h])]})
        caller = self._caller(tmp_path, monkeypatch)
        seen = {}
        async def fake_acompletion(**kwargs):
            seen["messages"] = kwargs["messages"]
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])
        monkeypatch.setattr("atlas.modules.llm.litellm_caller.acompletion", fake_acompletion)
        await caller.call_plain("gpt-4o", [{"role": "user", "content": "secret"}], user_email="u@example.gov")
        assert seen["messages"][-1]["content"] == "[REDACTED]"


# ----------------------------------------------------------- RagCall/Response


class TestRagHooks:
    def _service(self, tmp_path):
        cm = ConfigManager(atlas_root=Path(__file__).resolve().parents[1])
        from atlas.domain.unified_rag_service import UnifiedRAGService
        svc = UnifiedRAGService(cm)
        return svc

    async def test_no_config_passes_through(self, tmp_path, monkeypatch):
        svc = self._service(tmp_path)
        async def fake_impl(self_, username, qds, messages):
            return SimpleNamespace(content="rag answer", metadata=None, is_completion=False)
        monkeypatch.setattr(UnifiedRAGService, "_query_rag_impl", fake_impl)
        resp = await svc.query_rag("u@example.gov", "atlas_rag:docs", [{"role": "user", "content": "q"}])
        assert resp.content == "rag answer"

    async def test_rag_call_deny_returns_empty(self, tmp_path, monkeypatch):
        deny = _write_hook(tmp_path, "d", f'#!{sys.executable}\nimport sys; sys.stderr.write("no rag"); sys.exit(2)')
        _install_hooks(tmp_path, {"RagCall": [HookConfig(name="d", command=[deny])]})
        svc = self._service(tmp_path)
        called = {"v": False}
        async def fake_impl(self_, username, qds, messages):
            called["v"] = True
            return SimpleNamespace(content="should not see", metadata=None, is_completion=False)
        monkeypatch.setattr(UnifiedRAGService, "_query_rag_impl", fake_impl)
        resp = await svc.query_rag("u@example.gov", "atlas_rag:docs", [{"role": "user", "content": "q"}])
        assert resp.content == ""
        assert called["v"] is False

    async def test_rag_call_modify_rewrites_query(self, tmp_path, monkeypatch):
        body = f"""\
        #!{sys.executable}
        import json, sys
        print(json.dumps({{"decision": "modify", "payload": {{"query": "rewritten query"}}}}))
        sys.exit(0)
        """
        h = _write_hook(tmp_path, "rw", body)
        _install_hooks(tmp_path, {"RagCall": [HookConfig(name="r", command=[h])]})
        svc = self._service(tmp_path)
        seen = {}
        async def fake_impl(self_, username, qds, messages):
            seen["messages"] = messages
            return SimpleNamespace(content="a", metadata=None, is_completion=False)
        monkeypatch.setattr(UnifiedRAGService, "_query_rag_impl", fake_impl)
        await svc.query_rag("u@example.gov", "atlas_rag:docs", [{"role": "user", "content": "original"}])
        assert seen["messages"][-1]["content"] == "rewritten query"

    async def test_rag_response_modify_replaces_content(self, tmp_path, monkeypatch):
        body = f"""\
        #!{sys.executable}
        import json, sys
        env = json.load(sys.stdin)
        env["payload"]["content"] = "[REDACTED CONTEXT]"
        print(json.dumps({{"decision": "modify", "payload": env["payload"]}}))
        sys.exit(0)
        """
        h = _write_hook(tmp_path, "rr", body)
        _install_hooks(tmp_path, {"RagResponse": [HookConfig(name="r", command=[h])]})
        svc = self._service(tmp_path)
        async def fake_impl(self_, username, qds, messages):
            return SimpleNamespace(content="secret context", metadata=None, is_completion=False)
        monkeypatch.setattr(UnifiedRAGService, "_query_rag_impl", fake_impl)
        resp = await svc.query_rag("u@example.gov", "atlas_rag:docs", [{"role": "user", "content": "q"}])
        assert resp.content == "[REDACTED CONTEXT]"

    async def test_batch_narrowing_drops_unlisted_sources(self, tmp_path, monkeypatch):
        # Hook narrows to a subset; sources it lists that weren't in the original
        # must be dropped (cannot widen).
        body = f"""\
        #!{sys.executable}
        import json, sys
        print(json.dumps({{"decision": "modify", "payload": {{"qualified_data_sources": ["atlas_rag:docs", "atlas_rag:EVIL"]}}}}))
        sys.exit(0)
        """
        h = _write_hook(tmp_path, "narrow", body)
        _install_hooks(tmp_path, {"RagCall": [HookConfig(name="n", command=[h])]})
        svc = self._service(tmp_path)
        seen = {}
        async def fake_impl(self_, username, qds, messages):
            seen["qds"] = list(qds)
            return SimpleNamespace(content="a", metadata=None, is_completion=False)
        monkeypatch.setattr(UnifiedRAGService, "_query_rag_batch_impl", fake_impl)
        await svc.query_rag_batch("u@example.gov", ["atlas_rag:docs", "atlas_rag:news"], [{"role": "user", "content": "q"}])
        # EVIL was dropped (not in original); news was dropped by the hook; only docs survives.
        assert seen["qds"] == ["atlas_rag:docs"]


# --------------------------------------------------------- no-config invariant


class TestNoConfigInvariant:
    """Every wired event must be a complete no-op (no subprocess) with no hooks."""

    async def test_create_session_no_subprocess(self, tmp_path, monkeypatch):
        spawned = []
        async def fake_exec(*a, **k):
            spawned.append(1)
            raise RuntimeError("should not spawn")
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        s, _ = _make_chat_service(tmp_path)
        await s.create_session(uuid.uuid4(), "u@example.gov")
        assert spawned == []

    async def test_orchestrator_no_subprocess(self, monkeypatch):
        spawned = []
        async def fake_exec(*a, **k):
            spawned.append(1)
            raise RuntimeError("should not spawn")
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        orch, repo, _ = _make_orchestrator()
        sid = uuid.uuid4()
        await repo.create(Session(id=sid, user_email="u@example.gov"))
        await orch.execute(session_id=sid, content="hi", model="m")
        assert spawned == []

    async def test_tool_executor_no_subprocess(self, tmp_path, monkeypatch):
        spawned = []
        async def fake_exec(*a, **k):
            spawned.append(1)
            raise RuntimeError("should not spawn")
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        tc = SimpleNamespace(id="c1", function=SimpleNamespace(name="t", arguments="{}"))
        tm = MagicMock()
        tm.get_server_for_tool.return_value = "s"
        tm.get_tools_schema.return_value = [{"function": {"name": "t", "parameters": {"properties": {}}}}]
        tm.execute_tool = AsyncMock(return_value=ToolResult(tool_call_id="c1", content="ok", success=True))
        await execute_single_tool(tc, {"user_email": "u"}, tm, update_callback=AsyncMock(), skip_approval=True)
        assert spawned == []

    async def test_llm_caller_no_subprocess(self, monkeypatch):
        spawned = []
        async def fake_exec(*a, **k):
            spawned.append(1)
            raise RuntimeError("should not spawn")
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        caller = _real_litellm_caller_cls()()
        monkeypatch.setattr(caller, "_get_litellm_model_name", lambda name: name)
        monkeypatch.setattr(caller, "_get_model_kwargs", lambda name, t=None, user_email=None: {})
        async def fake_acompletion(**kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])
        monkeypatch.setattr("atlas.modules.llm.litellm_caller.acompletion", fake_acompletion)
        await caller.call_plain("gpt-4o", [{"role": "user", "content": "hi"}])
        assert spawned == []

    async def test_rag_no_subprocess(self, tmp_path, monkeypatch):
        spawned = []
        async def fake_exec(*a, **k):
            spawned.append(1)
            raise RuntimeError("should not spawn")
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        cm = ConfigManager(atlas_root=Path(__file__).resolve().parents[1])
        from atlas.domain.unified_rag_service import UnifiedRAGService
        svc = UnifiedRAGService(cm)
        async def fake_impl(self_, username, qds, messages):
            return SimpleNamespace(content="a", metadata=None, is_completion=False)
        monkeypatch.setattr(UnifiedRAGService, "_query_rag_impl", fake_impl)
        await svc.query_rag("u", "atlas_rag:docs", [{"role": "user", "content": "q"}])
        assert spawned == []
