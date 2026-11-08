#!/bin/bash
# Test script for PR #1185: steer retry hardening (issue #1184)
#
# Test plan:
# - A retry of a queued request id answers the remembered outcome after the run ends.
# - Draining leftovers forgets their request ids.
# - Local steer refusals carry steering:true and echo the request id.
# - The client expires retries by deadline and does not retry command_timeout.
# - Two real replicas on shared Redis still route steers (multi-replica harness).
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate

fail() { echo "FAILED: $*"; exit 1; }

echo "== registry and WebSocket steer tests"
(cd atlas && PYTHONPATH="$PROJECT_ROOT" python -m pytest -q -p no:cacheprovider \
    tests/test_steer_idempotency.py tests/test_run_addressing_table.py) || fail "backend tests"
echo "== client retry window"
(cd frontend && npx vitest run src/test/steer-retry.test.js) || fail "vitest"
echo "== multi-replica harness (two real replicas on shared Redis)"
bash test/multireplica_tests.sh || fail "multi-replica harness"
echo "PASSED: steer retry hardening"
