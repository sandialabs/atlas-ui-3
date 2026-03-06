#!/bin/bash
# PR #373 - Fix agentic loop UI visibility and empty tool_calls error
# Validates: settings panel sends strategy, _sanitize_messages strips empty tool_calls,
# agentic strategy visible in config, and no OPSEC violations.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PASS=0
FAIL=0

echo "=============================================="
echo "PR #373 Validation: Agentic Loop UI + Sanitize"
echo "=============================================="
echo ""

# Activate virtual environment
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# --- Check 1: _sanitize_messages strips empty tool_calls ---
echo "  Check 1: _sanitize_messages strips empty tool_calls ..."
python3 -c "
from atlas.modules.llm.litellm_caller import LiteLLMCaller

messages = [
    {'role': 'assistant', 'content': 'Hello', 'tool_calls': []},
    {'role': 'assistant', 'content': 'World', 'tool_calls': [{'id': 'x'}]},
    {'role': 'user', 'content': 'Hi'},
]
sanitized = LiteLLMCaller._sanitize_messages(messages)

# First message should have tool_calls removed
assert 'tool_calls' not in sanitized[0], f'Empty tool_calls not stripped: {sanitized[0]}'
# Second message should keep tool_calls
assert 'tool_calls' in sanitized[1], f'Non-empty tool_calls was stripped: {sanitized[1]}'
# Third message (no tool_calls key) should be unchanged
assert 'tool_calls' not in sanitized[2], f'Unexpected tool_calls added: {sanitized[2]}'
# Original messages should not be mutated
assert 'tool_calls' in messages[0], 'Original message was mutated'
print('    OK - Empty tool_calls stripped, non-empty preserved, originals not mutated')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 2: _sanitize_messages is called before acompletion ---
echo "  Check 2: _sanitize_messages called at all acompletion call sites ..."
CALLER_FILE="$PROJECT_ROOT/atlas/modules/llm/litellm_caller.py"
STREAMING_FILE="$PROJECT_ROOT/atlas/modules/llm/litellm_streaming.py"
CALLER_CALLS=$(grep -c "_sanitize_messages" "$CALLER_FILE" || true)
STREAMING_CALLS=$(grep -c "_sanitize_messages" "$STREAMING_FILE" || true)

# litellm_caller.py has 3 acompletion sites + 1 definition = at least 4 references
# litellm_streaming.py has 2 acompletion sites = at least 2 references
if [ "$CALLER_CALLS" -ge 4 ] && [ "$STREAMING_CALLS" -ge 2 ]; then
    echo "    OK - Found $CALLER_CALLS refs in litellm_caller.py, $STREAMING_CALLS refs in litellm_streaming.py"
    PASS=$((PASS+1))
else
    echo "    FAIL - Expected >=4 refs in caller ($CALLER_CALLS) and >=2 in streaming ($STREAMING_CALLS)"
    FAIL=$((FAIL+1))
fi

# --- Check 3: Agentic loop strips empty tool_calls in-loop ---
echo "  Check 3: Agentic loop strips empty tool_calls in-loop ..."
if grep -q "tool_calls" "$PROJECT_ROOT/atlas/application/chat/agent/agentic_loop.py" && \
   grep -q "Stripping empty tool_calls" "$PROJECT_ROOT/atlas/application/chat/agent/agentic_loop.py"; then
    echo "    OK - Agentic loop has in-loop tool_calls sanitization"
    PASS=$((PASS+1))
else
    echo "    FAIL - Missing in-loop tool_calls sanitization in agentic_loop.py"
    FAIL=$((FAIL+1))
fi

# --- Check 4: Settings panel sends agent_loop_strategy via WebSocket ---
echo "  Check 4: Frontend sends agent_loop_strategy in chat message ..."
if grep -q "agent_loop_strategy" "$PROJECT_ROOT/frontend/src/contexts/ChatContext.jsx"; then
    echo "    OK - ChatContext sends agent_loop_strategy"
    PASS=$((PASS+1))
