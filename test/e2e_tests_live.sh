#!/bin/bash
# Live end-to-end tests for the Atlas Python API and CLI.
#
# These tests make real LLM API calls and require:
#   - A valid API key (e.g. OPENROUTER_API_KEY in .env)
#   - MCP servers configured (calculator at minimum)
#
# Usage:
#   bash test/e2e_tests_live.sh
#
# NOT included in run_tests.sh -- run manually.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Use the project venv python by default
if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON="${PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
else
    PYTHON="${PYTHON:-python}"
fi

PASS=0
FAIL=0

# Use a minimal MCP config with just the calculator server
export MCP_CONFIG_FILE="$SCRIPT_DIR/fixtures/mcp-live-test.json"

cleanup() {
    unset MCP_CONFIG_FILE 2>/dev/null || true
}
trap cleanup EXIT

run_test() {
    local name="$1"
    shift
    echo -n "  $name ... "
    if "$@" ; then
        echo "PASS"
        PASS=$((PASS + 1))
    else
        echo "FAIL"
        FAIL=$((FAIL + 1))
    fi
}

echo "================================================================"
echo "Atlas Live E2E Tests (requires API key)"
echo "================================================================"
echo "Using python: $PYTHON"
echo "Project root: $PROJECT_ROOT"
echo ""

# ------------------------------------------------------------------
# 1. Python API: simple chat
# ------------------------------------------------------------------
run_test "Python API: simple chat returns a message" \
    "$PYTHON" -c "
import asyncio, sys, os
sys.path.insert(0, '$PROJECT_ROOT')
os.chdir('$PROJECT_ROOT')

async def test():
    from atlas import AtlasClient
    client = AtlasClient()
    result = await client.chat('Say hello in one word.')
    await client.cleanup()
    assert len(result.message) > 0, 'Empty message'
    assert result.session_id is not None, 'No session_id'
    print(f'  response: {result.message[:80]}', file=sys.stderr)

asyncio.run(test())
" 2>&1

# ------------------------------------------------------------------
# 2. Python API: tool use with calculator
# ------------------------------------------------------------------
run_test "Python API: calculator tool is invoked and returns result" \
    "$PYTHON" -c "
import asyncio, sys, os
sys.path.insert(0, '$PROJECT_ROOT')
os.chdir('$PROJECT_ROOT')

async def test():
    from atlas import AtlasClient
    client = AtlasClient()
    result = await client.chat(
        'Use the calculator tool to evaluate 2654687621*sqrt(2)',
        selected_tools=['calculator_evaluate'],
        tool_choice_required=True,
    )
    await client.cleanup()
    assert len(result.tool_calls) > 0, 'No tool calls recorded'
    assert result.tool_calls[0]['tool'] == 'calculator_evaluate', (
        f'Wrong tool: {result.tool_calls[0][\"tool\"]}'
    )
    assert result.tool_calls[0]['status'] == 'complete', 'Tool not complete'
    print(f'  tool result: {result.tool_calls[0].get(\"result\", \"\")}', file=sys.stderr)
    print(f'  message: {result.message[:80]}', file=sys.stderr)

asyncio.run(test())
" 2>&1

# ------------------------------------------------------------------
# 3. Python API: sync wrapper works
# ------------------------------------------------------------------
run_test "Python API: chat_sync wrapper works" \
    "$PYTHON" -c "
import sys, os
sys.path.insert(0, '$PROJECT_ROOT')
os.chdir('$PROJECT_ROOT')

from atlas import AtlasClient
client = AtlasClient()
result = client.chat_sync('Say OK.')
assert len(result.message) > 0, 'Empty message from chat_sync'
print(f'  response: {result.message[:80]}', file=sys.stderr)
" 2>&1

# ------------------------------------------------------------------
# 4. CLI: atlas-chat with --json output
# ------------------------------------------------------------------
run_test "CLI: atlas-chat returns valid JSON" \
    bash -c "cd $PROJECT_ROOT/atlas && $PYTHON atlas_chat_cli.py 'Say hi' --json 2>/dev/null | $PYTHON -c '
import sys, json
d = json.load(sys.stdin)
for k in (\"message\", \"tool_calls\", \"files\", \"session_id\"):
    assert k in d, f\"missing key: {k}\"
assert len(d[\"message\"]) > 0, \"empty message\"
'"

# ------------------------------------------------------------------
# 5. CLI: tool use via command line
# ------------------------------------------------------------------
run_test "CLI: calculator tool via --tools flag" \
    bash -c "cd $PROJECT_ROOT/atlas && $PYTHON atlas_chat_cli.py 'Use the calculator tool to evaluate 2654687621*sqrt(2)' --tools calculator_evaluate --json 2>/dev/null | $PYTHON -c '
import sys, json
d = json.load(sys.stdin)
assert len(d[\"tool_calls\"]) > 0, \"No tool calls\"
assert d[\"tool_calls\"][0][\"tool\"] == \"calculator_evaluate\", \"Wrong tool\"
'"

# ------------------------------------------------------------------
# Results
# ------------------------------------------------------------------
echo ""
echo "================================================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================================================"

[ "$FAIL" -eq 0 ]
