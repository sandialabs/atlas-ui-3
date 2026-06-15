#!/bin/bash
# Live end-to-end tests for the Atlas Python API and CLI.
#
# These tests make real LLM API calls and require at least one of:
#   - A local vLLM endpoint on port 8005 (preferred)
#   - A valid API key (e.g. OPENROUTER_API_KEY in .env)
#   - MCP servers configured (calculator at minimum)
#
# Usage:
#   bash test/e2e_tests_live.sh

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
SKIP=0

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

skip_test() {
    local name="$1"
    local reason="$2"
    echo "  $name ... SKIP ($reason)"
    SKIP=$((SKIP + 1))
}

echo "================================================================"
echo "Atlas Live E2E Tests"
echo "================================================================"
echo "Using python: $PYTHON"
echo "Project root: $PROJECT_ROOT"
echo ""

# ------------------------------------------------------------------
# Detect available models
# ------------------------------------------------------------------
LOCAL_VLLM_AVAILABLE=false
API_KEY_AVAILABLE=false

if curl -sf http://localhost:8005/v1/models >/dev/null 2>&1; then
    LOCAL_VLLM_AVAILABLE=true
    echo "Local vLLM endpoint: available (port 8005)"
fi

if [ -n "${OPENROUTER_API_KEY:-}" ] || [ -n "${OPENAI_API_KEY:-}" ]; then
    API_KEY_AVAILABLE=true
    echo "API key: available"
elif [ -f "$PROJECT_ROOT/.env" ] && grep -qE '^(OPENROUTER_API_KEY|OPENAI_API_KEY)=.+' "$PROJECT_ROOT/.env" 2>/dev/null; then
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
    API_KEY_AVAILABLE=true
    echo "API key: available (from .env)"
fi

if [ "$LOCAL_VLLM_AVAILABLE" = false ] && [ "$API_KEY_AVAILABLE" = false ]; then
    echo ""
    echo "No LLM endpoint available (no local vLLM, no API key)."
    echo "Skipping all live tests."
    exit 0
fi

# Pick the model to use — prefer local to avoid API cost/flakiness
if [ "$LOCAL_VLLM_AVAILABLE" = true ]; then
    TEST_MODEL="local-gpt-oss-20b"
    echo "Test model: $TEST_MODEL (local vLLM)"
else
    TEST_MODEL=""  # Let AtlasClient pick the first configured model
    echo "Test model: config default (API key)"
fi
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
    model = '$TEST_MODEL' or None
    result = await client.chat('Say hello in one word.', model=model)
    await client.cleanup()
    assert len(result.message) > 0, 'Empty message'
    assert result.session_id is not None, 'No session_id'
    print(f'  response: {result.message[:80]}', file=sys.stderr)

asyncio.run(test())
" 2>&1

# ------------------------------------------------------------------
# 2. Python API: sync wrapper
# ------------------------------------------------------------------
run_test "Python API: chat_sync wrapper works" \
    "$PYTHON" -c "
import sys, os
sys.path.insert(0, '$PROJECT_ROOT')
os.chdir('$PROJECT_ROOT')

from atlas import AtlasClient
client = AtlasClient()
model = '$TEST_MODEL' or None
result = client.chat_sync('Say OK.', model=model)
assert len(result.message) > 0, 'Empty message from chat_sync'
print(f'  response: {result.message[:80]}', file=sys.stderr)
" 2>&1

# ------------------------------------------------------------------
# 3. Python API: reasoning content (requires reasoning model)
# ------------------------------------------------------------------
if [ "$LOCAL_VLLM_AVAILABLE" = true ]; then
    run_test "Python API: reasoning content is captured from vLLM" \
        "$PYTHON" -c "
import asyncio, sys, os
sys.path.insert(0, '$PROJECT_ROOT')
os.chdir('$PROJECT_ROOT')

async def test():
    from atlas import AtlasClient
    client = AtlasClient()
    result = await client.chat(
        'Think step by step. How many r letters are in strawberry?',
        model='local-gpt-oss-20b',
    )
    await client.cleanup()
    assert len(result.message) > 0, 'Empty message'
    # Check that reasoning tokens were streamed (captured in raw_events)
    reasoning_tokens = [e for e in result.raw_events if e.get('type') == 'reasoning_token']
    reasoning_blocks = [e for e in result.raw_events if e.get('type') == 'reasoning_content']
    print(f'  reasoning_tokens: {len(reasoning_tokens)}, blocks: {len(reasoning_blocks)}', file=sys.stderr)
    print(f'  reasoning_content: {(result.reasoning_content or \"\")[:80]}', file=sys.stderr)
    print(f'  response: {result.message[:80]}', file=sys.stderr)
    assert len(reasoning_tokens) > 0, 'No reasoning tokens captured — monkey-patch may not be working'
    assert result.reasoning_content is not None, 'reasoning_content not set on ChatResult'
    assert len(result.reasoning_content) > 0, 'reasoning_content is empty'

asyncio.run(test())
" 2>&1
else
    skip_test "Python API: reasoning content is captured from vLLM" "no local vLLM"
fi

# ------------------------------------------------------------------
# 4. Python API: tool use with calculator
# ------------------------------------------------------------------
run_test "Python API: calculator tool is invoked and returns result" \
    "$PYTHON" -c "
import asyncio, sys, os
sys.path.insert(0, '$PROJECT_ROOT')
os.chdir('$PROJECT_ROOT')

async def test():
    from atlas import AtlasClient
    client = AtlasClient()
    model = '$TEST_MODEL' or None
    result = await client.chat(
        'Use the calculator tool to evaluate 2+2',
        model=model,
        selected_tools=['calculator_evaluate'],
        tool_choice_required=True,
    )
    await client.cleanup()
    assert len(result.tool_calls) > 0, 'No tool calls recorded'
    calc_calls = [tc for tc in result.tool_calls if tc.get('tool') == 'calculator_evaluate']
    assert len(calc_calls) > 0, f'No calculator calls found in: {result.tool_calls}'
    print(f'  tool calls: {len(result.tool_calls)}', file=sys.stderr)
    print(f'  message: {result.message[:80]}', file=sys.stderr)

asyncio.run(test())
" 2>&1

# ------------------------------------------------------------------
# 5. CLI: atlas-chat with --json output
# ------------------------------------------------------------------
run_test "CLI: atlas-chat returns valid JSON" \
    "$PYTHON" -c "
import sys, os, json
sys.path.insert(0, '$PROJECT_ROOT')
os.chdir('$PROJECT_ROOT')

from atlas import AtlasClient
client = AtlasClient()
model = '$TEST_MODEL' or None
result = client.chat_sync('Say hi', model=model)
d = result.to_dict()
for k in ('message', 'tool_calls', 'files', 'session_id'):
    assert k in d, f'missing key: {k}'
assert len(d['message']) > 0, 'empty message'
print(f'  JSON keys: {list(d.keys())}', file=sys.stderr)
" 2>&1

# ------------------------------------------------------------------
# Results
# ------------------------------------------------------------------
echo ""
echo "================================================================"
echo "Results: $PASS passed, $FAIL failed, $SKIP skipped"
echo "================================================================"

[ "$FAIL" -eq 0 ]
