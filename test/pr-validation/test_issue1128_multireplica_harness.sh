#!/bin/bash
# Issue #1128 (PR 0): multi-replica test harness.
#
# Runs the harness end to end -- two real Atlas API processes sharing
# podman-managed Redis and PostgreSQL -- and checks its outcome: every baseline
# test passes and every known gap is a strict expected failure. Then checks the
# harness is not collected by the normal backend suite, and runs that suite.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
source .venv/bin/activate

LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT

echo "== multi-replica harness"
bash test/multireplica_tests.sh 2>&1 | tee "$LOG" | grep -E "PASSED|FAILED|XFAIL|XPASS|ERROR" || true
SUMMARY="$(grep -E "^=+ .*(passed|failed).* =+$" "$LOG" | tail -1)"
echo "$SUMMARY"
# "7 xfailed" must not count as a failure: match the counts as whole words.
if ! grep -qE "5 passed, 7 xfailed" <<< "$SUMMARY" || grep -qE "[0-9]+ (failed|errors?|xpassed)\b" <<< "$SUMMARY"; then
    echo "FAILED: unexpected harness outcome"
    exit 1
fi
echo "PASSED: baseline passes and every gap is a strict expected failure"

if podman ps -a --format '{{.Names}}' | grep -q '^atlas-mr-'; then
    echo "FAILED: harness left containers behind"
    exit 1
fi
if pgrep -f "uvicorn atlas.main:app" > /dev/null; then
    echo "FAILED: harness left replica processes behind"
    exit 1
fi
echo "PASSED: containers and replica processes cleaned up"

echo "== the backend suite does not collect the harness"
(cd atlas && PYTHONPATH="$PROJECT_ROOT" python -m pytest --collect-only -q tests 2>/dev/null | grep -c multireplica) && {
    echo "FAILED: atlas/tests collects multireplica tests"; exit 1; } || true
echo "PASSED: test/atlas_tests.sh is unaffected"

echo "== backend unit tests"
./test/run_tests.sh backend
