#!/bin/bash
# Test script for PR #1167: run admission follow-ups from the #1161 review (issue #1128)
#
# 1. Fencing generations keep increasing across a Redis flush (the claim
#    script floors the counter at Redis time), under fakeredis's Lua engine.
# 2. Graceful shutdown releases the claims of live runs at once.
# 3. An unexpected admission refusal on the real /ws loop refuses the turn
#    instead of running it untracked.
# 4. The #1161 admission scenarios on two real replicas still pass.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate

fail() {
    echo "FAILED: $*"
    exit 1
}

echo "== unit: generation floor, shutdown release, WebSocket refusal"
(cd atlas && PYTHONPATH="$PROJECT_ROOT" python -m pytest -q -p no:cacheprovider \
    "tests/test_redis_run_coordinator.py::test_generations_keep_increasing_across_a_redis_flush" \
    "tests/test_run_admission.py::test_shutdown_releases_the_claims_of_live_runs" \
    "tests/test_websocket_admission_guard.py::test_any_other_admission_refusal_refuses_the_turn") \
    || fail "unit tests"

echo "== #1161 admission validation (unit + multi-replica)"
bash test/pr-validation/test_pr1161_redis_run_admission.sh || fail "#1161 admission scenarios"

echo "PASSED: PR #1167 validation"
