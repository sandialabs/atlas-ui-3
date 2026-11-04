#!/bin/bash
# Test script for PR #1161: atomic cluster-wide run admission through Redis (issue #1128)
#
# 1. The coordinator's Lua scripts and admit_run's failed-admission cleanup,
#    under fakeredis's Lua engine.
# 2. The admission scenarios end to end on two real replicas sharing podman
#    Redis + PostgreSQL: a busy conversation on A refused on B (WebSocket),
#    the per-user cap and racing launches across replicas, no slot leaked by
#    refusals, and the configured cap on a REST-only replica (the #1146 bug).
#    Every selected test must pass outright: none may be xfail any more. (The
#    cross-replica cap passing also proves the app factory bound the Redis
#    coordinator: without it each replica counts only its own runs.)
#
# The full harness, with its EXPECTED_GAPS accounting, is
# test_pr1146_multireplica_harness.sh.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate

fail() {
    echo "FAILED: $*"
    exit 1
}

echo "== unit: Redis run coordinator + admission cleanup"
(cd atlas && PYTHONPATH="$PROJECT_ROOT" python -m pytest -q -p no:cacheprovider \
    tests/test_redis_run_coordinator.py tests/test_run_admission.py) || fail "unit tests"

echo "== multi-replica: admission scenarios"
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT
bash test/multireplica_tests.sh -k \
    "test_cluster_admission or per_user_cap or concurrent_admission or configured_cap" \
    > "$LOG" 2>&1
RC=$?
grep -E "PASSED|FAILED|XFAIL|XPASS|ERROR" "$LOG" || true
SUMMARY="$(grep -E '^=+ .* in [0-9.]+s.* =+$' "$LOG" | tail -1)"
echo "summary: ${SUMMARY:-<none>} (exit $RC)"
[ "$RC" -eq 0 ] || { tail -40 "$LOG"; fail "harness exit $RC"; }
grep -qE "\b5 passed\b" <<< "$SUMMARY" || fail "expected exactly 5 admission tests to pass: $SUMMARY"
grep -qE "xfail|xpass|skipped|failed|error" <<< "$SUMMARY" && fail "unexpected outcome: $SUMMARY"

echo "PASSED: cluster-wide admission holds across replicas and failed admissions leak nothing"
