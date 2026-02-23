#!/bin/bash
# PR #358 - Multi-tool calling support (issue #353)
# Validates that multiple tool calls from a single LLM response are executed
# concurrently and that all agent loops handle them correctly.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PASS=0
FAIL=0

echo "=========================================="
echo "PR #358 Validation: Multi-Tool Calling"
echo "=========================================="
echo ""

# Activate virtual environment
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# --- Check 1: execute_multiple_tools function exists and is importable ---
echo "  Check 1: execute_multiple_tools is importable ..."
python3 -c "
from atlas.application.chat.utilities.tool_executor import execute_multiple_tools
assert callable(execute_multiple_tools), 'execute_multiple_tools should be callable'
print('    OK - execute_multiple_tools imported successfully')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 2: execute_multiple_tools handles empty list ---
echo "  Check 2: execute_multiple_tools handles empty list ..."
python3 -c "
import asyncio
from atlas.application.chat.utilities.tool_executor import execute_multiple_tools
result = asyncio.run(execute_multiple_tools(tool_calls=[], session_context={}, tool_manager=None))
assert result == [], f'Expected empty list, got {result}'
print('    OK - empty list returns empty results')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 3: Agent loops import and use execute_multiple_tools ---
echo "  Check 3: Agent loops reference execute_multiple_tools ..."
for loop_file in react_loop.py think_act_loop.py act_loop.py; do
    if grep -q "execute_multiple_tools" "$PROJECT_ROOT/atlas/application/chat/agent/$loop_file"; then
        echo "    OK - $loop_file uses execute_multiple_tools"
    else
        echo "    FAIL - $loop_file does not use execute_multiple_tools"
        FAIL=$((FAIL+1))
    fi
done
# Check that none of them use first_call pattern anymore
for loop_file in react_loop.py think_act_loop.py act_loop.py; do
    if grep -q "first_call" "$PROJECT_ROOT/atlas/application/chat/agent/$loop_file"; then
        echo "    FAIL - $loop_file still uses first_call pattern"
        FAIL=$((FAIL+1))
    else
        echo "    OK - $loop_file no longer uses first_call pattern"
    fi
done
PASS=$((PASS+1))

# --- Check 4: ToolsModeRunner streaming uses execute_multiple_tools ---
echo "  Check 4: ToolsModeRunner streaming uses execute_multiple_tools ..."
if grep -q "execute_multiple_tools" "$PROJECT_ROOT/atlas/application/chat/modes/tools.py"; then
    echo "    OK - tools.py uses execute_multiple_tools"
    PASS=$((PASS+1))
else
    echo "    FAIL - tools.py does not use execute_multiple_tools"
    FAIL=$((FAIL+1))
fi

# --- Check 5: Multi-tool test suite passes ---
echo "  Check 5: Multi-tool calling tests pass ..."
cd "$PROJECT_ROOT"
python -m pytest atlas/tests/test_multi_tool_calling.py -v --tb=short 2>&1 | tail -15
if python -m pytest atlas/tests/test_multi_tool_calling.py --tb=short -q 2>&1 | grep -q "passed"; then
    echo "PASS"
    PASS=$((PASS+1))
else
    echo "FAIL"
    FAIL=$((FAIL+1))
fi

# --- Check 6: Parallel execution verified ---
echo "  Check 6: Parallel execution verified (concurrency test) ..."
python3 -c "
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock
from atlas.application.chat.utilities.tool_executor import execute_multiple_tools
from atlas.domain.messages.models import ToolResult

log = []

async def slow_exec(tool_call_obj, context=None):
    log.append(f'start-{tool_call_obj.name}')
    await asyncio.sleep(0.05)
    log.append(f'end-{tool_call_obj.name}')
    return ToolResult(tool_call_id=tool_call_obj.id, content='ok', success=True)

mgr = MagicMock()
mgr.execute_tool = slow_exec
mgr.get_tools_schema = MagicMock(return_value=[])

tc1 = SimpleNamespace(id='1', type='function', function=SimpleNamespace(name='a', arguments='{}'))
tc2 = SimpleNamespace(id='2', type='function', function=SimpleNamespace(name='b', arguments='{}'))

results = asyncio.run(execute_multiple_tools(
    tool_calls=[tc1, tc2],
    session_context={},
    tool_manager=mgr,
    skip_approval=True,
))
assert len(results) == 2, f'Expected 2 results, got {len(results)}'
# Verify parallel: both start before either ends
assert log.index('start-a') < log.index('end-b'), 'Not parallel'
assert log.index('start-b') < log.index('end-a'), 'Not parallel'
print('    OK - tools executed in parallel (verified via timing)')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 7: Full backend test suite ---
echo "  Check 7: Full backend test suite ..."
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
