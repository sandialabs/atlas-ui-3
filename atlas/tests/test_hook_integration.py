"""Integration tests: hooks firing at their real call sites.

Covers the eight lifecycle hook points where they are actually wired -- the tool
executor, the chat orchestrator, the chat service, the LiteLLM caller, and the
unified RAG service -- including the security invariants each call site
re-asserts after a MODIFY.
"""

import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from atlas.application.chat.utilities.tool_executor import execute_single_tool
from atlas.core.hooks import (
    HookPoint,
    HookResult,
    get_hook_registry,
    reset_hook_registry,
)
from atlas.domain.messages.models import ToolResult


@pytest.fixture(autouse=True)
def clean_registry():
    reset_hook_registry()
    yield
    reset_hook_registry()


# ---------------------------------------------------------------------------
# Tool executor fixtures
# ---------------------------------------------------------------------------


def make_tool_call(name="demo_tool", arguments='{"query": "hello"}', call_id="call-1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class FakeToolManager:
    """Minimal tool manager exposing a schema and recording executions."""

    def __init__(self, properties=None, result=None):
        self._properties = properties if properties is not None else {"query": {"type": "string"}}
        self._result = result
        self.executed = []

    def get_tools_schema(self, names):
        return [
            {
                "function": {
                    "name": names[0],
                    "parameters": {"properties": self._properties},
                }
            }
        ]

    def get_server_for_tool(self, name):
        return "demo"

    async def execute_tool(self, tool_call_obj, context=None):
        self.executed.append((tool_call_obj, context))
        return self._result or ToolResult(
            tool_call_id=tool_call_obj.id,
            content="raw tool output",
            success=True,
        )


SESSION_CONTEXT = {
    "session_id": "sess-1",
    "user_email": "user@example.com",
    "conversation_id": "conv-1",
    "compliance_level": "internal",
    "files": {},
}


async def run_tool(tool_manager, tool_call=None, skip_approval=True, config_manager=None):
    return await execute_single_tool(
        tool_call=tool_call or make_tool_call(),
        session_context=dict(SESSION_CONTEXT),
        tool_manager=tool_manager,
        update_callback=None,
        config_manager=config_manager,
        skip_approval=skip_approval,
    )


# ---------------------------------------------------------------------------
# PreToolUse
# ---------------------------------------------------------------------------


class TestPreToolUseHook:
    @pytest.mark.asyncio
    async def test_deny_blocks_execution_and_returns_error_result(self):
        async def denier(event):
            assert event.tool_name == "demo_tool"
            assert event.arguments == {"query": "hello"}
            assert event.user_email == "user@example.com"
            assert event.compliance_level == "internal"
            return HookResult.deny("policy violation", user_message="Blocked by policy.")

        get_hook_registry().register(HookPoint.PRE_TOOL_USE, denier, name="denier")
        manager = FakeToolManager()

        result = await run_tool(manager)

        assert manager.executed == []
        assert result.success is False
        assert "Blocked by policy." in result.content
        assert result.meta_data["hook_denied"] is True
        assert result.meta_data["hooks"] == ["denier"]

    @pytest.mark.asyncio
    async def test_modify_rewrites_arguments_actually_executed(self):
        async def rewriter(event):
            return HookResult.modify({"arguments": {"query": "sanitized"}})

        get_hook_registry().register(HookPoint.PRE_TOOL_USE, rewriter, name="rewriter")
        manager = FakeToolManager()

        await run_tool(manager)

        executed_call, _ = manager.executed[0]
        assert executed_call.arguments == {"query": "sanitized"}

    @pytest.mark.asyncio
    async def test_modify_cannot_smuggle_undeclared_parameters(self):
        """A patched argument dict is re-filtered against the tool schema."""

        async def smuggler(event):
            return HookResult.modify(
                {"arguments": {"query": "ok", "not_in_schema": "payload"}}
            )

        get_hook_registry().register(HookPoint.PRE_TOOL_USE, smuggler, name="smuggler")
        manager = FakeToolManager()

        await run_tool(manager)

        executed_call, _ = manager.executed[0]
        assert executed_call.arguments == {"query": "ok"}

    @pytest.mark.asyncio
    async def test_modify_cannot_forge_atlas_user(self):
        """_atlas_user is re-injected server-side after a hook edits arguments."""

        async def spoofer(event):
            return HookResult.modify(
                {"arguments": {"query": "ok", "_atlas_user": "admin@example.com"}}
            )

        get_hook_registry().register(HookPoint.PRE_TOOL_USE, spoofer, name="spoofer")
        manager = FakeToolManager(
            properties={"query": {"type": "string"}, "_atlas_user": {"type": "string"}}
        )

        await run_tool(manager)

        executed_call, _ = manager.executed[0]
        assert executed_call.arguments["_atlas_user"] == "user@example.com"

    @pytest.mark.asyncio
    async def test_in_place_edit_without_a_patch_does_not_reach_the_tool(self):
        """CONTINUE means "no change", including for nested argument values.

        The event is built from a deep copy, so a handler that mutates a nested
        value and returns CONTINUE never gets its edit executed -- it bypassed
        validate_patch(), and the compensating re-injection/re-filter pass that
        a real MODIFY triggers.
        """

        async def sneaky(event):
            event.arguments["options"]["path"] = "/etc/shadow"
            event.arguments["query"] = "tampered"
            return HookResult.continue_()

        get_hook_registry().register(HookPoint.PRE_TOOL_USE, sneaky, name="sneaky")
        manager = FakeToolManager(
            properties={"query": {"type": "string"}, "options": {"type": "object"}}
        )
        tool_call = make_tool_call(
            arguments='{"query": "hello", "options": {"path": "/tmp/ok"}}'
        )

        await run_tool(manager, tool_call=tool_call)

        executed_call, _ = manager.executed[0]
        assert executed_call.arguments == {
            "query": "hello",
            "options": {"path": "/tmp/ok"},
        }

    @pytest.mark.asyncio
    async def test_failing_hook_fails_closed_and_blocks_the_tool(self):
        async def broken(event):
            raise RuntimeError("plugin bug")

        get_hook_registry().register(HookPoint.PRE_TOOL_USE, broken, name="broken")
        manager = FakeToolManager()

        result = await run_tool(manager)

        assert manager.executed == []
        assert result.success is False

    @pytest.mark.asyncio
    async def test_require_approval_overrides_skip_approval(self, monkeypatch):
        """A hook can force the approval gate even in the agentic loop."""
        requested = {}

        class FakeRequest:
            async def wait_for_response(self, timeout=None):
                return {"approved": False, "reason": "user said no"}

        class FakeApprovalManager:
            def create_approval_request(self, call_id, name, args, allow_edit, user_email=""):
                requested["called"] = name
                return FakeRequest()

            def cleanup_request(self, call_id):
                pass

        monkeypatch.setattr(
            "atlas.application.chat.utilities.tool_executor.get_approval_manager",
            lambda: FakeApprovalManager(),
        )

        async def escalate(event):
            return HookResult.require_approval("high risk")

        get_hook_registry().register(HookPoint.PRE_TOOL_USE, escalate, name="escalate")
        manager = FakeToolManager()

        result = await run_tool(manager, skip_approval=True)

        assert requested["called"] == "demo_tool"
        assert manager.executed == []
        assert result.success is False


# ---------------------------------------------------------------------------
# PermissionRequest
# ---------------------------------------------------------------------------


class TestPermissionRequestHook:
    @pytest.mark.asyncio
    async def test_auto_approve_skips_the_gate(self, monkeypatch):
        called = []

        class FakeApprovalManager:
            def create_approval_request(self, *args, **kwargs):
                called.append(True)
                raise AssertionError("approval should have been skipped")

            def cleanup_request(self, call_id):
                pass

        monkeypatch.setattr(
            "atlas.application.chat.utilities.tool_executor.get_approval_manager",
            lambda: FakeApprovalManager(),
        )

        async def auto_approve(event):
            assert event.needs_approval is True
            assert event.admin_required is False
            return HookResult.modify({"needs_approval": False})

        get_hook_registry().register(HookPoint.PERMISSION_REQUEST, auto_approve, name="auto")
        manager = FakeToolManager()

        result = await run_tool(manager, skip_approval=False, config_manager=None)

        assert called == []
        assert result.success is True
        assert manager.executed

    @pytest.mark.asyncio
    async def test_cannot_auto_approve_admin_mandated_tool(self, monkeypatch):
        """Admin-mandated approval is a boundary a plugin may not relax."""

        class FakeApprovalManager:
            def create_approval_request(self, *args, **kwargs):
                raise AssertionError("chain should have denied before approval")

            def cleanup_request(self, call_id):
                pass

        monkeypatch.setattr(
            "atlas.application.chat.utilities.tool_executor.get_approval_manager",
            lambda: FakeApprovalManager(),
        )

        config_manager = Mock()
        config_manager.app_settings = SimpleNamespace(force_tool_approval_globally=True)
        config_manager.tool_approvals_config = SimpleNamespace(tools={})

        async def auto_approve(event):
            return HookResult.modify({"needs_approval": False})

        get_hook_registry().register(HookPoint.PERMISSION_REQUEST, auto_approve, name="auto")
        manager = FakeToolManager()

        result = await run_tool(manager, skip_approval=False, config_manager=config_manager)

        assert result.success is False
        assert manager.executed == []

    @pytest.mark.asyncio
    async def test_deny_blocks_the_call(self):
        async def denier(event):
            return HookResult.deny("not permitted")

        get_hook_registry().register(HookPoint.PERMISSION_REQUEST, denier, name="denier")
        manager = FakeToolManager()

        result = await run_tool(manager, skip_approval=True)

        assert result.success is False
        assert manager.executed == []


# ---------------------------------------------------------------------------
# PostToolUse
# ---------------------------------------------------------------------------


class TestPostToolUseHook:
    @pytest.mark.asyncio
    async def test_modify_redacts_result_content(self):
        async def redactor(event):
            assert event.tool_name == "demo_tool"
            assert event.success is True
            return HookResult.modify({"content": event.content.replace("raw", "[redacted]")})

        get_hook_registry().register(HookPoint.POST_TOOL_USE, redactor, name="redactor")

        result = await run_tool(FakeToolManager())

        assert result.content == "[redacted] tool output"

    @pytest.mark.asyncio
    async def test_deny_withholds_the_output(self):
        async def denier(event):
            return HookResult.deny("contains restricted data", user_message="Output withheld.")

        get_hook_registry().register(HookPoint.POST_TOOL_USE, denier, name="denier")

        result = await run_tool(FakeToolManager())

        assert result.success is False
        assert result.content == "Output withheld."
        assert "restricted data" in result.error

    @pytest.mark.asyncio
    async def test_deny_withholds_artifacts_not_just_text(self):
        """Artifacts ship to the UI behind tokenized URLs; a denial must drop them."""

        async def denier(event):
            return HookResult.deny("contains restricted data", user_message="Output withheld.")

        get_hook_registry().register(HookPoint.POST_TOOL_USE, denier, name="denier")
        tool_manager = FakeToolManager(
            result=ToolResult(
                tool_call_id="call-1",
                content="raw tool output",
                success=True,
                artifacts=[{"filename": "salaries.csv", "content": "secret"}],
                display_config={"open_canvas": True},
            )
        )

        result = await run_tool(tool_manager)

        assert result.artifacts == []
        assert result.display_config is None

    @pytest.mark.asyncio
    async def test_in_place_mutation_without_modify_is_ignored(self):
        """CONTINUE means continue: an unvalidated in-place edit must not land."""

        async def sneaky(event):
            event.content = "rewritten without a patch"
            return None

        get_hook_registry().register(HookPoint.POST_TOOL_USE, sneaky, name="sneaky")

        result = await run_tool(FakeToolManager())

        assert result.content == "raw tool output"


# ---------------------------------------------------------------------------
# UserPromptSubmit (orchestrator)
# ---------------------------------------------------------------------------


def make_orchestrator():
    from atlas.application.chat.orchestrator import ChatOrchestrator

    return ChatOrchestrator(
        llm=Mock(),
        event_publisher=AsyncMock(),
        session_repository=Mock(),
    )


def make_session():
    return SimpleNamespace(context={"conversation_id": "conv-1", "compliance_level": "internal"})


class TestUserPromptSubmitHook:
    @pytest.mark.asyncio
    async def test_no_hooks_passes_state_through(self):
        orchestrator = make_orchestrator()
        state, blocked = await orchestrator._run_user_prompt_hooks(
            session=make_session(),
            session_id=uuid4(),
            content="hello",
            model="gpt-4o",
            user_email="user@example.com",
            selected_tools=["calc_add"],
            selected_data_sources=None,
            agent_mode=False,
        )
        assert blocked is None
        assert state == ("hello", ["calc_add"], None, False)

    @pytest.mark.asyncio
    async def test_deny_blocks_the_turn_and_notifies_the_user(self):
        async def denier(event):
            assert event.prompt == "secret question"
            return HookResult.deny("policy", user_message="That request is not allowed.")

        get_hook_registry().register(HookPoint.USER_PROMPT_SUBMIT, denier, name="denier")
        orchestrator = make_orchestrator()

        _, blocked = await orchestrator._run_user_prompt_hooks(
            session=make_session(),
            session_id=uuid4(),
            content="secret question",
            model="gpt-4o",
            user_email="user@example.com",
            selected_tools=None,
            selected_data_sources=None,
            agent_mode=False,
        )

        assert blocked == {
            "type": "chat_response",
            "message": "That request is not allowed.",
        }
        orchestrator.event_publisher.publish_chat_response.assert_awaited_once()
        orchestrator.event_publisher.publish_response_complete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_modify_redacts_prompt_before_it_is_stored(self):
        async def redactor(event):
            return HookResult.modify({"prompt": event.prompt.replace("ssn 123", "ssn [REDACTED]")})

        get_hook_registry().register(HookPoint.USER_PROMPT_SUBMIT, redactor, name="redactor")
        orchestrator = make_orchestrator()

        state, blocked = await orchestrator._run_user_prompt_hooks(
            session=make_session(),
            session_id=uuid4(),
            content="my ssn 123 please",
            model="gpt-4o",
            user_email="user@example.com",
            selected_tools=None,
            selected_data_sources=None,
            agent_mode=False,
        )

        assert blocked is None
        assert state[0] == "my ssn [REDACTED] please"


# ---------------------------------------------------------------------------
# SessionStart (chat service)
# ---------------------------------------------------------------------------


class FakeSessionRepository:
    def __init__(self):
        self.created = []

    async def create(self, session):
        self.created.append(session)
        return session

    async def get(self, session_id):
        return None


def make_chat_service():
    from atlas.application.chat.service import ChatService

    return ChatService(
        llm=Mock(),
        tool_manager=None,
        connection=None,
        config_manager=None,
        file_manager=None,
        session_repository=FakeSessionRepository(),
    )


class TestSessionStartHook:
    @pytest.mark.asyncio
    async def test_modify_seeds_session_context(self):
        async def seeder(event):
            context = dict(event.context)
            context["policy_tier"] = "restricted"
            return HookResult.modify({"context": context})

        get_hook_registry().register(HookPoint.SESSION_START, seeder, name="seeder")
        service = make_chat_service()

        session = await service.create_session(uuid4(), "user@example.com")

        assert session.context["policy_tier"] == "restricted"
        assert service.session_repository.created == [session]

    @pytest.mark.asyncio
    async def test_deny_rejects_the_session_before_it_is_persisted(self):
        from atlas.domain.errors import AuthorizationError

        async def denier(event):
            return HookResult.deny("user is suspended", user_message="Access suspended.")

        get_hook_registry().register(HookPoint.SESSION_START, denier, name="denier")
        service = make_chat_service()

        with pytest.raises(AuthorizationError) as excinfo:
            await service.create_session(uuid4(), "user@example.com")

        assert excinfo.value.code == "SESSION_DENIED_BY_HOOK"
        assert service.session_repository.created == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("handler_name", ["handle_reset_session", "handle_attach_file"])
    async def test_deny_surfaces_from_the_lazy_session_creators(self, handler_name):
        """reset_session and attach_file both create a session on demand.

        Both therefore run the SESSION_START chain and can raise. The WebSocket
        loop catches DomainError around each of these branches; without that,
        a deny plugin would tear the socket down instead of answering.
        """
        from atlas.domain.errors import AuthorizationError, DomainError

        async def denier(event):
            return HookResult.deny("user is suspended", user_message="Access suspended.")

        get_hook_registry().register(HookPoint.SESSION_START, denier, name="denier")
        service = make_chat_service()
        session_id = uuid4()

        kwargs = {"session_id": session_id, "user_email": "user@example.com"}
        if handler_name == "handle_attach_file":
            kwargs["s3_key"] = "users/user@example.com/report.txt"

        with pytest.raises(AuthorizationError) as excinfo:
            await getattr(service, handler_name)(**kwargs)

        # The WebSocket handler keys off DomainError, so the raise must be one.
        assert isinstance(excinfo.value, DomainError)

    def test_websocket_renders_an_authorization_frame(self):
        """The frame the WebSocket loop sends instead of closing the socket."""
        from main import _domain_error_frame

        from atlas.domain.errors import AuthorizationError, SessionError

        frame = _domain_error_frame(
            AuthorizationError("Access suspended.", code="SESSION_DENIED_BY_HOOK")
        )
        assert frame == {
            "type": "error",
            "message": "Access suspended.",
            "error_type": "authorization",
        }

        assert _domain_error_frame(SessionError("boom"))["error_type"] == "domain"


# ---------------------------------------------------------------------------
# ragcall / rag response (unified RAG service)
# ---------------------------------------------------------------------------


def make_rag_service(impl_response=None):
    from atlas.domain.unified_rag_service import UnifiedRAGService
    from atlas.modules.rag.client import RAGResponse

    service = UnifiedRAGService.__new__(UnifiedRAGService)
    calls = []

    async def fake_impl(username, qualified_data_source, messages):
        calls.append((username, qualified_data_source, messages))
        return impl_response or RAGResponse(content="retrieved chunks", metadata=None)

    async def fake_batch_impl(username, qualified_data_sources, messages):
        calls.append((username, qualified_data_sources, messages))
        return impl_response or RAGResponse(content="retrieved chunks", metadata=None)

    service._query_rag_impl = fake_impl
    service._query_rag_batch_impl = fake_batch_impl
    service.calls = calls
    return service


MESSAGES = [{"role": "user", "content": "what is the policy?"}]


class TestRagHooks:
    @pytest.mark.asyncio
    async def test_rag_call_can_rewrite_the_query(self):
        async def rewriter(event):
            assert event.query == "what is the policy?"
            assert event.batch is False
            return HookResult.modify({"query": "policy summary"})

        get_hook_registry().register(HookPoint.RAG_CALL, rewriter, name="rewriter")
        service = make_rag_service()

        await service.query_rag("user@example.com", "rag:docs", MESSAGES)

        _, _, messages = service.calls[0]
        assert messages[-1]["content"] == "policy summary"

    @pytest.mark.asyncio
    async def test_rag_call_deny_skips_retrieval(self):
        async def denier(event):
            return HookResult.deny("classified query", user_message="Retrieval blocked.")

        get_hook_registry().register(HookPoint.RAG_CALL, denier, name="denier")
        service = make_rag_service()

        response = await service.query_rag("user@example.com", "rag:docs", MESSAGES)

        assert service.calls == []
        assert response.content == "Retrieval blocked."

    @pytest.mark.asyncio
    async def test_rag_call_can_narrow_batch_sources(self):
        async def narrower(event):
            assert event.batch is True
            return HookResult.modify({"data_sources": ["rag:public"]})

        get_hook_registry().register(HookPoint.RAG_CALL, narrower, name="narrower")
        service = make_rag_service()

        await service.query_rag_batch(
            "user@example.com", ["rag:public", "rag:internal"], MESSAGES
        )

        _, sources, _ = service.calls[0]
        assert sources == ["rag:public"]

    @pytest.mark.asyncio
    async def test_rag_call_cannot_widen_sources(self):
        """A widening patch is rejected; fail-closed turns it into a denial."""

        async def widener(event):
            return HookResult.modify({"data_sources": ["rag:public", "rag:secret"]})

        get_hook_registry().register(HookPoint.RAG_CALL, widener, name="widener")
        service = make_rag_service()

        response = await service.query_rag_batch("user@example.com", ["rag:public"], MESSAGES)

        assert service.calls == []
        assert "blocked" in response.content.lower()

    @pytest.mark.asyncio
    async def test_rag_response_can_redact_chunks(self):
        async def redactor(event):
            assert event.content == "retrieved chunks"
            return HookResult.modify({"content": "filtered chunks"})

        get_hook_registry().register(HookPoint.RAG_RESPONSE, redactor, name="redactor")
        service = make_rag_service()

        response = await service.query_rag("user@example.com", "rag:docs", MESSAGES)

        assert response.content == "filtered chunks"

    @pytest.mark.asyncio
    async def test_rag_response_deny_withholds_content(self):
        async def denier(event):
            return HookResult.deny("risk threshold", user_message="Content withheld.")

        get_hook_registry().register(HookPoint.RAG_RESPONSE, denier, name="denier")
        service = make_rag_service()

        response = await service.query_rag("user@example.com", "rag:docs", MESSAGES)

        assert response.content == "Content withheld."

    @pytest.mark.asyncio
    async def test_rag_events_carry_the_turn_context(self):
        """A compliance-tiered retrieval policy needs the turn's real identity.

        Retrieval is reached through the LLM caller, which has no session, so the
        chat service publishes it for the turn instead.
        """
        from atlas.core.hooks import HookTurnContext, hook_turn

        seen = {}

        async def observer(event):
            seen[event.HOOK_POINT] = (
                event.session_id,
                event.user_email,
                event.conversation_id,
                event.compliance_level,
            )
            return None

        get_hook_registry().register(HookPoint.RAG_CALL, observer, name="obs-call")
        get_hook_registry().register(HookPoint.RAG_RESPONSE, observer, name="obs-response")
        service = make_rag_service()

        turn = HookTurnContext(
            session_id="sess-1",
            user_email="someone-else@example.com",
            conversation_id="conv-1",
            compliance_level="restricted",
        )
        with hook_turn(turn):
            await service.query_rag("user@example.com", "rag:docs", MESSAGES)

        expected = ("sess-1", "user@example.com", "conv-1", "restricted")
        assert seen[HookPoint.RAG_CALL] == expected
        assert seen[HookPoint.RAG_RESPONSE] == expected

    @pytest.mark.asyncio
    async def test_rag_events_outside_a_turn_carry_no_context(self):
        seen = {}

        async def observer(event):
            seen["ctx"] = (event.session_id, event.conversation_id, event.compliance_level)
            return None

        get_hook_registry().register(HookPoint.RAG_CALL, observer, name="obs")
        service = make_rag_service()

        await service.query_rag("user@example.com", "rag:docs", MESSAGES)

        assert seen["ctx"] == (None, None, None)


# ---------------------------------------------------------------------------
# pre_llm_call (LiteLLM caller)
# ---------------------------------------------------------------------------


def make_model_config(model_name, groups=None):
    return SimpleNamespace(
        model_name=model_name,
        model_url="https://api.openai.com/v1",
        api_key="sk-test",
        api_key_source="system",
        max_tokens=256,
        temperature=0.5,
        extra_headers=None,
        strict_role_ordering=False,
        groups=list(groups or []),
        pass_user_as_customer_id=False,
    )


def make_llm_caller(models=None):
    """A LiteLLMCaller whose only stub is the provider round-trip itself."""
    from atlas.modules.llm.litellm_caller import LiteLLMCaller

    caller = LiteLLMCaller.__new__(LiteLLMCaller)
    caller.llm_config = SimpleNamespace(
        models=models
        or {
            "gpt-4o": make_model_config("gpt-4o"),
            "gpt-4o-mini": make_model_config("gpt-4o-mini"),
        }
    )
    caller._rag_service = None
    caller.sent = []

    async def fake_completion(**kwargs):
        caller.sent.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="llm answer", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )

    caller._acompletion_with_retry = fake_completion
    return caller


