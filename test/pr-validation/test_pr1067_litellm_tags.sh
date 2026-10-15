#!/bin/bash
# Test script for PR #1067: LiteLLM request tags (x-litellm-tags header)
#
# Test plan:
# - Stand up an OpenAI-compatible model endpoint that records request headers
# - Drive the real LiteLLMCaller (real LiteLLM SDK over HTTP) with a fixture
#   llmconfig.yml and assert the x-litellm-tags header reaches the endpoint
# - Cover: tag list, comma-separated string, no tags, explicit extra_headers
#   precedence, tags combined with x-litellm-customer-id, and the streaming path
# - Run the backend unit test suite

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES_DIR="$SCRIPT_DIR/fixtures/pr1067"

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

cd "$PROJECT_ROOT"
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT"

# --- End-to-end header checks ----------------------------------------------
print_header "PR #1067: x-litellm-tags reaches a LiteLLM proxy request"
python "$FIXTURES_DIR/litellm_tags.py"
print_result $? "Tags header sent on every call path (fixture output above)"

# --- Unit tests -------------------------------------------------------------
print_header "Backend unit test suite"
env -u APP_CONFIG_DIR -u CAPABILITY_TOKEN_SECRET -u DEBUG_MODE -u SKIP_AUTHORIZATION_CHECKS \
    ./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests (./test/run_tests.sh backend)"

print_header "SUMMARY"
echo -e "Passed: ${GREEN}$PASSED${NC} | Failed: ${RED}$FAILED${NC}"
[ $FAILED -eq 0 ] && exit 0 || exit 1
