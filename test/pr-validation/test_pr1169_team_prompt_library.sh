#!/bin/bash
# Issue #1169: team-shared prompt library with group access control.
#
# Starts a real backend (DuckDB chat history, static groups) and the mock LLM,
# then drives the REST API and WebSocket as three identities and asserts, from
# the mock LLM's own request log, which system prompt each chat turn sent:
# privacy by default, group sharing (use only), ID probing, server-side
# resolution, exact-version pinning across publications, crafted frames,
# conflicting prompt selections, revocation and pin clearing (including a
# session that stays open across the clear), stale-revision refusals,
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
CHAT_HISTORY_DB_URL=duckdb:///$WORK/data/chat_history.db
APP_LOG_DIR=$WORK/logs
APP_CONFIG_DIR=$WORK/config
ENVFILE

echo "=== End-to-end: real backend + mock LLM ==="
python mocks/llm-mock/main.py > "$WORK/mock.log" 2>&1 &
MOCK_PID=$!
wait_for_health "http://127.0.0.1:$MOCK_LLM_PORT"
bash "$PROJECT_ROOT/agent_start.sh" --backend-only --env-file "$WORK/test.env" > "$WORK/backend.log" 2>&1 &
BACKEND_PID=$!
wait_for_health "http://127.0.0.1:$ATLAS_PORT/api"
python "$FIXTURES/library_e2e.py" "http://127.0.0.1:$ATLAS_PORT" "http://127.0.0.1:$MOCK_LLM_PORT"

echo "=== Prompt text never reaches the server logs ==="
if grep -rq "SYSTEM-PROMPT-V" "$WORK/logs" "$WORK/backend.log" 2>/dev/null; then
    echo "FAILED: prompt text found in server logs"
    exit 1
fi
echo "PASSED: no prompt text in server logs"

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
