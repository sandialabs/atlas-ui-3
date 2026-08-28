"""Tests for where the RAG context message is inserted into the message list.

The RAG context belongs immediately before the user turn it was retrieved for.
The old ``insert(-1, ...)`` encoded that as "before the last message", which is
only the same thing when the conversation ends on that user message.

Since search became an explicit tool call, the only remaining injector is
``call_with_rag``/``stream_with_rag`` (RAG mode, no tools). The index helper is
still what keeps the injected system message off the end of a message list that
does not end on the user turn, so it keeps its own tests.
"""

from atlas.modules.llm.litellm_caller import LiteLLMCaller


def _continuation_messages():
    """A message list that does not end on the user turn."""
    return [
        {"role": "user", "content": "find the policy and summarize it"},
        {
            "role": "assistant",
            "content": "looking it up",
            "tool_calls": [{
                "id": "call_slT5qS0Q05qPdumdHfhDZU4D",
                "type": "function",
                "function": {"name": "atlas_search", "arguments": '{"query":"policy"}'},
            }],
        },
        {"role": "tool", "content": "policy text", "tool_call_id": "call_slT5qS0Q05qPdumdHfhDZU4D"},
    ]


def test_insert_index_targets_last_user_message():
    messages = _continuation_messages()
    assert LiteLLMCaller._rag_insert_index(messages) == 0


def test_insert_index_matches_old_behavior_on_a_plain_turn():
    """Round 0 ends on the user message: unchanged from the previous insert(-1)."""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    assert LiteLLMCaller._rag_insert_index(messages) == len(messages) - 1


def test_insert_index_appends_when_there_is_no_user_message():
    messages = [{"role": "system", "content": "sys"}]
    assert LiteLLMCaller._rag_insert_index(messages) == 1
