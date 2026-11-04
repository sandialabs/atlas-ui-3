#!/bin/bash
# Test script for issue #1129: reproducible multi-replica staging deployment
# (Kubernetes/OpenShift overlay, lock-serialized schema migrations).
#
# 1. Offline render checks of deploy/kubernetes (both flavors).
# 2. The migration runner against a throwaway PostgreSQL: concurrent runs
#    apply the migration exactly once; a create_all database is refused; an
#    unreachable database fails after the wait.
# 3. The podman rehearsal of the staging overlay with a real image: three API
#    containers, concurrent migrate init containers, the manifest's startup
#    probe, then validate_replicas.py (health, auth, shared PostgreSQL,
#    session resumed across all replicas) and a failover drill (SIGTERM under
#    a live socket, resume on another replica).
# 4. Unit tests for the runner and the image recipe, then the backend suite.
#
# Needs podman and kubectl (rendering only; no cluster is contacted).
# ATLAS_IMAGE=<image> reuses a built image; otherwise this builds one (slow).
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PYTHON_DOTENV_DISABLED=1

PASSED=0
FAILED=0
print_result() {
    if [ "$1" -eq 0 ]; then echo "PASSED: $2"; PASSED=$((PASSED + 1)); else echo "FAILED: $2"; FAILED=$((FAILED + 1)); fi
}

WORK="$(mktemp -d)"
PG_NAME="pr1129-pg-$$"
cleanup() {
    podman rm -f -t 0 "$PG_NAME" >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT

echo "== 1. render checks"
bash test/k8s_render_checks.sh > "$WORK/render.log" 2>&1
RC=$?; tail -n 1 "$WORK/render.log"
grep FAILED "$WORK/render.log"
print_result $RC "Kubernetes/OpenShift staging manifests render and pass every check"

echo "== 2. migration runner against PostgreSQL"
PG_PORT="$(python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
podman run -d --name "$PG_NAME" -e POSTGRES_USER=atlas -e POSTGRES_PASSWORD=pr1129 -e POSTGRES_DB=atlas \
    --tmpfs /var/lib/postgresql/data --tmpfs /var/run/postgresql -p "127.0.0.1:$PG_PORT:5432" \
    docker.io/library/postgres:16-alpine > /dev/null
for _ in $(seq 1 60); do podman exec "$PG_NAME" pg_isready -U atlas -d atlas >/dev/null 2>&1 && break; sleep 1; done
sleep 2
podman exec "$PG_NAME" psql -U atlas -d atlas -qc "CREATE DATABASE concurrent" -c "CREATE DATABASE legacy" >/dev/null
URL="postgresql://atlas:pr1129@127.0.0.1:$PG_PORT"

for i in 1 2 3 4 5 6; do
    ( CHAT_HISTORY_DB_URL="$URL/concurrent" python -m atlas.modules.chat_history.schema_migration \
        > "$WORK/mig-$i.log" 2>&1; echo $? > "$WORK/mig-$i.rc" ) &
done
wait
RCS="$(cat "$WORK"/mig-*.rc | sort -u | tr '\n' ' ')"
UPGRADED="$(grep -l "Upgrading chat-history schema" "$WORK"/mig-*.log | wc -l)"
NOOP="$(grep -l "already at head" "$WORK"/mig-*.log | wc -l)"
HEAD="$(podman exec "$PG_NAME" psql -U atlas -d concurrent -tAc 'SELECT version_num FROM alembic_version')"
echo "exit codes: $RCS upgraded=$UPGRADED no-op=$NOOP alembic_version=$HEAD"
[ "$RCS" = "0 " ] && [ "$UPGRADED" -eq 1 ] && [ "$NOOP" -eq 5 ] && [ -n "$HEAD" ]
print_result $? "6 concurrent runners: exactly one applies the migration, five find head"

CHAT_HISTORY_DB_URL="$URL/concurrent" alembic check > "$WORK/check.log" 2>&1
grep -q "No new upgrade operations detected" "$WORK/check.log"
print_result $? "Alembic head matches the models (alembic check)"

python -c "from atlas.modules.chat_history.database import init_database; init_database('$URL/legacy')" >/dev/null 2>&1
CHAT_HISTORY_DB_URL="$URL/legacy" python -m atlas.modules.chat_history.schema_migration > "$WORK/legacy.log" 2>&1
RC=$?
[ $RC -eq 2 ] && grep -q "no alembic_version table" "$WORK/legacy.log"
print_result $? "a database created by create_all is refused with instructions (exit $RC)"

CHAT_HISTORY_DB_URL="postgresql://atlas:x@127.0.0.1:1/none" python -m atlas.modules.chat_history.schema_migration \
    --wait-seconds 2 > "$WORK/down.log" 2>&1
RC=$?
[ $RC -eq 1 ] && grep -q "Database unreachable after" "$WORK/down.log" && ! grep -q ":x@" "$WORK/down.log"
print_result $? "an unreachable database fails after the wait without logging the password (exit $RC)"

echo "== 3. podman rehearsal of the staging overlay"
IMAGE="${ATLAS_IMAGE:-}"
if [ -z "$IMAGE" ]; then
    IMAGE="localhost/atlas-ui-3:pr-validation-1129"
    echo "building $IMAGE (set ATLAS_IMAGE to reuse an image)"
    podman build -q -t "$IMAGE" . > "$WORK/build.log" 2>&1 || { tail -n 30 "$WORK/build.log"; }
fi
podman run --rm --entrypoint sh "$IMAGE" -c 'test -f /app/alembic.ini && test -d /app/alembic/versions && alembic --help >/dev/null'
print_result $? "image ships alembic.ini, the migrations and the alembic CLI"

python deploy/kubernetes/podman_rehearsal.py --image "$IMAGE" > "$WORK/rehearsal.log" 2>&1
RC=$?
grep -E "^(PASS|FAIL)|passed, " "$WORK/rehearsal.log"
[ $RC -eq 0 ] || tail -n 40 "$WORK/rehearsal.log"
print_result $RC "3-replica rehearsal: migrations, startup probe, validation, failover drill"

echo "== 4. unit tests"
(cd atlas && python -m pytest -q -p no:cacheprovider tests/test_schema_migration.py tests/test_container_security.py) \
    > "$WORK/unit.log" 2>&1
RC=$?
tail -n 1 "$WORK/unit.log"
print_result $RC "schema migration and container recipe unit tests"

# The pytest suite only: run_tests.sh also starts live-LLM E2E tests whenever
# an API key is in the environment, which need a configured model.
bash test/atlas_tests.sh > "$WORK/backend.log" 2>&1
RC=$?
grep -E "^=+ .*(passed|failed).* =+$" "$WORK/backend.log" | tail -n 1
print_result $RC "backend test suite"

echo
echo "Summary: $PASSED passed, $FAILED failed"
[ "$FAILED" -eq 0 ]
