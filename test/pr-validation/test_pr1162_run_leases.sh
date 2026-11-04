#!/bin/bash
# Test script for PR #1162: run ownership leases, heartbeat and fencing (issue #1128)
#
# 1. Unit tests for the lease coordinator (two coordinators on one fake Redis).
# 2. The lease-specific multi-replica tests, by name, against two real Atlas
#    processes on podman Redis + Postgres: SIGKILL -> interrupted, SIGSTOP ->
#    fenced, heartbeats keep a long run alive, no side effect replayed. Each
#    must PASS (not xfail).
# 3. The whole harness through the #1146 gate script, which checks the counts
#    (EXPECTED_GAPS: 1, stop routing; #1161 lowered it to 4, this PR to 1), cleanup, and the backend suite.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail() {
    echo "FAILED: $*"
    exit 1
}

echo "== lease coordinator unit tests"
(cd atlas && python -m pytest tests/test_run_leases.py -q -p no:cacheprovider) || fail "unit tests"
echo "PASSED: unit tests"

echo "== lease behavior across real replicas"
REQUIRED=(
    "test_run_coordination_gaps.py::test_owner_loss_is_reported_as_interrupted"
    "test_run_coordination_gaps.py::test_paused_owner_cannot_write_after_being_declared_lost"
    "test_run_coordination_gaps.py::test_non_owner_can_inspect_an_active_run"
    "test_run_leases.py::test_heartbeat_keeps_a_run_longer_than_its_lease"
    "test_run_leases.py::test_killed_owner_keeps_completed_output_and_bumps_the_generation"
    "test_run_leases.py::test_resumed_owner_is_fenced_out_of_shared_state"
    "test_harness_baseline.py::test_crashed_owner_never_replays_a_side_effect"
)
SELECTED=()
for test in "${REQUIRED[@]}"; do SELECTED+=("test/multireplica/$test"); done
LOG="$WORK/leases.log"
bash test/multireplica_tests.sh "${SELECTED[@]}" > "$LOG" 2>&1
RC=$?
for test in "${REQUIRED[@]}"; do
    if grep -qE "${test} PASSED" "$LOG"; then
        echo "PASSED: $test"
    else
        tail -60 "$LOG"
        fail "$test did not pass (harness exit $RC)"
    fi
done
[ "$RC" -eq 0 ] || { tail -40 "$LOG"; fail "harness exit code $RC"; }

echo "== full harness gate (counts, cleanup, backend suite)"
bash test/pr-validation/test_pr1146_multireplica_harness.sh || fail "multi-replica gate"
echo "PASSED: all checks"
