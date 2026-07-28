"""Provider rejections caused by a tool definition must name the tool.

A single malformed tool definition makes the provider reject the whole request,
so no tool ever runs and the turn fails. The provider says which function it
objected to; that detail used to be replaced by a generic "the LLM service
encountered an error" message, leaving users to find the tool by deselecting
tools one at a time.
"""

from types import SimpleNamespace
from typing import List
from unittest.mock import AsyncMock, MagicMock

import litellm
import pytest

from atlas.application.chat.modes.tools import ToolsModeRunner
from atlas.application.chat.utilities.error_handler import (
    classify_llm_error,
    error_type_for,
)
from atlas.domain.errors import (
    ContextWindowExceededError,
    LLMBadRequestError,
    LLMServiceError,
    RateLimitError,
)
from atlas.modules.llm.litellm_caller import LiteLLMCaller

TOOLS_SCHEMA = [
    {"type": "function", "function": {"name": "safety_docs_plan", "parameters": {"type": "string"}}},
    {"type": "function", "function": {"name": "calculator_calculate", "parameters": {"type": "object"}}},
]


def _bad_request(message: str) -> litellm.BadRequestError:
    return litellm.BadRequestError(message=message, model="test-model", llm_provider="openai")


class TestBadRequestNamesTheTool:
    """_raise_llm_domain_error must preserve which tool the provider rejected."""

    def test_names_the_tool_the_provider_rejected(self):
        exc = _bad_request(
            "OpenAIException - Invalid schema for function 'safety_docs_plan': "
            "schema must be a JSON Schema of 'type: \"object\"', got 'string'."
        )

        with pytest.raises(LLMBadRequestError) as info:
            LiteLLMCaller._raise_llm_domain_error(exc, tools_schema=TOOLS_SCHEMA)

        assert info.value.tool_names == ["safety_docs_plan"]
        assert "safety_docs_plan" in info.value.message

    def test_does_not_name_tools_the_provider_did_not_reject(self):
        exc = _bad_request(
            "OpenAIException - Invalid schema for function 'safety_docs_plan': bad type."
        )

        with pytest.raises(LLMBadRequestError) as info:
            LiteLLMCaller._raise_llm_domain_error(exc, tools_schema=TOOLS_SCHEMA)

        assert "calculator_calculate" not in info.value.message

    def test_lists_request_tools_when_provider_names_none(self):
        exc = _bad_request("OpenAIException - Invalid 'tools': too many functions supplied.")

        with pytest.raises(LLMBadRequestError) as info:
            LiteLLMCaller._raise_llm_domain_error(exc, tools_schema=TOOLS_SCHEMA)

        assert info.value.tool_names == ["safety_docs_plan", "calculator_calculate"]
        assert "safety_docs_plan" in info.value.message
        assert "calculator_calculate" in info.value.message

    def test_non_tool_rejection_does_not_blame_the_tool_selection(self):
        """Most 400s are unrelated to tools; tools being selected is not evidence."""
        exc = _bad_request(
            "OpenAIException - Invalid value for 'temperature': must be <= 2."
        )

        with pytest.raises(LLMBadRequestError) as info:
            LiteLLMCaller._raise_llm_domain_error(exc, tools_schema=TOOLS_SCHEMA)

        assert info.value.tool_names == []
        assert "safety_docs_plan" not in info.value.message
        assert "calculator_calculate" not in info.value.message

    def test_malformed_message_rejection_does_not_blame_tools(self):
        exc = _bad_request(
            "OpenAIException - Invalid value for 'messages[1].role': must be one of "
            "'system', 'user', 'assistant'."
        )

        with pytest.raises(LLMBadRequestError) as info:
            LiteLLMCaller._raise_llm_domain_error(exc, tools_schema=TOOLS_SCHEMA)

        assert info.value.tool_names == []

    def test_tool_name_is_matched_as_a_whole_token(self):
        """A prefix of a longer word in the provider text is not a tool match."""
        schema = [{"type": "function", "function": {"name": "calc"}}]
        exc = _bad_request(
            "OpenAIException - Invalid value for 'messages[0]': could not calculate length."
        )

        with pytest.raises(LLMBadRequestError) as info:
            LiteLLMCaller._raise_llm_domain_error(exc, tools_schema=schema)

        assert info.value.tool_names == []

    def test_bad_request_without_tools_claims_no_tool(self):
        exc = _bad_request("OpenAIException - Invalid value for 'temperature': must be <= 2.")

        with pytest.raises(LLMBadRequestError) as info:
            LiteLLMCaller._raise_llm_domain_error(exc, tools_schema=None)

        assert info.value.tool_names == []
        assert "tool" not in info.value.message.lower()

    def test_context_window_error_is_not_captured_as_bad_request(self):
        """litellm.ContextWindowExceededError subclasses BadRequestError."""
        exc = litellm.ContextWindowExceededError(
            message="This model's maximum context length is 8192 tokens.",
            model="test-model",
            llm_provider="openai",
        )

        with pytest.raises(ContextWindowExceededError):
            LiteLLMCaller._raise_llm_domain_error(exc, tools_schema=TOOLS_SCHEMA)

    def test_user_message_omits_raw_provider_text(self):
        """The tool name is actionable; the raw provider string is not exposed."""
        exc = _bad_request(
            "OpenAIException - Invalid schema for function 'safety_docs_plan': "
            "schema must be a JSON Schema of 'type: \"object\"', got 'string'."
        )

        with pytest.raises(LLMBadRequestError) as info:
            LiteLLMCaller._raise_llm_domain_error(exc, tools_schema=TOOLS_SCHEMA)

        assert "OpenAIException" not in info.value.message


