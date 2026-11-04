#!/bin/bash
# Test script for PR #1160: per-replica coordination observability and
# failure exercises (issue #1129).
#
# 1. Unit tests for the metrics module, admin routes, live database probe and
#    shutdown interrupt.
# 2. The failure-injection scripts: syntax, --help, refusal without a
#    namespace, and --dry-run command plans against a stub kubectl/oc.
# 3. The multi-replica harness (this PR touches the run registry, service.py
#    and main.py), via the PR #1146 validation script: the whole suite must
#    pass with the expected #1128 gaps, including the new failure exercises
#    (graceful stop reported interrupted on the other replica, Redis and
#    PostgreSQL outages visible in health, /admin/coordination and [COORD]
#    logs, then recovered). That script also runs the backend suite.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
fail() { echo "FAILED: $*"; exit 1; }

echo "== lint"
ruff check atlas/core/coordination_metrics.py atlas/infrastructure/dependency_probes.py \
    atlas/infrastructure/replica_lifecycle.py atlas/routes/coordination_routes.py \
    atlas/tests/test_coordination_metrics.py atlas/tests/test_coordination_routes.py \
    test/multireplica || fail "ruff"

echo "== unit tests"
(cd atlas && PYTHONPATH="$PROJECT_ROOT" python -m pytest -q -p no:cacheprovider \
    tests/test_coordination_metrics.py tests/test_coordination_routes.py \
    tests/test_chat_history_startup_status.py) || fail "unit tests"

echo "== failure-injection scripts"
FI=scripts/failure-injection
for f in "$FI"/*.sh; do
    bash -n "$f" || fail "syntax: $f"
done
for f in delete-pod rollout dependency-outage; do
    "$FI/$f.sh" --help > /dev/null || fail "$f --help"
done
mkdir -p "$WORK/bin"
for tool in kubectl oc; do
    printf '#!/bin/bash\necho "%s $*" >> "%s/calls.log"\n' "$tool" "$WORK" > "$WORK/bin/$tool"
    chmod +x "$WORK/bin/$tool"
done
export PATH="$WORK/bin:$PATH"

if "$FI/delete-pod.sh" --dry-run > "$WORK/out.txt" 2>&1; then
    fail "delete-pod.sh ran without a namespace"
fi
grep -q "never guess a namespace" "$WORK/out.txt" || fail "missing-namespace message"

expect_plan() {  # expect_plan <pattern> <script args...>
    local pattern="$1"; shift
    "$@" > "$WORK/plan.txt" 2>&1 || { cat "$WORK/plan.txt"; fail "dry run: $*"; }
    grep -qE "$pattern" "$WORK/plan.txt" || { cat "$WORK/plan.txt"; fail "dry run of $* lacks: $pattern"; }
}
expect_plan "DRY-RUN: oc -n stg delete pod atlas-0 --grace-period=0 --force" \
    env KUBECTL=oc "$FI/delete-pod.sh" -n stg --pod atlas-0 --force --dry-run --out "$WORK/e1"
expect_plan "DRY-RUN: kubectl -n stg delete pod atlas-0 --wait=true" \
    "$FI/delete-pod.sh" -n stg --pod atlas-0 --dry-run --out "$WORK/e2"
expect_plan "DRY-RUN: kubectl -n stg rollout restart deploy/atlas-api" \
    "$FI/rollout.sh" -n stg --deployment deploy/atlas-api --dry-run --out "$WORK/e3"
expect_plan "DRY-RUN: kubectl -n stg scale statefulset/redis --replicas=0" \
    "$FI/dependency-outage.sh" -n stg --dependency redis --dry-run --out "$WORK/e4"
grep -q "scale statefulset/redis --replicas=1" "$WORK/plan.txt" || fail "outage plan does not restore Redis"
expect_plan "DRY-RUN: podman pause pg" \
    "$FI/dependency-outage.sh" --podman pg --dependency postgres --url http://127.0.0.1:1 --dry-run --out "$WORK/e5"
[ ! -s "$WORK/calls.log" ] || { cat "$WORK/calls.log"; fail "a dry run invoked kubectl/oc"; }
echo "PASSED: scripts parse, refuse without a namespace, and plan the expected commands"

echo "== multi-replica harness (+ backend suite)"
bash test/pr-validation/test_pr1146_multireplica_harness.sh || fail "multi-replica harness"
echo "PASSED: all checks"
