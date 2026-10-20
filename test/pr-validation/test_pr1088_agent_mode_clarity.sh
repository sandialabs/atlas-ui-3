#!/bin/bash
# Test script for PR #1088: Agent Mode clarity and run visibility.
#
# Covers, in a real browser against real backends (fixtures/pr1088/ui_agent_mode.mjs):
#   1. Agent Mode on with no tools selected shows the expanded callout with the
#      explanation, the tool-capable model line, "Choose tools" (opens Settings
#      on the Tools tab), and "Turn off Agent Mode".
#   2. The header "What Agent Mode does" popover opens, closes on Escape, and
#      returns focus.
#   3. At 390x600 the callout title, "Choose tools", message box, and Send stay
#      on screen.
#   4. "Turn off Agent Mode" removes the callout and flips the header toggle.
#   5. "Reduce helper text" hides the help button and long copy, keeps actions.
#   6. A running agent shows the footer run status with its step count, it stays
#      visible with the transcript scrolled to the top, and its Stop ends the run.
#   7. With FEATURE_TOOLS_ENABLED=false the callout and popover offer no
#      "Choose tools" and say tools are disabled.
#   8. Frontend unit tests for the new components.
#
# The browser driver boots the scripted tool-calling mock model from
# fixtures/pr1074 and two backends on free ports; nothing on the path is
# stubbed. Set PR1088_SCREENSHOT_DIR to keep screenshots of each state.
#
# Prerequisites: `cd test_e2e && npm install && npx playwright install
# chromium`, and the frontend built (`cd frontend && npm run build`; the
# backend serves atlas/static when present, else frontend/dist).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASSED=0
FAILED=0

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"; PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"; FAILED=$((FAILED + 1))
    fi
}
print_header() { echo ""; echo "=========================================="; echo "$1"; echo "=========================================="; }

cd "$PROJECT_ROOT" || exit 1
source .venv/bin/activate

print_header "1-7. Agent Mode UI in a real browser"
UI_OUT="$(node test/pr-validation/fixtures/pr1088/ui_agent_mode.mjs 2>&1)"
UI_RC=$?
echo "$UI_OUT" | grep -E '^(PASS|FAIL|ALL PASSED)|Error' | head -40
print_result $UI_RC "browser scenario"

print_header "8. Frontend unit tests for Agent Mode components"
FE_OUT="$(cd frontend && npx vitest run src/test/agent-mode-help.test.jsx src/test/agent-mode-no-tools-banner.test.jsx \
    src/test/agent-run-status.test.jsx src/test/header-agent-mode-help.test.jsx \
    src/test/settings-panel-agent-mode.test.jsx 2>&1)"
FE_RC=$?
echo "$FE_OUT" | grep -E 'Test Files|Tests ' | tail -2
print_result $FE_RC "frontend unit tests"

print_header "Summary"
echo "Passed: $PASSED  Failed: $FAILED"
[ "$FAILED" -eq 0 ]
