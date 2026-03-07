#!/usr/bin/env bash
# PR #389 Validation: Fix RAG+tools conflict - don't bypass tools on is_completion
# When both RAG and tools are active and RAG returns is_completion=True,
# the response should be injected as context (not returned directly),
# so tools remain available to the LLM.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

pass() { echo "  PASSED: $1"; PASSED=$((PASSED + 1)); }
fail() { echo "  FAILED: $1"; FAILED=$((FAILED + 1)); }

echo "=== PR #389 Validation: RAG+Tools is_completion fix ==="
echo ""

# Activate venv
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# --------------------------------------------------------------------------
# Check 1: litellm_caller.py call_with_rag_and_tools no longer returns early
# on is_completion when tools are present
# --------------------------------------------------------------------------
echo "--- Check 1: litellm_caller.py does not return early on is_completion in call_with_rag_and_tools ---"

# The old code had: return LLMResponse(content=final_response) after is_completion check
# in call_with_rag_and_tools. The new code should NOT have that pattern.
if grep -A5 "LLM+RAG+Tools.*RAG returned" "$PROJECT_ROOT/atlas/modules/llm/litellm_caller.py" | grep -q "return LLMResponse"; then
    fail "litellm_caller.py still returns LLMResponse directly on is_completion in RAG+Tools path"
else
    pass "litellm_caller.py injects is_completion as context instead of returning early"
fi

# --------------------------------------------------------------------------
# Check 2: litellm_streaming.py stream_with_rag_and_tools no longer yields
# and returns early on is_completion when tools are present
# --------------------------------------------------------------------------
echo "--- Check 2: litellm_streaming.py does not return early on is_completion in stream_with_rag_and_tools ---"

# Find the stream_with_rag_and_tools method and check for early return on is_completion
# The old code had: yield LLMResponse(...) then return after is_completion in the tools path
STREAMING_FILE="$PROJECT_ROOT/atlas/modules/llm/litellm_streaming.py"

# In stream_with_rag_and_tools, the is_completion block should assign rag_content,
# not yield LLMResponse and return. Use grep to verify no "yield LLMResponse" appears
# near "stream_with_rag_and_tools" is_completion handling.
# Extract the method and check for yield+return pattern:
if python3 -c "
import re, sys
with open(sys.argv[1]) as f:
    content = f.read()
m = re.search(r'async def stream_with_rag_and_tools.*?(?=\n    async def |\nclass |\Z)', content, re.DOTALL)
if not m:
    print('METHOD_NOT_FOUND'); sys.exit(1)
method = m.group()
if 'is_completion' not in method:
    print('NO_IS_COMPLETION'); sys.exit(1)
after = method.split('is_completion')[1].split('else:')[0]
if 'yield LLMResponse' in after and 'return' in after:
    print('EARLY_RETURN'); sys.exit(1)
print('OK')
" "$STREAMING_FILE"; then
    pass "litellm_streaming.py stream_with_rag_and_tools injects is_completion as context"
else
    fail "litellm_streaming.py stream_with_rag_and_tools still returns early on is_completion"
fi

# --------------------------------------------------------------------------
# Check 3: Pre-synthesized context label is used
# --------------------------------------------------------------------------
echo "--- Check 3: Pre-synthesized context label used for is_completion ---"

if grep -q "Pre-synthesized answer from" "$PROJECT_ROOT/atlas/modules/llm/litellm_caller.py" && \
   grep -q "Pre-synthesized answer from" "$STREAMING_FILE"; then
    pass "Both files use 'Pre-synthesized answer from' label for is_completion content"
else
    fail "Missing 'Pre-synthesized answer from' context label"
fi

# --------------------------------------------------------------------------
# Check 4: RAG-only path (no tools) still returns directly on is_completion
# --------------------------------------------------------------------------
echo "--- Check 4: RAG-only path preserves direct return on is_completion ---"

# call_with_rag (no tools) should still have the early return
if python3 -c "
import re, sys
with open(sys.argv[1]) as f:
    content = f.read()
m = re.search(r'async def call_with_rag\b.*?(?=\n    async def |\nclass |\Z)', content, re.DOTALL)
if not m:
    print('METHOD_NOT_FOUND'); sys.exit(1)
method = m.group()
if 'is_completion' in method and 'return final_response' in method:
    print('OK')
else:
    print('NO_EARLY_RETURN'); sys.exit(1)
" "$PROJECT_ROOT/atlas/modules/llm/litellm_caller.py"; then
    pass "RAG-only path (call_with_rag) still returns directly on is_completion"
else
    fail "RAG-only path lost its is_completion early return"
fi

# --------------------------------------------------------------------------
# Check 5: _sanitize_messages still exists (regression guard for PR #373)
# --------------------------------------------------------------------------
echo "--- Check 5: _sanitize_messages still present (PR #373 regression guard) ---"

if grep -q "_sanitize_messages" "$PROJECT_ROOT/atlas/modules/llm/litellm_caller.py"; then
    pass "_sanitize_messages method still exists in litellm_caller.py"
else
    fail "_sanitize_messages was removed - this re-introduces the OpenAI empty tool_calls bug"
fi

# --------------------------------------------------------------------------
# Check 6: Run backend unit tests
# --------------------------------------------------------------------------
echo ""
echo "--- Check 6: Backend unit tests ---"
cd "$PROJECT_ROOT"
if ./test/run_tests.sh backend; then
    pass "Backend tests pass"
else
    fail "Backend tests failed"
fi

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------
echo ""
echo "=== PR #389 Validation Summary ==="
echo "  Passed: $PASSED"
echo "  Failed: $FAILED"
echo ""

if [ "$FAILED" -gt 0 ]; then
    echo "RESULT: FAILED"
    exit 1
else
    echo "RESULT: PASSED"
    exit 0
fi
