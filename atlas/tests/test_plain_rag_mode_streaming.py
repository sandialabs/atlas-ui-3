"""Keep plain/RAG streaming, persistence, and non-streaming fallbacks live."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from atlas.application.chat.modes.plain import PlainModeRunner
from atlas.application.chat.modes.rag import RagModeRunner
from atlas.domain.errors import AuthorizationError
from atlas.domain.messages.models import MessageRole
from atlas.domain.sessions.models import Session


@pytest.fixture(params=["plain", "rag"])
def mode(request):
    return request.param


def _harness(mode, tokens=(), stream_error=None, fallback_error=None):
    async def stream(*args, **kwargs):
        for token in tokens:
            yield token
        if stream_error:
            raise stream_error

    streaming = Mock(side_effect=stream)
    fallback = AsyncMock(return_value="Fallback answer", side_effect=fallback_error)
    llm = SimpleNamespace(
        stream_plain=streaming, stream_with_rag=streaming,
        call_plain=fallback, call_with_rag=fallback,
    )
    publisher = AsyncMock()
    runner = (PlainModeRunner if mode == "plain" else RagModeRunner)(llm, publisher)
    session = Session()
    kwargs = dict(
        session=session, model="test-model", messages=[{"role": "user", "content": "hello"}],
        user_email="reader@example.gov", temperature=0.2,
    )
    args = ("test-model", kwargs["messages"])
    call_kwargs = {"temperature": 0.2}
    if mode == "rag":
        kwargs["data_sources"] = ["policies"]
        args += (["policies"], "reader@example.gov")
    else:
        call_kwargs["user_email"] = "reader@example.gov"
    return runner, kwargs, streaming, fallback, args, call_kwargs


def _assert_saved(runner, kwargs, mode, content):
    messages = kwargs["session"].history.messages
    assert len(messages) == 1
    assert messages[0].role == MessageRole.ASSISTANT
    assert messages[0].content == content
    if mode == "rag":
        assert messages[0].metadata["data_sources"] == ["policies"]
    runner.event_publisher.publish_response_complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_tokens_are_published_in_order_and_persisted(mode):
    runner, kwargs, streaming, fallback, args, call_kwargs = _harness(mode, ["Hello", " world"])
    response = await runner.run_streaming(**kwargs)
    assert response["message"] == "Hello world"
    streaming.assert_called_once_with(*args, **call_kwargs)
    fallback.assert_not_awaited()
    assert runner.event_publisher.publish_token_stream.await_args_list == [
        call(token="Hello", is_first=True, is_last=False),
        call(token=" world", is_first=False, is_last=False),
        call(token="", is_first=False, is_last=True),
    ]
    runner.event_publisher.publish_chat_response.assert_not_awaited()
    _assert_saved(runner, kwargs, mode, "Hello world")


@pytest.mark.asyncio
@pytest.mark.parametrize("stream_error", [None, RuntimeError("stream unavailable")])
@pytest.mark.parametrize("raise_on_stream_error", [False, True])
async def test_empty_or_failed_stream_uses_live_fallback(mode, stream_error, raise_on_stream_error):
    runner, kwargs, streaming, fallback, args, call_kwargs = _harness(mode, stream_error=stream_error)
    runner.raise_on_stream_error = raise_on_stream_error
    response = await runner.run_streaming(**kwargs)
    assert response["message"] == "Fallback answer"
    streaming.assert_called_once_with(*args, **call_kwargs)
    fallback.assert_awaited_once_with(*args, **call_kwargs)
    runner.event_publisher.publish_chat_response.assert_awaited_once_with(
        message="Fallback answer", has_pending_tools=False,
    )
    _assert_saved(runner, kwargs, mode, "Fallback answer")


@pytest.mark.asyncio
async def test_partial_failure_keeps_streamed_text_without_repeating_call(mode):
    runner, kwargs, _, fallback, _, _ = _harness(
        mode, tokens=["Partial answer"], stream_error=RuntimeError("disconnected"),
    )
    response = await runner.run_streaming(**kwargs)
    assert response["message"] == "Partial answer"
    fallback.assert_not_awaited()
    _assert_saved(runner, kwargs, mode, "Partial answer")


@pytest.mark.asyncio
async def test_double_failure_returns_safe_classified_error(mode):
    runner, kwargs, _, fallback, _, _ = _harness(
        mode, stream_error=RuntimeError("Invalid API key: internal-detail"),
        fallback_error=RuntimeError("fallback internal-detail"),
    )
    response = await runner.run_streaming(**kwargs)
    assert "authentication issue" in response["message"]
    assert "internal-detail" not in response["message"]
    fallback.assert_awaited_once()
    _assert_saved(runner, kwargs, mode, response["message"])


@pytest.mark.asyncio
async def test_cli_double_failure_preserves_original_domain_error(mode):
    error = AuthorizationError("Access denied")
    runner, kwargs, _, fallback, _, _ = _harness(
        mode, stream_error=error, fallback_error=RuntimeError("fallback failed"),
    )
    runner.raise_on_stream_error = True
    with pytest.raises(AuthorizationError) as raised:
        await runner.run_streaming(**kwargs)
    assert raised.value is error
    fallback.assert_awaited_once()
    assert not kwargs["session"].history.messages
    runner.event_publisher.publish_response_complete.assert_not_awaited()
