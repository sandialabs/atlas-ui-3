#!/bin/bash
# Test script for issue #1220: replica-independent Agent Runs API.
#
# 1. Idempotency, API, shared status, and coordination unit tests.
# 2. Full multi-replica acceptance gate (real Redis/PostgreSQL), including
#    follow-up, retained listing, stale admission, and dependency outages.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

fail() { echo "FAILED: $*"; exit 1; }

echo "== Agent Runs API and coordination unit tests"
python -m pytest -q -p no:cacheprovider \
    atlas/tests/test_idempotency_store.py atlas/tests/test_agent_runs_api.py \
    atlas/tests/test_agent_runs_shared_status.py atlas/tests/test_redis_run_coordinator.py \
    atlas/tests/test_run_admission.py atlas/tests/test_run_leases.py \
    || fail "unit/API tests"

echo "== full multi-replica acceptance gate"
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT
bash test/multireplica_tests.sh > "$LOG" 2>&1
RC=$?
tail -15 "$LOG"
[ "$RC" -eq 0 ] || fail "multi-replica tests (exit $RC)"
grep -qE "[0-9]+ passed" "$LOG" || fail "no passing multi-replica tests reported"
if grep -qE "[0-9]+ (failed|errors?|skipped|xfailed|xpassed)" "$LOG"; then
    fail "unexpected outcomes in the multi-replica run"
fi

echo "PASSED: Agent Runs API is replica-independent with Redis"
