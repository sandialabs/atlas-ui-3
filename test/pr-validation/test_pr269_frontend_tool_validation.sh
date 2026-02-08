#!/bin/bash
# Test script for PR #269 - Frontend tool validation on config receive
#
# Covers:
# - Stale tool validation useEffect exists in ChatContext.jsx
# - Stale prompt validation useEffect exists in ChatContext.jsx
# - Stale active prompt validation exists in ChatContext.jsx
# - Stale marketplace server cleanup in MarketplaceContext.jsx
# - Frontend unit tests pass (including new validation tests)
# - Backend unit tests pass

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'
BOLD='\033[1m'

PASSED=0
FAILED=0

print_header() {
    echo ""
    echo -e "${BOLD}==========================================${NC}"
    echo -e "${BOLD}$1${NC}"
    echo -e "${BOLD}==========================================${NC}"
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

# Activate venv
source "$PROJECT_ROOT/.venv/bin/activate"

###############################################
print_header "Check 1: Tool validation useEffect in ChatContext.jsx"
###############################################

# Verify that ChatContext.jsx has the stale tool validation effect
TOOL_VALIDATION=$(grep -c "Removing stale tools from selection" "$PROJECT_ROOT/frontend/src/contexts/ChatContext.jsx" || true)
if [ "$TOOL_VALIDATION" -ge 1 ]; then
    print_result 0 "ChatContext has stale tool validation"
else
    print_result 1 "ChatContext missing stale tool validation"
fi

###############################################
print_header "Check 2: Prompt validation useEffect in ChatContext.jsx"
###############################################

PROMPT_VALIDATION=$(grep -c "Removing stale prompts from selection" "$PROJECT_ROOT/frontend/src/contexts/ChatContext.jsx" || true)
if [ "$PROMPT_VALIDATION" -ge 1 ]; then
    print_result 0 "ChatContext has stale prompt validation"
else
    print_result 1 "ChatContext missing stale prompt validation"
fi

###############################################
print_header "Check 3: Active prompt cleanup in ChatContext.jsx"
###############################################

ACTIVE_PROMPT=$(grep -c "Clearing stale active prompt" "$PROJECT_ROOT/frontend/src/contexts/ChatContext.jsx" || true)
if [ "$ACTIVE_PROMPT" -ge 1 ]; then
    print_result 0 "ChatContext has stale active prompt cleanup"
else
    print_result 1 "ChatContext missing stale active prompt cleanup"
fi

###############################################
print_header "Check 4: Marketplace stale server cleanup"
###############################################

MARKETPLACE_CLEANUP=$(grep -c "Removing stale marketplace server from selection" "$PROJECT_ROOT/frontend/src/contexts/MarketplaceContext.jsx" || true)
if [ "$MARKETPLACE_CLEANUP" -ge 1 ]; then
    print_result 0 "MarketplaceContext has stale server cleanup"
else
    print_result 1 "MarketplaceContext missing stale server cleanup"
fi

###############################################
print_header "Check 5: Validation test file exists"
###############################################

if [ -f "$PROJECT_ROOT/frontend/src/test/stale-selection-validation.test.js" ]; then
    print_result 0 "Stale selection validation test file exists"
else
    print_result 1 "Stale selection validation test file missing"
fi

###############################################
print_header "Check 6: Tool validation triggers on config.tools change"
###############################################

# Verify the useEffect depends on config.tools
TOOLS_DEPENDENCY=$(grep -A2 "Removing stale tools" "$PROJECT_ROOT/frontend/src/contexts/ChatContext.jsx" | grep -c "config.tools" || true)
# Check the eslint-disable comment before the dependency array
TOOLS_EFFECT=$(grep -c "\[config\.tools\]" "$PROJECT_ROOT/frontend/src/contexts/ChatContext.jsx" || true)
if [ "$TOOLS_EFFECT" -ge 1 ]; then
    print_result 0 "Tool validation effect depends on config.tools"
else
    print_result 1 "Tool validation effect missing config.tools dependency"
fi

###############################################
print_header "Check 7: Prompt validation triggers on config.prompts change"
###############################################

PROMPTS_EFFECT=$(grep -c "\[config\.prompts\]" "$PROJECT_ROOT/frontend/src/contexts/ChatContext.jsx" || true)
if [ "$PROMPTS_EFFECT" -ge 1 ]; then
    print_result 0 "Prompt validation effect depends on config.prompts"
else
    print_result 1 "Prompt validation effect missing config.prompts dependency"
fi

###############################################
print_header "Check 8: Frontend unit tests"
###############################################

cd "$PROJECT_ROOT/frontend"
npm test -- --run 2>&1
FRONTEND_TEST_EXIT=$?
print_result $FRONTEND_TEST_EXIT "Frontend unit tests"
cd "$PROJECT_ROOT"

###############################################
print_header "Check 9: Backend unit tests"
###############################################

"$PROJECT_ROOT/test/run_tests.sh" backend
BACKEND_TEST_EXIT=$?
print_result $BACKEND_TEST_EXIT "Backend unit tests"

###############################################
print_header "Summary"
###############################################

TOTAL=$((PASSED + FAILED))
echo ""
echo -e "Results: ${GREEN}$PASSED passed${NC}, ${RED}$FAILED failed${NC}, $TOTAL total"

if [ "$FAILED" -gt 0 ]; then
    echo -e "${RED}Some checks failed!${NC}"
    exit 1
fi

echo -e "${GREEN}All checks passed!${NC}"
exit 0
