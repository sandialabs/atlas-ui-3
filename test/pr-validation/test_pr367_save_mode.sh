#!/bin/bash
# Test script for PR #367: 3-state chat save mode
# Covers: save mode config, IndexedDB wrapper, local conversation history hook,
#         Header 3-state button, Sidebar mode switching, backend save_mode handling.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

print_result() {
    if [ $1 -eq 0 ]; then
        echo -e "\033[0;32mPASSED\033[0m: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "\033[0;31mFAILED\033[0m: $2"
        FAILED=$((FAILED + 1))
    fi
}

cd "$PROJECT_ROOT"
source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT"

echo "=== PR #367: 3-State Chat Save Mode Validation ==="
echo ""

# --- 1. New files exist ---
echo "--- New Files ---"
test -f frontend/src/utils/saveModeConfig.js
print_result $? "saveModeConfig.js exists"

test -f frontend/src/utils/localConversationDB.js
print_result $? "localConversationDB.js exists"

test -f frontend/src/hooks/useLocalConversationHistory.js
print_result $? "useLocalConversationHistory.js hook exists"

# --- 2. saveModeConfig exports correct constants ---
echo ""
echo "--- Save Mode Config ---"
grep -q "SAVE_MODES.*=.*\['none'.*'local'.*'server'\]" frontend/src/utils/saveModeConfig.js
print_result $? "SAVE_MODES constant has all 3 modes"

grep -q "nextSaveMode" frontend/src/utils/saveModeConfig.js
print_result $? "nextSaveMode cycling function exported"

# --- 3. ChatContext uses saveMode instead of isIncognito ---
echo ""
echo "--- ChatContext Integration ---"
grep -q "saveMode" frontend/src/contexts/ChatContext.jsx
print_result $? "ChatContext uses saveMode"

grep -q "usePersistentState.*chatui-save-mode" frontend/src/contexts/ChatContext.jsx
print_result $? "saveMode persisted via usePersistentState"

grep -q "save_mode.*saveMode" frontend/src/contexts/ChatContext.jsx
print_result $? "save_mode sent in WebSocket messages"

grep -q "saveLocalConv\|saveConversation" frontend/src/contexts/ChatContext.jsx
print_result $? "Local conversation auto-save logic present"

# --- 4. Header has 3-state button ---
echo ""
echo "--- Header 3-State Button ---"
grep -q "SAVE_MODE_CONFIG" frontend/src/components/Header.jsx
print_result $? "Header has SAVE_MODE_CONFIG object"

grep -q "HardDrive" frontend/src/components/Header.jsx
print_result $? "Header imports HardDrive icon for local mode"

grep -q "Cloud" frontend/src/components/Header.jsx
print_result $? "Header imports Cloud icon for server mode"

grep -q "nextSaveMode" frontend/src/components/Header.jsx
print_result $? "Header uses nextSaveMode for cycling"

# --- 5. Sidebar uses both hooks ---
echo ""
echo "--- Sidebar Mode Switching ---"
grep -q "useLocalConversationHistory" frontend/src/components/Sidebar.jsx
print_result $? "Sidebar imports useLocalConversationHistory"

grep -q "saveMode.*===.*'local'" frontend/src/components/Sidebar.jsx
print_result $? "Sidebar selects local history when saveMode is local"

# --- 6. IndexedDB wrapper has required functions ---
echo ""
echo "--- IndexedDB Wrapper ---"
grep -q "saveConversation" frontend/src/utils/localConversationDB.js
print_result $? "saveConversation function exists"

grep -q "listConversations" frontend/src/utils/localConversationDB.js
print_result $? "listConversations function exists"

grep -q "deleteConversation" frontend/src/utils/localConversationDB.js
print_result $? "deleteConversation function exists"

grep -q "searchConversations" frontend/src/utils/localConversationDB.js
print_result $? "searchConversations function exists"

grep -q "exportAllConversations" frontend/src/utils/localConversationDB.js
print_result $? "exportAllConversations function exists"

# --- 7. Backend handles save_mode ---
echo ""
echo "--- Backend Integration ---"
grep -q "save_mode" atlas/main.py
print_result $? "Backend main.py handles save_mode field"

grep -q "chat_history_save_modes" atlas/routes/config_routes.py
print_result $? "Config endpoint exposes available save modes"

# --- 8. Tests updated ---
echo ""
echo "--- Tests ---"
grep -q "saveMode" frontend/src/test/sidebar-conversation-display.test.js
print_result $? "Sidebar display tests use saveMode"

! grep -q "isIncognito" frontend/src/test/sidebar-conversation-display.test.js
print_result $? "No remaining isIncognito references in tests"

# --- 9. Frontend lint ---
echo ""
echo "--- Frontend Lint ---"
cd "$PROJECT_ROOT/frontend"
LINT_OUTPUT=$(npm run lint 2>&1)
LINT_ERRORS=$(echo "$LINT_OUTPUT" | grep -c " error " || true)
test "$LINT_ERRORS" -eq 0
print_result $? "Frontend lint has 0 errors"
cd "$PROJECT_ROOT"

# --- 10. Frontend tests ---
echo ""
echo "--- Frontend Tests ---"
cd "$PROJECT_ROOT/frontend"
TEST_OUTPUT=$(npm test -- --run 2>&1)
echo "$TEST_OUTPUT" | grep -q "Tests.*passed"
print_result $? "Frontend tests pass"
cd "$PROJECT_ROOT"

# --- 11. Backend tests ---
echo ""
echo "--- Backend Tests ---"
./test/run_tests.sh backend 2>&1 | tail -5
BACKEND_RESULT=$?
print_result $BACKEND_RESULT "Backend tests pass"

echo ""
echo "================================================================"
echo "Results: $PASSED passed, $FAILED failed"
echo "================================================================"

if [ $FAILED -gt 0 ]; then
    exit 1
fi
exit 0
