#!/bin/bash
# Test script for PR #511: Prevent cross-user tool approval bypass (F-03)
#
# Test plan:
# - Verify cross-user approval is rejected
# - Verify cross-user rejection is rejected
# - Verify cross-user argument injection is rejected
# - Verify same-user approval works
# - Verify request remains pending after failed cross-user attempt
# - Verify legacy requests (no user_email) still resolve
# - Backend approval manager test suite passes

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
FIXTURES_DIR="$SCRIPT_DIR/fixtures/pr511"

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

print_header "PR #511: Cross-User Tool Approval Prevention Tests"

# Activate virtual environment
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# Load fixture
set -a
source "$FIXTURES_DIR/.env"
set +a

# ==========================================
# 1. Cross-user approval is rejected
# ==========================================
print_header "1. Cross-user approval is rejected"

RESULT=$(python3 -c "
import logging; logging.disable(logging.CRITICAL)
from atlas.application.chat.approval_manager import ToolApprovalManager

mgr = ToolApprovalManager()
mgr.create_approval_request('tc-1', 'dangerous_tool', {'arg': 'val'}, user_email='$USER_A')
result = mgr.handle_approval_response('tc-1', approved=True, user_email='$USER_B')
print(result)
" 2>&1 | tail -1)

[ "$RESULT" = "False" ]
print_result $? "Cross-user approval returns False (got: $RESULT)"

# ==========================================
# 2. Cross-user rejection is rejected
# ==========================================
print_header "2. Cross-user rejection is rejected"

RESULT=$(python3 -c "
import logging; logging.disable(logging.CRITICAL)
from atlas.application.chat.approval_manager import ToolApprovalManager

mgr = ToolApprovalManager()
mgr.create_approval_request('tc-2', 'dangerous_tool', {'arg': 'val'}, user_email='$USER_A')
result = mgr.handle_approval_response('tc-2', approved=False, reason='nope', user_email='$USER_B')
print(result)
" 2>&1 | tail -1)

[ "$RESULT" = "False" ]
print_result $? "Cross-user rejection returns False (got: $RESULT)"

# ==========================================
# 3. Cross-user argument injection is rejected
# ==========================================
print_header "3. Cross-user argument injection is rejected"

RESULT=$(python3 -c "
import logging; logging.disable(logging.CRITICAL)
from atlas.application.chat.approval_manager import ToolApprovalManager

mgr = ToolApprovalManager()
mgr.create_approval_request('tc-3', 'dangerous_tool', {'arg': 'safe'}, user_email='$USER_A')
result = mgr.handle_approval_response('tc-3', approved=True, arguments={'arg': 'INJECTED'}, user_email='$USER_B')
print(result)
" 2>&1 | tail -1)

[ "$RESULT" = "False" ]
print_result $? "Cross-user argument injection returns False (got: $RESULT)"

# ==========================================
# 4. Same-user approval works
# ==========================================
print_header "4. Same-user approval works"

RESULT=$(python3 -c "
import logging; logging.disable(logging.CRITICAL)
from atlas.application.chat.approval_manager import ToolApprovalManager

mgr = ToolApprovalManager()
mgr.create_approval_request('tc-4', 'safe_tool', {'arg': 'val'}, user_email='$USER_A')
result = mgr.handle_approval_response('tc-4', approved=True, user_email='$USER_A')
print(result)
" 2>&1 | tail -1)

[ "$RESULT" = "True" ]
print_result $? "Same-user approval returns True (got: $RESULT)"

# ==========================================
# 5. Request remains pending after failed cross-user attempt
# ==========================================
print_header "5. Request remains pending after cross-user failure"

RESULT=$(python3 -c "
import logging; logging.disable(logging.CRITICAL)
from atlas.application.chat.approval_manager import ToolApprovalManager

mgr = ToolApprovalManager()
mgr.create_approval_request('tc-5', 'tool', {'a': '1'}, user_email='$USER_A')

# Cross-user attempt should fail
mgr.handle_approval_response('tc-5', approved=True, user_email='$USER_B')

# Original user should still be able to respond
result = mgr.handle_approval_response('tc-5', approved=True, user_email='$USER_A')
print(result)
" 2>&1 | tail -1)

[ "$RESULT" = "True" ]
print_result $? "Request still resolvable by owner after cross-user attempt (got: $RESULT)"

# ==========================================
# 6a. Empty user_email cannot bypass ownership check
# ==========================================
print_header "6a. Empty user_email response blocked on bound request"

RESULT=$(python3 -c "
import logging; logging.disable(logging.CRITICAL)
from atlas.application.chat.approval_manager import ToolApprovalManager

mgr = ToolApprovalManager()
mgr.create_approval_request('tc-6a', 'dangerous_tool', {'arg': 'val'}, user_email='$USER_A')
# Attacker sends empty user_email; fail-closed must reject.
result = mgr.handle_approval_response('tc-6a', approved=True, user_email='')
print(result)
" 2>&1 | tail -1)

[ "$RESULT" = "False" ]
print_result $? "Empty user_email cannot bypass owned request (got: $RESULT)"

# ==========================================
# 6b. Legacy requests (no user_email) still resolve
# ==========================================
print_header "6b. Legacy backward compatibility"

RESULT=$(python3 -c "
import logging; logging.disable(logging.CRITICAL)
from atlas.application.chat.approval_manager import ToolApprovalManager

mgr = ToolApprovalManager()
# No user_email on request (legacy)
mgr.create_approval_request('tc-6', 'tool', {'a': '1'})
result = mgr.handle_approval_response('tc-6', approved=True, user_email='anyone@example.com')
print(result)
" 2>&1 | tail -1)

[ "$RESULT" = "True" ]
print_result $? "Legacy request without user_email resolves normally (got: $RESULT)"

# ==========================================
# 7. Targeted test suite
# ==========================================
print_header "7. Targeted test suite"

cd "$ATLAS_DIR"
python3 -m pytest tests/test_approval_manager.py -x -q 2>&1
print_result $? "test_approval_manager.py passes (all 26 tests)"

# ==========================================
# Summary
# ==========================================
print_header "SUMMARY"
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo ""

if [ $FAILED -gt 0 ]; then
    echo -e "${RED}SOME TESTS FAILED${NC}"
    exit 1
else
    echo -e "${GREEN}ALL TESTS PASSED${NC}"
    exit 0
fi
