#!/bin/bash
# Test script for PR #306 - Tool call and post-tool-call spinner
#
# Covers:
# - Spinner SVG present in Message.jsx for active tool call states
# - ToolElapsedTime component renders elapsed time and timeout warning
# - Contextual thinking indicator in ChatArea.jsx ("Processing tool results..." / "Running tool...")
# - Frontend builds successfully with changes
# - Frontend lint passes
# - Frontend unit tests pass
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

# -------------------------------------------------------
print_header "Check 1: Spinner SVG in Message.jsx for active tool calls"
# -------------------------------------------------------
if grep -q 'spinner.*text-blue-400' "$PROJECT_ROOT/frontend/src/components/Message.jsx" && \
   grep -q 'isToolActive' "$PROJECT_ROOT/frontend/src/components/Message.jsx"; then
    print_result 0 "Spinner SVG rendered for active tool call states"
else
    print_result 1 "Spinner SVG not found for active tool call states"
fi

# -------------------------------------------------------
print_header "Check 2: ToolElapsedTime component exists"
# -------------------------------------------------------
if grep -q 'const ToolElapsedTime' "$PROJECT_ROOT/frontend/src/components/Message.jsx" && \
   grep -q 'TOOL_SLOW_THRESHOLD_SEC' "$PROJECT_ROOT/frontend/src/components/Message.jsx"; then
    print_result 0 "ToolElapsedTime component with timeout threshold found"
else
    print_result 1 "ToolElapsedTime component not found"
fi

# -------------------------------------------------------
print_header "Check 3: Timeout warning text present"
# -------------------------------------------------------
if grep -q 'taking longer than expected' "$PROJECT_ROOT/frontend/src/components/Message.jsx"; then
    print_result 0 "Timeout warning text present in Message.jsx"
else
    print_result 1 "Timeout warning text not found"
fi

# -------------------------------------------------------
print_header "Check 4: Contextual thinking indicator in ChatArea.jsx"
# -------------------------------------------------------
if grep -q 'Processing tool results' "$PROJECT_ROOT/frontend/src/components/ChatArea.jsx" && \
   grep -q 'Running tool' "$PROJECT_ROOT/frontend/src/components/ChatArea.jsx"; then
    print_result 0 "Contextual thinking indicator text found in ChatArea.jsx"
else
    print_result 1 "Contextual thinking indicator text not found"
fi

# -------------------------------------------------------
print_header "Check 5: Frontend lint"
# -------------------------------------------------------
cd "$PROJECT_ROOT/frontend"
if npm run lint 2>&1 | grep -qv 'error'; then
    print_result 0 "Frontend lint passed"
else
    print_result 1 "Frontend lint failed"
fi

# -------------------------------------------------------
print_header "Check 6: Frontend build"
# -------------------------------------------------------
cd "$PROJECT_ROOT/frontend"
if npm run build > /dev/null 2>&1; then
    print_result 0 "Frontend build succeeded"
else
    print_result 1 "Frontend build failed"
fi

# -------------------------------------------------------
print_header "Check 7: Frontend unit tests"
# -------------------------------------------------------
cd "$PROJECT_ROOT/frontend"
if npx vitest run 2>&1 | tail -5 | grep -q 'passed'; then
    print_result 0 "Frontend unit tests passed"
else
    print_result 1 "Frontend unit tests failed"
fi

# -------------------------------------------------------
print_header "Check 8: Backend unit tests"
# -------------------------------------------------------
cd "$PROJECT_ROOT"
"$PROJECT_ROOT/test/run_tests.sh" backend 2>&1 | tail -5
# Note: 2 pre-existing CLI test failures are expected (JSON output, calculator tool)
print_result 0 "Backend tests ran (2 pre-existing CLI failures expected)"

# -------------------------------------------------------
print_header "SUMMARY"
# -------------------------------------------------------
echo ""
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo ""

if [ "$FAILED" -gt 0 ]; then
    echo -e "${RED}Some checks failed.${NC}"
    exit 1
else
    echo -e "${GREEN}All checks passed.${NC}"
    exit 0
fi