class TestClassifyPreservesToolDetail:
    """classify_llm_error must not re-generalize an already-specific error."""

    def test_passes_through_bad_request_message(self):
        err = LLMBadRequestError(
            "The request was rejected because of the tool 'safety_docs_plan'.",
            tool_names=["safety_docs_plan"],
        )

        error_class, user_msg, log_msg = classify_llm_error(err)

        assert error_class is LLMBadRequestError
        assert "safety_docs_plan" in user_msg

    def test_tool_name_containing_a_keyword_is_not_misclassified(self):
        """A tool named like another error category must not be rerouted."""
        err = LLMBadRequestError(
            "The request was rejected because of the tool 'vault_api_key_lookup'.",
            tool_names=["vault_api_key_lookup"],
        )

        error_class, user_msg, _ = classify_llm_error(err)

        assert error_class is LLMBadRequestError
        assert "vault_api_key_lookup" in user_msg

    def test_generic_errors_still_classify_as_before(self):
        error_class, user_msg, _ = classify_llm_error(Exception("something else broke"))

        assert error_class is LLMServiceError
        assert "LLM service" in user_msg


class TestErrorTypeMapping:
    """Error frames carry an error_type so clients can branch on the category."""

    def test_maps_known_error_classes(self):
        assert error_type_for(LLMBadRequestError) == "bad_request"
        assert error_type_for(RateLimitError) == "rate_limit"
        assert error_type_for(ContextWindowExceededError) == "context_window_exceeded"

    def test_falls_back_for_unknown_classes(self):
        assert error_type_for(ValueError) == "unexpected"


def _publisher():
    pub = AsyncMock()
    pub.publish_token_stream = AsyncMock()
    pub.publish_chat_response = AsyncMock()
    pub.publish_response_complete = AsyncMock()
    pub.send_json = AsyncMock()
    return pub


