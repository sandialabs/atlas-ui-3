#!/bin/bash
# Test script for PR #544: Default session.context['conversation_id'] to session_id
#
# Test plan:
# - Verify first message with no client-sent conversation_id defaults to str(session_id)
# - Verify explicit client conversation_id still wins over the default
# - Verify existing session.context['conversation_id'] is not clobbered
# - Verify the regression test suite for this PR passes
# - Verify the session-manager test suite still passes (no downstream regressions)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"

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

print_header "PR #544: MCP conversation_id default Tests"

# Activate virtual environment
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# ==========================================
# 1. Default-when-missing
# ==========================================
print_header "1. First message with no client conversation_id defaults to str(session_id)"

cd "$ATLAS_DIR" && python3 -m pytest \
    tests/test_chat_service_conversation_id_default.py::test_first_message_defaults_conversation_id_to_session_id \
    -v --tb=short 2>&1
print_result $? "Default conversation_id = str(session_id) when client omits it"

# ==========================================
# 2. Explicit client id wins
# ==========================================
print_header "2. Explicit conversation_id from client wins over default"

cd "$ATLAS_DIR" && python3 -m pytest \
    tests/test_chat_service_conversation_id_default.py::test_explicit_conversation_id_wins_over_default \
    -v --tb=short 2>&1
print_result $? "Client-supplied conversation_id is preserved"

# ==========================================
# 3. Do not clobber existing context value
# ==========================================
print_header "3. Existing session.context['conversation_id'] is not overwritten"

cd "$ATLAS_DIR" && python3 -m pytest \
    tests/test_chat_service_conversation_id_default.py::test_default_does_not_overwrite_existing_context_value \
    -v --tb=short 2>&1
print_result $? "Pre-existing conversation_id in session.context is preserved"

# ==========================================
# 4. Full PR regression suite
# ==========================================
print_header "4. PR #544 regression suite"

cd "$ATLAS_DIR" && python3 -m pytest \
    tests/test_chat_service_conversation_id_default.py \
    -v --tb=short 2>&1
print_result $? "All 3 PR-544 regression tests pass"

# ==========================================
# 5. MCP session manager suite (no downstream regression)
# ==========================================
print_header "5. MCP session manager suite still green"

cd "$ATLAS_DIR" && python3 -m pytest \
    tests/test_session_manager.py \
    -q --tb=short 2>&1
print_result $? "No regressions in MCPSessionManager tests"

# ==========================================
# 6. Fix is actually wired (source-level sanity check)
# ==========================================
print_header "6. Source-level sanity check for the fix"

grep -nE "^\s*elif\s+\"conversation_id\"\s+not in session\.context:" \
    "$ATLAS_DIR/application/chat/service.py" >/dev/null
print_result $? "handle_chat_message contains the default-conversation_id branch"

grep -nE "session\.context\[\"conversation_id\"\]\s*=\s*str\(session_id\)" \
    "$ATLAS_DIR/application/chat/service.py" >/dev/null
print_result $? "Default value is str(session_id)"

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
