"""End-to-end harness for PR #920 (issue #919): retry for LLM calls.

Drives the REAL LiteLLMCaller retry paths -- real env reads, real
asyncio.sleep, real streaming generators -- with only the litellm boundary
mocked, the way a provider outage behaves from Atlas's side:

- LLM_MAX_RETRIES=2 makes a rate-limited call attempt 3 times, with
  increasing (exponential) backoff delays, and returns the provider answer.
- With the variables unset the default retry count is 5 (6 calls).
- LLM_RETRY_MAX_WAIT_SECONDS clamps the cumulative sleep and stops retries
  early once the budget is spent.
- An auth error never retries.
- A streaming rate limit at establishment is retried and the tokens then
  flow exactly once; a mid-stream failure after a token is surfaced without
  retrying.
"""

import asyncio
import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import litellm

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from atlas.modules.llm import litellm_caller as caller_module  # noqa: E402
from atlas.modules.llm import litellm_streaming as streaming_module  # noqa: E402
from atlas.modules.llm.litellm_caller import LiteLLMCaller  # noqa: E402
from atlas.modules.llm.models import LLMResponse  # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"  ok: {name}")
    else:
        print(f"  FAIL: {name} {detail}")
        FAILURES.append(name)


def rate_limit():
    return litellm.RateLimitError(message="429", llm_provider="t", model="t")


def response(content):
    """Build a minimal non-streaming completion response."""
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeStream:
    """Async-iterable stand-in for litellm's streaming response."""

    def __init__(self, contents, error=None):
        self._contents = list(contents)
        self._error = error
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._contents:
            delta = SimpleNamespace(content=self._contents.pop(0))
            return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])
        if self._error is not None:
            raise self._error
        raise StopAsyncIteration

    async def aclose(self):
        self.closed = True


def caller():
    return LiteLLMCaller(llm_config=SimpleNamespace(models={}))


async def case_nonstream_env_count():
    """LLM_MAX_RETRIES=2 -> 3 total attempts, exponential delays, success."""
    os.environ["LLM_MAX_RETRIES"] = "2"
    os.environ["LLM_RETRY_MAX_WAIT_SECONDS"] = "60"
    c = caller()
    flaky = AsyncMock(side_effect=[
        rate_limit(), rate_limit(), response("recovered"),
    ])
    with (
        patch.object(caller_module, "acompletion", flaky),
        patch.object(c, "_get_litellm_model_name", return_value="m"),
        patch.object(c, "_get_model_kwargs", return_value={}),
    ):
        out = await c.call_plain("m", [{"role": "user", "content": "hi"}])
    check("non-streaming retries and succeeds", out == "recovered")
    check("env retry count honored (3 attempts)", flaky.call_count == 3, flaky.call_count)


