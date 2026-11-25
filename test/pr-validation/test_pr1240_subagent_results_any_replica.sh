#!/bin/bash
# Test script for PR #1240: subagent run and result tools work from any replica (issue #1222)
#
# 1. Unit tests: the fallback from the local run registry to the shared run
#    records and the stored child conversations, the unchanged authorization,
#    and the lineage fields in the shared run record.
# 2. The multi-replica harness, subagent scenarios: replica A launches a child,
#    a parent turn on replica B reads it while it runs, after it finished, and
#    after its run record expired. Two real Atlas processes, one Redis, one
#    PostgreSQL, a scripted mock model.
# 3. Everything the harness started is gone.
# 4. The backend unit tests.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
MANIFEST="$WORK/manifest.jsonl"
: > "$MANIFEST"

fail() {
    echo "FAILED: $*"
    exit 1
}

echo "== shared-state fallback unit tests"
(cd atlas && PYTHONPATH="$PROJECT_ROOT" python -m pytest tests/test_shared_subagent_results.py \
    tests/test_conversation_lineage.py tests/test_atlas_launch_tool.py tests/test_redis_run_coordinator.py \
    -q -p no:cacheprovider) || fail "unit tests"
echo "PASSED: unit tests"

echo "== multi-replica harness: subagent scenarios"
LOG="$WORK/harness.log"
ATLAS_MULTIREPLICA_MANIFEST="$MANIFEST" bash test/multireplica_tests.sh -k subagent > "$LOG" 2>&1
RC=$?
grep -E "PASSED|FAILED|ERROR" "$LOG" || true
SUMMARY="$(grep -E '^=+ .* in [0-9.]+s.* =+$' "$LOG" | tail -1)"
echo "summary: ${SUMMARY:-<none>} (exit $RC)"
PASSED_COUNT="$(grep -oE "[0-9]+ passed" <<< "$SUMMARY" | head -1 | cut -d' ' -f1)"
[ "$RC" -eq 0 ] || { tail -40 "$LOG"; fail "harness exit code $RC"; }
[ "${PASSED_COUNT:-0}" -ge 4 ] || fail "expected at least 4 passing subagent scenarios, saw '${PASSED_COUNT:-0}'"
grep -qE "failed|error" <<< "$SUMMARY" && fail "harness reported failures: $SUMMARY"
echo "PASSED: ${PASSED_COUNT} subagent scenarios pass across two replicas"

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

echo "== backend unit tests"
PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" ./test/run_tests.sh backend || fail "backend unit tests"
echo "PASSED: all checks"
