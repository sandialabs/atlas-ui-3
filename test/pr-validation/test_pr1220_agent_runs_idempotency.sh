#!/bin/bash
# Test script for issue #1220: cluster-wide idempotency for the Agent Runs API.
#
# 1. Store unit tests (in-process and Redis implementations) and the REST API
#    tests, which drive POST /api/agent-runs and follow-up over HTTP with the
#    shared store bound.
# 2. The multi-replica harness case: the same key and body sent to replica A
#    then replica B launches one run and one conversation (two real API
#    processes, shared Redis and PostgreSQL). Requires podman, or
#    ATLAS_TEST_REDIS_URL / ATLAS_TEST_POSTGRES_URL.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

fail() { echo "FAILED: $*"; exit 1; }

echo "== idempotency store and REST API tests"
python -m pytest -q -p no:cacheprovider \
    atlas/tests/test_idempotency_store.py atlas/tests/test_agent_runs_api.py \
    || fail "unit/API tests"

echo "== multi-replica: same key on two replicas"
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT
bash test/multireplica_tests.sh test/multireplica/test_idempotency_across_replicas.py > "$LOG" 2>&1
RC=$?
tail -15 "$LOG"
[ "$RC" -eq 0 ] || fail "multi-replica idempotency tests (exit $RC)"
grep -qE "[0-9]+ passed" "$LOG" || fail "no passing multi-replica tests reported"
if grep -qE "[0-9]+ (failed|error|skipped|xpassed)" "$LOG"; then
    fail "unexpected outcomes in the multi-replica run"
fi

echo "PASSED: idempotency is cluster-wide with Redis and unchanged without it"
