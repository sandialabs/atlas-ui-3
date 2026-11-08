#!/bin/bash
# Test script for the route-complete run record and one retention window (issue #1128)
#
# Test plan:
# - The run record carries source, parent_run_id and follow_up_of from claim time.
# - A finished parent's record outlives its live descendants and starts its
#   retention window when the last one ends (fakeredis, real Lua scripts).
# - Rows S09 and R07 of the run addressing table pass; the other GAP rows stay xfail.
# - One retention constant is defined in atlas/ (outside tests).
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate

fail() { echo "FAILED: $*"; exit 1; }

echo "== one retention definition"
count=$(grep -rn "^RUN_RETENTION_SECONDS" atlas --include=*.py | grep -v tests | wc -l)
[ "$count" -eq 1 ] || fail "expected one RUN_RETENTION_SECONDS definition, found $count"
echo "PASSED: single definition"

echo "== record fields, subtree retention, addressing table"
(cd atlas && PYTHONPATH="$PROJECT_ROOT" python -m pytest -q -p no:cacheprovider \
    tests/test_redis_run_coordinator.py tests/test_run_addressing_table.py \
    tests/test_run_leases.py tests/test_run_control_service.py) || fail "unit tests"
echo "PASSED: route-complete run record"
