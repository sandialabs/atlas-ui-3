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
# Checks 1-3: SUPERSEDED BY PR #862
#
# #389 made the RAG+tools path inject an is_completion answer as context so
# tools stayed available. #862 deleted that path entirely: retrieval in
# tools/agent mode is now an explicit ``atlas_search`` tool call, and a
# pre-synthesized answer comes back as that call's tool result. What is left to
# guard is that the silent path did not come back.
# --------------------------------------------------------------------------
echo "--- Checks 1-3 (superseded by #862): the RAG+tools injection path stays deleted ---"

if grep -q "call_with_rag_and_tools" "$PROJECT_ROOT/atlas/modules/llm/litellm_caller.py" || \
   grep -q "stream_with_rag_and_tools" "$PROJECT_ROOT/atlas/modules/llm/litellm_streaming.py"; then
    fail "the RAG+tools pre-injection path is back (see PR #862)"
else
    pass "no RAG+tools pre-injection path in the LLM callers"
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