async def case_nonstream_default_count():
    """With no env override, the default retry count is 5 (6 attempts).

    Sleep is mocked here: the budget cap is exercised with real sleeps in
    case_wait_budget_cap, and this case only needs the attempt count.
    """
    os.environ.pop("LLM_MAX_RETRIES", None)
    os.environ["LLM_RETRY_MAX_WAIT_SECONDS"] = "600"
    c = caller()
    flaky = AsyncMock(side_effect=[rate_limit() for _ in range(10)])
    with (
        patch.object(caller_module, "acompletion", flaky),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        try:
            await c._acompletion_with_retry(model="t", messages=[])
        except litellm.RateLimitError:
            pass
    check("default retry count is 5 (6 attempts)", flaky.call_count == 6, flaky.call_count)


async def case_wait_budget_cap():
    """A 1.5s budget stops retries early and caps total sleep."""
    os.environ["LLM_MAX_RETRIES"] = "5"
    os.environ["LLM_RETRY_MAX_WAIT_SECONDS"] = "1.5"
    c = caller()
    flaky = AsyncMock(side_effect=[rate_limit() for _ in range(10)])
    with patch.object(caller_module, "acompletion", flaky):
        t0 = time.monotonic()
        try:
            await c._acompletion_with_retry(model="t", messages=[])
        except litellm.RateLimitError:
            pass
    elapsed = time.monotonic() - t0
    check("wait budget caps elapsed backoff", elapsed <= 2.2, f"{elapsed:.2f}s")
    check("wait budget stops retries early", flaky.call_count < 6, flaky.call_count)


async def case_auth_no_retry():
    """Auth errors raise immediately."""
    os.environ["LLM_MAX_RETRIES"] = "3"
    c = caller()
    flaky = AsyncMock(side_effect=litellm.AuthenticationError(
        message="bad key", llm_provider="t", model="t"))
    with patch.object(caller_module, "acompletion", flaky):
        try:
            await c._acompletion_with_retry(model="t", messages=[])
        except litellm.AuthenticationError:
            pass
    check("auth error not retried", flaky.call_count == 1, flaky.call_count)


async def case_stream_retry_before_token():
    """Streaming retries a rate limit at establishment, tokens flow once."""
    os.environ["LLM_MAX_RETRIES"] = "2"
    os.environ["LLM_RETRY_MAX_WAIT_SECONDS"] = "60"
    c = caller()
    stream = _FakeStream(["Hello", " ", "world"])
    flaky = AsyncMock(side_effect=[rate_limit(), rate_limit(), stream])
    with (
        patch.object(streaming_module, "acompletion", flaky),
        patch.object(c, "_get_litellm_model_name", return_value="m"),
        patch.object(c, "_get_model_kwargs", return_value={}),
    ):
        tokens = [t async for t in c.stream_plain("m", [{"role": "user", "content": "hi"}])]
    check("streaming retries establishment failures", tokens == ["Hello", " ", "world"], tokens)
    check("streaming retry count", flaky.call_count == 3, flaky.call_count)


async def case_stream_no_retry_after_token():
    """A mid-stream failure after the first token is surfaced, not retried."""
    c = caller()
    stream = _FakeStream(["partial"], error=rate_limit())
    flaky = AsyncMock(return_value=stream)
    with (
        patch.object(streaming_module, "acompletion", flaky),
        patch.object(c, "_get_litellm_model_name", return_value="m"),
        patch.object(c, "_get_model_kwargs", return_value={}),
    ):
        got = []
        try:
            async for tok in c.stream_plain("m", [{"role": "user", "content": "hi"}]):
                got.append(tok)
        except Exception as exc:
            check("mid-stream failure surfaces domain error", type(exc).__name__ == "RateLimitError", repr(exc))
    check("partial token preserved", got == ["partial"], got)
    check("no retry after first token", flaky.call_count == 1, flaky.call_count)


async def case_stream_tools():
    """stream_with_tools retries before any yield and yields the final LLMResponse."""
    os.environ["LLM_MAX_RETRIES"] = "2"
    os.environ["LLM_RETRY_MAX_WAIT_SECONDS"] = "60"
    c = caller()
    stream = _FakeStream(["tok"])
    flaky = AsyncMock(side_effect=[rate_limit(), stream])
    tools = [{"type": "function", "function": {"name": "test_tool"}}]
    with (
        patch.object(streaming_module, "acompletion", flaky),
        patch.object(c, "_get_litellm_model_name", return_value="m"),
        patch.object(c, "_get_model_kwargs", return_value={}),
    ):
        items = [i async for i in c.stream_with_tools("m", [{"role": "user", "content": "hi"}], tools)]
    check("tools stream yields text then LLMResponse", items[0] == "tok" and isinstance(items[-1], LLMResponse))
    check("tools stream retried once", flaky.call_count == 2, flaky.call_count)


async def main():
    await case_nonstream_env_count()
    await case_nonstream_default_count()
    await case_wait_budget_cap()
    await case_auth_no_retry()
    await case_stream_retry_before_token()
    await case_stream_no_retry_after_token()
    await case_stream_tools()
    print()
    if FAILURES:
        print(f"HARNESS FAILED ({len(FAILURES)}): {FAILURES}")
        return 1
    print("HARNESS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))