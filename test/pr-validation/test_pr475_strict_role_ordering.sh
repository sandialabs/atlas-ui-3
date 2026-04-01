#!/bin/bash
# Test script for PR #475: Strict role ordering for Mistral/vLLM models
#
# Test plan:
# - Verify ModelConfig recognizes strict_role_ordering field
# - Verify _enforce_strict_role_ordering converts post-tool system→user
# - Verify bridging assistant message inserted after tool results
# - Verify no-op when no tool messages present
# - Verify multi-turn tool call sequences handled correctly
# - Verify _prepare_messages applies strict ordering only when flag is set
# - Verify CLI-driven fixture transforms produce valid role sequences
# - Full strict role ordering test suite passes
# - Backend test suite has no regressions

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
FIXTURES_DIR="$SCRIPT_DIR/fixtures/pr475"

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

print_header "PR #475: Strict Role Ordering for Mistral/vLLM Tests"

# Activate virtual environment
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# Load fixture env
if [ -f "$FIXTURES_DIR/.env" ]; then
    set -a
    source "$FIXTURES_DIR/.env"
    set +a
fi

print_header "1. ModelConfig strict_role_ordering field"
cd "$ATLAS_DIR" && python -m pytest tests/test_strict_role_ordering.py::TestModelConfigStrictRoleOrdering -v --tb=short 2>&1
print_result $? "ModelConfig correctly recognizes strict_role_ordering=true and defaults to false"

print_header "2. System-after-tool conversion with bridge"
cd "$ATLAS_DIR" && python -m pytest tests/test_strict_role_ordering.py::TestEnforceStrictRoleOrdering::test_system_after_tool_converted_with_bridge -v --tb=short 2>&1
print_result $? "Post-tool system messages converted to user with bridging assistant"

print_header "3. Pre-tool system messages preserved"
cd "$ATLAS_DIR" && python -m pytest tests/test_strict_role_ordering.py::TestEnforceStrictRoleOrdering::test_system_before_tool_preserved -v --tb=short 2>&1
print_result $? "System messages before any tool call are untouched"

print_header "4. Multi-turn tool call sequences"
cd "$ATLAS_DIR" && python -m pytest tests/test_strict_role_ordering.py::TestEnforceStrictRoleOrdering::test_multi_turn_tool_calls -v --tb=short 2>&1
print_result $? "Multiple rounds of tool calls with system messages handled correctly"

print_header "5. No-op when no tool messages"
cd "$ATLAS_DIR" && python -m pytest tests/test_strict_role_ordering.py::TestEnforceStrictRoleOrdering::test_no_tool_messages_unchanged -v --tb=short 2>&1
print_result $? "Messages without tool calls pass through unchanged"

print_header "6. Original messages not mutated"
cd "$ATLAS_DIR" && python -m pytest tests/test_strict_role_ordering.py::TestEnforceStrictRoleOrdering::test_does_not_mutate_original -v --tb=short 2>&1
print_result $? "Input message dicts are not mutated by the transform"

print_header "7. _prepare_messages integration (strict vs non-strict)"
cd "$ATLAS_DIR" && python -m pytest tests/test_strict_role_ordering.py::TestPrepareMessages -v --tb=short 2>&1
print_result $? "_prepare_messages applies strict ordering only when config flag is set"

print_header "8. CLI fixture: tool-then-system transform"
cd "$PROJECT_ROOT" && python -c "
import json, sys

from atlas.modules.llm.litellm_caller import LiteLLMCaller

with open('$FIXTURES_DIR/messages_tool_then_system.json') as f:
    messages = json.load(f)

result = LiteLLMCaller._enforce_strict_role_ordering(messages)
roles = [m['role'] for m in result]

# Expected: system, user, assistant, tool, assistant(bridge), user(converted)
expected = ['system', 'user', 'assistant', 'tool', 'assistant', 'user']
assert roles == expected, f'Expected {expected}, got {roles}'

# Verify first system preserved, last system converted
assert result[0]['content'] == 'You are a helpful assistant.'
assert result[5]['content'] == 'Files manifest: results.txt'
assert result[5]['role'] == 'user'

# Verify bridge content
assert result[4]['role'] == 'assistant'

print('Fixture transform produced expected role sequence: ' + str(roles))
" 2>&1
print_result $? "Fixture messages_tool_then_system.json transforms correctly"

print_header "9. CLI fixture: no-tool passthrough"
cd "$PROJECT_ROOT" && python -c "
import json

from atlas.modules.llm.litellm_caller import LiteLLMCaller

with open('$FIXTURES_DIR/messages_no_tool.json') as f:
    messages = json.load(f)

result = LiteLLMCaller._enforce_strict_role_ordering(messages)
roles = [m['role'] for m in result]

# No tool messages: all system messages should stay as system
expected = ['system', 'user', 'assistant', 'system']
assert roles == expected, f'Expected {expected}, got {roles}'
assert len(result) == len(messages), 'No extra messages should be inserted'

print('No-tool fixture passed through unchanged: ' + str(roles))
" 2>&1
print_result $? "Fixture messages_no_tool.json passes through unchanged"

print_header "10. CLI fixture: multi-turn tool calls"
cd "$PROJECT_ROOT" && python -c "
import json

from atlas.modules.llm.litellm_caller import LiteLLMCaller

with open('$FIXTURES_DIR/messages_multi_turn.json') as f:
    messages = json.load(f)

result = LiteLLMCaller._enforce_strict_role_ordering(messages)
roles = [m['role'] for m in result]

# Verify no tool message is ever followed by system or user
for i, msg in enumerate(result):
    if msg['role'] == 'tool' and i + 1 < len(result):
        next_role = result[i + 1]['role']
        assert next_role in ('tool', 'assistant'), \
            f'tool at index {i} followed by {next_role}'

# Verify all system messages after first tool are converted
post_tool = False
for msg in result:
    if msg['role'] == 'tool':
        post_tool = True
    if post_tool:
        assert msg['role'] != 'system', 'No system messages should remain after tool'

print('Multi-turn fixture valid. Role sequence: ' + str(roles))
" 2>&1
print_result $? "Fixture messages_multi_turn.json produces valid Mistral-compatible sequence"

print_header "11. Role sequence invariant (full suite)"
cd "$ATLAS_DIR" && python -m pytest tests/test_strict_role_ordering.py::TestEnforceStrictRoleOrdering::test_role_sequence_always_valid -v --tb=short 2>&1
print_result $? "Invariant: tool is always followed by tool or assistant"

print_header "12. Full strict role ordering test suite"
cd "$ATLAS_DIR" && python -m pytest tests/test_strict_role_ordering.py -v --tb=short 2>&1
print_result $? "All strict role ordering tests pass"

print_header "13. Lint check on changed files"
cd "$PROJECT_ROOT" && python -m ruff check atlas/modules/llm/litellm_caller.py atlas/modules/llm/litellm_streaming.py atlas/modules/config/config_manager.py atlas/tests/test_strict_role_ordering.py 2>&1
print_result $? "Ruff lint clean on all changed files"

print_header "14. Backend test suite (no regressions)"
cd "$PROJECT_ROOT" && bash test/run_tests.sh backend 2>&1
print_result $? "Backend test suite"

echo ""
echo "=========================================="
echo "RESULTS: ${PASSED} passed, ${FAILED} failed"
echo "=========================================="

if [ $FAILED -gt 0 ]; then
    exit 1
fi
exit 0
