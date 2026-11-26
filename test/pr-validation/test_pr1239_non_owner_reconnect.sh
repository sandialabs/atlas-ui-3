#!/bin/bash
# Test script for PR #1239: a client on a replica that does not own a run
# (issue #1219).
#
# Runs the two-replica harness cases for the non-owner view and the watched
# run list (real Atlas processes sharing podman-managed Redis and Postgres,
# including a killed owner), then the backend and frontend unit tests that
# cover the same behaviour.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

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

echo "== two replicas: conversation read and run list from the non-owner"
bash test/multireplica_tests.sh -k non_owner_reconnect > "$WORK/harness.log" 2>&1
RC=$?
grep -E "PASSED|FAILED|passed|failed" "$WORK/harness.log" | tail -6
print_result "$RC" "non-owner GET shows a running view; watched list reports the end and an owner kill"

PYTHONPATH="$PROJECT_ROOT" python -m pytest \
    atlas/tests/test_run_elsewhere_view.py atlas/tests/test_run_in_flight_conversation.py -q > /dev/null 2>&1
print_result $? "non-owner view and snapshot unit tests"

(cd frontend && npx vitest run src/test/live-conversation-refresh.test.jsx > /dev/null 2>&1)
print_result $? "frontend resync retry and remote run end"

PYTHONPATH="$PROJECT_ROOT" PROJECT_ROOT="$PROJECT_ROOT" bash test/atlas_tests.sh > /dev/null 2>&1
print_result $? "backend suite"

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ $FAILED -eq 0 ] && exit 0 || exit 1
