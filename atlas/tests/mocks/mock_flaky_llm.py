"""Mock LLM that simulates transient failures for retry testing.

Usage:
    flaky = FlakyLLMResponse(fail_count=2, exception=litellm.RateLimitError("rate limit"))
    # First 2 calls raise the exception, 3rd call returns a successful response.

    mock = AsyncMock(side_effect=flaky.side_effect_list())
"""

from types import SimpleNamespace
from typing import Any, List, Optional


def make_llm_response(content: str = "Success"):
    """Build a minimal response object matching litellm's acompletion structure."""
    message = SimpleNamespace(
        content=content,
        tool_calls=None,
    )
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


class FlakyLLMResponse:
    """Generates side_effect lists for AsyncMock that fail N times then succeed.

    Args:
        fail_count: Number of times to raise the exception before succeeding.
        exception: The exception instance to raise on failure.
        success_content: The content string to return on success.
    """

    def __init__(
        self,
        fail_count: int = 2,
        exception: Optional[Exception] = None,
        success_content: str = "Success after retry",
    ):
        self.fail_count = fail_count
        self.exception = exception or Exception("transient error")
        self.success_content = success_content

    def side_effect_list(self) -> List[Any]:
        """Build a list suitable for AsyncMock(side_effect=[...])."""
        effects: List[Any] = [self.exception] * self.fail_count
        effects.append(make_llm_response(self.success_content))
        return effects


class AlwaysFailLLMResponse:
    """Generates side_effect lists for AsyncMock that always fail.

    Args:
        exception: The exception instance to raise on every call.
        count: How many calls to prepare for (should be >= MAX_LLM_RETRIES + 1).
    """

    def __init__(self, exception: Optional[Exception] = None, count: int = 10):
        self.exception = exception or Exception("permanent error")
        self.count = count

    def side_effect_list(self) -> List[Any]:
        """Build a list suitable for AsyncMock(side_effect=[...])."""
        return [self.exception] * self.count
