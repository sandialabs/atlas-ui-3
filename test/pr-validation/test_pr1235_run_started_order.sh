#!/bin/bash
# Test script for PR #1235 (issue #1216): run_started reaches the client before
# any run_status for the run, even when the session store yields (Redis).
#
# 1. The ordering tests drive the real /ws frame loop with a session store that
#    yields on every read, and check the run's task still starts only after
#    the run session is seeded.
# 2. The multi-replica harness (two real Atlas processes on shared Redis and
#    PostgreSQL), required for changes to the run handling in atlas/main.py.
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

echo "== frame order with a yielding session store"
(cd atlas && python -m pytest -q -p no:cacheprovider \
    tests/test_run_started_precedes_status.py \
    tests/test_websocket_admission_guard.py) || fail "ordering tests"
echo "PASSED: run_started precedes run_status; the task starts after seeding"

echo "== multi-replica harness"
bash test/multireplica_tests.sh || fail "multi-replica harness"
echo "PASSED: all checks"
