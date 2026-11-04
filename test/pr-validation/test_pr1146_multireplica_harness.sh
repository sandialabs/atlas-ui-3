#!/bin/bash
# Test script for PR #1146: multi-replica harness for distributed run coordination (issue #1128)
#
# Runs the harness end to end -- two real Atlas API processes sharing
# podman-managed Redis and PostgreSQL -- and checks its outcome from the parsed
# pytest counts: no failures, errors or XPASSes, at least one pass, and exactly
# EXPECTED_GAPS strict expected failures. Then checks that every process group
# and container this run started is gone, that the normal backend suite does
# not collect the harness, and runs that suite.
#
# When a PR closes a #1128 gap (and removes its xfail marker), lower
# EXPECTED_GAPS in the same PR.
set -uo pipefail

EXPECTED_GAPS=0   # #1128 closed admission, inspection, owner loss, fencing, stop routing and the cap bug

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
LOG="$WORK/harness.log"
MANIFEST="$WORK/manifest.jsonl"
: > "$MANIFEST"

fail() {
    echo "FAILED: $*"
    exit 1
}

echo "== multi-replica harness"
ATLAS_MULTIREPLICA_MANIFEST="$MANIFEST" bash test/multireplica_tests.sh > "$LOG" 2>&1
HARNESS_RC=$?
grep -E "PASSED|FAILED|XFAIL|XPASS|ERROR" "$LOG" || true
SUMMARY="$(grep -E '^=+ .* in [0-9.]+s.* =+$' "$LOG" | tail -1)"
echo "summary: ${SUMMARY:-<none>} (exit $HARNESS_RC)"

count() {  # count <word>: the number pytest reported for "<n> <word>", or 0
    local n
    n="$(grep -oE "[0-9]+ $1\b" <<< "$SUMMARY" | head -1 | cut -d' ' -f1)"
    echo "${n:-0}"
}
PASSED=$(count passed); FAILED=$(count failed); ERRORS=$(( $(count error) + $(count errors) ))
XFAILED=$(count xfailed); XPASSED=$(count xpassed); SKIPPED=$(count skipped)

problems=()
[ -n "$SUMMARY" ] || problems+=("no pytest summary line")
[ "$FAILED" -eq 0 ] || problems+=("failed=$FAILED, expected 0")
[ "$ERRORS" -eq 0 ] || problems+=("errors=$ERRORS, expected 0")
[ "$XPASSED" -eq 0 ] || problems+=("xpassed=$XPASSED, expected 0 (a gap closed: remove its marker and lower EXPECTED_GAPS)")
[ "$SKIPPED" -eq 0 ] || problems+=("skipped=$SKIPPED, expected 0")
[ "$PASSED" -gt 0 ] || problems+=("passed=$PASSED, expected > 0")
[ "$XFAILED" -eq "$EXPECTED_GAPS" ] || problems+=("xfailed=$XFAILED, expected $EXPECTED_GAPS")
# Counts are authoritative; a non-zero exit with clean counts is still wrong.
[ "$HARNESS_RC" -eq 0 ] || problems+=("harness exit code $HARNESS_RC, expected 0")
if [ ${#problems[@]} -gt 0 ]; then
    echo "observed: passed=$PASSED failed=$FAILED errors=$ERRORS xfailed=$XFAILED xpassed=$XPASSED skipped=$SKIPPED"
    printf '  - %s\n' "${problems[@]}"
    echo "---- last 40 lines of harness output ----"
    tail -40 "$LOG"
    fail "unexpected harness outcome"
fi
echo "PASSED: $PASSED baseline/known-good tests pass and all $EXPECTED_GAPS gaps are strict expected failures"

echo "== everything this run started is gone"
python - "$MANIFEST" <<'PY' || fail "harness left processes or containers behind"
import json, os, shutil, subprocess, sys

records = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
groups = [r for r in records if r["kind"] == "process"]
containers = [r["name"] for r in records if r["kind"] == "container"]
if not groups:
    sys.exit("manifest is empty: the harness did not record what it started")
left = []
for r in groups:
    try:
        os.killpg(r["pgid"], 0)
    except ProcessLookupError:
        continue
    except PermissionError:
        pass
    left.append(f"{r['role']} process group {r['pgid']}")
podman = shutil.which("podman")
for name in containers:
    if podman and subprocess.run([podman, "container", "exists", name]).returncode == 0:
        left.append(f"container {name}")
print(f"checked {len(groups)} process group(s) and {len(containers)} container(s)")
if left:
    sys.exit("still present: " + ", ".join(left))
PY
echo "PASSED: every recorded process group and container was cleaned up"

echo "== the backend suite does not collect the harness"
COLLECT="$WORK/collect.txt"
(cd atlas && PYTHONPATH="$PROJECT_ROOT" python -m pytest --collect-only -q tests > "$COLLECT" 2>&1)
COLLECT_RC=$?
if [ "$COLLECT_RC" -ne 0 ]; then
    tail -20 "$COLLECT"
    fail "pytest --collect-only on atlas/tests exited $COLLECT_RC"
fi
if grep -q multireplica "$COLLECT"; then
    grep multireplica "$COLLECT" | head -5
    fail "atlas/tests collects multireplica tests"
fi
echo "PASSED: test/atlas_tests.sh is unaffected"

echo "== backend unit tests"
PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" ./test/run_tests.sh backend || fail "backend unit tests"
echo "PASSED: all checks"