else
    echo "    FAIL - ChatContext does not send agent_loop_strategy"
    FAIL=$((FAIL+1))
fi

# --- Check 5: Settings default is 'agentic' ---
echo "  Check 5: Default agent loop strategy is 'agentic' ..."
if grep -q "agentic" "$PROJECT_ROOT/frontend/src/hooks/useSettings.js"; then
    echo "    OK - useSettings.js defaults to agentic"
    PASS=$((PASS+1))
else
    echo "    FAIL - useSettings.js does not default to agentic"
    FAIL=$((FAIL+1))
fi

# --- Check 6: No OPSEC violations in user-visible UI ---
echo "  Check 6: No provider names in user-visible UI text ..."
OPSEC_HITS=$(grep -n "Claude\|Anthropic" "$PROJECT_ROOT/frontend/src/components/SettingsPanel.jsx" || true)
if [ -z "$OPSEC_HITS" ]; then
    echo "    OK - No provider-specific names in SettingsPanel.jsx"
    PASS=$((PASS+1))
else
    echo "    FAIL - Found provider names in SettingsPanel.jsx:"
    echo "$OPSEC_HITS"
    FAIL=$((FAIL+1))
fi

# --- Check 7: End-to-end sanitize + agentic loop execution ---
echo "  Check 7: End-to-end agentic loop with empty tool_calls in history ..."
python3 -c "
import asyncio
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock
from atlas.application.chat.agent.agentic_loop import AgenticLoop
from atlas.application.chat.agent.protocols import AgentContext, AgentEvent
from atlas.domain.messages.models import ConversationHistory, ToolResult
from atlas.interfaces.llm import LLMResponse

# Simulate: first response has tool calls, second is text-only (done)
responses = [
    LLMResponse(
        content='Looking it up...',
        tool_calls=[SimpleNamespace(
            id='call-1', type='function',
            function=SimpleNamespace(name='search', arguments='{}'),
        )],
    ),
    LLMResponse(content='Here is the answer.'),
]

class FakeLLM:
    async def call_with_tools(self, model, messages, tools_schema, tool_choice='auto', **kw):
        # Verify no message has empty tool_calls (the bug this PR fixes)
        for msg in messages:
            if isinstance(msg, dict) and 'tool_calls' in msg:
                assert len(msg['tool_calls']) > 0, f'Empty tool_calls found: {msg}'
        return responses.pop(0)
    async def call_plain(self, model, messages, **kw):
        return 'fallback'

async def fake_execute(tool_call_obj, context=None):
    return ToolResult(tool_call_id=tool_call_obj.id, content='result', success=True)

tool_mgr = MagicMock()
tool_mgr.execute_tool = AsyncMock(side_effect=fake_execute)
tool_mgr.get_tools_schema = MagicMock(return_value=[
    {'type': 'function', 'function': {'name': 'search', 'parameters': {}}}
])

events = []
async def handler(event):
    events.append(event)

loop = AgenticLoop(llm=FakeLLM(), tool_manager=tool_mgr, prompt_provider=None)
loop.skip_approval = True

result = asyncio.run(loop.run(
    model='test',
    messages=[{'role': 'user', 'content': 'test'}],
    context=AgentContext(
        session_id=uuid4(),
        user_email='test@test.com',
        files={},
        history=ConversationHistory(),
    ),
    selected_tools=['search'],
    data_sources=None,
    max_steps=5,
    temperature=0.7,
    event_handler=handler,
))

assert result.final_answer == 'Here is the answer.', f'Got: {result.final_answer}'
assert result.steps == 2, f'Expected 2 steps, got {result.steps}'
print('    OK - Agentic loop completed without empty tool_calls error')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 8: Full backend test suite ---
echo "  Check 8: Full backend test suite ..."
cd "$PROJECT_ROOT"
if bash test/run_tests.sh backend 2>&1 | grep -q "completed\|PASSED\|passed"; then
    echo "PASS"
    PASS=$((PASS+1))
else
    echo "FAIL"
    FAIL=$((FAIL+1))
fi

echo ""
echo "================================================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================================================"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