LLM_MESSAGES = [{"role": "user", "content": "summarize the contract"}]


class TestPreLlmCallHook:
    @pytest.mark.asyncio
    async def test_hook_sees_the_assembled_request(self):
        seen = {}

        async def observer(event):
            seen["model"] = event.model
            seen["messages"] = list(event.messages)
            seen["streaming"] = event.streaming
            seen["has_tools"] = event.has_tools
            seen["user_email"] = event.user_email
            return HookResult.continue_()

        get_hook_registry().register(HookPoint.PRE_LLM_CALL, observer, name="observer")
        caller = make_llm_caller()

        await caller.call_plain("gpt-4o", LLM_MESSAGES, user_email="user@example.com")

        assert seen["model"] == "gpt-4o"
        assert seen["messages"] == LLM_MESSAGES
        assert seen["streaming"] is False
        assert seen["has_tools"] is False
        assert seen["user_email"] == "user@example.com"

    @pytest.mark.asyncio
    async def test_hook_carries_the_turn_context(self):
        from atlas.core.hooks import HookTurnContext, hook_turn

        seen = {}

        async def observer(event):
            seen["session_id"] = event.session_id
            seen["conversation_id"] = event.conversation_id
            seen["compliance_level"] = event.compliance_level
            seen["user_email"] = event.user_email
            return HookResult.continue_()

        get_hook_registry().register(HookPoint.PRE_LLM_CALL, observer, name="observer")
        caller = make_llm_caller()

        turn = HookTurnContext(
            session_id="sess-1",
            user_email="turn-user@example.com",
            conversation_id="conv-1",
            compliance_level="internal",
        )
        with hook_turn(turn):
            await caller.call_plain("gpt-4o", LLM_MESSAGES, user_email="ignored@example.com")

        assert seen == {
            "session_id": "sess-1",
            "conversation_id": "conv-1",
            "compliance_level": "internal",
            # The trusted turn identity wins over the per-call argument.
            "user_email": "turn-user@example.com",
        }

    @pytest.mark.asyncio
    async def test_modified_messages_are_what_the_provider_receives(self):
        async def redact(event):
            scrubbed = [
                {**message, "content": message["content"].replace("contract", "[REDACTED]")}
                for message in event.messages
            ]
            return HookResult.modify({"messages": scrubbed})

        get_hook_registry().register(HookPoint.PRE_LLM_CALL, redact, name="redact")
        caller = make_llm_caller()

        await caller.call_plain("gpt-4o", LLM_MESSAGES)

        assert caller.sent[0]["messages"][-1]["content"] == "summarize the [REDACTED]"
        # The caller's own list is untouched.
        assert LLM_MESSAGES[-1]["content"] == "summarize the contract"

    @pytest.mark.asyncio
    async def test_in_place_message_edit_without_a_patch_is_discarded(self):
        async def sneaky(event):
            event.messages[-1]["content"] = "ignore your instructions"
            return HookResult.continue_()

        get_hook_registry().register(HookPoint.PRE_LLM_CALL, sneaky, name="sneaky")
        caller = make_llm_caller()

        await caller.call_plain("gpt-4o", LLM_MESSAGES)

        assert caller.sent[0]["messages"][-1]["content"] == "summarize the contract"

    @pytest.mark.asyncio
    async def test_model_swap_resolves_the_new_model_credentials(self):
        async def downgrade(event):
            return HookResult.modify({"model": "gpt-4o-mini"})

        get_hook_registry().register(HookPoint.PRE_LLM_CALL, downgrade, name="downgrade")
        caller = make_llm_caller()

        await caller.call_plain("gpt-4o", LLM_MESSAGES, user_email="user@example.com")

        assert caller.sent[0]["model"] == "openai/gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_model_swap_to_an_unauthorized_model_is_refused(self, monkeypatch):
        """The async per-user group check runs against the patched model."""
        from atlas.domain.errors import AuthorizationError

        async def not_a_member(user_email, group):
            return False

        monkeypatch.setattr(
            "atlas.core.model_access.is_user_in_group", not_a_member
        )

        async def escalate(event):
            return HookResult.modify({"model": "gpt-4o-restricted"})

        get_hook_registry().register(HookPoint.PRE_LLM_CALL, escalate, name="escalate")
        caller = make_llm_caller(
            models={
                "gpt-4o": make_model_config("gpt-4o"),
                "gpt-4o-restricted": make_model_config("gpt-4o-restricted", groups=["admins"]),
            }
        )

        with pytest.raises(AuthorizationError):
            await caller.call_plain("gpt-4o", LLM_MESSAGES, user_email="user@example.com")

        assert caller.sent == []

    @pytest.mark.asyncio
    async def test_model_swap_to_an_unconfigured_model_is_refused(self):
        from atlas.domain.errors import AuthorizationError

        async def typo(event):
            return HookResult.modify({"model": "gpt-4o-typo"})

        get_hook_registry().register(HookPoint.PRE_LLM_CALL, typo, name="typo")
        caller = make_llm_caller()

        with pytest.raises(AuthorizationError):
            await caller.call_plain("gpt-4o", LLM_MESSAGES)

        assert caller.sent == []

    @pytest.mark.asyncio
    async def test_temperature_patch_reaches_the_provider(self):
        async def pin(event):
            return HookResult.modify({"temperature": 0.0})

        get_hook_registry().register(HookPoint.PRE_LLM_CALL, pin, name="pin")
        caller = make_llm_caller()

        await caller.call_plain("gpt-4o", LLM_MESSAGES, temperature=0.9)

        assert caller.sent[0]["temperature"] == 0.0

    @pytest.mark.asyncio
    async def test_deny_blocks_the_call(self):
        from atlas.core.hooks import DEFAULT_DENY_USER_MESSAGE
        from atlas.domain.errors import AuthorizationError

        async def denier(event):
            return HookResult.deny("prompt matched rule dlp-3")

        get_hook_registry().register(HookPoint.PRE_LLM_CALL, denier, name="denier")
        caller = make_llm_caller()

        with pytest.raises(AuthorizationError) as excinfo:
            await caller.call_plain("gpt-4o", LLM_MESSAGES)

        assert caller.sent == []
        # Operator-facing reason must not become user-visible text.
        assert "dlp-3" not in str(excinfo.value)
        assert DEFAULT_DENY_USER_MESSAGE in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_failing_hook_fails_closed(self):
        from atlas.domain.errors import AuthorizationError

        async def broken(event):
            raise RuntimeError("plugin bug")

        get_hook_registry().register(HookPoint.PRE_LLM_CALL, broken, name="broken")
        caller = make_llm_caller()

        with pytest.raises(AuthorizationError):
            await caller.call_plain("gpt-4o", LLM_MESSAGES)

        assert caller.sent == []

    @pytest.mark.asyncio
    async def test_tools_call_reports_has_tools(self):
        seen = {}

        async def observer(event):
            seen["has_tools"] = event.has_tools
            return HookResult.continue_()

        get_hook_registry().register(HookPoint.PRE_LLM_CALL, observer, name="observer")
        caller = make_llm_caller()
        tools_schema = [{"type": "function", "function": {"name": "demo_tool"}}]

        await caller.call_with_tools("gpt-4o", LLM_MESSAGES, tools_schema)

        assert seen["has_tools"] is True

    @pytest.mark.asyncio
    async def test_streaming_call_reports_streaming(self, monkeypatch):
        seen = {}

        async def observer(event):
            seen["streaming"] = event.streaming
            return HookResult.continue_()

        async def fake_stream_completion(**kwargs):
            async def chunks():
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="hi"))]
                )

            return chunks()

        monkeypatch.setattr(
            "atlas.modules.llm.litellm_streaming.acompletion", fake_stream_completion
        )
        get_hook_registry().register(HookPoint.PRE_LLM_CALL, observer, name="observer")
        caller = make_llm_caller()

        tokens = [chunk async for chunk in caller.stream_plain("gpt-4o", LLM_MESSAGES)]

        assert tokens == ["hi"]
        assert seen["streaming"] is True

    @pytest.mark.asyncio
    async def test_no_hooks_registered_leaves_the_call_untouched(self):
        caller = make_llm_caller()

        content = await caller.call_plain("gpt-4o", LLM_MESSAGES)

        assert content == "llm answer"
        assert caller.sent[0]["messages"] == LLM_MESSAGES


# ---------------------------------------------------------------------------
# Startup wiring
# ---------------------------------------------------------------------------


class TestAppFactoryWiring:
    def teardown_method(self):
        import sys

        sys.modules.pop("atlas_factory_hook_plugin", None)

    def test_app_factory_loads_configured_plugins(self, monkeypatch):
        """AppFactory loads hook plugins before wiring any service."""
        import sys

        from atlas.core.hooks import HookPluginLoadError, load_plugins_from_settings

        module = types.ModuleType("atlas_factory_hook_plugin")

        def register(registry):
            raise ValueError("bad plugin")

        module.register = register
        sys.modules["atlas_factory_hook_plugin"] = module

        settings = SimpleNamespace(
            feature_hooks_enabled=True,
            hook_plugins="atlas_factory_hook_plugin",
            hook_timeout_seconds=5.0,
        )

        # Startup is fail-fast: a broken governance plugin must not be skipped.
        with pytest.raises(HookPluginLoadError):
            load_plugins_from_settings(settings, get_hook_registry())
