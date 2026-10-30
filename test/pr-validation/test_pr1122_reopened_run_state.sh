#!/bin/bash
# Issue #1122: reopened conversations reconstruct the shared execution snapshot.
# Exercises two Redis clients plus the approval-timeout path. Does not start
# the full backend suite.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

print_result() {
    if [ "$1" -eq 0 ]; then
        echo "PASSED: $2"
        PASSED=$((PASSED + 1))
    else
        echo "FAILED: $2"
        FAILED=$((FAILED + 1))
    fi
}

cd "$PROJECT_ROOT"

if [ -x .venv/bin/python ]; then
    PY=".venv/bin/python"
elif [ -x ../atlas-ui-3/.venv/bin/python ]; then
    PY="../atlas-ui-3/.venv/bin/python"
else
    PY="python3"
fi

"$PY" test/pr-validation/fixtures/pr1122/scenario.py
print_result $? "shared projection and approval settlement"

"$PY" -m pytest -q \
    atlas/tests/test_execution_state.py \
    atlas/tests/test_reopened_run_approval.py \
    atlas/tests/test_approval_manager.py \
    atlas/tests/test_tool_approval_cleanup.py
print_result $? "execution-state unit tests"

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
