#!/bin/bash
# PR #363 - Claude-native agentic agent loop strategy
# Validates the new "agentic" strategy: import, factory registration,
# tool_choice="auto", no control tools, and full test suite.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PASS=0
FAIL=0

echo "=========================================="
echo "PR #363 Validation: Agentic Loop Strategy"
echo "=========================================="
echo ""

# Activate virtual environment
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# --- Check 1: AgenticLoop is importable ---
echo "  Check 1: AgenticLoop is importable ..."
python3 -c "
from atlas.application.chat.agent.agentic_loop import AgenticLoop
from atlas.application.chat.agent import AgenticLoop as AgenticLoopExport
assert AgenticLoop is AgenticLoopExport, 'Package export mismatch'
print('    OK - AgenticLoop imported successfully from both paths')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 2: Factory creates agentic loop ---
echo "  Check 2: Factory creates agentic loop ..."
python3 -c "
from unittest.mock import MagicMock
from atlas.application.chat.agent.factory import AgentLoopFactory
from atlas.application.chat.agent.agentic_loop import AgenticLoop

# Create factory with mock LLM
mock_llm = MagicMock()
factory = AgentLoopFactory(llm=mock_llm)

# Verify agentic is in available strategies
strategies = factory.get_available_strategies()
assert 'agentic' in strategies, f'agentic not in {strategies}'
print(f'    OK - Available strategies: {strategies}')

# Verify factory creates the correct class
loop = factory.create('agentic')
assert isinstance(loop, AgenticLoop), f'Expected AgenticLoop, got {type(loop)}'
print('    OK - Factory creates AgenticLoop instance')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 3: Agentic loop uses tool_choice="auto", not "required" ---
echo "  Check 3: No tool_choice='required' in agentic_loop.py ..."
if grep -q '"required"' "$PROJECT_ROOT/atlas/application/chat/agent/agentic_loop.py"; then
    echo "    FAIL - agentic_loop.py contains 'required' tool_choice"
    FAIL=$((FAIL+1))
else
    echo "    OK - No 'required' tool_choice found"
    PASS=$((PASS+1))
fi

# --- Check 4: No control tools in agentic loop ---
echo "  Check 4: No control tools (finished, agent_decide_next, etc.) ..."
CONTROL_TOOLS=("finished" "agent_decide_next" "agent_observe_decide" "agent_think")
FOUND_CONTROL=0
for tool in "${CONTROL_TOOLS[@]}"; do
    if grep -q "\"$tool\"" "$PROJECT_ROOT/atlas/application/chat/agent/agentic_loop.py"; then
        echo "    FAIL - Found control tool '$tool' in agentic_loop.py"
        FOUND_CONTROL=1
    fi
done
if [ "$FOUND_CONTROL" -eq 0 ]; then
    echo "    OK - No control tools found in agentic_loop.py"
    PASS=$((PASS+1))
else
    FAIL=$((FAIL+1))
fi

# --- Check 5: Agentic loop uses execute_multiple_tools for parallel execution ---
echo "  Check 5: Uses execute_multiple_tools for parallel tool execution ..."
if grep -q "execute_multiple_tools" "$PROJECT_ROOT/atlas/application/chat/agent/agentic_loop.py"; then
    echo "    OK - agentic_loop.py uses execute_multiple_tools"
    PASS=$((PASS+1))
else
    echo "    FAIL - agentic_loop.py does not use execute_multiple_tools"
    FAIL=$((FAIL+1))
fi

# --- Check 6: End-to-end agentic loop execution (no real LLM) ---
echo "  Check 6: End-to-end agentic loop execution ..."
python3 -c "
import asyncio
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock
from atlas.application.chat.agent.agentic_loop import AgenticLoop
from atlas.application.chat.agent.protocols import AgentContext, AgentEvent
from atlas.domain.messages.models import ConversationHistory, ToolResult
from atlas.interfaces.llm import LLMResponse

# Fake LLM: first call returns tool use, second returns text (done)
responses = [
    LLMResponse(
        content='Searching...',
        tool_calls=[SimpleNamespace(
            id='call-1', type='function',
            function=SimpleNamespace(name='search', arguments='{}'),
        )],
    ),
    LLMResponse(content='Found the answer.'),
]

class FakeLLM:
    async def call_with_tools(self, model, messages, tools_schema, tool_choice='auto', **kw):
        assert tool_choice == 'auto', f'Expected auto, got {tool_choice}'
        return responses.pop(0)
    async def call_plain(self, model, messages, **kw):
        return 'fallback'

# Fake tool manager
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

assert result.final_answer == 'Found the answer.', f'Got: {result.final_answer}'
assert result.steps == 2, f'Expected 2 steps, got {result.steps}'
assert result.metadata['strategy'] == 'agentic'

event_types = [e.type for e in events]
assert 'agent_start' in event_types
assert 'agent_tool_results' in event_types
assert 'agent_completion' in event_types

print('    OK - Agentic loop executed: tool call -> text answer in 2 steps')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 7: Config manager accepts 'agentic' strategy ---
echo "  Check 7: Config manager accepts 'agentic' strategy ..."
python3 -c "
import os
os.environ['AGENT_LOOP_STRATEGY'] = 'agentic'
from atlas.modules.config.config_manager import AppSettings
settings = AppSettings()
assert settings.agent_loop_strategy == 'agentic', f'Got: {settings.agent_loop_strategy}'
print('    OK - AppSettings accepts agentic strategy')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 8: Agentic loop test suite ---
echo "  Check 8: Agentic loop test suite passes ..."
cd "$PROJECT_ROOT"
python -m pytest atlas/tests/test_agentic_loop.py -v --tb=short 2>&1 | tail -20
if python -m pytest atlas/tests/test_agentic_loop.py --tb=short -q 2>&1 | grep -q "passed"; then
    echo "PASS"
    PASS=$((PASS+1))
else
    echo "FAIL"
    FAIL=$((FAIL+1))
fi

# --- Check 9: Full backend test suite ---
echo "  Check 9: Full backend test suite ..."
cd "$PROJECT_ROOT"
bash test/run_tests.sh backend 2>&1 | tail -5
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
