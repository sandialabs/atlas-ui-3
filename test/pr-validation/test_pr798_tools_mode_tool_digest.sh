#!/bin/bash
# Test script for PR #798: cross-turn tool digest reaches the default tools-mode path
#
# The digest added in #755 was attached only in agent mode, so a normal turn --
# which runs in tools mode by default -- never carried it and tool work was
# invisible to every later turn. This script exercises the production code path
# (real ToolsModeRunner.run_streaming, real close_open_turn, real
# ConversationHistory.get_messages_for_llm) with a stub LLM and verifies:
#
#   1. A completed tools-mode turn's closing assistant message carries the digest.
#   2. The next turn's LLM context contains the tool trajectory (the re-derivation
#      the issue describes is fixed).
#   3. A stopped tools-mode turn's terminal assistant message (from close_open_turn)
#      carries a digest from the flushed tool_call rows -- the "interrupted path"
#      the issue calls out as ideal to cover.
#   4. Backend regression suite still passes.
#
# Test plan items from the PR description.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

PASSED=0
FAILED=0

print_header() {
    echo ""
    echo "=========================================="
    echo "$1"
    echo "=========================================="
}

print_result() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

cd "$PROJECT_ROOT"

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

# ==========================================================================
# Check 1 + 2 + 3: end-to-end through the real production code paths
# ==========================================================================
print_header "Check 1-3: tools-mode digest on completion + interrupted paths"

python - <<'PY'
import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from atlas.application.chat.modes.tools import ToolsModeRunner
from atlas.application.chat.utilities.interrupted_turn import (
    INTERRUPTED_TURN_CONTENT,
    close_open_turn,
)
from atlas.domain.messages.models import (
    AGENT_TOOL_DIGEST_KEY,
    ConversationHistory,
    Message,
    MessageRole,
)
from atlas.domain.sessions.models import Session
from atlas.interfaces.llm import LLMResponse


