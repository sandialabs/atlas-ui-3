#!/bin/bash
# Test script for PR #1082: parent-child conversations in the history sidebar
# (issue #1081).
#
# Covers the PR's test plan at the API boundary, with a real backend and a real
# DuckDB history store:
#   1. A child conversation's saved metadata publishes its parent id in the
#      listing and in the full record.
#   2. Search results publish the same durable link.
#   3. A child whose parent belongs to another user gets no parent id and no
#      parent metadata -- it reads as an ordinary top-level conversation.
#   4. Deleting the parent leaves the child readable and top-level instead of
#      dangling or disappearing.
#   5. The stored relationship is server-side conversation metadata, so it is
#      still there before and after a backend process restart.
#   6. Backend unit suite.
#
# The frontend tree itself is covered by the Vitest suites added in the PR; this
# script exercises the API data those tests consume.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASSED=0
FAILED=0

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"; PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"; FAILED=$((FAILED + 1))
    fi
}
print_header() { echo ""; echo "=========================================="; echo "$1"; echo "=========================================="; }

free_port() { python - <<'PY'
import socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()
PY
}

cd "$PROJECT_ROOT" || exit 1
source .venv/bin/activate

WORK="$(mktemp -d)"
ATLAS_PORT="$(free_port)"
DB_PATH="$WORK/data/chat_history.db"
BACKEND_PID=""
trap 'kill "$BACKEND_PID" 2>/dev/null; rm -rf "$WORK"' EXIT
mkdir -p "$WORK/config" "$WORK/data" "$WORK/logs"
printf '{}\n' > "$WORK/config/mcp.json"
printf '{}\n' > "$WORK/config/rag-sources.json"

seed_conversations() {
    CHAT_HISTORY_DB_URL="duckdb:///$DB_PATH" PYTHONPATH="$PROJECT_ROOT" python - <<'PY'
from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database

init_database(__import__("os").environ["CHAT_HISTORY_DB_URL"])
repo = ConversationRepository(get_session_factory())

def save(conversation_id, user, parent_id=None):
    metadata = {"parent_conversation_id": parent_id} if parent_id else None
    repo.save_conversation(
        conversation_id=conversation_id,
        user_email=user,
        title=conversation_id,
        model="gpt-4o",
        messages=[
            {"role": "user", "content": f"{conversation_id} prompt", "message_type": "chat"},
            {"role": "assistant", "content": f"{conversation_id} answer", "message_type": "chat"},
        ],
        metadata=metadata,
    )

save("val-parent", "test@test.com")
save("val-child", "test@test.com", parent_id="val-parent")
save("val-foreign-parent", "other@test.com")
save("val-foreign-child", "test@test.com", parent_id="val-foreign-parent")
save("val-independent", "test@test.com")
print("seeded")
PY
}

start_backend() {
    (
      cd "$PROJECT_ROOT/atlas" && \
      DEBUG_MODE=true FEATURE_CHAT_HISTORY_ENABLED=true FEATURE_TOOLS_ENABLED=false \
      USE_MOCK_S3=true CHAT_HISTORY_DB_URL="duckdb:///$DB_PATH" \
      APP_LOG_DIR="$WORK/logs" APP_CONFIG_DIR="$WORK/config" PYTHONPATH="$PROJECT_ROOT" \
      python -m uvicorn main:app --host 127.0.0.1 --port "$ATLAS_PORT" > "$WORK/backend.log" 2>&1
    ) &
    BACKEND_PID=$!
    for _ in $(seq 1 60); do
        curl -sf "http://127.0.0.1:$ATLAS_PORT/api/health" > /dev/null 2>&1 && return 0
        sleep 1
    done
    return 1
}

api() {
    local path="$1"
    shift
    curl -sS -H "X-User-Email: test@test.com" "http://127.0.0.1:$ATLAS_PORT$path" "$@"
}

# Assert with Python so a failed check prints the payload that failed.
check_json() {
    local label="$1" payload="$2" expr="$3"
    local out
    out="$(PAYLOAD="$payload" EXPR="$expr" python - <<'PY'
import json, os, sys
try:
    data = json.loads(os.environ["PAYLOAD"])
    ok = eval(os.environ["EXPR"], {"d": data})
except Exception as exc:  # pragma: no cover - diagnostic path
    print(f"error: {exc}")
    ok = False
print("ok" if ok else "BAD: " + json.dumps(data)[:800])
sys.exit(0 if ok else 1)
PY
)"
    echo "$out"
    [ "$out" = "ok" ]
    print_result $? "$label"
}

print_header "1. Seed two owners and start a real backend on :$ATLAS_PORT"
seed_conversations
print_result $? "temporary history store seeded"
start_backend
print_result $? "backend is up"

print_header "2. The listing exposes an owned parent and hides a foreign one"
LIST="$(api "/api/conversations?limit=20")"
check_json "saved child publishes its parent" "$LIST" \
  "next(c for c in d['conversations'] if c['id']=='val-child')['parent_conversation_id']=='val-parent'"
check_json "foreign parent is not disclosed" "$LIST" \
  "next(c for c in d['conversations'] if c['id']=='val-foreign-child')['parent_conversation_id'] is None"
check_json "independent conversation stays top-level" "$LIST" \
  "next(c for c in d['conversations'] if c['id']=='val-independent')['parent_conversation_id'] is None"

print_header "3. Search and the full record carry the same durable link"
SEARCH="$(api "/api/conversations/search?q=val-child")"
check_json "search publishes the parent" "$SEARCH" \
  "d['conversations'][0]['parent_conversation_id']=='val-parent'"
FULL="$(api "/api/conversations/val-child")"
check_json "full record publishes the parent" "$FULL" \
  "d['parent_conversation_id']=='val-parent' and d['metadata']['parent_conversation_id']=='val-parent'"
FOREIGN_FULL="$(api "/api/conversations/val-foreign-child")"
check_json "full record strips the foreign parent from metadata" "$FOREIGN_FULL" \
  "d['parent_conversation_id'] is None and 'parent_conversation_id' not in d['metadata']"

print_header "4. The relationship survives a backend restart"
kill "$BACKEND_PID" 2>/dev/null
wait "$BACKEND_PID" 2>/dev/null
start_backend
print_result $? "backend restarted"
LIST="$(api "/api/conversations?limit=20")"
check_json "parent link is still published after restart" "$LIST" \
  "next(c for c in d['conversations'] if c['id']=='val-child')['parent_conversation_id']=='val-parent'"

print_header "5. Deleting the parent orphans the child safely"
DELETE="$(api "/api/conversations/val-parent" -X DELETE)"
check_json "parent deleted" "$DELETE" "d['deleted'] is True"
LIST="$(api "/api/conversations?limit=20")"
check_json "orphaned child stays visible and top-level" "$LIST" \
  "any(c['id']=='val-child' and c['parent_conversation_id'] is None for c in d['conversations'])"
check_json "child transcript is still readable" "$(api "/api/conversations/val-child")" \
  "d['id']=='val-child' and len(d['messages'])==2"

print_header "6. Backend unit suite"
PYTEST_OUT="$(./test/run_tests.sh backend 2>&1)"
PYTEST_RC=$?
echo "$PYTEST_OUT" | tail -3
print_result $PYTEST_RC "backend unit tests"

print_header "Summary"
echo "Passed: $PASSED  Failed: $FAILED"
[ "$FAILED" -eq 0 ]
