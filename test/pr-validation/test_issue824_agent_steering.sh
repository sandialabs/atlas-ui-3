#!/bin/bash
# Test script for issue #824: inject a user prompt into a running agent loop
# for steering, without breaking or stopping the loop.
#
# Covers the test plan:
#   1. A user message sent while the agent loop is running is injected as a
#      normal user turn at the next iteration boundary (not mid-step).
#   2. The loop is not stopped by steering -- it continues and finishes.
#   3. The steering message persists in history as a normal USER message that
#      later turns can see (land as a normal user turn, per the issue).
#   4. If the model produces a text-only (would-be final) response while a
#      steer is pending, the loop continues instead of ignoring the steer.
#   5. The steering channel is deactivated when the turn ends, so a later chat
#      never routes into a finished loop.
#
# These are exercised through the real AgenticLoop code path with a
# programmable fake LLM (steering mid-run needs a model that loops long enough
# to steer, which a real provider can't guarantee in CI). The backend unit
# tests run as the final step.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "\033[0;32mPASSED\033[0m: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "\033[0;31mFAILED\033[0m: $2"
        FAILED=$((FAILED + 1))
    fi
}

cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
source .venv/bin/activate 2>/dev/null || true

export PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH"

# --- Drive the real AgenticLoop with a fake LLM and a steering channel ----
python - <<'PYEOF'
import asyncio
import sys
from types import SimpleNamespace
from uuid import uuid4

from atlas.application.chat.agent.agentic_loop import AgenticLoop
from atlas.application.chat.agent.protocols import AgentContext
from atlas.application.chat.agent.steering import SteeringChannel
from atlas.domain.messages.models import ConversationHistory, ToolResult
from atlas.interfaces.llm import LLMResponse


def tool_call(cid, name):
    return SimpleNamespace(id=cid, type="function",
                           function=SimpleNamespace(name=name, arguments="{}"))


class FakeToolManager:
    async def execute_tool(self, tc, context=None):
        return ToolResult(tool_call_id=getattr(tc, "id", "x"), content="ok", success=True)

    def get_tools_schema(self, names):
        return [{"type": "function", "function": {"name": "noop", "parameters": {}}}]


class SteeringLLM:
    """Tool call on step 1 (queueing a steer mid-call), final text on step 2."""
    def __init__(self, channel):
        self.channel = channel
        self.step = 0
        self.seen_user = []

    async def call_with_tools(self, model, messages, tools_schema, tool_choice="auto",
                              temperature=0.7, user_email=None):
        self.step += 1
        self.seen_user.extend(m.get("content") for m in messages if m.get("role") == "user")
        if self.step == 1:
            # User steers while step 1's LLM call is in flight.
            self.channel.queue.put_nowait("now also summarize")
            return LLMResponse(content="running tool", tool_calls=[tool_call("c1", "noop")])
        return LLMResponse(content="done after steering")

    async def call_with_rag_and_tools(self, *a, **kw):
        return await self.call_with_tools(a[0], a[1], a[3], "auto")

    async def call_plain(self, *a, **kw):
        return "fallback"

    async def stream_plain(self, *a, **kw):
        yield "fallback"

    async def stream_with_tools(self, model, messages, tools_schema, tool_choice="auto",
                                temperature=0.7, user_email=None):
        resp = await self.call_with_tools(model, messages, tools_schema, tool_choice,
                                          temperature, user_email)
        if self.step == 1:
            yield resp
        else:
            for w in (resp.content or "").split(" "):
                yield w + " "
            yield resp

    async def stream_with_rag_and_tools(self, *a, **kw):
        async for item in self.stream_with_tools(a[0], a[1], a[3], "auto"):
            yield item


async def main():
    failures = []

    # Test 1+2+3: steering injected between steps, loop continues, persists as USER.
    channel = SteeringChannel()
    llm = SteeringLLM(channel)
    loop = AgenticLoop(llm=llm, tool_manager=FakeToolManager(), prompt_provider=None)
    loop.skip_approval = True
    ctx = AgentContext(session_id=uuid4(), user_email="t@example.com",
                       files={}, history=ConversationHistory())
    result = await loop.run(
        model="m", messages=[{"role": "user", "content": "run"}],
        context=ctx, selected_tools=["noop"], data_sources=None,
        max_steps=5, temperature=0.7,
        event_handler=lambda e: asyncio.sleep(0), steering=channel,
    )
    if result.steps != 2:
        failures.append(f"loop should take 2 steps, took {result.steps}")
    if "now also summarize" not in llm.seen_user:
        failures.append("steering text never reached the LLM")
    if result.final_answer != "done after steering":
        failures.append(f"loop did not finish after steering: {result.final_answer!r}")
    user_msgs = [m for m in ctx.history.messages if m.role.value == "user"]
    if not any(m.content == "now also summarize" for m in user_msgs):
        failures.append("steering message not persisted as a user turn")
    llm_msgs = ctx.history.get_messages_for_llm()
    if not any(m["role"] == "user" and m["content"] == "now also summarize" for m in llm_msgs):
        failures.append("steering user turn not visible to later LLM turns")
    if channel.active:
        failures.append("channel should be inactive after run() returns")

    # Test 4: steering during a text-only (would-be final) response continues.
    channel2 = SteeringChannel()
    llm2 = SteeringLLM(channel2)
    llm2.__class__ = type("TxtLLM", (SteeringLLM,), {})  # distinct instance is fine
    # Override to produce text-only on step 1 while a steer is pending.
    async def call_with_tools_txt(self, model, messages, tools_schema, tool_choice="auto",
                                  temperature=0.7, user_email=None):
        self.step += 1
        self.seen_user.extend(m.get("content") for m in messages if m.get("role") == "user")
        if self.step == 1:
            self.channel.queue.put_nowait("also do X")
            return LLMResponse(content="here is the answer")  # text-only, would-be final
        return LLMResponse(content="final after X")
    llm2.call_with_tools = lambda *a, **kw: call_with_tools_txt(llm2, *a, **kw)
    loop2 = AgenticLoop(llm=llm2, tool_manager=FakeToolManager(), prompt_provider=None)
    loop2.skip_approval = True
    ctx2 = AgentContext(session_id=uuid4(), user_email="t@example.com",
                        files={}, history=ConversationHistory())
    res2 = await loop2.run(
        model="m", messages=[{"role": "user", "content": "summarize"}],
        context=ctx2, selected_tools=["noop"], data_sources=None,
        max_steps=5, temperature=0.7,
        event_handler=lambda e: asyncio.sleep(0), steering=channel2,
    )
    if llm2.step != 2:
        failures.append(f"loop should continue past text-only when steering pending (steps={llm2.step})")
    if res2.final_answer != "final after X":
        failures.append(f"unexpected final answer for continue case: {res2.final_answer!r}")

    if failures:
        for f in failures:
            print("FAIL:", f)
        sys.exit(1)
    print("steering scenario OK")


asyncio.run(main())
PYEOF
print_result $? "AgenticLoop steering injection end-to-end (fake LLM)"

# Final: run backend unit tests for the affected areas.
python -m pytest atlas/tests/test_agentic_loop.py atlas/tests/test_agent_mode_integration.py -q > /dev/null 2>&1
print_result $? "Backend unit tests (agentic loop + agent mode integration)"

# Summary
echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ "$FAILED" -eq 0 ] && exit 0 || exit 1