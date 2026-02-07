#!/bin/bash
# Test script for PR #287: _mcp_data special input arg for MCP tools
# Covers test plan items from the PR description.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

print_result() {
    if [ $1 -eq 0 ]; then
        echo -e "\033[0;32mPASSED\033[0m: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "\033[0;31mFAILED\033[0m: $2"
        FAILED=$((FAILED + 1))
    fi
}

cd "$PROJECT_ROOT"
source .venv/bin/activate

# Tests 1-7 require atlas/ as cwd for imports
cd "$PROJECT_ROOT/atlas"

# --- Test 1: tool_accepts_mcp_data function exists and is importable ---
python -c "from application.chat.utilities.tool_executor import tool_accepts_mcp_data; print('OK')" 2>&1 | grep -q "OK"
print_result $? "tool_accepts_mcp_data function is importable"

# --- Test 2: build_mcp_data function exists and is importable ---
python -c "from application.chat.utilities.tool_executor import build_mcp_data; print('OK')" 2>&1 | grep -q "OK"
print_result $? "build_mcp_data function is importable"

# --- Test 3: build_mcp_data returns correct structure with mock data ---
python -c "
from application.chat.utilities.tool_executor import build_mcp_data
from unittest.mock import MagicMock

class FakeTool:
    def __init__(self, name, description='', inputSchema=None):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema or {}

mgr = MagicMock()
mgr.available_tools = {
    'server1': {
        'tools': [FakeTool('search', 'Search docs', {'type': 'object', 'properties': {'q': {'type': 'string'}}})],
        'config': {'description': 'Test Server'}
    }
}
result = build_mcp_data(mgr)
assert 'available_servers' in result, 'Missing available_servers key'
assert len(result['available_servers']) == 1, 'Expected 1 server'
srv = result['available_servers'][0]
assert srv['server_name'] == 'server1', f'Wrong server name: {srv[\"server_name\"]}'
assert srv['description'] == 'Test Server', f'Wrong description: {srv[\"description\"]}'
assert len(srv['tools']) == 1, f'Expected 1 tool, got {len(srv[\"tools\"])}'
assert srv['tools'][0]['name'] == 'server1_search', f'Wrong tool name: {srv[\"tools\"][0][\"name\"]}'
print('OK')
" 2>&1 | grep -q "OK"
print_result $? "build_mcp_data returns correct structure"

# --- Test 4: build_mcp_data skips canvas server ---
python -c "
from application.chat.utilities.tool_executor import build_mcp_data
from unittest.mock import MagicMock

class FakeTool:
    def __init__(self, name, description='', inputSchema=None):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema or {}

mgr = MagicMock()
mgr.available_tools = {
    'canvas': {'tools': [FakeTool('canvas')], 'config': {}},
    'real_server': {'tools': [FakeTool('tool1')], 'config': {}}
}
result = build_mcp_data(mgr)
names = [s['server_name'] for s in result['available_servers']]
assert 'canvas' not in names, 'Canvas should be excluded'
assert 'real_server' in names, 'real_server should be included'
print('OK')
" 2>&1 | grep -q "OK"
print_result $? "build_mcp_data skips canvas server"

# --- Test 5: inject_context_into_args injects _mcp_data when schema declares it ---
python -c "
from application.chat.utilities.tool_executor import inject_context_into_args
from unittest.mock import MagicMock

class FakeTool:
    def __init__(self, name, description='', inputSchema=None):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema or {}

tool = FakeTool('planner', inputSchema={
    'type': 'object',
    'properties': {'task': {'type': 'string'}, '_mcp_data': {'type': 'object'}}
})
mgr = MagicMock()
mgr.available_tools = {'demo': {'tools': [tool], 'config': {}}}
def get_schema(names):
    return [{'type': 'function', 'function': {'name': 'demo_planner', 'description': '', 'parameters': tool.inputSchema}}]
mgr.get_tools_schema = MagicMock(side_effect=get_schema)

result = inject_context_into_args({'task': 'test'}, {'user_email': 'u@test.com'}, 'demo_planner', mgr)
assert '_mcp_data' in result, '_mcp_data not injected'
assert 'available_servers' in result['_mcp_data'], 'Missing available_servers'
print('OK')
" 2>&1 | grep -q "OK"
print_result $? "inject_context_into_args injects _mcp_data when schema declares it"

# --- Test 6: inject_context_into_args does NOT inject _mcp_data when schema lacks it ---
python -c "
from application.chat.utilities.tool_executor import inject_context_into_args
from unittest.mock import MagicMock

class FakeTool:
    def __init__(self, name, description='', inputSchema=None):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema or {}

tool = FakeTool('search', inputSchema={
    'type': 'object',
    'properties': {'query': {'type': 'string'}}
})
mgr = MagicMock()
mgr.available_tools = {'demo': {'tools': [tool], 'config': {}}}
def get_schema(names):
    return [{'type': 'function', 'function': {'name': 'demo_search', 'description': '', 'parameters': tool.inputSchema}}]
mgr.get_tools_schema = MagicMock(side_effect=get_schema)

result = inject_context_into_args({'query': 'hello'}, {'user_email': 'u@test.com'}, 'demo_search', mgr)
assert '_mcp_data' not in result, '_mcp_data should not be injected'
print('OK')
" 2>&1 | grep -q "OK"
print_result $? "inject_context_into_args does NOT inject _mcp_data when schema lacks it"

# --- Test 7: Demo server plan_with_tools tool works with _mcp_data ---
python -c "
import sys
sys.path.insert(0, 'mcp/username-override-demo')
import importlib.util
spec = importlib.util.spec_from_file_location('uod_main', 'mcp/username-override-demo/main.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
# @mcp.tool wraps the function in FunctionTool; .fn gets the raw callable
fn = mod.plan_with_tools.fn
result = fn(task='test task', _mcp_data={'available_servers': [{'server_name': 's', 'tools': [{'name': 's_t', 'description': 'd'}], 'description': ''}]})
assert result['results']['task'] == 'test task'
assert result['results']['available_server_count'] == 1
assert result['results']['available_tool_count'] == 1
print('OK')
" 2>&1 | grep -q "OK"
print_result $? "Demo plan_with_tools tool works with _mcp_data"

# --- Test 8: Run _mcp_data injection unit tests ---
python -m pytest tests/test_mcp_data_injection.py -v --tb=short 2>&1 | tail -5
python -m pytest tests/test_mcp_data_injection.py --tb=short -q > /dev/null 2>&1
print_result $? "_mcp_data injection unit tests pass"

cd "$PROJECT_ROOT"

# --- Test 9: tool_planner format_tools_for_llm produces readable output ---
python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('tp', 'atlas/mcp/tool_planner/main.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
data = {
    'available_servers': [{
        'server_name': 'calc',
        'description': 'Calculator',
        'tools': [{
            'name': 'calc_eval',
            'description': 'Evaluate expression',
            'parameters': {
                'type': 'object',
                'properties': {'expr': {'type': 'string', 'description': 'Math expression'}},
                'required': ['expr']
            }
        }]
    }]
}
result = mod.format_tools_for_llm(data)
assert 'Server: calc (Calculator)' in result
assert 'Tool: calc_eval' in result
assert 'expr (string, required): Math expression' in result
print('OK')
" 2>&1 | grep -q "OK"
print_result $? "tool_planner format_tools_for_llm produces readable output"

# --- Test 10: tool_planner build_planning_prompt includes CLI instructions ---
python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('tp', 'atlas/mcp/tool_planner/main.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
result = mod.build_planning_prompt('create a pptx', 'Server: pptx')
assert 'Task: create a pptx' in result
assert 'atlas_chat_cli.py' in result
assert '--tools' in result
assert 'Server: pptx' in result
print('OK')
" 2>&1 | grep -q "OK"
print_result $? "tool_planner build_planning_prompt includes CLI instructions"

# --- Test 11: tool_planner plan_with_tools without ctx returns artifact ---
python -c "
import asyncio, base64, importlib.util
spec = importlib.util.spec_from_file_location('tp', 'atlas/mcp/tool_planner/main.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
# .fn gets the raw async function from the FastMCP FunctionTool wrapper
fn = mod.plan_with_tools.fn
result = asyncio.run(
    fn(task='test', _mcp_data={'available_servers': []})
)
assert 'artifacts' in result, 'Missing artifacts key'
assert 'display' in result, 'Missing display key'
script = base64.b64decode(result['artifacts'][0]['b64']).decode('utf-8')
assert 'Sampling unavailable' in script
assert result['artifacts'][0]['name'].endswith('.sh')
assert result['display']['open_canvas'] is True
print('OK')
" 2>&1 | grep -q "OK"
print_result $? "tool_planner plan_with_tools returns downloadable artifact"

# --- Test 12: tool_planner server files exist and are valid ---
python -c "
import os, importlib.util
# Verify the server entry point exists
server_path = 'atlas/mcp/tool_planner/main.py'
assert os.path.isfile(server_path), f'{server_path} does not exist'
# Verify it can be loaded as a module
spec = importlib.util.spec_from_file_location('tp', server_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
# Verify required functions exist
assert hasattr(mod, 'plan_with_tools'), 'plan_with_tools not found'
assert hasattr(mod, 'format_tools_for_llm'), 'format_tools_for_llm not found'
assert hasattr(mod, 'build_planning_prompt'), 'build_planning_prompt not found'
print('OK')
" 2>&1 | grep -q "OK"
print_result $? "tool_planner server files exist and are valid"

# --- Test 13: Run tool_planner unit tests ---
cd "$PROJECT_ROOT/atlas"
python -m pytest tests/test_tool_planner.py -v --tb=short 2>&1 | tail -10
python -m pytest tests/test_tool_planner.py --tb=short -q > /dev/null 2>&1
print_result $? "tool_planner unit tests pass"
cd "$PROJECT_ROOT"

# Final: run backend unit tests
./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests"

# Summary
echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ $FAILED -eq 0 ] && exit 0 || exit 1