def _tc(call_id, name, arguments="{}"):
    return SimpleNamespace(
        id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class _ScriptedToolsLLM:
    def __init__(self, turns, synthesis="Final summary."):
        self._turns = list(turns)
        self.synthesis = synthesis

    async def stream_with_tools(self, model, messages, tools_schema,
                                tool_choice="auto", temperature=0.7, user_email=None):
        text, tool_calls = self._turns.pop(0) if self._turns else (None, None)
        if text:
            yield text
        yield LLMResponse(content=text or "", tool_calls=tool_calls)

    async def stream_with_rag_and_tools(self, *a, **k):
        async for item in self.stream_with_tools(*a, **k):
            yield item

    async def stream_plain(self, model, messages, temperature=0.7, user_email=None):
        yield self.synthesis

    async def call_plain(self, model, messages, temperature=0.7, user_email=None):
        return self.synthesis


async def _fake_execute_multiple_tools(tool_calls, session_context, tool_manager,
                                       update_callback=None, config_manager=None,
                                       skip_approval=False):
    from atlas.domain.messages.models import ToolResult
    results = []
    for tc in tool_calls:
        if update_callback is not None:
            await update_callback({
                "type": "tool_start", "tool_call_id": tc.id,
                "tool_name": tc.function.name, "server_name": "calc",
                "arguments": {"a": 1, "b": 2},
            })
            await update_callback({
                "type": "tool_complete", "tool_call_id": tc.id,
                "tool_name": tc.function.name, "success": True, "result": "3",
            })
        results.append(ToolResult(tool_call_id=tc.id, content="3", success=True))
    return results


def _publisher():
    pub = AsyncMock()
    pub.publish_token_stream = AsyncMock()
    pub.publish_chat_response = AsyncMock()
    pub.publish_response_complete = AsyncMock()
    pub.send_json = AsyncMock()
    return pub


def _runner(llm):
    tool_manager = MagicMock()
    tool_manager.get_tools_schema = MagicMock(return_value=[{"type": "function"}])
    return ToolsModeRunner(
        llm=llm, tool_manager=tool_manager, event_publisher=_publisher(),
        config_manager=SimpleNamespace(
            app_settings=SimpleNamespace(
                tools_mode_max_extra_rounds=3,
                feature_agent_mode_available=False,
            ),
        ),
    )


async def _run_streaming(runner, session, messages, selected_tools):
    with patch("atlas.application.chat.modes.tools.tool_executor") as mock_te:
        mock_te.execute_multiple_tools = _fake_execute_multiple_tools
        mock_te.build_files_manifest = MagicMock(return_value=None)
        return await runner.run_streaming(
            session=session, model="test-model", messages=messages,
            selected_tools=selected_tools,
        )


async def main():
    # --- Check 1: completed tools-mode turn carries a digest -----------------
    session = Session()
    session.history.add_message(Message(role=MessageRole.USER, content="add 1 and 2"))
    llm = _ScriptedToolsLLM(turns=[
        ("computing", [_tc("c1", "calc_add", '{"a":1,"b":2}')]),
        ("Done! The answer is 3.", None),
    ])
    runner = _runner(llm)
    await _run_streaming(runner, session,
                        messages=[{"role": "user", "content": "add 1 and 2"}],
                        selected_tools=["calc_add"])

    final = session.history.messages[-1]
    assert final.role == MessageRole.ASSISTANT, "final message must be assistant"
    assert final.content == "Done! The answer is 3."
    digest = final.metadata.get(AGENT_TOOL_DIGEST_KEY)
    assert digest and "calc_add" in digest, (
        "completed tools-mode turn must carry a digest (issue #798)"
    )

    # --- Check 2: next turn's LLM context contains the tool trajectory --------
    llm_messages = session.history.get_messages_for_llm()
    assert [m["role"] for m in llm_messages] == ["user", "assistant"], (
        "role alternation must be unchanged (display-only tool_call row excluded)"
    )
    assert "calc_add" in llm_messages[-1]["content"], (
        "the follow-up turn must see what the prior turn's tools did -- the "
        "re-derivation the issue reports"
    )
    print("OK: completed turn carries a digest; next turn sees the tool trajectory")

    # --- Check 3: interrupted tools-mode turn carries a digest ---------------
    # close_open_turn is what ChatService calls on the cancel path for plain /
    # RAG / tools mode (agent mode closes its own turn). After ToolsModeRunner's
    # recorder unwinds, history ends on a display-only tool_call row; the digest
    # must be built from those rows and attached to the terminal message.
    history = ConversationHistory()
    history.add_message(Message(role=MessageRole.USER, content="add 1 and 2"))
    history.add_message(Message(
        role=MessageRole.TOOL, content="Tool call: calc_add",
        metadata={
            "message_type": "tool_call", "tool_call_id": "tc1",
            "tool_name": "calc_add", "server_name": "calc",
            "arguments": {"a": 1, "b": 2}, "result": "3", "status": "completed",
        },
    ))
    assert close_open_turn(history) is True
    terminal = history.messages[-1]
    assert terminal.role == MessageRole.ASSISTANT
    assert terminal.metadata.get("interrupted") is True
    digest = terminal.metadata.get(AGENT_TOOL_DIGEST_KEY)
    assert digest and "calc_add" in digest, (
        "interrupted tools-mode turn must carry a digest of the flushed tool_call rows"
    )
    assert terminal.content == INTERRUPTED_TURN_CONTENT, (
        "terminal content stays the base text; the digest's own header labels "
        "the record that is folded in"
    )
    # And the next turn's context sees the trajectory.
    live = history.get_messages_for_llm()
    assert [m["role"] for m in live] == ["user", "assistant"]
    assert "calc_add" in live[-1]["content"]
    print("OK: interrupted turn carries a digest; next turn sees the trajectory")

    # --- Check 3b: no tool calls -> no digest, base content ------------------
    history2 = ConversationHistory()
    history2.add_message(Message(role=MessageRole.USER, content="write a poem"))
    assert close_open_turn(history2) is True
    terminal2 = history2.messages[-1]
    assert terminal2.metadata.get(AGENT_TOOL_DIGEST_KEY) is None
    assert terminal2.content == INTERRUPTED_TURN_CONTENT
    print("OK: no tool calls -> no digest, base interrupted content")


try:
    asyncio.run(main())
except AssertionError as exc:
    print(f"FAIL: {exc}")
    sys.exit(1)
except Exception as exc:
    print(f"ERROR: {exc}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
PY
print_result $? "tools-mode digest on completion and interrupted paths"

# ==========================================================================
# Check 4: regression unit suite
# ==========================================================================
print_header "Check 4: regression unit tests"

(cd "$PROJECT_ROOT/atlas" && python -m pytest tests/test_interrupted_turn_persistence.py tests/test_tool_call_persistence.py tests/test_tools_mode_iteration.py -q)
print_result $? "focused unit tests (interrupted-turn, tool-call persistence, tools iteration)"

./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests"

echo ""
echo "=========================================="
echo -e "Passed: ${GREEN}$PASSED${NC} | Failed: ${RED}$FAILED${NC}"
echo "=========================================="
[ $FAILED -eq 0 ] && exit 0 || exit 1