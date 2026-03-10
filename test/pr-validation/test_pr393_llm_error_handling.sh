#!/usr/bin/env bash
# PR #393 - LLM errors cause chat to hang indefinitely instead of returning error to user
# Validates that LLM errors are properly classified and propagated to the frontend.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
PASSED=0
FAILED=0

pass() { echo "PASSED: $1"; PASSED=$((PASSED + 1)); }
fail() { echo "FAILED: $1"; FAILED=$((FAILED + 1)); }

# Activate venv
source "$PROJECT_ROOT/.venv/bin/activate"
export PYTHONPATH="$PROJECT_ROOT"

echo "============================================"
echo "PR #393 - LLM Error Handling Validation"
echo "============================================"

# -------------------------------------------------------------------
# 1. Verify LiteLLMCaller raises domain errors instead of generic Exception
# -------------------------------------------------------------------
echo ""
echo "--- Test 1: LiteLLMCaller imports domain error classes ---"
if python -c "
from atlas.modules.llm.litellm_caller import LiteLLMCaller
import inspect
src = inspect.getsource(LiteLLMCaller._raise_llm_domain_error)
assert 'RateLimitError' in src, 'Missing RateLimitError'
assert 'LLMTimeoutError' in src, 'Missing LLMTimeoutError'
assert 'LLMAuthenticationError' in src, 'Missing LLMAuthenticationError'
assert 'LLMServiceError' in src, 'Missing LLMServiceError'
print('_raise_llm_domain_error handles all expected error types')
"; then
    pass "LiteLLMCaller._raise_llm_domain_error handles all domain error types"
else
    fail "LiteLLMCaller._raise_llm_domain_error missing error types"
fi

# -------------------------------------------------------------------
# 2. Verify _raise_llm_domain_error raises correct domain errors
# -------------------------------------------------------------------
echo ""
echo "--- Test 2: _raise_llm_domain_error maps litellm errors correctly ---"
if python -c "
import litellm
from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.domain.errors import RateLimitError, LLMTimeoutError, LLMAuthenticationError, LLMServiceError

caller = LiteLLMCaller.__new__(LiteLLMCaller)

# Test rate limit
try:
    caller._raise_llm_domain_error(litellm.RateLimitError('rate limit exceeded', 'model', None, None))
    assert False, 'Should have raised'
except RateLimitError:
    print('  Rate limit -> RateLimitError: OK')

# Test timeout
try:
    caller._raise_llm_domain_error(litellm.Timeout('request timed out', 'model', None))
    assert False, 'Should have raised'
except LLMTimeoutError:
    print('  Timeout -> LLMTimeoutError: OK')

# Test auth error
try:
    caller._raise_llm_domain_error(litellm.AuthenticationError('invalid api key', 'model', None, None))
    assert False, 'Should have raised'
except LLMAuthenticationError:
    print('  AuthenticationError -> LLMAuthenticationError: OK')

# Test generic error
try:
    caller._raise_llm_domain_error(Exception('something went wrong'))
    assert False, 'Should have raised'
except LLMServiceError:
    print('  Generic Exception -> LLMServiceError: OK')

print('All error mappings correct')
"; then
    pass "LiteLLMCaller maps litellm exceptions to domain errors correctly"
else
    fail "LiteLLMCaller error mapping is incorrect"
fi

# -------------------------------------------------------------------
# 3. Verify call_plain no longer raises generic Exception
# -------------------------------------------------------------------
echo ""
echo "--- Test 3: call_plain raises domain errors, not generic Exception ---"
if python -c "
import inspect
from atlas.modules.llm.litellm_caller import LiteLLMCaller
src = inspect.getsource(LiteLLMCaller.call_plain)
# Should use _raise_llm_domain_error, not 'raise Exception'
assert '_raise_llm_domain_error' in src, 'call_plain should use _raise_llm_domain_error'
assert 'raise Exception' not in src, 'call_plain should not raise generic Exception'
print('call_plain uses domain error classification')
"; then
    pass "call_plain raises domain errors"
else
    fail "call_plain still raises generic Exception"
fi

# -------------------------------------------------------------------
# 4. Verify call_with_tools no longer raises generic Exception
# -------------------------------------------------------------------
echo ""
echo "--- Test 4: call_with_tools raises domain errors, not generic Exception ---"
if python -c "
import inspect
from atlas.modules.llm.litellm_caller import LiteLLMCaller
src = inspect.getsource(LiteLLMCaller.call_with_tools)
# Should use _raise_llm_domain_error, not 'raise Exception'
assert '_raise_llm_domain_error' in src, 'call_with_tools should use _raise_llm_domain_error'
assert 'raise Exception' not in src, 'call_with_tools should not raise generic Exception'
print('call_with_tools uses domain error classification')
"; then
    pass "call_with_tools raises domain errors"
else
    fail "call_with_tools still raises generic Exception"
fi

# -------------------------------------------------------------------
# 5. Verify AgentModeRunner has error handling
# -------------------------------------------------------------------
echo ""
echo "--- Test 5: AgentModeRunner catches errors and sends agent_completion ---"
if python -c "
import inspect
from atlas.application.chat.modes.agent import AgentModeRunner
src = inspect.getsource(AgentModeRunner.run)
assert 'except Exception' in src, 'AgentModeRunner.run should catch exceptions'
assert 'agent_completion' in src, 'AgentModeRunner.run should send agent_completion on error'
print('AgentModeRunner has proper error handling')
"; then
    pass "AgentModeRunner handles errors and cleans up UI state"
else
    fail "AgentModeRunner missing error handling"
fi

# -------------------------------------------------------------------
# 6. Verify frontend error handler resets agent state
# -------------------------------------------------------------------
echo ""
echo "--- Test 6: Frontend error handler resets agent state ---"
HANDLER_FILE="$PROJECT_ROOT/frontend/src/handlers/chat/websocketHandlers.js"
if grep -A 5 "case 'error':" "$HANDLER_FILE" | grep -q "setCurrentAgentStep(0)"; then
    pass "Frontend error handler resets currentAgentStep"
else
    fail "Frontend error handler does not reset currentAgentStep"
fi

if grep -A 5 "case 'error':" "$HANDLER_FILE" | grep -q "setAgentPendingQuestion"; then
    pass "Frontend error handler clears agent pending question"
else
    fail "Frontend error handler does not clear agent pending question"
fi

# -------------------------------------------------------------------
# 7. Verify frontend thinking timeout exists
# -------------------------------------------------------------------
echo ""
echo "--- Test 7: Frontend has thinking timeout ---"
CONTEXT_FILE="$PROJECT_ROOT/frontend/src/contexts/ChatContext.jsx"
if grep -q "THINKING_TIMEOUT_MS" "$CONTEXT_FILE"; then
    pass "Frontend has thinking timeout constant"
else
    fail "Frontend missing thinking timeout"
fi

if grep -q "thinkingTimeoutRef" "$CONTEXT_FILE"; then
    pass "Frontend has thinking timeout ref and effect"
else
    fail "Frontend missing thinking timeout implementation"
fi

# -------------------------------------------------------------------
# 8. Run backend unit tests
# -------------------------------------------------------------------
echo ""
echo "--- Test 8: Backend unit tests ---"
cd "$PROJECT_ROOT"
if bash test/run_tests.sh backend; then
    pass "Backend unit tests pass"
else
    fail "Backend unit tests failed"
fi

# -------------------------------------------------------------------
# Summary
# -------------------------------------------------------------------
echo ""
echo "============================================"
echo "RESULTS: $PASSED passed, $FAILED failed"
echo "============================================"

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
exit 0
