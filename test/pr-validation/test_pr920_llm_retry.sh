#!/bin/bash
# Validation script for PR #920 (issue #919): retry for LLM calls.
#
# High-traffic LLM services reject calls with rate-limit/timeout/5xx errors.
# The streaming path (the main chat flow) had NO retry, and the non-streaming
# path was hardcoded to 3 retries with no total-wait ceiling. This PR adds:
#   - LLM_MAX_RETRIES (env, default 5 retries after the first attempt)
#   - LLM_RETRY_MAX_WAIT_SECONDS (env, default 300s = 5 minutes) capping the
#     cumulative exponential backoff
#   - streaming retry until the first token is yielded (never after, to avoid
#     duplicating partial output)
#
# Test plan (exercised through the REAL LiteLLMCaller retry loops, real env
# reads and real asyncio.sleep in the harness; only the litellm boundary is
# mocked, since a provider 429 cannot be produced on demand):
# - Env-configured retry count: LLM_MAX_RETRIES=2 -> 3 attempts, then the
#   provider answer is returned.
# - Default retry count with no env: 6 attempts.
# - Wait budget: LLM_RETRY_MAX_WAIT_SECONDS=1.5 clamps the total sleep and
#   stops retrying before the count is exhausted.
# - Auth errors are never retried.
# - Streaming: rate limit at establishment is retried, then tokens flow
#   exactly once; a mid-stream failure after the first token surfaces the
#   domain error without retrying; stream_with_tools behaves the same.
# - Backend unit tests pass.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

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
    if [ "$1" -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

print_header "PR #920: LLM retry with configurable count and 5-minute backoff cap"
python "$SCRIPT_DIR/fixtures/pr920/harness.py"
print_result $? "retry policy harness (count from env, wait cap, streaming)"

print_header "Backend unit tests"
"$PROJECT_ROOT/test/run_tests.sh" backend >/dev/null 2>&1
print_result $? "test/run_tests.sh backend"

echo ""
echo "=========================================="
echo "Results: $PASSED passed, $FAILED failed"
echo "=========================================="
[ "$FAILED" -eq 0 ] && [ "$PASSED" -eq 2 ]