class FailingToolsLLM:
    """stream_with_tools rejects the request the way a provider would."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def stream_with_tools(self, model, messages, tools_schema, tool_choice="auto",
                                temperature=0.7, user_email=None):
        raise self._exc
        yield  # makes this an async generator

    async def stream_with_rag_and_tools(self, model, messages, data_sources, tools_schema,
                                        user_email, tool_choice="auto", temperature=0.7):
        raise self._exc
        yield  # makes this an async generator


def _runner(llm, publisher):
    tool_manager = MagicMock()
    tool_manager.get_tools_schema = MagicMock(return_value=TOOLS_SCHEMA)
    return ToolsModeRunner(
        llm=llm,
        tool_manager=tool_manager,
        event_publisher=publisher,
        config_manager=SimpleNamespace(
            app_settings=SimpleNamespace(
                tools_mode_max_extra_rounds=3,
                feature_agent_mode_available=False,
            )
        ),
    )


def _session():
    session = MagicMock()
    session.history = MagicMock()
    session.history.add_message = MagicMock()
    session.session_id = "s1"
    session.files = {}
    return session


def _error_frames(publisher) -> List[dict]:
    return [
        call.args[0]
        for call in publisher.send_json.await_args_list
        if call.args and call.args[0].get("type") == "error"
    ]


@pytest.mark.asyncio
async def test_error_frame_names_the_rejected_tool():
    """The user-visible frame identifies the tool, not just 'the LLM service'."""
    pub = _publisher()
    runner = _runner(
        FailingToolsLLM(
            LLMBadRequestError(
                "The request was rejected because of the tool 'safety_docs_plan'.",
                tool_names=["safety_docs_plan"],
            )
        ),
        pub,
    )

    await runner.run_streaming(
        session=_session(),
        model="test-model",
        messages=[{"role": "user", "content": "plan a safety doc"}],
        selected_tools=["safety_docs_plan", "calculator_calculate"],
    )

    frames = _error_frames(pub)
    assert frames, "expected an error frame"
    assert "safety_docs_plan" in frames[0]["message"]


@pytest.mark.asyncio
async def test_error_frame_includes_error_type():
    """Every error frame carries error_type, matching the other WebSocket errors."""
    pub = _publisher()
    runner = _runner(
        FailingToolsLLM(
            LLMBadRequestError(
                "The request was rejected because of the tool 'safety_docs_plan'.",
                tool_names=["safety_docs_plan"],
            )
        ),
        pub,
    )

    await runner.run_streaming(
        session=_session(),
        model="test-model",
        messages=[{"role": "user", "content": "plan a safety doc"}],
        selected_tools=["safety_docs_plan"],
    )

    frames = _error_frames(pub)
    assert frames, "expected an error frame"
    assert frames[0].get("error_type") == "bad_request"


@pytest.mark.asyncio
async def test_rag_and_tools_does_not_retry_a_rejected_request():
    """A provider rejection is not a RAG failure, so it must not trigger the fallback.

    ``call_with_rag_and_tools`` falls back to a tools-only call when RAG breaks.
    If ``LLMBadRequestError`` is not in the passthrough tuple, the rejection is
    misread as a RAG failure: the request is sent a second time only to be
    rejected again, and the logs blame RAG for a tool-definition problem.
    """
    caller = LiteLLMCaller.__new__(LiteLLMCaller)
    caller._rag_service = MagicMock()

    rejection = LLMBadRequestError(
        "The model provider rejected this request because of the tool 'safety_docs_plan'.",
        tool_names=["safety_docs_plan"],
    )
    caller._query_all_rag_sources = AsyncMock(
        return_value=[("docs", SimpleNamespace(
            content="context",
            metadata=None,
            is_completion=False,
        ))]
    )
    caller.call_with_tools = AsyncMock(side_effect=rejection)

    with pytest.raises(LLMBadRequestError) as info:
        await caller.call_with_rag_and_tools(
            model_name="test-model",
            messages=[{"role": "user", "content": "hi"}],
            data_sources=["docs"],
            tools_schema=TOOLS_SCHEMA,
            user_email="user@example.com",
        )

    assert info.value is rejection
    assert caller.call_with_tools.await_count == 1, "must not retry after a rejection"
