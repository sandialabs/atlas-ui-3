#!/bin/bash
# Test script for PR #1163: cross-replica stop, steer and approval (issue #1128)
#
# 1. Unit tests for the Redis Streams transport and the owner-side handlers
#    (two replicas, each a run coordinator and a bus, on one fakeredis),
#    including generation fencing against the #1161 run record.
# 2. The routing tests on two real Atlas processes sharing podman Redis and
#    PostgreSQL: a run on replica A stopped (WebSocket and REST), steered and
#    approved through replica B, with duplicate and retried commands, another
#    user's commands and a frozen owner. Each must PASS, and the side-effect
#    log must show the approval-gated tool ran exactly once.
# 3. The full multi-replica harness with its gap accounting (PR #1146 script;
#    "stop sent to a non-owner" is no longer a gap), which also runs the
#    backend suite.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

fail() {
    echo "FAILED: $*"
    exit 1
}

echo "== run-control unit tests"
(cd atlas && python -m pytest -q -p no:cacheprovider tests/test_run_control_bus.py \
    tests/test_run_control_service.py tests/test_reopened_run_approval.py \
    tests/test_redis_run_coordinator.py) || fail "unit tests"
echo "PASSED: transport and handler unit tests"

echo "== routing on two replicas"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
LOG="$WORK/routing.log"
bash test/multireplica_tests.sh -k "run_control_routing or stop_sent_to_non_owner" > "$LOG" 2>&1
RC=$?
grep -E "::test_.*(PASSED|FAILED|ERROR|XPASS|XFAIL)" "$LOG"
for name in \
    test_websocket_stop_via_non_owner_and_other_users_are_refused \
    test_repeated_cancel_via_non_owner_is_idempotent \
    test_command_to_a_frozen_owner_times_out_and_is_not_applied_late \
    test_steer_via_non_owner_reaches_the_loop_once \
    test_approval_via_non_owner_runs_the_tool_once \
    test_stop_sent_to_non_owner_reaches_the_owner; do
    grep -qE "::$name PASSED" "$LOG" || { tail -60 "$LOG"; fail "$name did not pass"; }
done
[ "$RC" -eq 0 ] || { tail -40 "$LOG"; fail "routing run exited $RC"; }
echo "PASSED: stop, steer and approval reach the owner through another replica"

echo "== full harness and backend suite"
bash test/pr-validation/test_pr1146_multireplica_harness.sh || fail "multi-replica harness"
echo "PASSED: all checks"
