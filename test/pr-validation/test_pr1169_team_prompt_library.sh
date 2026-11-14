#!/bin/bash
# Issue #1169: team-shared prompt library with group access control.
#
# Starts a real backend (DuckDB chat history, then -- with
# ATLAS_TEST_POSTGRES_URL -- a fresh PostgreSQL database; static groups) and the mock LLM,
# then drives the REST API and WebSocket as three identities and asserts, from
# the mock LLM's own request log, which system prompt each chat turn sent:
# privacy by default, group sharing (use only), ID probing, server-side
# resolution, exact-version pinning across publications, crafted frames,
# conflicting prompt selections, revocation and pin clearing (including a
# session that stays open across the clear), stale-revision refusals,
# owner recovery of damaged pin state (PostgreSQL run),
# save-then-publish against a concurrent save, forks and audit. Then runs the targeted unit/integration tests
# (the PostgreSQL ones too when ATLAS_TEST_POSTGRES_URL is set), the frontend
# library tests, and the backend suite.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
source .venv/bin/activate

if pgrep -f '[u]vicorn main:app' > /dev/null; then
    echo "Refusing to restart an existing backend; stop it before running this validation."
    exit 1
fi

WORK="$(mktemp -d)"
FIXTURES="$PROJECT_ROOT/test/pr-validation/fixtures/pr1169"
BACKEND_PID=""
MOCK_PID=""
cleanup() {
    if [ -n "$BACKEND_PID" ]; then
        pkill -P "$BACKEND_PID" 2>/dev/null || true
        kill "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
    fi
    if [ -n "$MOCK_PID" ]; then
        kill "$MOCK_PID" 2>/dev/null || true
        wait "$MOCK_PID" 2>/dev/null || true
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT

free_port() {
    python - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}
wait_for_health() {
    for _ in $(seq 1 90); do
        curl -sf "$1/health" > /dev/null && return 0
        sleep 1
    done
    cat "$WORK"/*.log
    return 1
}

export MOCK_LLM_PORT="$(free_port)"
export ATLAS_PORT="$(free_port)"
mkdir -p "$WORK/config" "$WORK/data" "$WORK/logs"
sed "s/__MOCK_LLM_PORT__/$MOCK_LLM_PORT/" "$FIXTURES/llmconfig.yml" > "$WORK/config/llmconfig.yml"
echo '{}' > "$WORK/config/mcp.json"
export MCP_TOKEN_ENCRYPTION_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
# AUTH_STATIC_GROUPS is quoted: agent_start.sh sources the env file, and an
# unquoted ';' would end the assignment there.
write_env() {  # $1 = chat history database URL, $2 = log dir
    mkdir -p "$2"
    cat > "$WORK/test.env" <<ENVFILE
PORT=$ATLAS_PORT
DEBUG_MODE=true
ENVIRONMENT=development
FEATURE_CHAT_HISTORY_ENABLED=true
FEATURE_CUSTOM_PROMPTS_ENABLED=true
FEATURE_WORKSPACES_ENABLED=true
FEATURE_TOOLS_ENABLED=false
FEATURE_RAG_ENABLED=false
USE_MOCK_S3=true
AUTH_STATIC_GROUPS="eng:owner@example.com,member@example.com;ops:owner@example.com"
CHAT_HISTORY_DB_URL=$1
APP_LOG_DIR=$2
APP_CONFIG_DIR=$WORK/config
ENVFILE
}

stop_backend() {
    if [ -n "$BACKEND_PID" ]; then
        pkill -P "$BACKEND_PID" 2>/dev/null || true
        kill "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
        BACKEND_PID=""
    fi
    # The next run reuses the port: never let it talk to this server.
    for _ in $(seq 1 30); do
        curl -sf "http://127.0.0.1:$ATLAS_PORT/api/health" > /dev/null 2>&1 || return 0
        pkill -f "[u]vicorn main:app" 2>/dev/null || true
        sleep 1
    done
    echo "FAILED: the previous backend did not stop"
    exit 1
}

# $1 = label, $2 = database URL for the server, $3 = URL the e2e may write to
# directly (PostgreSQL only; empty for DuckDB, whose file the server locks).
run_e2e() {
    echo "=== End-to-end ($1): real backend + mock LLM ==="
    write_env "$2" "$WORK/logs-$1"
    if [ -z "$3" ]; then
        bash "$PROJECT_ROOT/agent_start.sh" --backend-only --env-file "$WORK/test.env" > "$WORK/backend-$1.log" 2>&1 &
    else
        # agent_start.sh would start its bundled PostgreSQL container for a
        # loopback URL; this run uses the operator-managed database instead,
        # so it starts uvicorn exactly as agent_start.sh's start_backend does.
        # The schema comes from the server's own startup migration.
        (
            set -a; . "$WORK/test.env"; set +a
            export ATLAS_ENV_FILE="$WORK/test.env" MCP_EXTERNAL_API_TOKEN="${MCP_EXTERNAL_API_TOKEN:-test-api-key-123}"
            cd "$PROJECT_ROOT/atlas"
            exec "$PROJECT_ROOT/.venv/bin/uvicorn" main:app --host 127.0.0.1 --port "$ATLAS_PORT" --no-use-colors
        ) > "$WORK/backend-$1.log" 2>&1 &
    fi
    BACKEND_PID=$!
    wait_for_health "http://127.0.0.1:$ATLAS_PORT/api"
    python "$FIXTURES/library_e2e.py" "http://127.0.0.1:$ATLAS_PORT" "http://127.0.0.1:$MOCK_LLM_PORT" "$3"
    stop_backend
    echo "=== Prompt text never reaches the server logs ($1) ==="
    if grep -rq "SYSTEM-PROMPT-V" "$WORK/logs-$1" "$WORK/backend-$1.log" 2>/dev/null; then
        echo "FAILED: prompt text found in server logs"
        exit 1
    fi
    echo "PASSED: no prompt text in server logs ($1)"
}

python mocks/llm-mock/main.py > "$WORK/mock.log" 2>&1 &
MOCK_PID=$!
wait_for_health "http://127.0.0.1:$MOCK_LLM_PORT"

run_e2e duckdb "duckdb:///$WORK/data/chat_history.db" ""

if [ -n "${ATLAS_TEST_POSTGRES_URL:-}" ]; then
    # A fresh database: the server's own startup migration creates the schema.
    E2E_PG_URL="$(python - <<'PY'
import os, uuid
from sqlalchemy import create_engine, make_url, text
base = os.environ["ATLAS_TEST_POSTGRES_URL"]
name = f"atlas_e2e_{uuid.uuid4().hex[:10]}"
admin = create_engine(base, isolation_level="AUTOCOMMIT")
with admin.connect() as conn:
    conn.execute(text(f'CREATE DATABASE "{name}"'))
admin.dispose()
print(make_url(base).set(database=name).render_as_string(hide_password=False))
PY
)"
    drop_e2e_db() {
        E2E_PG_URL="$E2E_PG_URL" python - <<'PY' || true
import os
from sqlalchemy import create_engine, make_url, text
url = make_url(os.environ["E2E_PG_URL"])
admin = create_engine(os.environ["ATLAS_TEST_POSTGRES_URL"], isolation_level="AUTOCOMMIT")
with admin.connect() as conn:
    conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
admin.dispose()
PY
    }
    trap 'drop_e2e_db; cleanup' EXIT
    run_e2e postgresql "$E2E_PG_URL" "$E2E_PG_URL"
else
    echo "NOTE: ATLAS_TEST_POSTGRES_URL not set; the PostgreSQL end-to-end run (and its recovery checks) was skipped."
fi

echo "=== Targeted tests (authorization, routes, chat turn, migration, legacy prompts) ==="
python -m pytest -q -p no:warnings \
    atlas/tests/test_prompt_library_access.py \
    atlas/tests/test_prompt_library_routes.py \
    atlas/tests/test_prompt_library_chat_turn.py \
    atlas/tests/test_prompt_library_migration.py \
    atlas/tests/test_prompt_library_concurrency.py \
    atlas/tests/test_prompt_library_storage.py \
    atlas/tests/test_conversation_prompt_pin.py \
    atlas/tests/test_prompt_library_bounds.py \
    atlas/tests/test_prompt_library_archived.py \
    atlas/tests/test_prompt_library_listing_columns.py \
    atlas/tests/test_prompt_pin_conflict_frames.py \
    atlas/tests/test_prompt_pin_recovery.py \
    atlas/tests/test_prompt_library_listing_bounds.py \
    atlas/tests/test_run_in_flight_conversation.py \
    atlas/tests/test_user_prompts.py \
    atlas/tests/test_schema_migration.py
if [ -z "${ATLAS_TEST_POSTGRES_URL:-}" ]; then
    echo "NOTE: ATLAS_TEST_POSTGRES_URL not set; the PostgreSQL migration and concurrency tests were skipped."
fi

echo "=== Frontend library tests ==="
(cd frontend && npx vitest run \
    src/test/prompt-library-selection.test.js \
    src/test/prompt-library-chat-send.test.jsx \
    src/test/prompt-library-panel.test.jsx \
    src/test/prompt-library-history-sharing.test.jsx \
    src/test/prompt-selector.test.jsx \
    src/test/prompt-library-panel-lifecycle.test.jsx \
    src/test/prompt-selector-library-states.test.jsx \
    src/test/useSelections.test.js > "$WORK/vitest.log" 2>&1) \
    || { cat "$WORK/vitest.log"; exit 1; }
tail -n 4 "$WORK/vitest.log"

echo "=== Backend suite ==="
DEBUG_MODE=true "$PROJECT_ROOT/test/run_tests.sh" backend
echo "PASSED: team prompt library validation"
