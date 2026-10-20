#!/bin/bash
# Test script for PR #1098: walking back through older saved conversations
# (issue #1085).
#
# Covers the PR's test plan at the API boundary, with a real backend and a
# real DuckDB history store:
#   1. The listing reports how much of the history is left, so a client can
#      tell "there is an older page" from "that is all of them".
#   2. The pages walk the history newest first, without repeating a row and
#      without skipping one.
#   3. Every page is scoped to the caller: another account's conversations
#      never appear on any page.
#   4. A tag filter applies to every page, and the count follows it.
#   5. A conversation recorded under another security level is still refused
#      when it is opened from an older page.
#   6. Backend unit suite.
#
# The sidebar control itself (loading, error/retry, end-of-history, scroll
# position) is covered by the Vitest suites added in the PR; this script
# exercises the API data those tests consume.

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
export ATLAS_PORT
DB_PATH="$WORK/data/chat_history.db"
BACKEND_PID=""
trap 'kill "$BACKEND_PID" 2>/dev/null; rm -rf "$WORK"' EXIT
mkdir -p "$WORK/config" "$WORK/data" "$WORK/logs"
printf '{}\n' > "$WORK/config/mcp.json"
printf '{}\n' > "$WORK/config/rag-sources.json"

# Seed one account with more conversations than a single page holds, plus a
# conversation that belongs to somebody else.
seed_conversations() {
    CHAT_HISTORY_DB_URL="duckdb:///$DB_PATH" PYTHONPATH="$PROJECT_ROOT" python - <<'PY'
import os
import time

from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database

init_database(os.environ["CHAT_HISTORY_DB_URL"])
repo = ConversationRepository(get_session_factory())

OWNER = "test@test.com"
OTHER = "other@test.com"


def save(conversation_id, user, title=None, metadata=None):
    repo.save_conversation(
        conversation_id=conversation_id,
        user_email=user,
        title=title or conversation_id,
        model="gpt-4o",
        messages=[
            {"role": "user", "content": f"{conversation_id} prompt", "message_type": "chat"},
            {"role": "assistant", "content": f"{conversation_id} answer", "message_type": "chat"},
        ],
        metadata=metadata,
    )


for i in range(60):
    save(f"val-page-{i:03d}", OWNER, title=f"Page {i:03d}")
    # Distinct update times, so the order is the save order.
    time.sleep(0.002)

save("val-foreign", OTHER, title="Somebody else's chat")
repo.add_tag("val-page-005", "important", OWNER)
print("seeded")
PY
}

start_backend() {
    (
      cd "$PROJECT_ROOT/atlas" && \
      DEBUG_MODE=true FEATURE_CHAT_HISTORY_ENABLED=true FEATURE_TOOLS_ENABLED=false \
      FEATURE_COMPLIANCE_LEVELS_ENABLED=true \
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

print_header "1. Seed a history larger than one page and start a real backend on :$ATLAS_PORT"
seed_conversations
print_result $? "temporary history store seeded"
start_backend
print_result $? "backend is up"

print_header "2. The listing says how much of the history is left"
FIRST="$(api "/api/conversations?limit=50&offset=0")"
check_json "first page holds 50 conversations" "$FIRST" "len(d['conversations'])==50"
check_json "the total counts the whole history" "$FIRST" "d['total']==60"
check_json "more pages remain" "$FIRST" "d['has_more'] is True"

LAST="$(api "/api/conversations?limit=50&offset=50")"
check_json "the last page holds the remaining 10" "$LAST" "len(d['conversations'])==10"
check_json "the last page reports the end" "$LAST" "d['has_more'] is False"

print_header "3. The pages walk the history newest first, without repeating or skipping"
check_json "the first page starts at the newest conversation" "$FIRST" \
  "d['conversations'][0]['id']=='val-page-059'"
check_json "the second page continues where the first stopped" "$LAST" \
  "d['conversations'][0]['id']=='val-page-009'"
check_json "no conversation is handed out twice" \
  "$(python - <<'PY'
import json, os, subprocess
ids = []
for offset in (0, 50):
    out = subprocess.run(
        ["curl", "-sS", "-H", "X-User-Email: test@test.com",
         f"http://127.0.0.1:{os.environ['ATLAS_PORT']}/api/conversations?limit=50&offset={offset}"],
        capture_output=True, text=True, check=True).stdout
    ids += [c["id"] for c in json.loads(out)["conversations"]]
print(json.dumps({"ids": ids}))
PY
)" "len(d['ids'])==60 and len(set(d['ids']))==60"

print_header "4. Every page is scoped to the caller"
check_json "another account's conversation is on no page" \
  "$(python - <<'PY'
import json, os, subprocess
ids = []
for offset in (0, 50):
    out = subprocess.run(
        ["curl", "-sS", "-H", "X-User-Email: test@test.com",
         f"http://127.0.0.1:{os.environ['ATLAS_PORT']}/api/conversations?limit=50&offset={offset}"],
        capture_output=True, text=True, check=True).stdout
    ids += [c["id"] for c in json.loads(out)["conversations"]]
print(json.dumps({"ids": ids}))
PY
)" "'val-foreign' not in d['ids']"
check_json "the other account sees only its own conversation" \
  "$(curl -sS -H "X-User-Email: other@test.com" "http://127.0.0.1:$ATLAS_PORT/api/conversations?limit=50")" \
  "len(d['conversations'])==1 and d['conversations'][0]['id']=='val-foreign' and d['total']==1"

print_header "5. A tag filter applies to every page, and the count follows it"
TAGGED="$(api "/api/conversations?limit=50&offset=0&tag=important")"
check_json "only the tagged conversation is listed" "$TAGGED" \
  "len(d['conversations'])==1 and d['conversations'][0]['id']=='val-page-005'"
check_json "the count follows the filter" "$TAGGED" "d['total']==1 and d['has_more'] is False"
check_json "an unknown tag lists nothing" \
  "$(api "/api/conversations?limit=50&offset=0&tag=no-such-tag")" \
  "d['conversations']==[] and d['total']==0 and d['has_more'] is False"

print_header "6. A conversation recorded under another level is still refused"
REFUSED="$(curl -sS -o "$WORK/refused.json" -w '%{http_code}' \
    -H "X-User-Email: test@test.com" \
    "http://127.0.0.1:$ATLAS_PORT/api/conversations/val-page-005?compliance_level=Public")"
[ "$REFUSED" = "409" ]
print_result $? "a conversation from another level is refused (HTTP $REFUSED)"
check_json "the refusal carries no content" "$(cat "$WORK/refused.json")" \
  "'messages' not in d and d.get('error_type')=='conversation_classification'"

print_header "7. Backend unit suite"
PYTEST_OUT="$(./test/run_tests.sh backend 2>&1)"
PYTEST_RC=$?
echo "$PYTEST_OUT" | tail -3
print_result $PYTEST_RC "backend unit tests"

print_header "Summary"
echo "Passed: $PASSED  Failed: $FAILED"
[ "$FAILED" -eq 0 ]
