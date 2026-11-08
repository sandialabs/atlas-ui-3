#!/bin/bash
# Test script for the resolver multi-target PR (issue #1184)
#
# Test plan:
# - A conversation address resolves to every run a stop must reach (rows S19, S20, S31, S32).
# - The addressing table has no strict xfail left.
# - A steer retry is bound to the run it first targeted (rows C18 to C22).
# - Two real replicas on shared Redis: one stop frame reaches a finished run and its follow-up.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate

fail() { echo "FAILED: $*"; exit 1; }

echo "== addressing table (no xfail may remain)"
out=$(cd atlas && PYTHONPATH="$PROJECT_ROOT" python -m pytest -q -p no:cacheprovider \
    tests/test_run_addressing_table.py tests/test_websocket_run_directory_outage.py \
    tests/test_run_control_service.py 2>&1) || { echo "$out" | tail -20; fail "backend tests"; }
echo "$out" | tail -2
echo "$out" | grep -q "xfailed" && fail "an xfail is still present"
echo "== client retry binding"
(cd frontend && npx vitest run src/test/steer-retry.test.js) || fail "vitest"
echo "== multi-replica harness (two real replicas on shared Redis)"
bash test/multireplica_tests.sh || fail "multi-replica harness"
echo "PASSED: resolver returns every target of a conversation address"
