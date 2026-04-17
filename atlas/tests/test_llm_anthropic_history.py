"""Regression test: Anthropic requests must tolerate message history with tool_calls.

Scenario: a conversation previously invoked tools (assistant message with
tool_calls + tool-role result message), then the app calls plain LLM (no
tools) — e.g. for title generation or a follow-up reply after the user
switches models. Without `litellm.modify_params = True`, Anthropic's
transformer raises `UnsupportedParamsError` because it refuses a request
whose history references tool use but whose current call has no `tools=`.

This test guards the module-level `litellm.modify_params` setting by
confirming it is enabled at import time.
"""

import litellm

from atlas.modules.llm import litellm_caller as caller_module  # noqa: F401


def test_modify_params_enabled_for_anthropic_history_compat():
    """modify_params must be True so litellm injects a dummy tool when
    needed, instead of raising UnsupportedParamsError on Anthropic calls
    whose message history contains tool_call blocks."""
    assert litellm.modify_params is True


def test_drop_params_still_enabled():
    """Ensure the existing drop_params setting was not regressed."""
    assert litellm.drop_params is True
