#!/bin/bash
# PR #990 Validation Script: Postgres chat history with SQLAlchemy 2.1 (issue #988)
# SQLAlchemy 2.1 made psycopg (v3) the default driver for postgresql:// URLs.
# Starts a real PostgreSQL container and the backend with the default
# DB_DRIVER=postgresql, and checks that chat history initializes and serves
# conversations instead of failing with "No module named 'psycopg'".

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=========================================="
echo "PR #990 Validation: Postgres Driver for SQLAlchemy 2.1"
echo "=========================================="

cd "$PROJECT_ROOT"

# Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "FAILED: Virtual environment not found at $PROJECT_ROOT/.venv"
    exit 1
fi

CLI="${CONTAINER_CLI:-}"
if [ -z "$CLI" ]; then
    if command -v docker > /dev/null 2>&1; then CLI=docker
    elif command -v podman > /dev/null 2>&1; then CLI=podman
    else
        echo "FAILED: docker or podman is required to run PostgreSQL"
        exit 1
    fi
fi
PG_CONTAINER="atlas-pr990-postgres"
PG_PORT=55432
PORT=8212
LOG=/tmp/pr990-backend.log
BACKEND_PID=""

cleanup() {
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true
    "$CLI" rm -f "$PG_CONTAINER" > /dev/null 2>&1 || true
}
trap cleanup EXIT

echo ""
echo "1. SQLAlchemy's default PostgreSQL driver"
echo "-----------------------------------------"
python -c 'import importlib.util as u, sqlalchemy; from sqlalchemy.engine.url import make_url; d = make_url("postgresql://u@h/d").get_dialect().driver; print(f"SQLAlchemy {sqlalchemy.__version__}: postgresql:// uses {d} (installed: {u.find_spec(d) is not None})")' \
    || { echo "FAILED: could not inspect SQLAlchemy"; exit 1; }

echo ""
echo "2. Start PostgreSQL"
echo "-------------------"
"$CLI" rm -f "$PG_CONTAINER" > /dev/null 2>&1 || true
"$CLI" run -d --name "$PG_CONTAINER" -p "127.0.0.1:$PG_PORT:5432" \
    -e POSTGRES_USER=atlas -e POSTGRES_PASSWORD=atlas -e POSTGRES_DB=atlas_chat_history \
    postgres:16-alpine > /dev/null || { echo "FAILED: could not start PostgreSQL"; exit 1; }
for i in $(seq 1 60); do
    "$CLI" exec "$PG_CONTAINER" pg_isready -U atlas -d atlas_chat_history > /dev/null 2>&1 && break
    sleep 1
done
"$CLI" exec "$PG_CONTAINER" pg_isready -U atlas -d atlas_chat_history > /dev/null 2>&1 \
    || { echo "FAILED: PostgreSQL did not become ready"; exit 1; }
echo "PASSED: PostgreSQL is ready"

echo ""
echo "3. Start the backend with the default DB_DRIVER"
echo "-----------------------------------------------"
if curl -sf "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
    echo "FAILED: Port $PORT is already in use by another process"
    exit 1
fi
(
    cd "$PROJECT_ROOT/atlas"
    exec env -u CHAT_HISTORY_DB_URL -u DB_DRIVER \
        PORT=$PORT ATLAS_HOST=127.0.0.1 DEBUG_MODE=true \
        FEATURE_CHAT_HISTORY_ENABLED=true \
        DB_HOST=127.0.0.1 DB_PORT=$PG_PORT DB_NAME=atlas_chat_history DB_USER=atlas DB_PASSWORD=atlas \
        MCP_TOKEN_ENCRYPTION_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
        python main.py
) > "$LOG" 2>&1 &
BACKEND_PID=$!
HEALTHY=0
for i in $(seq 1 60); do
    if curl -sf "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
        HEALTHY=1
        break
    fi
    sleep 1
done
if [ $HEALTHY -eq 1 ]; then
    echo "PASSED: backend is healthy"
else
    tail -30 "$LOG"
    echo "FAILED: backend did not become healthy (log: $LOG)"
    exit 1
fi

echo ""
echo "4. Check chat history on PostgreSQL"
echo "-----------------------------------"
if grep -q "Failed to initialize chat history" "$LOG"; then
    grep -A3 "Failed to initialize chat history" "$LOG" | head -5
    echo "FAILED: chat history did not initialize"
    exit 1
else
    echo "PASSED: no chat history startup error"
fi
TABLES="$("$CLI" exec "$PG_CONTAINER" psql -U atlas -d atlas_chat_history -tAc \
    "select count(*) from information_schema.tables where table_schema = 'public'")"
if [ "${TABLES:-0}" -gt 0 ]; then
    echo "PASSED: chat history created $TABLES tables in PostgreSQL"
else
    echo "FAILED: no chat history tables in PostgreSQL"
    exit 1
fi
CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/conversations")"
if [ "$CODE" = "200" ]; then
    echo "PASSED: GET /api/conversations returned 200"
else
    echo "FAILED: GET /api/conversations returned $CODE"
    exit 1
fi
cleanup
BACKEND_PID=""

echo ""
echo "5. Run backend unit tests"
echo "-------------------------"
cd "$PROJECT_ROOT"
bash ./test/run_tests.sh backend > /dev/null 2>&1 || bash ./test/run_tests.sh backend
echo "PASSED: Backend tests passed"

echo ""
echo "=========================================="
echo "All PR #990 validation checks PASSED"
echo "=========================================="
